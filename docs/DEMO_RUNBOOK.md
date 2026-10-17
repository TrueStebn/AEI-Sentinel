# AEI Sentinel: 110-second local demonstration

For the protected Public Demo process, first follow [Windows preparation/startup](DEPLOYMENT.md). Use only the predefined three-step scenario; Historical Replay, imports and persistent workspaces are unavailable there. Warm the pinned Qwen3 8B model locally before the presentation and keep Manual Search available. Leave GPU capacity free for the model; competing games/renderers can exhaust VRAM and cause bounded inference timeouts. The instructions below otherwise describe the original Developer Mode workflow.

## Prepare

- Follow the [README](../README.md), import the six original synthetic CSVs, and run `python -m streamlit run app.py --server.address 127.0.0.1` inside the virtual environment.
- Keep the service local. Use a private workspace and keep its URL/token out of recordings and shared screenshots.
- Confirm Laboratory Dataset, revision 1, date 2026-09-27. Start in Guided. Use Expert for the detailed patient-aware segment; mode changes preserve context.
- Preflight the original PHC-S finding, normal PDF download and three-batch Scenario. A saved workspace is a preference/replay reference, not a database backup.

## Live sequence

| Time | Action and narration |
|---|---|
| 0-15 s | Show Overview: healthcare units, two descriptive preliminary signals and zero supported alerts. No alert does not establish no resistance. |
| 15-30 s | Investigate PHC-S / E. coli / AMP / urine. Read current 45.95%, historical 30.65%, +15.30 pp, N=74 versus 620 and 33 shared patients. The independent full-population test is withheld. |
| 30-55 s | Switch to Expert and open Patient-Aware Analysis. SS/SR/RS/RR = 8/13/7/5; exact McNemar raw p=0.263176. Excluding shared patients gives +8.70 pp, raw Fisher p=0.293650. These are different populations and neither changes the original global alert. |
| 55-85 s | Data & Settings → Simulated Surveillance Scenario. State explicitly that it is artificial and isolated. Advance three fresh batches; current S/R/N=60/40/100, baseline=90/10/100, +30 pp, Fisher/BY p≈1.24334e-6, family 1, one original supported alert. |
| 85-110 s | Return to Laboratory Dataset, reopen PHC-S and enter Reports. Verify Current Investigation and its source/date/cohort before generating the aggregate PDF. Point out S/R/N, overlap, paired/restricted populations, unadjusted evidence and limitations. |

## Chart and scope walkthrough

On the PHC-S investigation choose Weekly: all 57 existing Investigation periods are unavailable, so a compact explanation replaces the axes while the aggregate remains 45.95%. The retained basic Resistance Trends view uses 53 existing weeks. Choose View monthly trend to display valid monthly values without lowering N.

From Reports, deliberately choose Surveillance Briefing for a Hospital-wide summary or Department-focused briefing. Read the selected scope before generation. Current Investigation includes patient filters; department Briefing does not. Keep source/version/date visible.

In a fresh Scenario show No Data before imports. After three batches, Investigation / Weekly has two separate valid observations with missing intervening weeks and no connecting line. Return to Laboratory Dataset and verify restored source preferences.

## Fallback

If Replay fails, keep the laboratory evidence intact. Use a separately preflighted completed Scenario workspace only after confirming source, revision and values. Otherwise show a clearly identified saved demonstration screenshot/report and say the live replay is unavailable. Do not substitute Scenario values for laboratory results.

If download/viewing is slow, identify a prepared same-revision PDF as pre-generated; do not claim it was just created. If a browser PDF preview is blank, use a local viewer. Do not describe findings as clinical treatment advice or confirmation of an outbreak.
