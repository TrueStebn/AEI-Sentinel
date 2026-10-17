# Smart Search and local inference

Smart Search is the primary entry on Overview. Write an English question and press Enter or Search once. Typing never starts inference. Explore provides a Smart Search action that returns to the same Overview interface. The single-line field has ordinary text editing and keyboard behavior. There is deliberately no autocomplete or sentence constructor: text is never rewritten while typing.

**Build a query manually** is collapsed by default. It runs without Ollama and also offers corrections after a successful interpretation. Both paths use `SmartQuery`, source metadata validation and `execute_query`; the AI never supplies statistics or SQL. Parameters and source revision appear before results. A failed new search removes the previous result rather than displaying it as the answer.

## Supported operations

| Operation | Existing engine and scope |
|---|---|
| Resistance summary / resistant first-isolate count | AdvancedAnalysisEngine; ward, organism, antibiotic, specimen, sex and inclusive integer age range |
| Historical comparison | Existing current 28 days vs preceding non-overlapping 365 days; original dependence and sample-size guards |
| Patient-group comparison | Existing descriptive sex, fixed age groups or ward groups, with unrelated restrictions retained; no new significance test |
| Monthly trend | Existing monthly calendar buckets across the current-plus-historical context; independently ranked, not pooled |
| Unusual increases / anomaly scan | Complete original AnomalyEngine scan and BY family first, positive-change display filtering second; no age/sex restrictions. Use the full Briefing for both directions. |

Summaries support current 28 days, rolling 365 days and the original historical baseline. Dates use the active source analysis cutoff, normally the latest sample, rather than the wall clock. An explicit YYYY-MM-DD cutoff is supported. Last calendar month is not a synonym for 28 days. Arbitrary windows, weekly search intent, exclusion filters, custom comparison groups, forecasting, treatments and unsupported dimensions require clarification. Weekly trends and custom basic periods remain accessible through their existing Explore tools.

An organism and antibiotic are required for focused resistance operations. A missing name is not silently filled from the current sidebar. Anomaly scans can leave those dimensions unrestricted. Recognised abbreviations include E. coli, CIP/cipro, TMP and URO. Named filters must be present in the active source catalog. The model receives only terminology, not patient identifiers or records. Do not put private patient information in the search field.

Age is measured at sampling. Older than 65 becomes minimum 66; 65 and older becomes minimum 65. Younger than 40 becomes maximum 39; 40 and younger becomes maximum 40. Numeric ranges are inclusive. For recognised explicit numeric comparisons, a conservative normalizer corrects a model boundary already extracted as the stated literal or its adjacent integer; it never fills a dropped bound. The validator checks common lost/invented constraints. This does not prove that an LLM can interpret every possible sentence: review the displayed parameters. Unknown or failed interpretations stop rather than silently falling back to a broader query.

## Results and handoff

Results are session-local and submitted explicitly. Source identity/revision/cutoff changes invalidate old results. Workspace restoration clears search state; raw questions and results are not persisted in restore URLs. Reruns never repeat inference. A two-second session cooldown and one process slot prevent ordinary duplicates. Public Demo Mode adds a nonwaiting cross-process OS lock and shared rolling AI budget (6/minute, 30/hour). Manual Search remains usable when AI is busy or exhausted. These safeguards are not authentication or a bound on idle WebSocket sessions; see [Deployment](DEPLOYMENT.md).

Open Detailed Investigation transfers the cohort, antibiotic, cutoff, grouping and group period into the existing workflow, clearing unrelated retained restrictions. Source and Replay ownership stay unchanged. Existing Explore controls represent an open age side as the valid domain endpoint 0 or 130; this preserves the same cohort. Anomaly findings carry their real original locator and BY/preliminary classification.

Percentages remain absent below the existing sample threshold. Counts are labelled as resistant/tested first isolates, not infected patients. Monthly context covers the unchanged engine timeline; it is explicitly distinguished from the selected summary window. Historical statistical assessment is shown only for a current-window summary. Group comparisons are descriptive. First-isolate rules, S/R eligibility, Wilson, Fisher, BY, McNemar, sensitivity analysis and PDF calculations are unchanged.

## Windows startup

Install Ollama from its [official Windows distribution](https://ollama.com/download/windows). Exit/restart the tray application after changing its server environment, or run the server in a separate PowerShell terminal:

```powershell
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
ollama serve
```

If the tray service already owns that port, use it instead of starting a second server. Verify that it listens on loopback. In a second terminal, explicitly download only the candidate needed (the application never pulls weights):

```powershell
ollama pull qwen3:8b
ollama list
```

The only accepted model is [qwen3:8b](https://ollama.com/library/qwen3:8b), Q4_K_M, digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`. Identity and quantization are checked before inference; a mismatch disables AI safely while Manual Search remains available. There is no automatic 4B fallback. Thinking is disabled (`think: false`). No 14B or larger model is used. Weights stay in Ollama's external cache. The [archived comparison](SMART_SEARCH_BENCHMARK.md) explains the selection.

From the project root, using the Python environment already installed for AEI Sentinel:

```powershell
$env:AEI_OLLAMA_URL = 'http://127.0.0.1:11434'
# Fixed model; smaller/larger overrides are rejected.
$env:AEI_OLLAMA_MODEL = 'qwen3:8b'
python -m tools.check_local_model
python -m streamlit run app.py --server.address 127.0.0.1
```

Use the actual environment's Python executable if it lives in a parent directory. A fresh installation follows the [README](../README.md); no Python Ollama SDK or cloud key is required. `http.client` calls only a configured loopback HTTP endpoint, without proxy forwarding, redirects, remote endpoints or tools. Generation uses temperature 0, seed 14, 4,096 context tokens, 500 output tokens, a 45-second request timeout, a 64-KiB response bound and five-minute keep-alive. A cold load can exceed the interactive timeout; warm the selected model explicitly or retry once it is loaded. GPU offload is managed by Ollama; check `ollama ps` for GPU use.

If Ollama is stopped, the model is missing, output is malformed, the slot is busy or the request times out, the UI explains the failure and Manual Search remains available. There is no pretend AI/deterministic fallback and no automatic startup download.

## Separate 8B benchmark and archived comparison

Normal automated tests mock Ollama. The 64 explicitly authored expected outputs are in `tests/fixtures/smart_search_queries.json`. They cover order, aliases, age boundaries, periods, groups, missing/ambiguous/conflicting inputs, unsupported operations and injection attempts.

```powershell
python -m tools.benchmark_smart_search --model qwen3:8b --output outputs/benchmark-8b.json
python -m tools.score_smart_search outputs/benchmark-8b.json
```

The live tool now accepts only pinned 8B. The earlier 4B comparison is historical evidence, not a supported runtime alternative. Saved responses can be rescored without loading models. Reports distinguish raw extraction from validated accuracy, count withheld valid requests as incorrect, and retain timing/resource evidence. This is development-set evaluation, not clinical validation. Outputs are ignored by Git. For Public Demo Mode use the isolated [startup script](DEPLOYMENT.md), not the developer command above.

## Future public demo

See [deployment protections and remaining blockers](DEPLOYMENT.md). Keep both services bound to loopback. No tunnel, firewall change, router change or public deployment is included in Smart Search.
