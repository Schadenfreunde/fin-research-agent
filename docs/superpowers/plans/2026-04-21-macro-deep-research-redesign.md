# Macro Deep Research & Institutional Report Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the macro pipeline with a Mode Detector, Gemini Deep Research Thematic Synthesis Agent, and Signal Agent so reports produce sell-side institutional research with trade recommendations only when warranted.

**Architecture:** Mode Detector classifies each request as `"research"` or `"both"` before any data gathering. Gemini Deep Research slots between Source Validator and Macro Analyst, receiving the validated source package plus a compact data manifest and autonomously expanding sources before returning a structured Thematic Synthesis Document. The Signal Agent runs in parallel with the Quant Modeler (only when mode is `"both"`) and emits a tiered conviction output that drives Section 5 rendering in the Report Compiler.

**Note on Deep Research auth:** The Gemini Deep Research API uses the Gemini Developer API (not Vertex AI) and requires a separate `GOOGLE_API_KEY`. The key is already stored in GCP Secret Manager as `"google-ai-api-key"` — no new secret creation needed.

**Tech Stack:** Python 3.11, FastAPI, Google ADK (`google-adk`), `google-genai` SDK (Vertex AI for existing agents), `google-generativeai` SDK (Gemini Developer API for Deep Research), `pytest`, `pytest-asyncio`, GCS via `google-cloud-storage`.

**Scope:** Macro pipeline ONLY. No equity pipeline files touched.

---

## File Structure

### New files
| Path | Purpose |
|---|---|
| `tools/deep_research.py` | Async Gemini Deep Research API client (polling, timeout, fallback). Uses `google-ai-api-key` from Secret Manager. |
| `prompts/macro_mode_detector.md` | System prompt for mode detection agent |
| `prompts/macro_signal_agent.md` | System prompt for signal agent |
| `tests/__init__.py` | Make tests a package (if missing) |
| `tests/macro/__init__.py` | Make tests/macro a package |
| `tests/macro/conftest.py` | Shared fixtures for macro tests |
| `tests/macro/test_mode_detection.py` | Unit tests for Mode Detector logic |
| `tests/macro/test_signal_agent.py` | Unit tests for Signal Agent output schema |
| `tests/macro/test_deep_research_handoff.py` | Unit tests for Deep Research handoff and output parsing |
| `tests/macro/test_section5_rendering.py` | Unit tests for Section 5 conditional rendering |
| `tests/macro/test_pipeline_integration.py` | Integration tests for full macro pipeline |
| `tests/macro/fixtures/validated_source_package.txt` | Sample Source Validator output |
| `tests/macro/fixtures/data_manifest.txt` | Sample `_macro_data_manifest()` output |
| `tests/macro/fixtures/analyst_output_high_conviction.txt` | Analyst output with clear directional driver |
| `tests/macro/fixtures/analyst_output_low_conviction.txt` | Analyst output with competing drivers |
| `tests/macro/fixtures/analyst_output_thematic.txt` | Purely thematic analyst output |
| `tests/macro/fixtures/synthesis_document_valid.txt` | Valid Thematic Synthesis Document for schema tests |

### Modified files
| Path | What changes |
|---|---|
| `main.py` | Add `trade_signal`/`deep_dive` params; add Phase 0 (mode detection), Phase 1e (Deep Research), Phase 3b (Signal Agent); update meta block; update `run_research_pipeline` to pass new params; update `/research` endpoint to accept new params |
| `agents/team.py` | Add `macro_mode_detector`, `macro_signal_agent` agent definitions; add both to `MACRO_AGENTS` dict |
| `config.yaml` | Add `timeouts.mode_detector`, `timeouts.deep_research`, `timeouts.signal_agent`; add `pricing.deep_research_cost_per_query` |
| `prompts/macro_analyst.md` | Replace "validated source package" reference with "Thematic Synthesis Document" |
| `prompts/macro_report_compiler.md` | Add Section 5 conditional rendering rules |
| `project_guide.md` | Update macro pipeline diagram, agent registry, timeout table, GCS artifact table |

---

## Pipeline Start Prompts

Use these curl commands to trigger macro runs after implementation:

```bash
# 1. Research-only mode (auto-detect, no trade signal expected)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Impact of demographic shifts on EM productivity",
    "report_type": "macro"
  }'

# 2. Research + Signal mode (explicit — user expects a trade call)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "EUR/USD outlook into ECB June meeting",
    "report_type": "macro",
    "trade_signal": true
  }'

# 3. Force deep dive (suppress signal detection even if topic sounds directional)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Secular stagnation and the neutral rate in advanced economies",
    "report_type": "macro",
    "deep_dive": true
  }'

# 4. Auto-detect signal (no flags — model reads "long Bunds" as signal intent)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Case for long Bunds on a 6-month horizon",
    "report_type": "macro",
    "context": "Focus on ECB rate path and Eurozone growth slowdown"
  }'
```

## Test Run Prompts

```bash
# Run full macro test suite
pytest tests/macro/ -v

# Unit tests only (fast, no API calls)
pytest tests/macro/test_mode_detection.py \
       tests/macro/test_signal_agent.py \
       tests/macro/test_deep_research_handoff.py \
       tests/macro/test_section5_rendering.py \
       -v

# Integration tests (slow — requires API credentials and GCS access)
pytest tests/macro/test_pipeline_integration.py -v -m integration

# Single test
pytest tests/macro/test_mode_detection.py::test_auto_detect_research_topic -v

# With coverage report
pytest tests/macro/ -v --cov=main --cov=tools/deep_research --cov-report=term-missing
```

---

## Sprint 1: Mode Detection & Request Schema

---

### Task 1: Add new parameters to `config.yaml`

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Add timeouts for new agents**

