# FinResearchAgent — Project Guide

## What It Is
FastAPI app on **Google Cloud Run** that runs AI-powered buy-side research pipelines using **Google ADK** (Agent Development Kit) with **Gemini 3 Flash Preview / Gemini 3.1 Pro Preview** on Vertex AI. Two pipelines: **Equity** (21-section memo) and **Macro** (8-section report).

---

## File Map
```
main.py              — FastAPI entry point + all pipeline orchestration logic
agents/team.py       — All 16 agent definitions (models, tools, instructions)
prompts/*.md         — Agent system prompts (loaded at import/build time, NOT dynamic)
tools/               — Python functions exposed as ADK FunctionTools
  http_client.py     — Shared HTTP client: api_get, api_get_with_auth, get_api_key,
                       @handle_api_errors — used by all financial API tool modules
  pricing_lookup.py  — Live pricing from Google Cloud Billing Catalog API (falls back
                       to config.yaml). Cached per-process. Used by debug_report.py.
  debug_report.py    — Per-run debug .md saved to GCS after every pipeline run
config.yaml          — All settings: models, model_region, timeouts, concurrency,
                       thresholds, secrets, search throttle, pricing fallback
deploy.sh            — One-command: docker build → push → Cloud Run deploy
Dockerfile           — python:3.11-slim, installs requirements.txt, copies . to /app
```

---

## GCP Config
| Field | Value |
|---|---|
| Project ID | `equity-research-488917` |
| Cloud Run region | `us-central1` |
| Model API region | `global` (required for Gemini 3.x — set via `config.yaml → google_cloud.model_region`) |
| Service | `fin-research-agent` |
| Reports bucket | `gs://{REPORTS_BUCKET}/` — set via `config.yaml → google_cloud.reports_bucket` |
| Debug reports | `gs://{REPORTS_BUCKET}/debug/{run_id}.md` |
| API keys | GCP Secret Manager (names in `config.yaml → secrets`) |

> **⚠️ Important:** Gemini 3.x models (`gemini-3-flash-preview`, `gemini-3.1-pro-preview`) are only available via `location="global"`. The Cloud Run service itself stays in `us-central1`. `search_web` uses a separate client hardcoded to `us-central1` because `gemini-2.5-flash` (the search model) is regional-only.

---

## Agent Pipelines

### Equity Pipeline (`_run_equity_pipeline`)
```
1a. _gather_structured_data()    — Python: Finnhub + FMP + AV + Polygon (18 parallel calls via
                                   _DATA_EXECUTOR / 20 workers) + SEC EDGAR (6 parallel)
1b. data_harvester               — Web search + Coverage Log
1c. context_processor            — (optional) enriches user context notes
2.  [PARALLEL x6] — each agent receives only its relevant structured-data sections
    fundamental_analyst_market   — Sections 1-2 (thesis, market)
    fundamental_analyst_financials — Sections 9,11,12,13 (financials)
    competitive_analyst          — Sections 3-8 (customers, product, competition, GTM)
    risk_analyst                 — Sections 14-19 (moat, risks, M&A)
    valuation_analyst            — Sections 20-21 + Quality Scorecard
    earnings_quality_agent       — Forensic accounting, GAAP vs non-GAAP
2r. [ANALYST RETRY ≤3 rounds] — any analyst that returned a placeholder is re-run
    in parallel. Each round only retries agents still failing. Labels: {agent}-retry-{N}
3.  quant_modeler_equity         — Technical indicators, beta, VaR, volatility
4.  report_compiler              — Assembles 21-section memo
5.  [REVIEW LOOP ≤3 passes — fact_checker ∥ review_agent run in parallel each pass]
    fact_checker ─┐ → report_compiler (revision)
    review_agent  ┘
6.  research_orchestrator        — Writes Executive Summary last (up to 3 attempts;
    if all fail, report sent with "summary unavailable" note)
```

