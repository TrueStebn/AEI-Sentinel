# Presentation and chart contract

## Context and modes

Overview, Explore, Reports and Data & Settings share one canonical source-scoped cohort. Guided and Expert use the same analytical services, thresholds and primary typography. Mode actions preserve source, date/follow-latest, ward, organism, antibiotic, specimen, patient restrictions and original finding locator. Missing optional fields in older format-1 workspaces receive defaults; old page identifiers normalize to current destinations.

Native Streamlit widgets use temporary keys and copy changes into non-widget canonical values. Hidden Expert controls do not clear patient restrictions. PDF inclusion preferences retain their canonical value when their Expert widget disappears. A source switch stores and restores that source's preferences separately. `presentation_mode` and `explorer_frequency` are saved preferences; PDF bytes and analytical results are not.

## Visual system

The local brand mark is in `assets/brand-mark.svg`. The palette uses navy #142C48, teal #177E91, background #F4F7FB and restrained amber #D8A04C. Theme textColor #001428 compensates for native caption opacity. Colors indicate presentation and descriptive emphasis, not clinical safety thresholds.

Primary metrics are 36 px in both modes; body/caption sizes remain readable and unchanged by mode. The sidebar contains the mark/name; the main header has a text title, source, synthetic label, View mode and accessible date popover. Routine autosave details live in Data & Settings; failures remain visible from other routes.

Unit cards use wrapping native containers at nominal 240 × 290 px. Names, culture count, findings and latest sample have reserved space. Selection changes native button/tint and a text marker without adding controls or changing height. Actions appear in a separate selected-unit panel. Native focus outlines and keyboard-friendly selectors complement pointer selection. Narrow layouts wrap/stack and may need vertical scrolling. These measures are not a complete production accessibility certification.

CSS targets owned classes and explicit keys; no generated hash selectors, nth-child layout rules, browser DOM mutation or external frontend framework is required.

## Availability before Plotly

`chart_states` reads existing immutable results, without extra SQL or recalculation. No valid percentage means a compact information panel instead of creating a figure. Distinct reasons are Insufficient Data, No Data and Unsupported Combination. Partial Data retains gaps; Single Valid Observation uses marker-only temporal rendering. Valid 0% and 100% remain real measurements. Denominators are named according to antibiotic or phenotype mode.

All calendar buckets remain in a temporal series; unavailable percentages are null, never zero. `connectgaps=False` prevents interpolation. Advanced count bars are masked where percentages are unavailable so those points do not scale the count axis; Expert evidence retains real unavailable-period counts. A weekly empty state offers a monthly alternative only when already-calculated monthly values meet the same minimum. Guided stays concise; Expert adds the unavailable-period breakdown. Neither reduces sample thresholds.

This applies to retained basic/custom-period trends, Investigation antibiotic/phenotype trends, group comparisons, selected Briefing trends and empty aggregate comparisons. Patient transitions require complete unambiguous pairs. The Surveillance Map retains labeled ND/IN/NA availability cells, which convey meaningful quality information.

## Report scope and integrity

Current Investigation is the contextual Reports default only when the active antibiotic investigation matches source, date, cohort, code and mode. Otherwise the default is Hospital-wide summary in Surveillance Briefing. Restoring Reports preserves saved choices. Scope descriptions and a source/version/date/cohort panel precede generation; patient filters are named explicitly. Department-focused Briefing excludes patient filters and says so.

Normal PDFs remain available in Guided; full technical exports are Expert controls. Existing cache signatures, before/after revision checks, aggregate-only manifests and escaped PDF text remain. Empty PDF comparisons become notes. The investigation PDF identifies unavailable weekly coverage while retaining valid aggregate results and methodological limits.

## Limits

Basic tools and Briefing retain their documented cohort scopes; Current Investigation is the patient-filtered report path. Chart availability is descriptive and cannot establish clinical infection, cause or treatment suitability. The original scan, test family, first-isolate rules and patient-aware populations remain separate from presentation. Private bearer-token state is not production authentication; hiding widgets is not authorization. See [Security](../SECURITY.md) and [Deployment](DEPLOYMENT.md).
