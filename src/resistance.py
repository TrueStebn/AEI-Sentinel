"""Read-only S/R surveillance calculations using first isolates per period.

Deduplicate by patient, organism and specimen after cohort/date filtering,
before looking up the chosen antibiotic. Each series bucket is its own period.
No clinical interpretation or treatment recommendations are produced.
"""

from calendar import monthrange
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Literal

from src.config import Settings, get_settings
from src.database import connect
from src.cohort_queries import ranked_isolates_sql

DateInput = date | str
ResultStatus = Literal["ok", "insufficient_data", "no_data"]
Frequency = Literal["week", "month"]


@dataclass(frozen=True)
class ResistanceFilters:
    """One antibiotic is required; None means no restriction for other fields."""

    antibiotic_code: str
    ward_code: str | None = None
    organism: str | None = None
    specimen: str | None = None

    def __post_init__(self):
        for name, value in asdict(self).items():
            if value is None and name != "antibiotic_code":
                continue
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a nonempty string without surrounding whitespace")
        if self.specimen not in (None, "urine", "blood", "wound"):
            raise ValueError("specimen must be urine, blood, wound or None")


@dataclass(frozen=True)
class ResistanceResult:
    """Aggregate counts; resistance_pct is absent below the configured threshold.

    n_first_isolates includes untested first isolates. n_tested counts only those
    with an S/R result for the chosen antibiotic and is the rate denominator.
    Unanchored rolling windows on an empty database have no period dates.
    """

    start_date: date | None
    end_date: date | None
    filters: ResistanceFilters
    n_first_isolates: int
    n_susceptible: int
    n_resistant: int
    n_tested: int
    resistance_pct: float | None
    status: ResultStatus
    minimum_sample_size: int
    data_version: int

    def to_dict(self) -> dict:
        """Return JSON-safe aggregate data, including ISO date strings."""
        result = asdict(self)
        result["start_date"] = self.start_date.isoformat() if self.start_date else None
        result["end_date"] = self.end_date.isoformat() if self.end_date else None
        return result


@dataclass(frozen=True)
class ResistanceTimeSeries:
    """Chronological, independently deduplicated calendar buckets, including gaps."""

    start_date: date
    end_date: date
    frequency: Frequency
    filters: ResistanceFilters
    minimum_sample_size: int
    data_version: int
    points: tuple[ResistanceResult, ...]

    def to_dict(self) -> dict:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "frequency": self.frequency,
            "filters": asdict(self.filters),
            "minimum_sample_size": self.minimum_sample_size,
            "data_version": self.data_version,
            "points": [point.to_dict() for point in self.points],
        }


def _date(value: DateInput, name: str) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise ValueError(f"{name} must be a date or a valid YYYY-MM-DD string")


def _period(start_date: DateInput, end_date: DateInput) -> tuple[date, date]:
    start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date must not be after end_date")
    return start, end