### Macro Pipeline (`_run_macro_pipeline`)
```
1a. _gather_macro_data()         — Python: 11 parallel pre-fetches →
                                   • FRED: yield curve snapshot + recession indicators
                                   • FRED: 10 FX series (EUR, JPY, GBP, CHF, KRW, INR,
                                           MXN, BRL, CNY, Trade-weighted USD)
                                   • FRED: 9 macro indicators (WTI, Brent, gold, gas,
                                           ISM Mfg/Svc PMI, consumer sentiment, HY/IG spreads)
                                   • Alpha Vantage: WTI, Brent, gas, copper, wheat, corn (monthly)
                                   • Polygon: 11 FX pairs previous-day close
                                   • World Bank: GDP, inflation, unemployment, CA, debt —
                                                 10 economies, 5-year annual history
                                   • OECD: CLI (20 countries) + GDP projections
                                   • IMF WEO: GDP, inflation, unemployment, CA forecasts
                                   • ECB: deposit rate, refi rate, HICP headline+core, M3
                                   • NewsAPI: topic-specific recent news (if configured)
                                   Returns dict[label → JSON text] sliced per-agent via
                                   _MACRO_AGENT_SECTIONS + _slice_macro_data()
1b. macro_data_agent             — Topic-specific FRED + web search + source log
1c. macro_source_validator       — Validates source geography/theme fit; fills gaps
1d. context_processor            — (optional)
2.  macro_analyst                — 8-section report (incl. Literature Review = Section 8)
3.  quant_modeler_macro          — Yield curve, regression, time series, source credibility
4.  macro_report_compiler        — Assembles 8 sections + Quant Appendix + Source Log
5.  [REVIEW LOOP ≤3 passes]
    fact_checker → review_agent → macro_report_compiler (revision)
```

