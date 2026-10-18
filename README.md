# AEI Sentinel

AEI Sentinel is a Python application for exploring antimicrobial resistance in synthetic laboratory data. Built for the **Tyks Laboratories × Mylab challenge at JunctionX Turku 2026**, it helps users examine observed changes, sample sizes, patient overlap and the limits of statistical evidence.

**All supplied observations are synthetic. This prototype supports surveillance review, not diagnosis, prescribing or confirmation of an outbreak.**

## What the application provides

- **Overview:** local AI Smart Search with an optional AI-free manual form, healthcare-unit activity, descriptive findings and original statistically supported alerts.
- **Explore:** resistance trends, custom periods, a ward/antibiotic heatmap, phenotype monitoring, patient groups and focused investigations.
- **Reports:** the current investigation, department-focused briefings, hospital-wide summaries and aggregate PDFs.
- **Data & Settings:** local CSV imports, isolated Replay, workspace autosave and recovery.

**Guided** shows concise explanations, essential limitations and ordinary PDFs. **Expert** adds patient filters, statistical details, transitions and technical exports. Both use identical calculations and preserve the selected context. Empty trends explain why percentages are unavailable; a monthly alternative appears only when the same reporting rules are met.

The stack is **Python, Streamlit, SQLite, pandas, Plotly, SciPy and ReportLab**. Use **Python 3.12** to reproduce the validated environment. `requirements-lock.txt` records the tested runtime and test dependencies; `requirements.txt` contains runtime ranges, and `requirements-dev.txt` adds testing tools. The Streamlit minimum reflects the layout/tab APIs actually used.

## Local installation

Clone the repository and run commands from its root. Importing the bundled data creates and initializes a new SQLite database; no existing database is required.

Windows PowerShell:

```powershell
git clone https://github.com/TrueStebn/AEI-Sentinel.git
cd AEI-Sentinel
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m src.data_loader --expect-original
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

macOS / Linux:

```bash
git clone https://github.com/TrueStebn/AEI-Sentinel.git
cd AEI-Sentinel
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m src.data_loader --expect-original
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
```

Open [localhost:8501](http://localhost:8501). The initial import inserts 253,766 source rows at revision 1. Reimporting the same bundle inserts no duplicates and leaves the revision unchanged. An empty database can also be opened directly; the application initializes its schema and shows an explicit empty state.

## Smart Search

On Overview, type an English surveillance question and press Enter or Search. For example: "Show E. coli resistance to ciprofloxacin in Urology among patients aged 65 and older during the last 28 days." A local Ollama model interprets filters; the existing analytical engines calculate results. Review the recognised parameters and open the same cohort in Detailed Investigation. **Build a query manually** remains available without AI. No cloud API key or patient-record transmission is required. See [local model startup and supported requests](docs/SMART_SEARCH.md), [measured model comparison](docs/SMART_SEARCH_BENCHMARK.md) and [public-demo safeguards](docs/DEPLOYMENT.md).

## The six original synthetic CSVs

The byte-preserved inputs are included in [`data/synthetic/`](data/synthetic/README.md). Finnish source labels remain unchanged. No runtime database, patient-derived export, private workspace or Replay state is included.

| File | Rows | Meaning |
|---|---:|---|
| `patients.csv` | 10,000 | Synthetic patient references |
| `wards.csv` | 9 | Healthcare-unit references |
| `antibiotics.csv` | 13 | Antibiotic references and reporting scope |
| `cultures.csv` | 46,792 | Samples, dates, ward and specimen context |
| `isolates.csv` | 24,571 | Organisms and phenotype flags |
| `susceptibility.csv` | 172,381 | Isolate/antibiotic S/R results |

Samples span **2023-10-02 through 2026-09-27**. The default reporting date follows the latest sample, not today's clock. Set `AEI_DATASET_PATH` for another six-file directory, `AEI_DATABASE_PATH` for a different laboratory database, or `AEI_WORKSPACE_DATABASE_PATH` for separate workspace storage. Defaults are relative to the application location; runtime directories are created as needed. See [Database design](docs/DATABASE_DESIGN.md) and [Deployment](docs/DEPLOYMENT.md).

## Try the artificial surveillance scenario

Open **Data & Settings → Data Source → Simulated Surveillance Scenario**. Press **Next Batch** three times. Two batches give current 10% and baseline 10%; the third gives current 40%, baseline 10%, a +30 percentage-point change, one descriptive signal and one original Fisher/BY-supported exploratory alert. This deliberately constructed scenario has 200 distinct patients and its own database. It never modifies the original laboratory source. **Historical Replay** instead introduces the original samples in chronological weekly packets. See the [demo runbook](docs/DEMO_RUNBOOK.md).

## Interpretation and prototype limits

Resistance is `100 × R / (S + R)` among first isolates selected per patient, organism, specimen and reporting period **before** test lookup. Missing tests are not susceptible. Percentages require at least 30 tested first isolates; insufficient and absent data remain unavailable, including in charts. Weekly/monthly buckets are independently deduplicated and must not be summed to derive a whole-period rate.

The current 28 days and historical 365-day anomaly baseline do not overlap; the rolling annual display overlaps current. Descriptive preliminary signals differ from statistically supported alerts. Independent-sample Fisher tests require adequate counts and no detected patient dependence; the original scan applies Benjamini-Yekutieli correction. Patient-aware exact McNemar and restricted sensitivity results are additional exploratory evidence, not new global alerts. Repeated exploration, unmeasured confounding and synthetic-data limitations remain. Detailed [statistical documentation](docs/README.md) explains these rules.

**An owner-operated synthetic demo is available at [AEI Sentinel](https://secular-bundle-uncapped.ngrok-free.dev/).** ngrok Free may first show a Visit Site button; no registration is required. Availability depends on the owner PC, network and provider quotas. The bounded Windows deployment uses a dedicated standard account, kernel resource limits and shared HTTP/WebSocket admission controls. See [public hosting, verified behavior and shutdown](docs/PUBLIC_HOSTING.md).

Developer mode retains local imports/workspaces and must remain private. `AEI_PUBLIC_DEMO=1` uses verified immutable synthetic snapshots, temporary visitor state, predefined Replay and shared AI/analytics budgets; persistent restoration and imports are disabled below the UI. Only pinned Qwen3 8B/Q4_K_M is accepted. [Start-PublicDemo.ps1](Start-PublicDemo.ps1) is the [local-only baseline](docs/DEPLOYMENT.md); it does not provide the dedicated-account isolation required for the public endpoint. See [Security](SECURITY.md).

## Tests and project structure

```powershell
.venv\Scripts\python.exe -m pytest -q
```

On macOS/Linux use `.venv/bin/python`. Tests use temporary databases; bundled-data checks verify counts and first-isolate reference results. Import the original bundle first to enable the working-database read-only integration checks. For development with version ranges, install `requirements-dev.txt` instead of the lock file.

`app.py` is the entry point; `src/` contains analytical services and presentation, `db/schema.sql` is the schema, `tests/` contains regressions, `assets/` contains the local brand mark, and `docs/` contains current technical explanations. Generated artifacts are ignored. Start with the [documentation index](docs/README.md).
