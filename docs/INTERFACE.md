# Application interface

The app has four destinations, sharing a source, revision, selected cohort and analysis date. The header identifies Synthetic Demo Data, active source/revision, View mode and the date popover. Data & Settings contains Replay, imports and private workspace controls.

## Navigation

| Destination | Main workflow |
|---|---|
| Overview | Select a healthcare unit or investigate an original finding |
| Explore | Surveillance Map, Resistance Trends, Detailed Period or Investigation |
| Reports | Current Investigation or Surveillance Briefing |
| Data & Settings | Choose source, operate Replay, manage private workspaces and local imports |

Guided presents concise summaries, patient groups, ordinary PDFs and essential limitations. Expert adds advanced patient filters, statistical methods, transitions, diagnostics and technical JSON/CSV downloads. Technical panels are omitted from Guided, not merely collapsed. View mode changes presentation only; hidden filters remain active and are identified. Expert is not an access-control role.

## Shared controls and dates

Ward/organism/antibiotic/specimen options come from SQLite. The default date follows the newest source sample; selecting a date pins it, and Use latest sample resumes following. An empty source has no invented date. Detailed Period has an independent inclusive range; it does not change the fixed current/baseline windows.

Investigation applies all active patient/healthcare filters. The retained basic Resistance Trends and Detailed Period tools use ward/organism/antibiotic/specimen scope and explicitly identify when retained patient filters apply only in Investigation. Source-specific preferences, mode and navigation autosave; reconnect restores validated choices before widgets. Idle revision polling uses the existing nonblocking Streamlit fragment.

## Charts and empty states

No reportable percentage means no Plotly axes, grid or legend. A compact panel distinguishes Insufficient Data, No Data and Unsupported Combination. A valid aggregate result is still shown when its temporal buckets are unavailable. Partial series retain nulls and gaps; lines do not bridge missing periods. One valid temporal value uses a marker. Real 0% and 100% remain valid at adequate N. Phenotypes use eligible first isolates; antibiotic percentages use eligible tested first isolates.

When weekly values are unavailable, View monthly trend is offered only if the existing monthly results meet the same minimum. The frequency visibly changes and retains source/date/cohort/patient filters. If monthly values are also unavailable, there is an explanation and no ineffective action. Expert adds separate unavailable-period counts; Guided does not acquire technical status tables. Independently deduplicated buckets must not be summed.

The retained basic trend uses the rolling annual calendar; Investigation uses its existing current-plus-baseline calendar. For PHC-S / E. coli / AMP / urine at 2026-09-27 these contain 53 and 57 weeks respectively, all unavailable, while the 28-day aggregate remains 34/74 = 45.95%. The difference is a reporting-window distinction, not a new calculation.

## Report selection

Navigation from a matching active antibiotic investigation defaults to Current Investigation. Without a valid context, Reports defaults to Surveillance Briefing / Hospital-wide summary. Restoring a workspace already on Reports retains its saved selection. Visible descriptions explain the alternatives.

Current Investigation includes the selected ward, organism, antibiotic, specimen and patient filters. The selected source, revision, date and exact scope appear above Generate investigation PDF. Surveillance Briefing offers Hospital-wide summary or Department-focused briefing; the latter uses ward/organism/specimen plus the selected antibiotic and excludes patient filters. Its legacy stored value remains `Selected investigation` for compatibility. Hospital scope explicitly includes all source wards.

Ordinary PDFs are available in both modes. Technical manifests/downloads are Expert controls. PDF generation is explicit; changing source/version/date/filters invalidates prepared downloads. Reports retain S/R/N, source/revision and method limitations and never include patient-level identifiers or private restore URLs. See [investigation integrity](INVESTIGATION_EXPERIENCE.md).

## Errors and safeguards

Mixed source revisions cause a refresh message instead of combined evidence. Validation, unavailable data, failed imports and workspace-save errors have explicit messages; save failures keep unsaved state in memory and offer retry. Imports can change the shared source only through Data & Settings / Expert local controls, with all existing transactional validation. This unauthenticated prototype must remain local until the [deployment blockers](DEPLOYMENT.md) are addressed.