**Data slicing**: `_MACRO_AGENT_SECTIONS` controls which pre-gathered sections each agent receives (analogous to equity's `_ANALYST_SECTIONS`). `macro_data_agent` gets yield curve + recession + World Bank + OECD + IMF + news; `quant_modeler_macro` gets yield curve + recession + FRED FX/macro + ECB + IMF (no news/World Bank); `macro_analyst` gets everything except AV commodities + Polygon FX (redundant with FRED). Context size per agent: ~20–40K chars instead of ~80K for the full dump.

---

## Model Tiers
| Tier | Model | Agents |
|---|---|---|
| tier1 | `gemini-3.1-pro-preview` | research_orchestrator, fact_checker, review_agent |
| tier2 | `gemini-3-flash-preview` | valuation_analyst |
| tier3 | `gemini-3-flash-preview` | all other analysts, data agents, quant agents |
| tier_compiler | `gemini-3.1-pro-preview` | report_compiler, macro_report_compiler |

Change all at once in `config.yaml → models`. Model API calls always use `model_region: "global"`.

**max_output_tokens:** Several agents have explicit ceilings set in `agents/team.py` via `generate_content_config`. The ADK default is 8192 — insufficient for multi-section reports. Current values:
| Agent | max_output_tokens |
|---|---|
| report_compiler, macro_report_compiler | 65,536 |
| quant_modeler_equity, macro_analyst | 32,768 |
| valuation_analyst, quant_modeler_macro | 32,768 |

---

## Data Sources (tools/)
| Source | Tool file | What it provides |
|---|---|---|
| **Shared HTTP** | `http_client.py` | `api_get`, `api_get_with_auth`, `get_api_key`, `@handle_api_errors` — used by all API modules below |
| Finnhub | `finnhub_data.py` | Quote, 2yr OHLCV, financials, metrics, earnings, ratings |
| FMP | `fmp_data.py` | Income stmt, balance sheet, cash flow, metrics, estimates |
| Alpha Vantage | `stock_data.py` | Price, overview, income, EPS; commodity prices: WTI, Brent, gas, copper, wheat, corn (25 calls/day — 6 used for commodities) |
| Polygon | `polygon_data.py` | Ticker details (sector/SIC fix), OHLCV, news; FX previous-day close for 11 pairs (unlimited free tier) |
| SEC EDGAR | `sec_filings.py` | Filings, XBRL facts, insider transactions (thread-safe rate limiter) |
| FRED | `macro_data.py` | Series, yield curve snapshot, recession indicators; FX rates (10 pairs); macro indicators (commodities, PMI, credit spreads) |
| World Bank | `worldbank_data.py` | GDP, inflation, unemployment, current account, gov debt, trade balance — 10 economies, 5-year annual history (free, no key) |
| OECD | `oecd_data.py` | Composite Leading Indicators (20 countries) + GDP growth projections (SDMX, free, no key) |
| IMF WEO | `imf_data.py` | GDP, inflation, unemployment, current account forecasts — 190+ countries, biannual WEO (free, no key) |
| ECB | `ecb_data.py` | Deposit facility rate, main refi rate, Eurozone HICP headline+core, M3 money supply (SDMX, free, no key) |
| Web | `web_search.py` | search_web, search_news, search_earnings_transcript, search_analyst_reports, search_academic_papers, search_competitor_filings |
| CORE | `core_api.py` | Academic papers (no Vertex AI quota cost; auto-falls back to Semantic Scholar) |
| Quant | `quant_tools.py` | RSI, SMA, EMA, MACD (O(n) incremental), Bollinger, ATR, HV, drawdown, VaR, beta, correlation, skew/kurt, linear regression, yield spread, z-score |
| Earnings quality | `earnings_quality_tools.py` | SBC, GAAP gap, accruals, deferred revenue, goodwill, debt |
| Storage | `storage.py` | save_report, load_report, list_reports, save_run_metadata, **save_latex_report** → GCS. After every pipeline run the report is saved as both `.md` and `.tex` (via pandoc `--standalone`). |

---

## Key Runtime Settings (config.yaml)
| Setting | Value | Notes |
|---|---|---|
| `google_cloud.model_region` | `"global"` | Required for Gemini 3.x models. Set separately from `region` (Cloud Run deployment stays `us-central1`). |
| `concurrency.max_parallel_agents` | 3 | Semaphore on Vertex AI calls |
| `concurrency.max_rate_limit_retries` | 15 | Exponential backoff on 429s and INVALID_ARGUMENT retries |
| `retry.max_analyst_retries` | 3 | Retry rounds for failed equity analysts after Step 2 (0 = disabled) |
| `retry.max_exec_summary_retries` | 3 | Retry attempts for executive summary orchestrator in Step 6 |
| `review.max_passes` | 3 | Review loop iterations |
| `search.min_interval_seconds` | 2.0 | Min gap between web_search() calls. Set to 10.0 on free tier GCP. Deployed via `SEARCH_MIN_INTERVAL` env var. |
| `timeouts.default` | 720s | Hard wall-clock per agent |
| `timeouts.data_harvester` | 720s | |
| `timeouts.macro_data_agent` | 720s | |
| `timeouts.fundamental_analyst_market` | 480s | Raised from 360s after NVDA run showed 2 consecutive timeouts due to search queue contention |
| `timeouts.quant_modeler_equity` | 480s | |
| `timeouts.macro_analyst` | 480s | |
| Equity buy hurdle | 30% return, 25% MoS, 1.7× skew | In `config.yaml → report` |
| Quality pass/sell | 70 / 60 | Scorecard thresholds |

---

## Deployment
```bash
./deploy.sh          # build Docker image → push to GCR → gcloud run deploy
```
**Prompt edits always require redeploy.** Instructions are loaded at `import` time via `_load_prompt()` and baked into the Docker image. There is no hot-reload.

---

## Bug Log Rule
**`fixed_bugs.md`** (project root) is the permanent, append-only record of every bug fixed in this project.
- **Every time a bug is fixed, add an entry to `fixed_bugs.md` before closing the session.**
- Never delete or edit existing entries — only append.
- Entry format: bug ID (BUG-NNN, increment), date, affected agents, symptom, root cause, fix, files changed, detection command.
- See `fixed_bugs.md` for all previously fixed bugs before investigating a new issue (avoid re-diagnosing solved problems).

---

## Known Issues & Fixes

### ⚠️ ADK Context Variable Bug (FIXED)
**Symptom:** `Unexpected error on <agent>: 'Context variable not found: \`y\`.'`

**Cause:** Google ADK renders `Agent(instruction=...)` via Python `str.format_map(session.state)`. Any `{var}` pattern in a `.md` instruction file is treated as a template placeholder. If `var` is not in session state, it crashes.

**Fix:** Remove ALL `{...}` pairs from instruction examples. The ADK uses the regex `{+[^{}]*}+` (one-or-more `{`, non-brace content, one-or-more `}`) — so `{{y}}` matches identically to `{y}`. Double-bracing does NOT work. Only safe approach: zero `{` or `}` in instruction examples.

**Fixed in:** `prompts/macro_report_compiler.md` and `prompts/report_compiler.md` — LaTeX example lines replaced with brace-free equivalents (`$R^2 = 0.72$`, `$\beta = 1.3$`, etc.) plus plain-text description of LaTeX syntax.

**Detection (uses exact ADK regex):**
```bash
python3 -c "
import re, pathlib
regex = re.compile(r'\{+[^{}]*\}+')
prompts = pathlib.Path('prompts').glob('*.md')
found = False
for f in sorted(prompts):
    for i, line in enumerate(f.read_text().splitlines(), 1):
        if regex.findall(line): print(f'{f.name}:{i}: {regex.findall(line)}'); found = True
if not found: print('CLEAN')
"
```
Run this after any `.md` edit. Any output = must fix before deploying.

### ℹ️ `fundamental_analyst_market` search overuse — FIXED (BUG-011)
Previously searched the web for revenue/segment data already available in APIs, wasting ~$1.33 in search fees per run (46% of total cost) and causing repeated timeouts. Fixed by:
- Adding `income_statement_fmp` to its data slice and as a callable tool
- Adding `key_metrics_finnhub` + `key_metrics_fmp` to `competitive_analyst` data slice
- Removing redundant `income_statement_av` (FMP overlap) from `fundamental_financials` slice
- Removing duplicate `historical_ohlcv_polygon` from `valuation_analyst` slice
- Updating both prompts to list what's in context and forbid unnecessary searches
See BUG-011 in `fixed_bugs.md` for full details and NOTE-001 for search throttle future work.

### ⚠️ quant-macro low output (ongoing)
`quant-macro` sometimes produces ~941 chars in 552s (hits tool/context limit mid-run). Falls back to intermediate text chunk. Output is sparse — compiler still runs but quant section will be thin. Watch debug reports for this pattern.

### ℹ️ Transient INVALID_ARGUMENT (400) — MITIGATED
When 6 analysts start simultaneously via `asyncio.gather`, occasional race conditions cause one agent's first LLM call to fail with `400 INVALID_ARGUMENT` (model "—", 0 tokens in debug report). Fixed by adding INVALID_ARGUMENT to the retryable error list in `_run_agent` — the agent retries with ~15s backoff and succeeds on the second attempt. If you see a debug report with model "—" and `rl:1`, this retry fired. If still failing, check Cloud Run logs for the full traceback.

### ⚠️ Pandoc `.tex` conversion — FIXED (BUG-010)
`save_latex_report` failed with `pandoc failed: YAML parse exception at line 1, column 1` on pandoc 3.x (Debian Bookworm). Root cause: `header-includes: |` (literal block scalar) is rejected by HsYAML (pandoc 3.x YAML parser). Fixed by using a YAML list with single-quoted strings — the canonical pandoc format. **The YAML front matter `header-includes` in `main.py` must remain a list of single-quoted strings.** Single quotes are required because `\thepage`, `\fancyhf`, `\usepackage`, etc. contain backslash sequences that YAML double-quoted strings would interpret as escape codes.

### ℹ️ Compiler 0 output tokens — FIXED
`report-compiler` and `macro-report-compiler` produced 0 output tokens when upstream agents failed and left `[ERROR: ...]` or `[TIMEOUT: ...]` placeholder strings in the compile context. Gemini models refuse to process requests that contain these literal error strings. Fixed by `_clean_for_compiler()` in `main.py` — applied to both equity and macro pipelines before compilers are called.

---

## Debug Reports
Every run saves a debug `.md` to GCS and locally (Downloads folder). Shows per-agent: status ✅/❌, duration, output chars, timeouts, rate-limit retries, error messages. Check this first when a run fails.

---

## Adding / Editing Agents
1. Edit or create `.md` in `prompts/`
2. If new agent: define `Agent(...)` in `agents/team.py`, import in `main.py`, wire into pipeline in `_run_equity_pipeline` or `_run_macro_pipeline`
3. **Never use ANY `{word}` or `{{word}}` in `.md` files** — the ADK regex `{+[^{}]*}+` matches both. Only safe approach: zero `{` or `}` in instruction examples. (See BUG-001 in `fixed_bugs.md`)
4. Run `./deploy.sh`
5. If fixing a bug: append an entry to `fixed_bugs.md` (never delete existing entries)