class ResistanceEngine:
    """Query an existing version-1 database without initialization or data writes.

    Each call pins source revision, latest sample date and counts to one SQLite
    read snapshot. Calls do not share a connection or user filter state.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @contextmanager
    def _snapshot(self):
        with connect(self.settings, read_only=True) as con:
            con.execute("BEGIN")
            if con.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("Resistance analysis requires an initialized version-1 database")
            version = con.execute(
                "SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
            latest = con.execute("SELECT MAX(sample_date) FROM cultures").fetchone()[0]
            yield con, version, _date(latest, "latest sample date") if latest else None

    def _result(self, filters, start, end, first, susceptible, resistant, version):
        tested = susceptible + resistant
        threshold = self.settings.minimum_sample_size
        status: ResultStatus = ("no_data" if tested == 0 else
                                "insufficient_data" if tested < threshold else "ok")
        return ResistanceResult(
            start_date=start, end_date=end, filters=filters, n_first_isolates=first,
            n_susceptible=susceptible, n_resistant=resistant, n_tested=tested,
            resistance_pct=100.0 * resistant / tested if status == "ok" else None,
            status=status, minimum_sample_size=threshold, data_version=version,
        )

    def _calculate(self, con, filters, start, end, version):
        conditions = ["c.sample_date BETWEEN :start_date AND :end_date"]
        parameters = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                      "antibiotic_code": filters.antibiotic_code}
        for column, name in (("c.ward_code", "ward_code"), ("i.organism", "organism"),
                             ("c.specimen", "specimen")):
            value = getattr(filters, name)
            if value is not None:
                conditions.append(f"{column} = :{name}")
                parameters[name] = value
        # Only fixed column names enter SQL text; all caller-supplied values are bound.
        row = con.execute(f"""
            WITH ranked AS (
                {ranked_isolates_sql(conditions)}
            )
            SELECT COUNT(*) AS n_first_isolates,
                   COALESCE(SUM(CASE WHEN s.result='S' THEN 1 ELSE 0 END),0) AS n_susceptible,
                   COALESCE(SUM(CASE WHEN s.result='R' THEN 1 ELSE 0 END),0) AS n_resistant
            FROM ranked AS r
            LEFT JOIN susceptibility AS s
              ON s.isolate_id = r.isolate_id AND s.antibiotic_code = :antibiotic_code
            WHERE r.isolate_rank = 1
        """, parameters).fetchone()
        return self._result(filters, start, end, row["n_first_isolates"],
                            row["n_susceptible"], row["n_resistant"], version)

    def calculate(self, filters: ResistanceFilters, *, start_date: DateInput,
                  end_date: DateInput) -> ResistanceResult:
        """Calculate an arbitrary inclusive reporting period."""
        start, end = _period(start_date, end_date)
        with self._snapshot() as (con, version, _):
            return self._calculate(con, filters, start, end, version)

    def _rolling(self, filters, days, as_of_date):
        explicit_end = _date(as_of_date, "as_of_date") if as_of_date is not None else None
        with self._snapshot() as (con, version, latest):
            end = explicit_end if explicit_end is not None else latest
            if end is None:
                return self._result(filters, None, None, 0, 0, 0, version)
            try:
                start = end - timedelta(days=days - 1)
            except OverflowError as exc:
                raise ValueError("as_of_date is too early for the requested rolling window") from exc
            return self._calculate(con, filters, start, end, version)

    def last_28_days(self, filters: ResistanceFilters, *,
                     as_of_date: DateInput | None = None) -> ResistanceResult:
        """Use [as_of_date - 27 days, as_of_date], defaulting to MAX(sample_date)."""
        return self._rolling(filters, 28, as_of_date)

    def last_365_days(self, filters: ResistanceFilters, *,
                      as_of_date: DateInput | None = None) -> ResistanceResult:
        """Use a rolling 365-day window, overlapping last_28_days, not a calendar year."""
        return self._rolling(filters, 365, as_of_date)

    def time_series(self, filters: ResistanceFilters, *, start_date: DateInput,
                    end_date: DateInput, frequency: Frequency = "week") -> ResistanceTimeSeries:
        """Use Monday-Sunday weeks or calendar months, clipped to the requested range.

        Every point independently selects first isolates inside its clipped
        period. Its denominator is not a partition of the whole-period cohort.
        Empty points have no_data status and None percentage, never an assumed 0%.
        """
        start, end = _period(start_date, end_date)
        if frequency not in ("week", "month"):
            raise ValueError("frequency must be week or month")
        points = []
        with self._snapshot() as (con, version, _):
            current = start
            while True:
                remaining = (6 - current.weekday() if frequency == "week" else
                             monthrange(current.year, current.month)[1] - current.day)
                bucket_end = current + timedelta(days=min(remaining, (end - current).days))
                points.append(self._calculate(con, filters, current, bucket_end, version))
                if bucket_end == end:
                    break
                current = bucket_end + timedelta(days=1)
        return ResistanceTimeSeries(start, end, frequency, filters,
                                    self.settings.minimum_sample_size, version, tuple(points))
