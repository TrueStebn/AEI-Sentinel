# Investigation evidence and PDF integrity

## Separate original and selected populations

An Investigate action preserves the original exact finding's source identity, revision, date and ward/organism/antibiotic/specimen. Investigation Summary describes the currently selected cohort independently, including patient restrictions. Changing a filter does not rewrite the original finding or inherit its global alert classification. If the originating revision is unavailable, the locator remains and old evidence is withheld.

The summary reuses AdvancedReport and PatientAwareReport: current/historical rates, N, difference, preliminary status and overlap. No new test or alert rule is introduced. Patient-aware evidence distinguishes the full population, complete returning-patient pairs and the restricted population excluding all shared observed patients. Those estimands must not be conflated. See [patient-aware methods](PATIENT_AWARE_SURVEILLANCE.md).

## Presentation and report scope

Guided shows the essential result, availability and limitations; Expert exposes complete populations, methods and technical exports. Reports defaults to Current Investigation after a valid active investigation. The exact source, revision, date, ward, organism, antibiotic, specimen and retained patient filters are identified above generation. Hospital-wide and department-focused alternatives are explicit in Surveillance Briefing. Phenotype aggregate exports remain separate from antibiotic patient-aware PDFs.

## Export integrity

The investigation PDF and its canonical aggregate manifest use already-computed, matching advanced/patient-aware reports. Source identities, revisions, dates, cohorts and S/R/N must match. Source revision is checked before and after rendering and again before download. Full-precision manifest data has a canonical SHA-256 printed in the PDF; displayed percentages are rounded only for presentation.

Complete input signatures invalidate session-local downloads after a source/version/date/filter/threshold change. Generating a report is explicit. PDF bytes are not serialized in workspace preferences and do not silently refresh after an import. Reproduction requires the stated source revision, input settings and generation timestamp; old source revisions are not archived automatically.

Reports contain aggregate evidence only: actual S/R/N, first-isolate counts, observed-patient membership, paired transitions when available, restricted comparisons, source metadata, tests, thresholds and limitations. No patient/culture/isolate identifiers, private token, restore URL or personal filesystem path are included. ReportLab XML text is escaped and bundled fonts avoid a host-font dependency.

An unavailable weekly chart is represented by a short note with actual period counts and minimum N, retaining a valid 28-day result. Empty Briefing comparisons use text instead of empty vector axes. Patient-transition diagrams require complete pairs; ambiguous or missing evidence is labeled unavailable. Existing partial aggregate comparisons retain their availability labels.

## Operational limits

Manifest digests verify report inputs, not authenticity of the source or clinical certification. The hashed source identity describes a configured path, not an immutable source-file archive. Repeated selected-cohort tests remain exploratory and unadjusted; they never create a new original global BY alert. Restore tokens and runtime files remain private. The application is a local synthetic prototype; [public hosting needs separate safeguards](DEPLOYMENT.md).
