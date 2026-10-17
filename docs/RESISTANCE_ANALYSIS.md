# Resistance analysis engine

## Scope and data source

The engine queries the existing schema-version-1 SQLite database through `src.database.connect(..., read_only=True)`. It does not initialize a database, change laboratory records, import files, populate caches or persist user state. Missing databases raise an SQLite error without creating a file or directory. No additional dependencies or schema migrations are needed.

The original dataset README was found in the sibling `../data/raw/synthetic_microbiology_dataset/synthetic_microbiology_dataset/README.md`. It specifies the first isolate per patient, species and specimen type in each reporting period, S/R results only, and a minimum of 30 isolates. This module implements those reporting rules without adding clinical treatment or susceptibility interpretation rules.

## API

```python
from src.resistance import ResistanceEngine, ResistanceFilters

engine = ResistanceEngine()  # Alternatively pass an existing Settings instance.
filters = ResistanceFilters(
    antibiotic_code="CIP",
    ward_code="URO",
    organism="Escherichia coli",
    specimen="urine",
)

recent = engine.last_28_days(filters)
annual = engine.last_365_days(filters, as_of_date="2026-09-27")
custom = engine.calculate(filters, start_date="2026-01-01", end_date="2026-06-30")
weekly = engine.time_series(
    filters, start_date="2026-08-31", end_date="2026-09-27", frequency="week",
)
monthly = engine.time_series(
    filters, start_date="2025-09-28", end_date="2026-09-27", frequency="month",
)
payload = recent.to_dict()  # JSON-safe dates, filters, counts and metadata.
points = monthly.to_dict()["points"]
```

The antibiotic is a required single code, ensuring one denominator per antibiotic. Other filters are optional; `None` means all available values. Identifiers and organism names are exact, case-sensitive values from the source, and SQL parameters are bound. Unknown well-formed codes/names return no matching results. Unsupported specimen types and malformed, empty or whitespace-padded filters raise `ValueError`.

Dates accept Python `date` objects or exact ISO `YYYY-MM-DD` strings; timestamps and reversed periods are rejected. Both endpoints are inclusive. Custom periods and series require explicit endpoints. `as_of_date` can be changed by a future interface; when omitted, it is the global maximum `cultures.sample_date`, including cultures outside the selected filters and cultures without isolates. The computer date is never used. An explicit end date is respected even when it falls beyond the available samples; it is not silently clamped.

## First-isolate selection

1. Restrict cultures by `sample_date`, optional ward and specimen, and restrict isolates by optional organism.
2. Rank isolates within `(patient_id, organism, specimen)` by `(sample_date, culture_id, isolate_no, isolate_id)` ascending. These are real source columns; the final ID provides a deterministic final tie-breaker.
3. Retain rank 1 in each group, regardless of whether that isolate has the chosen antibiotic result.
4. Left-join the chosen antibiotic using the unique `(isolate_id, antibiotic_code)` susceptibility key. The join can contribute at most one S/R result per selected isolate.

There is no replacement by a later isolate when the first was untested. Neither an additional antibiotic result nor a second isolate of the same species in the same culture increases the selected group's denominator. Another species or specimen creates a separate group.

Ward policy follows the supplied database design: filtering one ward selects the first isolate within that ward and period. With no ward filter, deduplication spans all wards and ward is not part of the grouping key. This is an explicit prototype policy, not a new clinical convention. When organism or specimen filters are omitted, counts combine the separately deduplicated groups; they are not patient counts or culture counts.

## Result contract and sample threshold

`ResistanceResult` is immutable and contains:

| Field | Meaning |
| --- | --- |
| `start_date`, `end_date` | Inclusive period bounds, as dates |
| `filters` | Immutable applied filter values |
| `n_first_isolates` | Selected first isolates, including those without the selected test |
| `n_susceptible`, `n_resistant` | S and R results on those first isolates for one antibiotic |
| `n_tested` | `S + R`, the rate denominator and threshold population |
| `resistance_pct` | `100.0 * R / (S + R)` at or above the threshold; otherwise `None` |
| `status` | `ok`, `insufficient_data` or `no_data` |
| `minimum_sample_size` | Applied configured threshold |
| `data_version` | Source revision from the same read snapshot as the counts |

The engine preserves the unrounded percentage. A future UI may round for display while retaining counts. `ok` means the sample-size rule was met; it is not a significance test, confidence interval or clinical reliability claim.

