"""Read-only source metadata for interface controls; no analytical calculations."""

from dataclasses import dataclass
from datetime import date

from src.config import Settings
from src.database import SOURCE_TABLES, connect


@dataclass(frozen=True)
class DataCatalog:
    wards: dict[str, str]
    antibiotics: dict[str, str]
    organisms: tuple[str, ...]
    specimens: tuple[str, ...]
    default_antibiotic: str | None
    default_organism: str | None
    default_specimen: str | None
    first_sample: date | None
    latest_sample: date | None
    version: int
    updated_at: str
    counts: dict[str, int]
    last_successful_import: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.latest_sample and self.antibiotics and self.organisms and self.specimens)


def load_catalog(settings: Settings) -> DataCatalog:
    with connect(settings, read_only=True) as con:
        con.execute("BEGIN")
        revision = con.execute("SELECT version,updated_at FROM data_version WHERE singleton_id=1").fetchone()
        bounds = con.execute("SELECT MIN(sample_date),MAX(sample_date) FROM cultures").fetchone()
        wards = {r[0]: f"{r[1]} ({r[0]})" for r in con.execute(
            "SELECT ward_code,ward_en FROM wards ORDER BY ward_en,ward_code")}
        antibiotics = {r[0]: f"{r[1]} ({r[0]})" for r in con.execute(
            "SELECT antibiotic_code,antibiotic FROM antibiotics ORDER BY antibiotic,antibiotic_code")}
        organisms = tuple(r[0] for r in con.execute("SELECT DISTINCT organism FROM isolates ORDER BY organism"))
        specimens = tuple(r[0] for r in con.execute("SELECT DISTINCT specimen FROM cultures ORDER BY specimen"))
        preferred = []
        for query in (
            "SELECT antibiotic_code FROM susceptibility GROUP BY antibiotic_code ORDER BY COUNT(*) DESC,antibiotic_code LIMIT 1",
            "SELECT organism FROM isolates GROUP BY organism ORDER BY COUNT(*) DESC,organism LIMIT 1",
            "SELECT specimen FROM cultures GROUP BY specimen ORDER BY COUNT(*) DESC,specimen LIMIT 1",
        ):
            row = con.execute(query).fetchone()
            preferred.append(row[0] if row else None)
        return DataCatalog(
            wards, antibiotics, organisms, specimens,
            preferred[0] or next(iter(antibiotics), None), preferred[1], preferred[2],
            date.fromisoformat(bounds[0]) if bounds[0] else None,
            date.fromisoformat(bounds[1]) if bounds[1] else None,
            revision[0], revision[1],
            {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in SOURCE_TABLES},
            con.execute("SELECT MAX(finished_at) FROM import_batches WHERE status='completed'").fetchone()[0],
        )
