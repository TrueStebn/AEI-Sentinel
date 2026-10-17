# Original synthetic microbiology inputs

These are the six unchanged CSV files supplied for the Tyks Laboratories × Mylab challenge at JunctionX Turku 2026. All patients, samples and results are artificial; they do not represent an actual hospital or person. Finnish categorical labels in the source are retained as data values.

| File | Rows | Primary key |
|---|---:|---|
| patients.csv | 10,000 | patient_id |
| wards.csv | 9 | ward_code |
| antibiotics.csv | 13 | antibiotic_code |
| cultures.csv | 46,792 | culture_id |
| isolates.csv | 24,571 | isolate_id |
| susceptibility.csv | 172,381 | isolate_id + antibiotic_code |

The CSVs use comma delimiters, UTF-8 and ISO dates. Samples span 2023-10-02 to 2026-09-27. The source README describes reproducible generation with seed 2026, approximate calibration of selected E. coli/ESBL/MRSA patterns to Finres 2023, illustrative other patterns, and EUCAST Expected Resistant Phenotypes v1.2 context. These descriptions are provenance, not validation of this application as a clinical surveillance implementation.

Limitations: S/R only; no I category, MIC or zone diameters; no diagnosis or medication fields. Urinary antibiotics have urine-only reporting restrictions. Phenotype flags are separate from antibiotic S/R. See [resistance rules](../../docs/RESISTANCE_ANALYSIS.md) and [phenotype eligibility](../../docs/ADVANCED_ANOMALY_EXPLORER.md).

`SHA256SUMS.json` records the exact bytes of each supplied CSV. Git attributes disable text conversion for CSVs. This English reference summarizes the supplied bilingual dataset README. No source rows or categorical values were translated, regenerated or edited.

Initialize a new local database from the repository root:

```bash
python -m src.data_loader --expect-original
```

The application discovers this directory automatically. `AEI_DATASET_PATH` overrides it. Runtime SQLite files and private imports do not belong in this directory.