The default threshold is 30 in `Settings.minimum_sample_size`. Override it with `AEI_MINIMUM_SAMPLE_SIZE` or a supplied `Settings` instance. Only integers of at least 30 are accepted. The threshold is applied to tested first isolates, not all raw cultures, all raw tests or all selected but untested first isolates.

- `n_tested == 0`: `no_data`, percentage `None`, S/R counts 0. `n_first_isolates` still distinguishes an empty cohort from first isolates with no selected test.
- `0 < n_tested < threshold`: `insufficient_data`, percentage `None`, actual counts retained.
- `n_tested >= threshold`: `ok`, percentage calculated, including a real 0% or 100% if supported by the counts.

No missing percentage is replaced with zero. On an initialized database with no cultures, a rolling call without `as_of_date` returns `no_data` with both period dates `None`; it does not invent a date. An explicit `as_of_date` or custom period preserves the requested bounds even on empty data.

## Periods and time series

For reference date `d`:

- Recent: `[d - 27 days, d]`, exactly 28 days.
- Annual: `[d - 364 days, d]`, exactly 365 days, including when a leap day is present. This is not the current calendar year or a 366-day interval.

The recent and annual periods overlap, and first-isolate selection is recomputed independently in each. Their percentages can have different selected patients/isolates. Future anomaly detection must request a separate historical baseline ending before the recent window begins.

`ResistanceTimeSeries` contains frequency, overall inclusive bounds, applied filters, sample threshold, data revision and a chronological tuple of `ResistanceResult` points:

- `week`: calendar Monday through Sunday, including weeks crossing December/January without splitting at January 1.
- `month`: calendar months, including leap-year February.
- The first and last buckets are clipped to the requested bounds and use only observations inside those clipped bounds.
- Every bucket is a separate reporting period and independently selects its first isolates. One patient/species/specimen group can appear once in multiple buckets. The sum of bucket denominators need not equal the denominator for one calculation over the entire range. Percentages must not be averaged to derive the whole-period rate.
- Empty buckets remain present with `no_data` and `None` percentage. The minimum sample threshold is applied separately to each bucket. There is no interpolation or carry-forward.

Each public call uses a single SQLite read transaction for metadata and calculations. A concurrent successful import cannot mix a new dataset revision with old counts or change data midway through a time series. Subsequent calls see the committed revision. Different public calls can observe different revisions if data changes between them; callers should compare their `data_version` before combining results.

## Verified examples from the existing synthetic database

Reference date **2026-09-27**, source revision **1**. Recent period **2026-08-31–2026-09-27**; annual period **2025-09-28–2026-09-27**. Percentages below are rounded for documentation only.

| Cohort | Period | S | R | S + R | R% | Status |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| URO, E. coli, urine, TMP | Recent | 26 | 7 | 33 | 21.21% | ok |
| URO, E. coli, urine, TMP | Annual | 256 | 81 | 337 | 24.04% | ok |
| URO, E. coli, urine, CIP | Recent | 28 | 5 | 33 | 15.15% | ok |
| URO, E. coli, urine, CIP | Annual | 306 | 31 | 337 | 9.20% | ok |
| All wards, E. coli, urine, CIP | Recent | 251 | 23 | 274 | 8.39% | ok |
| All wards, E. coli, urine, CIP | Annual | 2,334 | 223 | 2,557 | 8.72% | ok |
| ICU, S. aureus, blood, FOX | Recent | 1 | 0 | 1 | — | insufficient_data |
| ICU, S. aureus, blood, FOX | Annual | 7 | 5 | 12 | — | insufficient_data |

These are synthetic surveillance counts and are not treatment recommendations. The working database was queried without reimporting the source dataset. Its database-file SHA-256 was unchanged before and after verification.

## Validation

`tests/test_resistance.py` covers manual cohorts, sample-size boundaries 0/1/29/30/45, true 0%/100%, each filter, first untested isolates, tie-breaking, all-ward versus selected-ward policy, period boundaries, leap years, independent reporting windows, calendar series, empty buckets, parameter validation, JSON serialization, deterministic results, read-only safety and consistent snapshots during a concurrent commit. A read-only working-database integration test compares counts against an independent Python reference that reads isolates and tests separately, sorts observations and keeps the first group occurrence without SQL window functions.