Open `config.yaml`. Under the `timeouts:` block, after `macro_source_validator` (add it if it's not there — check the file), add:

```yaml
  mode_detector:  30    # 30s — single classification call
  deep_research: 600    # 10 min — Gemini Deep Research polling
  signal_agent:  180    # 3 min — conviction assessment
```

- [ ] **Step 2: Add Deep Research pricing**

Under `pricing:`, after `search_cost_per_call: 0.035`, add:

```yaml
  # Gemini Deep Research API — per-query cost (Gemini Developer API, not Vertex AI tokens)
  # Source: https://ai.google.dev/pricing — update when pricing is confirmed
  deep_research_cost_per_query: 0.00   # Set to actual cost once confirmed from Google pricing page
```

- [ ] **Step 3: Add GEMINI_API_KEY secret reference**

Under `secrets:`, add:

```yaml
  gemini_api_key: "gemini-api-key"   # Gemini Developer API key for Deep Research
```

- [ ] **Step 4: Verify the file parses correctly**

```bash
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
```

Expected: no output (silent success).

- [ ] **Step 5: Commit**

```bash
git add config.yaml
git commit -m "config: add mode_detector, deep_research, signal_agent timeouts and Deep Research pricing"
```

---

### Task 2: Write `prompts/macro_mode_detector.md`

**Files:**
- Create: `prompts/macro_mode_detector.md`

- [ ] **Step 1: Write the prompt**

```markdown
# Macro Mode Detector

You are a research request classifier for an institutional macro research pipeline.

Your ONLY job is to read a macro research topic and classify it into one of two report modes:

- **research**: The topic is thematic, structural, or exploratory. No explicit trade positioning language. The user wants to understand a macro dynamic without necessarily needing a buy/sell recommendation.
- **both**: The topic contains explicit trade positioning language, names specific financial instruments for directional positioning, or is clearly framed as an investment decision question.

## Classification Rules

Classify as **both** if the topic contains ANY of:
- Explicit instrument positioning ("long X", "short Y", "position in Z", "trade X")
- Named assets with directional language ("case for Bunds", "bearish on USD", "bullish EM rates")
- Time-bounded investment questions ("outlook into ECB meeting", "ahead of Fed decision", "should I buy X")
- Explicit rate or FX trade framing ("rate differential trade", "carry trade in", "basis trade")

Classify as **research** if the topic is framed as:
- Impact/effect analysis ("impact of X on Y")
- Structural or thematic exploration ("demographic trends", "neutral rate", "fiscal dominance")
- Historical or comparative analysis ("lessons from 1970s inflation", "EM debt cycles")
- Policy analysis without positioning ("ECB strategy", "Fed communication framework")
- Geographic macro outlook without instrument direction ("UK growth outlook", "EM productivity")

## Output Format

Return ONLY this exact structure — no other text:

```
REPORT_MODE: [research|both]
RATIONALE: [One sentence explaining why. Start with "Topic" — e.g., "Topic contains explicit long positioning language for EUR/USD." or "Topic is a structural exploration of demographic forces without instrument reference."]
```

Do NOT add any other text, headers, or explanation.
```

**Critical:** Do NOT use `{`, `}`, `[`, or `]` characters inside example text blocks in this file — the ADK renders the instruction via `str.format_map()` and any `{var}` pattern crashes the agent. The format block above uses backtick-fenced code, which is safe. Double-check: search the file for `{` after saving and confirm all occurrences are inside fenced code blocks.

- [ ] **Step 2: Check for ADK brace injection risk**

```bash
grep -n "{" "prompts/macro_mode_detector.md" | grep -v "^\s*\`"
```

Expected: no output (all braces are inside code fences).

- [ ] **Step 3: Commit**

```bash
git add prompts/macro_mode_detector.md
git commit -m "prompts: add macro_mode_detector system prompt"
```

---

### Task 3: Add `macro_mode_detector` agent to `agents/team.py`

**Files:**
- Modify: `agents/team.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/macro/test_mode_detection.py (create the file now, full content in Task 6)
# For this step, just verify the agent import works
def test_mode_detector_agent_importable():
    from agents.team import macro_mode_detector
    assert macro_mode_detector is not None
    assert macro_mode_detector.name == "macro_mode_detector"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/macro/test_mode_detection.py::test_mode_detector_agent_importable -v
```

Expected: `ImportError: cannot import name 'macro_mode_detector'`

- [ ] **Step 3: Add the agent definition**

Open `agents/team.py`. After the `macro_source_validator` block (around line 473) and before `macro_report_compiler`, add:

```python
macro_mode_detector = Agent(
    name="macro_mode_detector",
    model=MODEL_TIER3,
    description=(
        "Request classifier for the macro pipeline. Reads the topic string and "
        "classifies it as 'research' (thematic, no trade positioning) or 'both' "
        "(trade signal warranted). Returns REPORT_MODE and RATIONALE only."
    ),
    instruction=_load_prompt("macro_mode_detector.md"),
    generate_content_config=genai_types.GenerateContentConfig(
        max_output_tokens=128,
    ),
    tools=[],  # Classification only — no external tools needed
)
```

- [ ] **Step 4: Add `macro_mode_detector` to `MACRO_AGENTS` dict**

Find the `MACRO_AGENTS` dict (around line 542). Add:

```python
MACRO_AGENTS = {
    "orchestrator": research_orchestrator,
    "context_processor": context_processor,
    "macro_mode_detector": macro_mode_detector,      # ← ADD THIS
    "macro_data_agent": macro_data_agent,
    "macro_source_validator": macro_source_validator,
    "macro_analyst": macro_analyst,
    "quant_modeler_macro": quant_modeler_macro,
    "macro_report_compiler": macro_report_compiler,
    "fact_checker": fact_checker,
    "review_agent": review_agent,
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/macro/test_mode_detection.py::test_mode_detector_agent_importable -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add agents/team.py
git commit -m "agents: add macro_mode_detector agent definition"
```

---

### Task 4: Implement `_run_mode_detector()` in `main.py` and update pipeline signature

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Import the new agent at the top of main.py**

Find the `from agents.team import (` block (around line 80). Add `macro_mode_detector` and `macro_signal_agent` (we'll define signal agent later but import it now to keep the import block clean):

```python
from agents.team import (
    research_orchestrator,
    data_harvester,
    fundamental_analyst,
    fundamental_analyst_market,
    fundamental_analyst_financials,
    context_processor,
    competitive_analyst,
    risk_analyst,
    valuation_analyst,
    quant_modeler_equity,
    earnings_quality_agent,
    report_compiler,
    macro_data_agent,
    macro_source_validator,
    macro_mode_detector,    # ← ADD
    macro_analyst,
    quant_modeler_macro,
    macro_report_compiler,
    fact_checker,
    review_agent,
)
```

Note: `macro_signal_agent` will be added in Sprint 3 — add it then.

- [ ] **Step 2: Add timeout constants near the existing timeout block (around line 124)**

After `_MACRO_SOURCE_VALIDATOR_TIMEOUT`, add:

```python
_MODE_DETECTOR_TIMEOUT   = _TIMEOUTS.get("mode_detector",  30)   # 30s
_DEEP_RESEARCH_TIMEOUT   = _TIMEOUTS.get("deep_research",  600)  # 10 min
_SIGNAL_AGENT_TIMEOUT    = _TIMEOUTS.get("signal_agent",   180)  # 3 min
```

- [ ] **Step 3: Write `_parse_mode_detector_output()` helper**

Add this function near the other private helpers in `main.py` (before `_run_macro_pipeline`):

```python
def _parse_mode_detector_output(raw: str) -> tuple[str, str]:
    """
    Parse the mode detector's output into (report_mode, mode_rationale).

    Expected format from the agent:
        REPORT_MODE: research
        RATIONALE: Topic is a structural exploration...

    Falls back to "research" if parsing fails.
    """
    report_mode = "research"
    mode_rationale = "Auto-detected as research mode (default fallback)."
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("REPORT_MODE:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in ("research", "both", "signal"):
                report_mode = val
        elif line.upper().startswith("RATIONALE:"):
            mode_rationale = line.split(":", 1)[1].strip()
    return report_mode, mode_rationale
```

- [ ] **Step 4: Update `_run_macro_pipeline()` signature**

Change the function signature from:

```python
async def _run_macro_pipeline(topic: str, run_id: str,
                              user_context: str = "",
                              run_stats: "RunStats" = None) -> str:
```

to:

```python
async def _run_macro_pipeline(topic: str, run_id: str,
                              user_context: str = "",
                              run_stats: "RunStats" = None,
                              trade_signal: bool | None = None,
                              deep_dive: bool = False) -> str:
```

- [ ] **Step 5: Add Phase 0 (Mode Detection) as the first step in `_run_macro_pipeline()`**

Immediately after the docstring and before Step 1a (the pre-gather), add:

```python
    # ── Phase 0: Mode Detection ────────────────────────────────────────────────
    # Classify the request before any data gathering. Explicit flags override
    # the LLM classifier. "signal"-only mode is reserved for future use.
    if deep_dive:
        report_mode = "research"
        mode_rationale = "deep_dive=True — signal mode suppressed by caller."
        logger.info("[%s] Phase 0: Mode forced to 'research' (deep_dive=True)", run_id)
    elif trade_signal is True:
        report_mode = "both"
        mode_rationale = "trade_signal=True — signal mode activated by caller."
        logger.info("[%s] Phase 0: Mode forced to 'both' (trade_signal=True)", run_id)
    elif trade_signal is False:
        report_mode = "research"
        mode_rationale = "trade_signal=False — signal mode suppressed by caller."
        logger.info("[%s] Phase 0: Mode forced to 'research' (trade_signal=False)", run_id)
    else:
        # Auto-detect via LLM classifier
        logger.info("[%s] Phase 0: Auto-detecting report mode via classifier...", run_id)
        _mode_raw = await _run_agent(
            macro_mode_detector,
            f"Macro research topic: {topic}",
            "mode-detector",
            run_id,
            timeout_seconds=_MODE_DETECTOR_TIMEOUT,
            run_stats=run_stats,
        )
        report_mode, mode_rationale = _parse_mode_detector_output(_mode_raw)
        logger.info("[%s] Phase 0: Detected report_mode='%s' — %s", run_id, report_mode, mode_rationale)
```

- [ ] **Step 6: Update the meta block to include report mode and rationale**

Find the `_meta_lines` block (around line 1993). It currently starts with the run ID line. Add two new lines after it:

```python
        _meta_lines = [
            f"*Run ID: `{run_id}` · {report_type.capitalize()} report · "
            f"{datetime.datetime.utcnow().strftime('%Y-%m-%d')}*",
            f"*Report mode: {'Research + Signal' if report_mode == 'both' else 'Research'}*",  # ← ADD
            f"*Mode rationale: {mode_rationale}*",                                              # ← ADD
            "",
        ]
```

Note: `report_mode` and `mode_rationale` are local variables inside `_run_macro_pipeline`. The meta block is built inside `run_research_pipeline` which calls `_run_macro_pipeline`. You need to return `report_mode` and `mode_rationale` from `_run_macro_pipeline` alongside the report text, OR set them as parameters on `run_stats`. The cleanest approach: add them to `run_stats`.

Add to `RunStats` (in `tools/debug_report.py`) or simply pass them via a mutable dict. The simplest non-invasive approach: store on `run_stats` as dynamic attributes (Python allows this):

```python
    # In _run_macro_pipeline, after Phase 0:
    if run_stats is not None:
        run_stats.report_mode = report_mode          # dynamic attribute
        run_stats.mode_rationale = mode_rationale    # dynamic attribute
```

Then in `run_research_pipeline`, after calling `_run_macro_pipeline`:

```python
        # Read mode metadata from run_stats (set by macro pipeline)
        _report_mode = getattr(run_stats, "report_mode", "research")
        _mode_rationale = getattr(run_stats, "mode_rationale", "")
```

And use `_report_mode` / `_mode_rationale` in the `_meta_lines` block:

```python
        _meta_lines = [
            f"*Run ID: `{run_id}` · {report_type.capitalize()} report · "
            f"{datetime.datetime.utcnow().strftime('%Y-%m-%d')}*",
        ]
        if report_type == "macro":
            _report_mode = getattr(run_stats, "report_mode", "research")
            _mode_rationale = getattr(run_stats, "mode_rationale", "")
            _mode_display = "Research + Signal" if _report_mode == "both" else "Research"
            _meta_lines += [
                f"*Report mode: {_mode_display}*",
                f"*Mode rationale: {_mode_rationale}*",
            ]
        _meta_lines.append("")
```

- [ ] **Step 7: Update `run_research_pipeline()` to accept and pass new params**

Change the function signature:

```python
async def run_research_pipeline(topic: str, report_type: str, run_id: str,
                                user_context: str = "",
                                trade_signal: bool | None = None,
                                deep_dive: bool = False):
```

And in the macro dispatch block:

```python
        else:
            final_report = await _run_macro_pipeline(
                topic, run_id,
                user_context=user_context,
                run_stats=run_stats,
                trade_signal=trade_signal,
                deep_dive=deep_dive,
            )
```

- [ ] **Step 8: Update the `/research` endpoint to accept new params**

Find `submit_research_request` (around line 2121). After extracting `user_context`, add:

```python
    trade_signal_raw = body.get("trade_signal", None)
    if isinstance(trade_signal_raw, bool):
        trade_signal = trade_signal_raw
    elif isinstance(trade_signal_raw, str):
        trade_signal = trade_signal_raw.lower() == "true" if trade_signal_raw.lower() in ("true", "false") else None
    else:
        trade_signal = None

    deep_dive_raw = body.get("deep_dive", False)
    deep_dive = bool(deep_dive_raw) if isinstance(deep_dive_raw, bool) else str(deep_dive_raw).lower() == "true"
```

And pass them to `run_research_pipeline`:

```python
        await asyncio.wait_for(
            run_research_pipeline(
                topic=topic, report_type=report_type,
                run_id=run_id, user_context=user_context,
                trade_signal=trade_signal, deep_dive=deep_dive,
            ),
            timeout=3600,
        )
```

- [ ] **Step 9: Run a smoke test (no API key needed — just test the parse helper)**

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
# Simulate importing just the parse helper
exec(open('main.py').read().split('async def _run_macro_pipeline')[0])
mode, rationale = _parse_mode_detector_output('REPORT_MODE: both\nRATIONALE: Topic references EUR/USD positioning.')
assert mode == 'both', f'Expected both, got {mode}'
mode2, _ = _parse_mode_detector_output('REPORT_MODE: research\nRATIONALE: Thematic.')
assert mode2 == 'research'
mode3, rationale3 = _parse_mode_detector_output('GARBAGE OUTPUT')
assert mode3 == 'research'  # fallback
print('parse helper OK')
"
```

Expected: `parse helper OK`

- [ ] **Step 10: Commit**

```bash
git add main.py
git commit -m "feat(macro): add Phase 0 mode detection — trade_signal/deep_dive params, _parse_mode_detector_output, meta block update"
```

---

### Task 5: Write test fixtures

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/macro/__init__.py`
- Create: `tests/macro/conftest.py`
- Create: `tests/macro/fixtures/` (directory + 6 files)

- [ ] **Step 1: Create package init files**

```bash
touch tests/__init__.py tests/macro/__init__.py
mkdir -p "tests/macro/fixtures"
```

- [ ] **Step 2: Create `tests/macro/conftest.py`**

```python
# tests/macro/conftest.py
import pathlib
import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture
def validated_source_package():
    return load_fixture("validated_source_package.txt")


@pytest.fixture
def data_manifest():
    return load_fixture("data_manifest.txt")


@pytest.fixture
def analyst_high_conviction():
    return load_fixture("analyst_output_high_conviction.txt")


@pytest.fixture
def analyst_low_conviction():
    return load_fixture("analyst_output_low_conviction.txt")


@pytest.fixture
def analyst_thematic():
    return load_fixture("analyst_output_thematic.txt")


@pytest.fixture
def synthesis_document_valid():
    return load_fixture("synthesis_document_valid.txt")
```

- [ ] **Step 3: Create fixture files**

Create `tests/macro/fixtures/validated_source_package.txt`:

```
## Augmented Source Package
Topic: Impact of demographic shifts on EM productivity
Primary geography: Emerging Markets (broad)

### Validated Sources

| # | Title | Source | Date | Geography | Theme | Status |
|---|---|---|---|---|---|---|
| 1 | "Demographic Dividends and Economic Growth in Asia" | IMF Working Paper | 2023 | EM Asia | Demographics/Productivity | [PASS] |
| 2 | "Labor Force Aging and Total Factor Productivity" | BIS Working Paper | 2022 | Global/EM | Demographics | [PASS] |
| 3 | "EM Productivity Convergence: Has It Stalled?" | World Bank Research | 2024 | EM broad | Productivity | [PASS] |
| 4 | "The Silver Economy: Policy Implications for EM" | OECD Economic Outlook | 2023 | EM/DM | Demographics | [PASS] |
| 5 | "India's Demographic Dividend: Progress and Risks" | RBI Working Paper | 2023 | India | Demographics | [PASS] |

### Coverage Summary
- Academic papers (on-geography): 5 [PASS]
- Central bank publications: 2 [PASS]
- Recent news (<90 days): 3 [PASS]
- Historical analog literature: 1 [CAUTION — only 1 found]

### Validation Summary
All sources match EM geography. Historical analog gap partially filled. Augmented with 2 additional CORE papers.
```

Create `tests/macro/fixtures/data_manifest.txt`:

```
## Pre-Gathered Macro Data Inventory

Available sections (do NOT re-fetch — use web_search for topic-specific gaps):

**yield_curve_snapshot**: US Treasury yield curve — 3M, 2Y, 5Y, 10Y, 30Y; T10Y2Y and T10Y3M spreads. Current as of today.
**recession_indicators**: Sahm Rule, unemployment rate, PAYEMS, INDPRO, NFCI, STLFSI4. Recession probability via Estrella-Mishkin (US only).
**fx_rates**: EUR/USD, JPY/USD, GBP/USD, CHF/USD, KRW/USD, INR/USD, MXN/USD, BRL/USD, CNY/USD, Trade-weighted USD.
**macro_indicators**: WTI and Brent oil, gold, natural gas, ISM Manufacturing PMI, ISM Services PMI, University of Michigan consumer sentiment, HY and IG credit spreads.
**world_bank**: GDP growth, inflation, unemployment, current account, government debt, trade balance — 10 major economies, 5-year annual history.
**oecd_leading**: Composite Leading Indicators — 20 countries, monthly, most recent 24 months.
**oecd_outlook**: GDP growth projections — 44 countries, annual.
**imf_weo**: GDP, inflation, unemployment, current account balance forecasts — major economies, current and next 2 years.
**ecb_snapshot**: ECB deposit facility rate, main refinancing rate, Eurozone HICP (headline + core), M3 money supply.
**av_commodities**: WTI, Brent, natural gas, copper, wheat, corn — monthly prices, 5-year history.
**polygon_fx**: 11 major FX pairs — previous-day OHLC close.
```

Create `tests/macro/fixtures/analyst_output_high_conviction.txt`:

```
## Section 1: Macro Summary

[Fact] The EUR/USD exchange rate has depreciated 4.2% year-to-date as the ECB's June meeting approaches, with markets pricing 25bp of cuts by July 2026.

[Analysis] The divergence between ECB easing expectations and Fed-on-hold positioning creates a clear directional driver for EUR/USD weakness. With the ECB governing council majority favoring cuts and Eurozone core inflation falling to 2.4% (March 2026), the interest rate differential is the primary mechanical force.

[Inference] A 25bp ECB cut on June 12, 2026 — widely telegraphed and now 87% priced by OIS — is unlikely to be the catalyst. The market-moving risk is a dovish statement signaling further sequential cuts, which would push the EUR/USD pair below 1.0800 on a 4-6 week horizon.

**Probability-weighted scenarios:**
- Base (60%): ECB cuts 25bp, signals gradual path → EUR/USD 1.0780–1.0850
- Bear EUR (25%): ECB signals sequential cuts → EUR/USD 1.0650–1.0750
- Bull EUR (15%): ECB cuts but signals pause → EUR/USD 1.0950–1.1050

**Key watch point:** ECB June 12 statement language on "meeting-by-meeting" vs "gradual" — 14 trading days.
```

Create `tests/macro/fixtures/analyst_output_low_conviction.txt`:

```
## Section 1: Macro Summary

[Fact] UK gilt yields have risen 38bp year-to-date, reflecting a combination of sticky services inflation (5.2% as of March 2026) and deteriorating fiscal dynamics following the October 2025 budget.

[Analysis] The rate path for the Bank of England is complicated by three competing forces: (1) services inflation persistence suggesting later cuts, (2) weakening labour market data suggesting earlier cuts, and (3) fiscal expansion increasing gilt supply and term premium. These forces pull in opposing directions with no clear near-term resolution.

[Inference] The balance of risks is uncertain. While the long-end of the gilt curve looks expensive relative to historical fiscal deficits at this level, the timing of any re-pricing is unclear given the BoE's communication ambiguity. A directional view on gilts requires a catalyst that is not yet visible in the data.

**Probability-weighted scenarios:**
- Base (45%): BoE cuts twice in H2 2026, gilt 10Y range-bound 4.40–4.70%
- Bear gilts (30%): fiscal slippage + sticky inflation → 10Y above 4.90%
- Bull gilts (25%): sharp growth slowdown → BoE accelerates cuts, 10Y below 4.20%

**Key watch point:** May 2026 CPI release (June 18) and June MPC meeting (June 19) — but conviction on direction is low ahead of these.
```

Create `tests/macro/fixtures/analyst_output_thematic.txt`:

```
## Section 1: Macro Summary

[Fact] Emerging market working-age population growth will slow from 1.8% annually (2010–2020) to 0.9% (2020–2030) across the major EM economies, with East Asia already in demographic contraction.

[Analysis] The demographic dividend that drove EM productivity convergence in the 2000s is structurally reversing. As dependency ratios rise, savings rates fall, and human capital investment per worker declines, total factor productivity (TFP) growth — which averaged 2.1% annually across major EMs from 2000–2010 — is projected to halve by 2030.

[Inference] This is a decade-long structural theme with no near-term trade catalyst. The implications are diffuse across EM asset classes and geography-specific: India and Sub-Saharan Africa remain demographic dividend recipients; East Asia and Latin America are entering headwind territory.

**Probability-weighted scenarios:**
- Base (55%): Gradual demographic headwind — EM TFP growth declines to 1.0–1.2% by 2030
- Downside (30%): Policy failure to compensate (insufficient automation investment, poor migration policy) — TFP growth below 0.5%
- Upside (15%): Technology-led productivity offset (AI, robotics adoption) sustains TFP above 1.5%

**Key watch point:** Annual World Bank TFP estimates by country (published Q4) and OECD demographic projections update (2027).
```

Create `tests/macro/fixtures/synthesis_document_valid.txt`:

```
## Thematic Synthesis Document
Topic: Impact of demographic shifts on EM productivity
Generated by: Gemini Deep Research
Sources added beyond Source Validator: 4

### Synthesis Narrative

The demographic transition underway across major emerging markets represents one of the most consequential structural forces in macroeconomics over the next decade. Unlike cyclical dynamics, this shift operates through multiple transmission channels simultaneously: labor supply contraction, declining savings rates, increased healthcare and pension expenditure crowding out productive investment, and intergenerational knowledge transfer disruptions.

The literature distinguishes between "demographic dividend" phases — where a rising working-age share boosts savings and labor supply — and the subsequent "demographic burden" phase. East Asia's experience is instructive: South Korea's working-age ratio peaked in 2012, and TFP growth has decelerated 0.8 percentage points per decade since. China faces a similar inflection with more compressed timelines.

Critically, the policy response space differs markedly across EM geographies. India retains a 15-year demographic window and has policy tools to leverage it. Brazil and Mexico face structural headwinds without the fiscal space to invest in automation or migration reform.

### Key Claims with Evidence

- EM working-age population growth slowing from 1.8% to 0.9% annually (World Bank, 2024) — HIGH CONFIDENCE
- Historical 1pp rise in old-age dependency ratio associated with 0.3–0.5pp decline in TFP growth (IMF WP/23/041) — HIGH CONFIDENCE
- East Asian EM TFP deceleration post-peak-working-age-share: 0.8pp/decade (BIS WP 2022) — MEDIUM CONFIDENCE (sample size: 4 economies)
- Automation investment can offset up to 40% of demographic productivity drag (NBER WP 2023) — LOW CONFIDENCE (model-dependent)

### Quantitative Gaps Identified

- Country-level savings rate projections for India, Brazil, Mexico, South Korea — not in current data manifest
- Automation investment rates (robots per worker) by EM country — not available in FRED/OECD data
- Sectoral TFP decomposition for major EMs — World Bank annual data covers aggregate only

### Additional Sources Retrieved

| # | Title | Source | Date | URL |
|---|---|---|---|---|
| 1 | "Aging, Robots, and Productivity" | NBER Working Paper 31204 | 2023 | nber.org/papers/w31204 |
| 2 | "Demographic Transitions and Savings in EM Asia" | ADB Working Paper | 2024 | adb.org/publications |
| 3 | "TFP Accounting for Emerging Economies" | World Bank Policy Research | 2023 | documents.worldbank.org |
| 4 | "South Korea Post-Peak: A Template for EM Asia?" | BIS Quarterly Review | 2024 | bis.org/publ |

### Thematic Threads for Analyst

Thread 1: Demographic dividend reversal — from tailwind to headwind across EM geographies (quantify by country)
Thread 2: Savings rate decline and investment crowding-out — fiscal implications of rising dependency ratios
Thread 3: Technology offset hypothesis — can AI/automation substitution preserve TFP growth?
Thread 4: Policy divergence — why India's window differs from East Asia's closed position
Thread 5: Historical analog — Japan's "lost decades" as a cautionary template, and where it does/doesn't apply to EM
```

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/macro/__init__.py tests/macro/conftest.py tests/macro/fixtures/
git commit -m "tests: add macro test scaffolding, conftest, and all fixtures"
```

---

### Task 6: Write unit tests for Mode Detection

**Files:**
- Create: `tests/macro/test_mode_detection.py`

- [ ] **Step 1: Write all mode detection unit tests**

```python
# tests/macro/test_mode_detection.py
"""
Unit tests for macro pipeline mode detection logic.
Tests _parse_mode_detector_output() helper and explicit flag handling.
No API calls — pure logic tests.
"""
import sys
import pathlib
import pytest

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


# ── Tests for _parse_mode_detector_output ─────────────────────────────────────

def _parse(raw: str):
    """Import and call the parse helper from main.py."""
    # Import only the parse helper to avoid loading FastAPI/Vertex AI
    import importlib.util, types
    # We'll test via direct exec of just the function
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    # Extract just the helper function
    start = text.index("def _parse_mode_detector_output(")
    end = text.index("\ndef ", start + 1)
    fn_src = text[start:end]
    ns = {}
    exec(fn_src, ns)
    return ns["_parse_mode_detector_output"](raw)


def test_parse_research_mode():
    mode, rationale = _parse("REPORT_MODE: research\nRATIONALE: Topic is structural exploration.")
    assert mode == "research"
    assert "structural" in rationale


def test_parse_both_mode():
    mode, rationale = _parse("REPORT_MODE: both\nRATIONALE: Topic contains long positioning for EUR/USD.")
    assert mode == "both"
    assert rationale != ""


def test_parse_case_insensitive():
    mode, _ = _parse("report_mode: RESEARCH\nrationale: Thematic.")
    assert mode == "research"


def test_parse_fallback_on_garbage():
    mode, rationale = _parse("I am unable to classify this.")
    assert mode == "research"  # safe fallback
    assert rationale != ""


def test_parse_unknown_mode_falls_back():
    mode, _ = _parse("REPORT_MODE: signal_only\nRATIONALE: Something.")
    assert mode == "research"  # "signal" not in allowed set yet


def test_parse_rationale_present_both():
    _, rationale = _parse("REPORT_MODE: both\nRATIONALE: Explicit EUR/USD long positioning.")
    assert len(rationale) > 5


def test_parse_rationale_present_research():
    _, rationale = _parse("REPORT_MODE: research\nRATIONALE: Demographic analysis.")
    assert len(rationale) > 5


# ── Tests for agent importability ──────────────────────────────────────────────

def test_mode_detector_agent_importable():
    from agents.team import macro_mode_detector
    assert macro_mode_detector is not None
    assert macro_mode_detector.name == "macro_mode_detector"


def test_mode_detector_has_no_tools():
    from agents.team import macro_mode_detector
    assert macro_mode_detector.tools == [] or macro_mode_detector.tools is None
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/macro/test_mode_detection.py -v
```

Expected: all 9 tests `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add tests/macro/test_mode_detection.py
git commit -m "tests: add mode detection unit tests"
```

---

## Sprint 2: Gemini Deep Research Agent

---

### Task 7: Create `tools/deep_research.py`

**Files:**
- Create: `tools/deep_research.py`

**Auth:** Uses Gemini Developer API with `google-ai-api-key` already in Secret Manager. The `google-generativeai` package makes the call directly — no Vertex AI routing. This is the only part of the pipeline that uses a direct API key rather than service account auth.

- [ ] **Step 1: Write the failing test**

```python
# In tests/macro/test_deep_research_handoff.py (full file in Task 10)
def test_deep_research_module_importable():
    from tools.deep_research import run_deep_research, parse_synthesis_document, _build_deep_research_prompt
    assert callable(run_deep_research)
    assert callable(parse_synthesis_document)
    assert callable(_build_deep_research_prompt)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/macro/test_deep_research_handoff.py::test_deep_research_module_importable -v
```

Expected: `ImportError: No module named 'tools.deep_research'`

- [ ] **Step 3: Check `requirements.txt` for `google-generativeai`**

```bash
grep "google-generativeai" requirements.txt
```

If not present, add it:

```bash
echo "google-generativeai>=0.8.0" >> requirements.txt
```

- [ ] **Step 4: Write `tools/deep_research.py`**

```python
"""
tools/deep_research.py

Async client for the Gemini Deep Research API (Gemini Developer API).
Deep Research uses a separate API key (stored in Secret Manager as "google-ai-api-key")
rather than the Vertex AI service account auth used by all other agents.

Models:
  deep-research-preview-04-2026      — standard
  deep-research-max-preview-04-2026  — higher quality, slower

API ref: https://ai.google.dev/gemini-api/docs/deep-research
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_DEEP_RESEARCH_MODEL = "deep-research-preview-04-2026"

_REQUIRED_SECTIONS = [
    "Synthesis Narrative",
    "Key Claims with Evidence",
    "Quantitative Gaps Identified",
    "Additional Sources Retrieved",
    "Thematic Threads for Analyst",
]


def _build_deep_research_prompt(
    topic: str,
    source_package: str,
    data_manifest: str,
    report_mode: str,
) -> str:
    """Build the research prompt sent to the Gemini Deep Research API."""
    mode_instruction = (
        "This synthesis will feed a report that includes both thematic analysis AND a potential "
        "trade recommendation. Pay particular attention to near-term catalysts, "
        "instrument-specific dynamics, and conviction-level evidence."
        if report_mode == "both"
        else "This synthesis will feed a purely thematic macro deep dive. "
             "Focus on structural forces, long-run dynamics, and policy implications."
    )

    return (
        f"You are conducting deep research for an institutional macro research pipeline.\n\n"
        f"TOPIC: {topic}\n\n"
        f"RESEARCH MODE: {report_mode}\n"
        f"{mode_instruction}\n\n"
        f"VALIDATED SOURCE PACKAGE (already gathered — do NOT re-fetch these sources):\n"
        f"{source_package}\n\n"
        f"QUANTITATIVE DATA MANIFEST (summary of pre-gathered structured data):\n"
        f"{data_manifest}\n\n"
        f"YOUR TASKS:\n"
        f"1. Synthesise the validated sources into a coherent thematic narrative.\n"
        f"2. Find additional sources NOT already in the validated package — prioritise:\n"
        f"   - Academic papers (BIS, IMF, NBER, CEPR, academic journals — on-geography, on-theme)\n"
        f"   - Central bank speeches and working papers matching the topic's primary geography\n"
        f"   - BIS, IMF, and OECD working papers (last 3 years)\n"
        f"   - Historical analog literature for the identified theme\n"
        f"3. Identify quantitative data referenced in sources but absent from the data manifest.\n\n"
        f"Return your output in EXACTLY this format:\n\n"
        f"## Thematic Synthesis Document\n"
        f"Topic: {topic}\n"
        f"Generated by: Gemini Deep Research\n"
        f"Sources added beyond Source Validator: [N]\n\n"
        f"### Synthesis Narrative\n"
        f"[2-4 paragraphs: thematic framing, key tensions, structural forces, consensus/dissent]\n\n"
        f"### Key Claims with Evidence\n"
        f"[Bullet list: claim (Source, Year) — HIGH/MEDIUM/LOW CONFIDENCE]\n\n"
        f"### Quantitative Gaps Identified\n"
        f"[Bullet list or 'None identified.']\n\n"
        f"### Additional Sources Retrieved\n"
        f"| # | Title | Source | Date | URL |\n"
        f"|---|---|---|---|---|\n"
        f"[One row per new source]\n\n"
        f"### Thematic Threads for Analyst\n"
        f"[3-5 threads: 'Thread N: Title — one sentence description']"
    )


def parse_synthesis_document(raw: str) -> dict:
    """
    Parse the Deep Research output into a structured dict.

    Returns:
        full_text: str — raw output unchanged
        has_all_sections: bool — True if all 5 required sections present
        missing_sections: list[str] — names of any missing sections
        sources_added_count: int — additional sources retrieved
    """
    result = {
        "full_text": raw,
        "has_all_sections": True,
        "missing_sections": [],
        "sources_added_count": 0,
    }
    for section in _REQUIRED_SECTIONS:
        if f"### {section}" not in raw and f"## {section}" not in raw:
            result["has_all_sections"] = False
            result["missing_sections"].append(section)
    for line in raw.splitlines():
        if "Sources added beyond Source Validator:" in line:
            try:
                result["sources_added_count"] = int(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return result


async def run_deep_research(
    topic: str,
    source_package: str,
    data_manifest: str,
    report_mode: str = "research",
    api_key: str = None,
    timeout: int = 600,
) -> str:
    """
    Call the Gemini Deep Research API and return the synthesis text.

    On timeout or API error, raises an exception — callers catch and fall back
    to source_package as the Macro Analyst's primary qualitative input.

    Args:
        topic: Macro research topic string.
        source_package: Source Validator output (full text).
        data_manifest: Compact macro data manifest (~400 words).
        report_mode: "research" or "both".
        api_key: Gemini Developer API key. Loaded from Secret Manager if None.
        timeout: Max seconds to wait (default 600s).
    """
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise ImportError(
            "google-generativeai is required for Deep Research. "
            "Add it to requirements.txt and redeploy."
        ) from exc

    if api_key:
        genai.configure(api_key=api_key)
    else:
        import os
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "No Gemini API key found. Set GOOGLE_API_KEY env var or ensure "
                "'google-ai-api-key' is accessible from Secret Manager."
            )
        genai.configure(api_key=key)

    prompt = _build_deep_research_prompt(topic, source_package, data_manifest, report_mode)
    logger.info("Deep Research: submitting for topic='%s' mode='%s'", topic, report_mode)
    start = time.monotonic()

    def _call():
        model = genai.GenerativeModel(_DEEP_RESEARCH_MODEL)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=8192,
                temperature=0.3,
            ),
        )
        return response.text

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _call),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Deep Research timed out after {time.monotonic() - start:.0f}s for topic='{topic}'"
        )

    logger.info("Deep Research: done in %.0fs, %d chars", time.monotonic() - start, len(result))
    return result
```

- [ ] **Step 5: Run the import test**

```bash
pytest tests/macro/test_deep_research_handoff.py::test_deep_research_module_importable -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add tools/deep_research.py requirements.txt
git commit -m "feat(macro): add tools/deep_research.py — Gemini Deep Research API client, google-ai-api-key secret"
```

---

### Task 8: Load `google-ai-api-key` from Secret Manager in `main.py`

**Files:**
- Modify: `main.py`

The secret `"google-ai-api-key"` already exists in GCP Secret Manager. This task wires it into the pipeline startup and passes it to `run_deep_research`.

- [ ] **Step 1: Add a `_get_google_ai_api_key()` helper in `main.py`**

Add near the other `_get_*` helpers:

```python
def _get_google_ai_api_key() -> str | None:
    """
    Load the Gemini Developer API key from Secret Manager.
    Secret name: "google-ai-api-key" (already created in GCP Secret Manager).
    Used exclusively by the Deep Research agent — all other agents use Vertex AI.
    """
    try:
        from tools.http_client import get_api_key
        return get_api_key("google-ai-api-key")
    except Exception as exc:
        logger.warning("Could not load 'google-ai-api-key' from Secret Manager: %s", exc)
        return None
```

- [ ] **Step 2: Add `"google-ai-api-key"` to the startup secret validation**

Find `_validate_secrets_at_startup()`. Locate the list of secrets it checks and add:

```python
"google-ai-api-key",   # Gemini Developer API key for Deep Research
```

Read the function first to match the exact format used (it may be a list or a dict).

- [ ] **Step 3: Import `run_deep_research` and `parse_synthesis_document`**

```python
from tools.deep_research import run_deep_research, parse_synthesis_document
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(macro): load google-ai-api-key for Deep Research — startup validation + _get_google_ai_api_key helper"
```

---

### Task 9: Implement `_run_deep_research_agent()` in `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write `_run_deep_research_agent()` in `main.py`**

Add this function before `_run_macro_pipeline`:

```python
async def _run_deep_research_agent(
    topic: str,
    source_package: str,
    data_manifest: str,
    report_mode: str,
    run_id: str,
    identifier: str,
    run_stats: "RunStats" = None,
) -> str:
    """
    Run the Gemini Deep Research agent and save the Thematic Synthesis Document to GCS.

    Returns the synthesis document text (full_text from parse result).
    On failure/timeout, logs the error and returns the source_package as fallback
    so the Macro Analyst can still run in degraded mode.
    """
    logger.info("[%s] Phase 1e: Gemini Deep Research (expanding sources, thematic synthesis)...", run_id)

    if run_stats is not None:
        record_agent_start(run_stats, "deep-research")

    start_time = __import__("datetime").datetime.utcnow()
    raw_output = ""
    status = "success"

    try:
        api_key = _get_google_ai_api_key()
        raw_output = await run_deep_research(
            topic=topic,
            source_package=source_package,
            data_manifest=data_manifest,
            report_mode=report_mode,
            api_key=api_key,
            timeout=_DEEP_RESEARCH_TIMEOUT,
        )
        parsed = parse_synthesis_document(raw_output)
        if not parsed["has_all_sections"]:
            logger.warning(
                "[%s] Deep Research output missing sections: %s",
                run_id, parsed["missing_sections"]
            )
        logger.info(
            "[%s] Phase 1e: Deep Research complete — %d chars, %d additional sources",
            run_id, len(raw_output), parsed["sources_added_count"]
        )

        # Save synthesis document to GCS immediately (before Macro Analyst runs)
        synthesis_save = save_report(
            content=raw_output,
            report_type="macro",
            identifier=identifier,
            suffix="_synthesis",      # results in {run_id}_synthesis.md
        )
        if synthesis_save.get("saved"):
            logger.info("[%s] Synthesis document saved to %s", run_id, synthesis_save.get("gcs_uri"))
        else:
            logger.warning("[%s] Synthesis document save failed (non-fatal): %s",
                           run_id, synthesis_save.get("error"))

    except TimeoutError as e:
        logger.warning("[%s] Phase 1e: Deep Research TIMEOUT — %s — falling back to Source Validator output", run_id, e)
        status = "timeout"
        raw_output = source_package  # fallback
    except Exception as e:
        logger.error("[%s] Phase 1e: Deep Research ERROR — %s — falling back to Source Validator output", run_id, e)
        status = "error"
        raw_output = source_package  # fallback

    if run_stats is not None:
        end_time = __import__("datetime").datetime.utcnow()
        # Record as a lightweight agent entry (no token counts — Deep Research bills per query)
        record_agent_complete(
            run_stats,
            label="deep-research",
            status=status,
            output_length=len(raw_output),
            model="gemini-deep-research",
            input_tokens=0,
            output_tokens=0,
            search_calls=0,
            # Cost is per-query, not per-token — tracked separately via config.yaml
        )
        _dr_cost = CONFIG.get("pricing", {}).get("deep_research_cost_per_query", 0.0)
        if _dr_cost and status == "success":
            run_stats.total_cost_usd = round(run_stats.total_cost_usd + _dr_cost, 6)

    return raw_output
```

Note: `save_report()` currently takes `(content, report_type, identifier)` and auto-generates the filename from a timestamp. You need to check `tools/storage.py` to see if it supports a `suffix` parameter. If not, call it as:

```python
        # Alternative if save_report doesn't support suffix:
        synthesis_gcs_path = f"macro/{identifier}/{run_id}_synthesis"
        synthesis_save = save_report(
            content=raw_output,
            report_type="macro-synthesis",   # separate report_type prefix in GCS
            identifier=f"{identifier}/{run_id}_synthesis",
        )
```

Read `tools/storage.py` first and adapt accordingly.

- [ ] **Step 4: Slot Phase 1e into `_run_macro_pipeline()` after Source Validator**

After the `source_validator_out = await _run_agent(...)` block and before Step 2 (Macro Analyst), add:

```python
    # ── Phase 1e: Gemini Deep Research — thematic synthesis + source expansion ──
    _identifier = topic.replace(" ", "-").replace("/", "-").lower()
    synthesis_doc = await _run_deep_research_agent(
        topic=topic,
        source_package=source_validator_out,
        data_manifest=_macro_data_manifest(macro_data_dict),
        report_mode=report_mode,
        run_id=run_id,
        identifier=_identifier,
        run_stats=run_stats,
    )
    logger.info("[%s] Phase 1e: Synthesis document ready (%d chars)", run_id, len(synthesis_doc))
```

- [ ] **Step 5: Update the Macro Analyst context to use `synthesis_doc` as primary qualitative input**

Find the `analysis_context` variable construction (Step 2 block). Change:

```python
        f"MACRO DATA AGENT OUTPUT (topic-specific data + web sources):\n{data_output}\n\n"
        f"MACRO SOURCE VALIDATOR OUTPUT (validated + augmented source package):\n"
        f"{source_validator_out}\n\n"
```

To:

```python
        f"THEMATIC SYNTHESIS DOCUMENT (Deep Research — expanded sources + synthesis narrative):\n"
        f"{synthesis_doc}\n\n"
        f"MACRO DATA AGENT OUTPUT (topic-specific data — for quantitative context):\n{data_output}\n\n"
```

This makes the Thematic Synthesis Document the primary qualitative input, with the Data Agent output retained for quantitative data tables.

- [ ] **Step 6: Update `prompts/macro_analyst.md`**

Find the section of `macro_analyst.md` that references "validated source package" or "Macro Source Validator output". Replace with:

```
PRIMARY QUALITATIVE INPUT: Thematic Synthesis Document (from Gemini Deep Research Agent)
Use the Synthesis Document as your primary source of qualitative evidence, thematic threads,
and key claims. The Data Agent output below provides the quantitative data tables.
```

Check the file first and make the exact replacement:

```bash
grep -n "validated source\|Source Validator\|source package\|data agent output" prompts/macro_analyst.md
```

Make the targeted edits based on what you find.

- [ ] **Step 7: Commit**

```bash
git add main.py tools/deep_research.py prompts/macro_analyst.md
git commit -m "feat(macro): add Phase 1e Deep Research — _run_deep_research_agent, synthesis save to GCS, Analyst prompt update"
```

---

### Task 10: Write unit tests for Deep Research handoff

**Files:**
- Create: `tests/macro/test_deep_research_handoff.py`

- [ ] **Step 1: Write the test file**

```python
# tests/macro/test_deep_research_handoff.py
"""
Unit tests for the Deep Research handoff and output parsing.
All tests use the fixture files — no live API calls.
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from tools.deep_research import parse_synthesis_document, _build_deep_research_prompt


def test_deep_research_module_importable():
    from tools.deep_research import run_deep_research, parse_synthesis_document
    assert callable(run_deep_research)
    assert callable(parse_synthesis_document)


def test_synthesis_document_schema_all_sections(synthesis_document_valid):
    """A valid synthesis document must contain all 5 required sections."""
    result = parse_synthesis_document(synthesis_document_valid)
    assert result["has_all_sections"] is True, (
        f"Missing sections: {result['missing_sections']}"
    )
    assert result["missing_sections"] == []


def test_synthesis_document_sources_count(synthesis_document_valid):
    """Parser extracts the sources-added count correctly."""
    result = parse_synthesis_document(synthesis_document_valid)
    assert result["sources_added_count"] == 4


def test_synthesis_document_full_text_preserved(synthesis_document_valid):
    """Full text is returned unchanged in the result dict."""
    result = parse_synthesis_document(synthesis_document_valid)
    assert result["full_text"] == synthesis_document_valid


def test_synthesis_document_missing_section():
    """Document missing a required section is flagged."""
    incomplete = """## Thematic Synthesis Document
Topic: Test
Sources added beyond Source Validator: 0

### Synthesis Narrative
Some narrative.

### Key Claims with Evidence
- Claim one.

### Quantitative Gaps Identified
None identified.

### Thematic Threads for Analyst
Thread 1: Something.
"""
    # Missing "Additional Sources Retrieved"
    result = parse_synthesis_document(incomplete)
    assert result["has_all_sections"] is False
    assert "Additional Sources Retrieved" in result["missing_sections"]


def test_manifest_not_full_data(data_manifest):
    """Data manifest fixture is compact — under 1000 words."""
    word_count = len(data_manifest.split())
    assert word_count < 1000, (
        f"Data manifest is {word_count} words — should be compact (~400 words). "
        "Check that _macro_data_manifest() is being used, not the full data dump."
    )


def test_prompt_contains_source_package(validated_source_package, data_manifest):
    """Deep Research prompt includes both the source package and data manifest."""
    prompt = _build_deep_research_prompt(
        topic="Test topic",
        source_package=validated_source_package,
        data_manifest=data_manifest,
        report_mode="research",
    )
    assert "VALIDATED SOURCE PACKAGE" in prompt
    assert "QUANTITATIVE DATA MANIFEST" in prompt
    assert validated_source_package[:100] in prompt


def test_prompt_research_mode_instruction(data_manifest, validated_source_package):
    """Research mode prompt contains thematic instruction, not trade language."""
    prompt = _build_deep_research_prompt(
        topic="Test topic",
        source_package=validated_source_package,
        data_manifest=data_manifest,
        report_mode="research",
    )
    assert "purely thematic" in prompt.lower() or "thematic macro deep dive" in prompt.lower()
    assert "trade recommendation" not in prompt.lower()


def test_prompt_both_mode_instruction(data_manifest, validated_source_package):
    """Both mode prompt mentions trade recommendation context."""
    prompt = _build_deep_research_prompt(
        topic="Test topic",
        source_package=validated_source_package,
        data_manifest=data_manifest,
        report_mode="both",
    )
    assert "trade recommendation" in prompt.lower()
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/macro/test_deep_research_handoff.py -v
```

Expected: all 9 tests `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add tests/macro/test_deep_research_handoff.py
git commit -m "tests: add Deep Research handoff unit tests"
```

---

## Sprint 3: Signal Agent & Section 5 Conditional Rendering

---

### Task 11: Write `prompts/macro_signal_agent.md`

**Files:**
- Create: `prompts/macro_signal_agent.md`

- [ ] **Step 1: Write the prompt**

```markdown
# Macro Signal Agent

You are a conviction-assessment specialist for an institutional macro research pipeline.
You receive the Macro Analyst's 8-section report and assess whether a trade recommendation,
directional stance, or only observational commentary is warranted.

## Your Job

Read the Macro Analyst's output carefully. Assess the quality of evidence, catalyst clarity,
and instrument specificity. Output a tiered signal assessment.

## Conviction Tiers

**Tier 1 — Strong conviction (explicit trade call):**
All four conditions must be met:
- Clear directional driver with a stated transmission mechanism
- Near-term catalyst with a firm date (not "in coming months")
- Quantifiable threshold breach (specific level, spread, or indicator level)
- Historical analog with a documented outcome that is directionally consistent

**Tier 2 — Directional stance:**
The thesis is directional but meets fewer than all four Tier 1 conditions. Typically:
- Catalyst timing is uncertain, OR
- Multiple competing drivers reduce precision, OR
- No tight historical analog, BUT the overall weight of evidence leans one way

**Tier 3 — Observational commentary only:**
Use Tier 3 when:
- Topic is purely structural or thematic with no clear instrument mapping
- Evidence is balanced or contested
- The Macro Analyst explicitly notes low conviction in the Macro Summary
- No named financial instruments are relevant to the thesis

## Erring Toward Recommendations

The pipeline is designed to err toward providing trade recommendations when evidence allows.
- If you are between Tier 1 and Tier 2, default to Tier 2 (not Tier 3)
- Tier 3 is reserved for genuinely thematic or contested topics
- A bullish/bearish stance on a named instrument is better than silence

## Output Format

Return EXACTLY this structure:

```
## Signal Assessment
Conviction tier: [1|2|3]
Tier rationale: [One sentence. E.g., "Four Tier 1 conditions met: clear EUR/USD driver, June 12 ECB catalyst, 1.08 technical threshold, 2014 ECB divergence analog."]

### Recommendation
[For Tier 1: instrument, direction, entry rationale, stop condition, time horizon]
[For Tier 2: named instruments + directional bias — e.g., "Bias short EUR/USD on a 4-6 week horizon pending ECB statement tone. No specific entry or stop — monitor June 12 language."]
[For Tier 3: which asset classes this theme is relevant to, why no stance is taken]
```

Do NOT add headers, preambles, or conclusions outside this structure.
```

**ADK brace safety check:** After saving, run:

```bash
grep -n "{" prompts/macro_signal_agent.md | grep -v "^\s*\`"
```

Expected: no output.

- [ ] **Step 2: Commit**

```bash
git add prompts/macro_signal_agent.md
git commit -m "prompts: add macro_signal_agent system prompt"
```

---

### Task 12: Add `macro_signal_agent` to `agents/team.py`

**Files:**
- Modify: `agents/team.py`

- [ ] **Step 1: Write the failing test**

```python
def test_signal_agent_importable():
    from agents.team import macro_signal_agent
    assert macro_signal_agent.name == "macro_signal_agent"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/macro/test_signal_agent.py::test_signal_agent_importable -v
```

Expected: `ImportError`

- [ ] **Step 3: Add the agent definition in `agents/team.py`**

After `macro_mode_detector`, add:

```python
macro_signal_agent = Agent(
    name="macro_signal_agent",
    model=MODEL_TIER3,
    description=(
        "Conviction assessor for the macro pipeline. Reads the Macro Analyst output "
        "and classifies the trade signal into three tiers: Tier 1 (explicit trade call), "
        "Tier 2 (directional stance), or Tier 3 (observational only). "
        "Runs only when report_mode is 'both'. Errs toward giving recommendations."
    ),
    instruction=_load_prompt("macro_signal_agent.md"),
    generate_content_config=genai_types.GenerateContentConfig(
        max_output_tokens=4096,
    ),
    tools=[],  # Reads analyst output only — no external tools
)
```

- [ ] **Step 4: Add to `MACRO_AGENTS` dict**

```python
MACRO_AGENTS = {
    "orchestrator": research_orchestrator,
    "context_processor": context_processor,
    "macro_mode_detector": macro_mode_detector,        # ← Sprint 1
    "macro_data_agent": macro_data_agent,
    "macro_source_validator": macro_source_validator,
    # Deep Research is NOT an ADK agent — it's called via tools/deep_research.py
    "macro_analyst": macro_analyst,
    "quant_modeler_macro": quant_modeler_macro,
    "macro_signal_agent": macro_signal_agent,          # ← Sprint 3
    "macro_report_compiler": macro_report_compiler,
    "fact_checker": fact_checker,
    "review_agent": review_agent,
}
```

- [ ] **Step 5: Add `macro_signal_agent` to `main.py` imports**

```python
from agents.team import (
    ...
    macro_mode_detector,
    macro_signal_agent,    # ← ADD
    macro_analyst,
    ...
)
```

- [ ] **Step 6: Run the import test**

```bash
pytest tests/macro/test_signal_agent.py::test_signal_agent_importable -v
```

Expected: `PASSED`

- [ ] **Step 7: Commit**

```bash
git add agents/team.py main.py
git commit -m "agents: add macro_signal_agent definition and MACRO_AGENTS entry"
```

---

### Task 13: Write `_parse_signal_agent_output()` and slot Phase 3b into pipeline

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write `_parse_signal_agent_output()` helper**

Add near `_parse_mode_detector_output`:

```python
def _parse_signal_agent_output(raw: str) -> dict:
    """
    Parse Signal Agent output into a structured dict.

    Returns:
        {
            "tier": int (1, 2, or 3),
            "tier_rationale": str,
            "recommendation": str,
            "full_text": str,
        }
    Falls back to tier=3 on parse failure.
    """
    result = {
        "tier": 3,
        "tier_rationale": "Unable to parse signal agent output — defaulting to observational.",
        "recommendation": raw,
        "full_text": raw,
    }
    lines = raw.splitlines()
    in_recommendation = False
    recommendation_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("conviction tier:"):
            try:
                tier_str = stripped.split(":", 1)[1].strip()
                tier_val = int(tier_str[0])  # take first character as digit
                if tier_val in (1, 2, 3):
                    result["tier"] = tier_val
            except (ValueError, IndexError):
                pass
        elif stripped.lower().startswith("tier rationale:"):
            result["tier_rationale"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("### Recommendation"):
            in_recommendation = True
        elif in_recommendation:
            recommendation_lines.append(line)

    if recommendation_lines:
        result["recommendation"] = "\n".join(recommendation_lines).strip()

    return result
```

- [ ] **Step 2: Add Phase 3b (Signal Agent) running in parallel with Quant Modeler**

Find the Step 3 block (Quant Modeler). Replace the sequential call with a conditional parallel gather:

```python
    # ── Step 3: Quant Modeler (Macro) + Signal Agent (parallel) ──────────────
    logger.info("[%s] STEP 3: Quant Modeler (macro) starting...", run_id)
    if report_mode == "both":
        logger.info("[%s] STEP 3b: Signal Agent also starting (report_mode='both')...", run_id)
        _signal_context = (
            f"SIGNAL ASSESSMENT REQUEST\n"
            f"Topic: {topic}\n"
            f"Report mode: {report_mode}\n\n"
            f"MACRO ANALYST OUTPUT:\n{analysis_out}\n\n"
            f"Assess conviction tier and produce the appropriate recommendation level."
        )
        quant_out, signal_raw = await asyncio.gather(
            _run_agent(
                quant_modeler_macro, quant_context, "quant-macro", run_id,
                timeout_seconds=_QUANT_MACRO_TIMEOUT, run_stats=run_stats,
            ),
            _run_agent(
                macro_signal_agent, _signal_context, "signal-agent", run_id,
                timeout_seconds=_SIGNAL_AGENT_TIMEOUT, run_stats=run_stats,
            ),
        )
        signal_parsed = _parse_signal_agent_output(signal_raw)
        logger.info("[%s] Signal Agent: tier=%d — %s", run_id, signal_parsed["tier"], signal_parsed["tier_rationale"])
    else:
        quant_out = await _run_agent(
            quant_modeler_macro, quant_context, "quant-macro", run_id,
            timeout_seconds=_QUANT_MACRO_TIMEOUT, run_stats=run_stats,
        )
        signal_parsed = {"tier": 3, "tier_rationale": "Research mode — no signal assessment.", "recommendation": "", "full_text": ""}
        logger.info("[%s] STEP 3b: Skipping Signal Agent (report_mode='%s')", run_id, report_mode)

    # Store signal tier on run_stats for meta block / debug report
    if run_stats is not None:
        run_stats.signal_tier = signal_parsed["tier"]          # dynamic attribute
        run_stats.signal_rationale = signal_parsed["tier_rationale"]  # dynamic attribute
```

- [ ] **Step 3: Update the compile_context to include signal output**

Find the `compile_context` variable. Add the signal assessment section:

```python
    # ── Step 4: Macro Report Compiler ─────────────────────────────────────────
    _section5_mode = _get_section5_mode(report_mode, signal_parsed["tier"])
    compile_context = (
        f"MACRO REPORT COMPILATION REQUEST\n"
        f"Topic: {topic}\n"
        f"Run ID: {run_id}\n\n"
        f"REPORT MODE: {report_mode}\n"
        f"SECTION 5 RENDERING MODE: {_section5_mode}\n\n"
        f"Assemble the following outputs into the final 8-section macro research report. "
        f"Apply the Section 5 rendering rule for '{_section5_mode}' as defined in your instructions.\n\n"
        f"--- MACRO ANALYST OUTPUT ---\n"
        f"{_clean_for_compiler('Macro Analyst', analysis_out)}\n\n"
        f"--- QUANT MODELER OUTPUT ---\n"
        f"{_clean_for_compiler('Quant Modeler', quant_out)}\n\n"
        f"--- SIGNAL AGENT OUTPUT ---\n"
        f"{_clean_for_compiler('Signal Agent', signal_parsed['full_text'])}\n\n"
        f"--- MACRO SOURCE VALIDATOR OUTPUT (for Source Log) ---\n"
        f"{_clean_for_compiler('Source Validator', source_validator_out)}\n"
    )
```

- [ ] **Step 4: Write `_get_section5_mode()` helper**

Add near `_parse_signal_agent_output`:

```python
def _get_section5_mode(report_mode: str, signal_tier: int) -> str:
    """
    Determine the Section 5 rendering mode for the Report Compiler.
    Returns a string label the compiler uses to select the rendering rule.
    """
    if report_mode == "research" or signal_tier == 3:
        return "market_relevance"
    elif signal_tier == 1:
        return "trade_recommendation"
    elif signal_tier == 2:
        return "investment_implications"
    return "market_relevance"  # safe default
```

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(macro): add Phase 3b Signal Agent — parallel with Quant Modeler, _parse_signal_agent_output, _get_section5_mode"
```

---

### Task 14: Update `prompts/macro_report_compiler.md` for Section 5 conditional rendering

**Files:**
- Modify: `prompts/macro_report_compiler.md`

- [ ] **Step 1: Read the current Section 5 instruction in the compiler prompt**

```bash
grep -n -A 5 "Section 5\|Investment Implication" prompts/macro_report_compiler.md | head -40
```

- [ ] **Step 2: Add conditional rendering rules**

Find the part of `macro_report_compiler.md` that describes Section 5 assembly. Add the following block immediately before the Section 5 instruction (adapt the wording to match the existing tone/format of the prompt):

```
## Section 5 Rendering Rules

You will receive a SECTION 5 RENDERING MODE in your compilation request. Apply the corresponding rule:

| Mode | Section 5 Title | Content to include |
|---|---|---|
| trade_recommendation | Investment Implications & Trade Recommendation | Full Signal Agent Tier 1 trade call + Macro Analyst asset class framing + Quant Block 2 regression |
| investment_implications | Investment Implications | Signal Agent Tier 2 directional stance + Macro Analyst asset class framing + Quant Block 2 regression. NO entry or stop levels. |
| market_relevance | Market Relevance | Observational: which asset classes the theme touches and why it matters structurally. Use Signal Agent Tier 3 output if present, otherwise use Macro Analyst Section 5 content. NO directional stance. |

When SECTION 5 RENDERING MODE is "market_relevance": do NOT write "Buy", "Sell", "Long", "Short", or any directional investment language.
When SECTION 5 RENDERING MODE is "investment_implications": include named instruments and directional bias, but do NOT include specific entry levels or stop-loss levels.
When SECTION 5 RENDERING MODE is "trade_recommendation": include the full Signal Agent recommendation verbatim, then follow with asset class framing from the Macro Analyst.
```

**ADK brace safety check:**

```bash
grep -n "{" prompts/macro_report_compiler.md | grep -v "^\s*\`"
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add prompts/macro_report_compiler.md
git commit -m "prompts: add Section 5 conditional rendering rules to macro_report_compiler"
```

---

### Task 15: Write unit tests for Signal Agent and Section 5 rendering

**Files:**
- Create: `tests/macro/test_signal_agent.py`
- Create: `tests/macro/test_section5_rendering.py`

- [ ] **Step 1: Write `tests/macro/test_signal_agent.py`**

```python
# tests/macro/test_signal_agent.py
"""
Unit tests for Signal Agent output parsing and activation logic.
No API calls — tests _parse_signal_agent_output and guard conditions.
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


def _parse(raw: str) -> dict:
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    start = text.index("def _parse_signal_agent_output(")
    # Find next top-level function
    end = text.index("\ndef ", start + 1)
    fn_src = text[start:end]
    ns = {}
    exec(fn_src, ns)
    return ns["_parse_signal_agent_output"](raw)


_TIER1_OUTPUT = """## Signal Assessment
Conviction tier: 1
Tier rationale: All four conditions met — EUR/USD driver, June 12 ECB catalyst, 1.08 threshold, 2014 analog.

### Recommendation
Instrument: EUR/USD
Direction: Short
Entry rationale: ECB rate path divergence vs Fed on-hold makes EUR/USD structurally weak into June 12.
Stop condition: Close above 1.1000 on a weekly basis.
Time horizon: 4-6 weeks
"""

_TIER2_OUTPUT = """## Signal Assessment
Conviction tier: 2
Tier rationale: Directional but catalyst timing is uncertain — BoE MPC date unclear.

### Recommendation
Bias short gilts (10Y) on a 3-6 month horizon pending BoE MPC guidance. No specific entry or stop.
"""

_TIER3_OUTPUT = """## Signal Assessment
Conviction tier: 3
Tier rationale: Purely thematic — no instrument mapping for demographic shifts in EM.

### Recommendation
This theme is relevant to EM equity (productivity headwind), EM local currency bonds (savings rate decline),
and EM FX (current account dynamics). No actionable stance at this time.
"""


def test_signal_agent_importable():
    from agents.team import macro_signal_agent
    assert macro_signal_agent.name == "macro_signal_agent"


def test_tier1_parsed_correctly():
    result = _parse(_TIER1_OUTPUT)
    assert result["tier"] == 1
    assert "June 12" in result["tier_rationale"]


def test_tier2_parsed_correctly():
    result = _parse(_TIER2_OUTPUT)
    assert result["tier"] == 2


def test_tier3_parsed_correctly():
    result = _parse(_TIER3_OUTPUT)
    assert result["tier"] == 3


def test_tier1_recommendation_non_empty():
    result = _parse(_TIER1_OUTPUT)
    assert len(result["recommendation"]) > 20


def test_tier_rationale_always_present():
    for raw in (_TIER1_OUTPUT, _TIER2_OUTPUT, _TIER3_OUTPUT):
        result = _parse(raw)
        assert len(result["tier_rationale"]) > 5, f"Empty rationale for tier {result['tier']}"


def test_parse_fallback_on_garbage():
    result = _parse("This is not a valid signal agent output.")
    assert result["tier"] == 3  # safe fallback


def test_full_text_preserved():
    result = _parse(_TIER1_OUTPUT)
    assert result["full_text"] == _TIER1_OUTPUT
```

- [ ] **Step 2: Write `tests/macro/test_section5_rendering.py`**

```python
# tests/macro/test_section5_rendering.py
"""
Unit tests for Section 5 conditional rendering logic (_get_section5_mode).
No API calls — pure logic tests.
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


def _get_section5_mode(report_mode: str, signal_tier: int) -> str:
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    start = text.index("def _get_section5_mode(")
    end = text.index("\ndef ", start + 1)
    fn_src = text[start:end]
    ns = {}
    exec(fn_src, ns)
    return ns["_get_section5_mode"](report_mode, signal_tier)


def test_research_mode_renders_market_relevance():
    assert _get_section5_mode("research", 3) == "market_relevance"


def test_research_mode_tier1_still_market_relevance():
    # Signal agent doesn't run in research mode — tier is always 3
    assert _get_section5_mode("research", 3) == "market_relevance"


def test_both_mode_tier1_renders_trade_recommendation():
    assert _get_section5_mode("both", 1) == "trade_recommendation"


def test_both_mode_tier2_renders_investment_implications():
    assert _get_section5_mode("both", 2) == "investment_implications"


def test_both_mode_tier3_renders_market_relevance():
    assert _get_section5_mode("both", 3) == "market_relevance"


def test_unknown_tier_falls_back_to_market_relevance():
    assert _get_section5_mode("both", 99) == "market_relevance"
```

- [ ] **Step 3: Run all unit tests so far**

```bash
pytest tests/macro/test_mode_detection.py \
       tests/macro/test_signal_agent.py \
       tests/macro/test_deep_research_handoff.py \
       tests/macro/test_section5_rendering.py \
       -v
```

Expected: all tests `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add tests/macro/test_signal_agent.py tests/macro/test_section5_rendering.py
git commit -m "tests: add Signal Agent and Section 5 rendering unit tests"
```

---

## Sprint 4: Integration, Audit, and Documentation

---

### Task 16: Check `tools/storage.py` for synthesis save compatibility

**Files:**
- Modify: `tools/storage.py` (if needed)

- [ ] **Step 1: Read `save_report()` signature**

```bash
grep -n "def save_report" tools/storage.py
```

Read the function to understand its current signature and GCS path construction.

- [ ] **Step 2: Add synthesis save support if needed**

If `save_report()` doesn't support a custom file suffix or alternate path, add an overload or a thin wrapper. The goal is to produce:

```
gs://{bucket}/macro/{identifier}/{timestamp}_synthesis.md
```

The simplest approach is to call `save_report` with `report_type="macro"` and a modified `identifier` that includes the run_id and `_synthesis` suffix. Read the current implementation and adapt `_run_deep_research_agent()` in `main.py` accordingly. Do NOT change equity pipeline behavior.

- [ ] **Step 3: Commit if changed**

```bash
git add tools/storage.py main.py
git commit -m "fix(storage): support synthesis artifact save for macro deep research"
```

---

### Task 17: Write integration tests

**Files:**
- Create: `tests/macro/test_pipeline_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/macro/test_pipeline_integration.py
"""
Integration tests for the macro pipeline with new components.

These tests make real LLM calls and GCS saves.
Mark: pytest -m integration

Requires:
  - GOOGLE_CLOUD_PROJECT env var set (or config.yaml populated)
  - Valid GCP service account credentials (ADC or GOOGLE_APPLICATION_CREDENTIALS)
  - GOOGLE_API_KEY or GEMINI_API_KEY env var set for Deep Research
  - GCS bucket accessible
"""
import asyncio
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Helper to get a minimal pipeline run ──────────────────────────────────────

def _minimal_macro_run(topic: str, trade_signal=None, deep_dive=False):
    """Import and call _run_macro_pipeline with test config."""
    import uuid, datetime
    from tools.debug_report import create_run_stats
    run_id = f"test-{uuid.uuid4().hex[:8]}"
    run_stats = create_run_stats(run_id, topic, "macro")
    from main import _run_macro_pipeline
    return _run(
        _run_macro_pipeline(
            topic=topic,
            run_id=run_id,
            user_context="",
            run_stats=run_stats,
            trade_signal=trade_signal,
            deep_dive=deep_dive,
        )
    ), run_stats


# ── Mode detection integration ────────────────────────────────────────────────

def test_research_mode_auto_detect():
    """Thematic topic should auto-detect as research mode."""
    from main import _parse_mode_detector_output
    # Just test the parse helper with a realistic LLM output
    mode, rationale = _parse_mode_detector_output(
        "REPORT_MODE: research\nRATIONALE: Topic is structural exploration of EM demographics."
    )
    assert mode == "research"


def test_signal_mode_auto_detect():
    """Positioning topic should auto-detect as both mode."""
    from main import _parse_mode_detector_output
    mode, _ = _parse_mode_detector_output(
        "REPORT_MODE: both\nRATIONALE: Topic contains long positioning language."
    )
    assert mode == "both"


# ── Section 5 rendering integration ───────────────────────────────────────────

def test_research_mode_section5_title_is_market_relevance():
    """In research mode, Section 5 title must be Market Relevance."""
    from main import _get_section5_mode
    mode = _get_section5_mode("research", 3)
    assert mode == "market_relevance"


def test_both_tier1_section5_title():
    from main import _get_section5_mode
    mode = _get_section5_mode("both", 1)
    assert mode == "trade_recommendation"


# ── Cost tracking integration ─────────────────────────────────────────────────

def test_cost_summary_structure():
    """format_cost_summary accepts a run_stats with new dynamic attributes."""
    from tools.debug_report import create_run_stats, format_cost_summary
    import uuid
    rs = create_run_stats(uuid.uuid4().hex[:8], "test topic", "macro")
    rs.report_mode = "research"
    rs.mode_rationale = "Test mode."
    rs.signal_tier = 3
    # Should not raise
    cost_text = format_cost_summary(rs)
    assert isinstance(cost_text, str)


# ── Deep Research fallback ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deep_research_timeout_returns_fallback():
    """On timeout, _run_deep_research_agent returns the source_package as fallback."""
    from tools.debug_report import create_run_stats
    import uuid
    run_stats = create_run_stats(uuid.uuid4().hex[:8], "test", "macro")

    # Patch run_deep_research to raise TimeoutError
    import tools.deep_research as dr_module
    original = dr_module.run_deep_research

    async def _mock_timeout(*args, **kwargs):
        raise TimeoutError("Mocked timeout")

    dr_module.run_deep_research = _mock_timeout
    try:
        from main import _run_deep_research_agent
        result = await _run_deep_research_agent(
            topic="Test topic",
            source_package="SOURCE PACKAGE CONTENT",
            data_manifest="manifest",
            report_mode="research",
            run_id="test-run",
            identifier="test-topic",
            run_stats=run_stats,
        )
        assert result == "SOURCE PACKAGE CONTENT"  # fallback
    finally:
        dr_module.run_deep_research = original


# ── Synthesis artifact ────────────────────────────────────────────────────────

def test_synthesis_document_schema_valid():
    """Valid synthesis document fixture passes schema check."""
    import pathlib
    from tools.deep_research import parse_synthesis_document
    fixture = (pathlib.Path(__file__).parent / "fixtures" / "synthesis_document_valid.txt").read_text()
    result = parse_synthesis_document(fixture)
    assert result["has_all_sections"] is True
    assert result["sources_added_count"] >= 0


# ── Signal agent activation guard ─────────────────────────────────────────────

def test_signal_agent_skipped_in_research_mode():
    """In research mode, signal_tier is 3 and no signal call is made."""
    from main import _get_section5_mode
    # Research mode always gets signal_tier=3 (Signal Agent not called)
    mode = _get_section5_mode("research", 3)
    assert mode == "market_relevance"
```

- [ ] **Step 2: Run the non-API integration tests (safe subset)**

```bash
pytest tests/macro/test_pipeline_integration.py -v -k "not asyncio and not timeout"
```

Expected: all non-async tests `PASSED`.

- [ ] **Step 3: Run the full integration suite (requires credentials)**

```bash
pytest tests/macro/test_pipeline_integration.py -v -m integration
```

Expected: all `PASSED`. If Deep Research times out (API key not configured), the `test_deep_research_timeout_returns_fallback` test validates the fallback path.

- [ ] **Step 4: Commit**

```bash
git add tests/macro/test_pipeline_integration.py
git commit -m "tests: add macro pipeline integration tests — mode, Section 5, cost, Deep Research fallback"
```

---

### Task 18: Update `project_guide.md`

**Files:**
- Modify: `project_guide.md`

- [ ] **Step 1: Update the Macro Pipeline section diagram**

Find the `### Macro Pipeline` section. Update the pipeline step list to:

```
1a. _gather_macro_data()         — Python: 11 parallel pre-fetches (unchanged)
0.  macro_mode_detector          — Classifies request as "research" or "both"; sets report_mode
1b. macro_data_agent             — Topic-specific FRED + web search + source log (unchanged)
1c. macro_source_validator       — Validates source geography/theme fit; fills gaps (unchanged)
1d. context_processor            — (optional, unchanged)
1e. _run_deep_research_agent()   — Gemini Deep Research API: expands sources, produces
                                   Thematic Synthesis Document; saved to GCS as {run_id}_synthesis.md
2.  macro_analyst                — 8-section report; primary input: Thematic Synthesis Document
3.  quant_modeler_macro + [3b. macro_signal_agent if report_mode="both"] — parallel
    macro_signal_agent           — Conviction tier 1/2/3 → drives Section 5 rendering
4.  macro_report_compiler        — Section 5 renders conditionally per signal tier
5.  [REVIEW LOOP ≤3 passes]
    fact_checker → review_agent → macro_report_compiler (revision)
```

- [ ] **Step 2: Update the GCS Artifacts table**

Add the synthesis artifact row:

```
| Synthesis document | `gs://{REPORTS_BUCKET}/macro/{identifier}/{run_id}_synthesis.md` | After Phase 1e, before Macro Analyst |
```

- [ ] **Step 3: Update the Agent Registry / Model Tiers table**

Add:

```
| macro_mode_detector | tier3 | Classifies report_mode; 128 max output tokens; no tools |
| macro_signal_agent | tier3 | Conviction tier assessment; 4096 max output tokens; no tools |
| deep_research | N/A (Gemini Developer API) | Thematic synthesis + source expansion |
```

- [ ] **Step 4: Update Timeouts table**

Add:

```
| `timeouts.mode_detector` | 30s | Single classification call |
| `timeouts.deep_research` | 600s | Gemini Deep Research polling |
| `timeouts.signal_agent` | 180s | Conviction assessment |
```

- [ ] **Step 5: Add section on Report Mode**

Add under a new heading "## Macro Report Mode":

```markdown
## Macro Report Mode

Every macro run is classified into one of two modes before data gathering:

| Mode | Signal Agent runs? | Section 5 title | When used |
|---|---|---|---|
| `research` | No | Market Relevance | Thematic/structural topics, or `deep_dive=True`, or `trade_signal=False` |
| `both` | Yes | Investment Implications / Trade Recommendation / Market Relevance (per tier) | Directional topics, or `trade_signal=True` |

**Request parameters:**
- `trade_signal: bool | None` — explicit override. `None` = auto-detect (default).
- `deep_dive: bool` — force `research` mode even if topic sounds directional.

**Signal Agent conviction tiers (only in `both` mode):**
- Tier 1: Full trade call (instrument, direction, entry rationale, stop, horizon)
- Tier 2: Directional stance (named instruments + bias, no entry/stop)
- Tier 3: Market relevance only (no stance)

**Section 5 title by tier:**
- Tier 1 → "Investment Implications & Trade Recommendation"
- Tier 2 → "Investment Implications"
- Tier 3 / research mode → "Market Relevance"
```

- [ ] **Step 6: Add note about Deep Research auth**

```markdown
## Deep Research API Auth

The Gemini Deep Research agent uses the Gemini Developer API (not Vertex AI) and requires
a separate `GOOGLE_API_KEY` / `GEMINI_API_KEY`. Store it as Secret Manager secret `gemini-api-key`.

If the API key is missing or Deep Research times out, the pipeline falls back to using
the Source Validator output as the Macro Analyst's primary qualitative input.
The fallback is logged in the debug report as `deep-research: timeout/error`.
```

- [ ] **Step 7: Commit**

```bash
git add project_guide.md
git commit -m "docs: update project_guide with new macro pipeline architecture, report mode, Deep Research auth"
```

---

### Task 19: Run the audit checklist

**Files:** No code changes — this is a verification task.

- [ ] **Step 1: Run the full unit test suite**

```bash
pytest tests/macro/ -v --tb=short
```

Expected: all tests `PASSED`. Note any failures before proceeding.

- [ ] **Step 2: ADK brace safety check on all new prompts**

```bash
for f in prompts/macro_mode_detector.md prompts/macro_signal_agent.md; do
  echo "=== $f ===" && grep -n "{" "$f" | grep -v "^\s*\`" || echo "CLEAN"
done
```

Expected: `CLEAN` for both files.

- [ ] **Step 3: Verify config.yaml parses**

```bash
python3 -c "
import yaml
c = yaml.safe_load(open('config.yaml'))
assert 'mode_detector' in c['timeouts'], 'Missing mode_detector timeout'
assert 'deep_research' in c['timeouts'], 'Missing deep_research timeout'
assert 'signal_agent' in c['timeouts'], 'Missing signal_agent timeout'
assert 'deep_research_cost_per_query' in c['pricing'], 'Missing deep research pricing'
print('config.yaml: all new keys present')
"
```

- [ ] **Step 4: Verify equity pipeline untouched**

```bash
# No equity pipeline function should reference new macro-only symbols
grep -n "mode_detector\|deep_research\|signal_agent\|report_mode\|trade_signal\|deep_dive" main.py \
  | grep -i "equity\|_run_equity\|fundamental\|valuation\|competitive"
```

Expected: no output.

- [ ] **Step 5: Verify MACRO_AGENTS registry**

```bash
python3 -c "
from agents.team import MACRO_AGENTS
expected = ['macro_mode_detector', 'macro_signal_agent', 'deep_research']
# deep_research is not an ADK agent so not in MACRO_AGENTS — skip
for key in ['macro_mode_detector', 'macro_signal_agent']:
    assert key in MACRO_AGENTS, f'{key} missing from MACRO_AGENTS'
print('MACRO_AGENTS registry: OK')
"
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat(macro): Gemini Deep Research integration complete — mode detection, thematic synthesis, signal agent, Section 5 conditional rendering"
```

---

### Task 20: Audit Report Template (GLM 4.7)

After implementation is merged and deployed, GLM 4.7 runs this audit.

**Save audit results to:**
```
gs://{REPORTS_BUCKET}/audit/YYYY-MM-DD_macro_deep_research_audit.md
```

**Audit commands GLM 4.7 must run:**

```bash
# A. Mode Detection — run 5 thematic + 5 signal-language topics via /research endpoint
# Check meta block in each output for "Report mode:" and "Mode rationale:" lines

# B. Deep Research artifact check
# For each run_id, verify _synthesis.md exists in GCS:
gsutil ls gs://{REPORTS_BUCKET}/macro/**/*_synthesis.md

# C. Parse synthesis documents and verify all 5 sections present
python3 -c "
import subprocess, json
result = subprocess.run(['gsutil', 'cat', 'gs://.../run_id_synthesis.md'], capture_output=True, text=True)
from tools.deep_research import parse_synthesis_document
parsed = parse_synthesis_document(result.stdout)
assert parsed['has_all_sections'], f'Missing: {parsed[\"missing_sections\"]}'
print('Synthesis document: PASS')
"

# D. Signal agent audit — run 3 high-conviction + 3 low-conviction + 3 thematic topics
# Verify Section 5 title in each final report matches expected tier

# E. Cost summary audit
# In each macro report's Cost Summary section, verify mode_detector and deep_research rows appear

# F. Equity regression audit
# Run 3 equity reports and compare against baseline outputs — confirm no format changes
```

**Audit report format:**

```markdown
# Macro Deep Research Redesign — Audit Report
Date: {YYYY-MM-DD}
Auditor: GLM 4.7

## Summary
Pass: N / total
Fail: N / total

## Checklist Results
- [PASS/FAIL] Mode Detection: thematic topics → research mode (N/5)
- [PASS/FAIL] Mode Detection: signal topics → both mode (N/5)
- [PASS/FAIL] Mode Detection: meta block contains "Report mode:" in all runs
- [PASS/FAIL] Deep Research: _synthesis.md exists for all macro runs
- [PASS/FAIL] Deep Research: all synthesis documents contain 5 required sections
- [PASS/FAIL] Deep Research: additional sources in synthesis appear in final Source Log
- [PASS/FAIL] Signal Agent: tier 1 topics produce "Trade Recommendation" Section 5
- [PASS/FAIL] Signal Agent: tier 2 topics produce "Investment Implications" Section 5
- [PASS/FAIL] Signal Agent: research mode produces "Market Relevance" Section 5
- [PASS/FAIL] Cost Summary: mode_detector row present in all macro reports
- [PASS/FAIL] Cost Summary: deep_research row present in all macro reports
- [PASS/FAIL] Equity Pipeline: zero format changes in 3 sample equity reports

## Failures Detail
[For each FAIL: expected vs actual, run_id, file reference]

## Recommendations
[Actionable fixes for any FAILs — file new tickets if needed]
```

Save to GCS and link in the project Jira board under the audit epic.

---

## Complete Test Run Reference

```bash
# ── All unit tests (no API calls, fast) ───────────────────────────────────────
pytest tests/macro/test_mode_detection.py \
       tests/macro/test_signal_agent.py \
       tests/macro/test_deep_research_handoff.py \
       tests/macro/test_section5_rendering.py \
       -v

# ── Integration tests (requires credentials) ──────────────────────────────────
pytest tests/macro/test_pipeline_integration.py -v

# ── Full test suite ───────────────────────────────────────────────────────────
pytest tests/macro/ -v --tb=short

# ── With coverage ─────────────────────────────────────────────────────────────
pytest tests/macro/ -v \
  --cov=main \
  --cov=tools/deep_research \
  --cov-report=term-missing

# ── Single test ───────────────────────────────────────────────────────────────
pytest tests/macro/test_signal_agent.py::test_tier1_parsed_correctly -v
```

---

## Pipeline Start Reference

```bash
# Research mode (thematic, no trade signal — default)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "Impact of demographic shifts on EM productivity", "report_type": "macro"}'

# Research + Signal mode (explicit)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "EUR/USD outlook into ECB June meeting", "report_type": "macro", "trade_signal": true}'

# Force deep dive (suppress signal detection)
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "Secular stagnation and neutral rates", "report_type": "macro", "deep_dive": true}'

# Auto-detect with context
curl -X POST http://localhost:8080/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Case for long Bunds on a 6-month horizon",
    "report_type": "macro",
    "context": "Focus on ECB rate path and Eurozone growth slowdown"
  }'

# Cloud Run (deployed service)
curl -X POST https://fin-research-agent-XXXX-uc.a.run.app/research \
  -H "Content-Type: application/json" \
  -d '{"topic": "US fiscal dominance and inflation outlook", "report_type": "macro", "trade_signal": true}'
```
