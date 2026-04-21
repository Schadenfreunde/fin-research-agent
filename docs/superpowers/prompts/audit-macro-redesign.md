# Audit Prompt — Macro Deep Research Redesign
**For:** GLM 4.7
**Session type:** Verification only (read, test, report — no code changes)
**When to run:** After the implementation plan is complete and all unit tests pass, BEFORE deploying to Cloud Run

---

## Your Job

Verify that the macro pipeline redesign is correctly implemented and ready for deployment. You are the last check before the code goes to production. Be thorough and skeptical — your job is to find problems, not to declare success.

Save your full audit report to:
```
docs/superpowers/audit/YYYY-MM-DD-macro-deep-research-audit.md
```
(create the `docs/superpowers/audit/` directory if it doesn't exist)

## What Was Implemented

The macro pipeline was extended with:

1. **Mode Detector (`macro_mode_detector` agent)** — classifies requests as `"research"` or `"both"` using new `trade_signal` and `deep_dive` parameters. Stores `report_mode` and `mode_rationale` on `run_stats` as dynamic attributes. Adds two lines to the meta block of every macro report.

2. **Gemini Deep Research Agent** — called via `tools/deep_research.py` → `run_deep_research()`. Uses Gemini Developer API (`deep-research-preview-04-2026`), auth via Secret Manager key `"google-ai-api-key"`. Slots between Source Validator and Macro Analyst in `_run_macro_pipeline()`. Output (Thematic Synthesis Document) saved to GCS as `{run_id}_synthesis.md` before Macro Analyst runs. On timeout/error, falls back to Source Validator output.

3. **Signal Agent (`macro_signal_agent` agent)** — runs in parallel with Quant Modeler when `report_mode = "both"`. Returns a tier (1/2/3) that drives Section 5 rendering. Skipped entirely in `"research"` mode.

4. **Section 5 conditional rendering** — Report Compiler renders Section 5 title and content based on `signal_tier`: Tier 1 → "Investment Implications & Trade Recommendation", Tier 2 → "Investment Implications", Tier 3 or research mode → "Market Relevance".

5. **New helpers in `main.py`**: `_parse_mode_detector_output()`, `_parse_signal_agent_output()`, `_get_section5_mode()`, `_get_google_ai_api_key()`, `_run_deep_research_agent()`.

## Audit Checklist

Work through each section in order. Record PASS or FAIL for every item. For FAILs, record: what was expected, what was found, and the file/line.

---

### Section A: Code Structure Verification

- [ ] **A1** — `tools/deep_research.py` exists and contains `run_deep_research`, `parse_synthesis_document`, `_build_deep_research_prompt`
  ```bash
  python3 -c "from tools.deep_research import run_deep_research, parse_synthesis_document, _build_deep_research_prompt; print('OK')"
  ```

- [ ] **A2** — `prompts/macro_mode_detector.md` exists and is non-empty
  ```bash
  wc -l prompts/macro_mode_detector.md
  ```
  Expected: > 10 lines

- [ ] **A3** — `prompts/macro_signal_agent.md` exists and is non-empty
  ```bash
  wc -l prompts/macro_signal_agent.md
  ```
  Expected: > 10 lines

- [ ] **A4** — ADK brace safety on all new prompt files
  ```bash
  for f in prompts/macro_mode_detector.md prompts/macro_signal_agent.md; do
    echo "=== $f ===" && grep -n "{" "$f" | grep -v "^\s*\`" || echo "CLEAN"
  done
  ```
  Expected: `CLEAN` for both files. Any `{var}` outside a fenced code block = FAIL (will crash the ADK at runtime).

- [ ] **A5** — `macro_mode_detector` agent importable and in `MACRO_AGENTS`
  ```bash
  python3 -c "
  from agents.team import macro_mode_detector, MACRO_AGENTS
  assert macro_mode_detector.name == 'macro_mode_detector'
  assert 'macro_mode_detector' in MACRO_AGENTS
  print('OK')
  "
  ```

- [ ] **A6** — `macro_signal_agent` agent importable and in `MACRO_AGENTS`
  ```bash
  python3 -c "
  from agents.team import macro_signal_agent, MACRO_AGENTS
  assert macro_signal_agent.name == 'macro_signal_agent'
  assert 'macro_signal_agent' in MACRO_AGENTS
  print('OK')
  "
  ```

- [ ] **A7** — Deep Research is NOT in `MACRO_AGENTS` (it's not an ADK agent)
  ```bash
  python3 -c "
  from agents.team import MACRO_AGENTS
  assert 'deep_research' not in MACRO_AGENTS, 'deep_research should not be an ADK agent'
  print('OK — Deep Research correctly absent from MACRO_AGENTS')
  "
  ```

- [ ] **A8** — `_parse_mode_detector_output` and `_parse_signal_agent_output` and `_get_section5_mode` exist in `main.py`
  ```bash
  grep -n "def _parse_mode_detector_output\|def _parse_signal_agent_output\|def _get_section5_mode\|def _get_google_ai_api_key\|def _run_deep_research_agent" main.py
  ```
  Expected: 5 lines, one per function.

- [ ] **A9** — `_run_macro_pipeline` signature has `trade_signal` and `deep_dive` parameters
  ```bash
  grep -A 3 "async def _run_macro_pipeline" main.py
  ```
  Expected: `trade_signal: bool | None = None` and `deep_dive: bool = False` in signature.

- [ ] **A10** — `/research` endpoint extracts `trade_signal` and `deep_dive` from request body
  ```bash
  grep -n "trade_signal\|deep_dive" main.py | head -20
  ```
  Expected: extraction logic in `submit_research_request` AND forwarding to `run_research_pipeline`.

---

### Section B: Configuration Verification

- [ ] **B1** — `config.yaml` contains all three new timeouts
  ```bash
  python3 -c "
  import yaml
  c = yaml.safe_load(open('config.yaml'))
  t = c['timeouts']
  assert 'mode_detector' in t, 'Missing timeouts.mode_detector'
  assert 'deep_research' in t, 'Missing timeouts.deep_research'
  assert 'signal_agent' in t, 'Missing timeouts.signal_agent'
  print(f'Timeouts OK: mode_detector={t[\"mode_detector\"]}s, deep_research={t[\"deep_research\"]}s, signal_agent={t[\"signal_agent\"]}s')
  "
  ```
  Expected values: mode_detector=30, deep_research=600, signal_agent=180.

- [ ] **B2** — `config.yaml` contains Deep Research pricing entry
  ```bash
  python3 -c "
  import yaml
  c = yaml.safe_load(open('config.yaml'))
  assert 'deep_research_cost_per_query' in c['pricing'], 'Missing pricing.deep_research_cost_per_query'
  print(f'Deep Research pricing: {c[\"pricing\"][\"deep_research_cost_per_query\"]} USD/query')
  "
  ```

- [ ] **B3** — `config.yaml` references `google-ai-api-key` in secrets section
  ```bash
  grep "google-ai-api-key" config.yaml
  ```
  Expected: one line referencing the secret name.

- [ ] **B4** — `config.yaml` parses without errors
  ```bash
  python3 -c "import yaml; yaml.safe_load(open('config.yaml')); print('YAML OK')"
  ```

---

### Section C: Unit Test Suite

Run the full unit test suite. Every test must pass before deployment.

- [ ] **C1** — Mode detection unit tests
  ```bash
  pytest tests/macro/test_mode_detection.py -v --tb=short
  ```
  Expected: all PASSED. Zero failures, zero errors.

- [ ] **C2** — Signal Agent unit tests
  ```bash
  pytest tests/macro/test_signal_agent.py -v --tb=short
  ```
  Expected: all PASSED.

- [ ] **C3** — Deep Research handoff unit tests
  ```bash
  pytest tests/macro/test_deep_research_handoff.py -v --tb=short
  ```
  Expected: all PASSED.

- [ ] **C4** — Section 5 rendering unit tests
  ```bash
  pytest tests/macro/test_section5_rendering.py -v --tb=short
  ```
  Expected: all PASSED.

- [ ] **C5** — Integration tests (non-API subset)
  ```bash
  pytest tests/macro/test_pipeline_integration.py -v --tb=short -k "not asyncio"
  ```
  Expected: all PASSED.

- [ ] **C6** — Full macro test suite (all tests together)
  ```bash
  pytest tests/macro/ -v --tb=short 2>&1 | tail -20
  ```
  Expected: final line shows `N passed, 0 failed`.

---

### Section D: Logic Verification (no API calls)

Verify the helper functions behave correctly by running inline Python checks.

- [ ] **D1** — `_parse_mode_detector_output` fallback
  ```bash
  python3 -c "
  import sys; sys.path.insert(0, '.')
  import pathlib, types
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _parse_mode_detector_output(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_parse_mode_detector_output']
  assert f('GARBAGE')[0] == 'research', 'Fallback should be research'
  assert f('REPORT_MODE: both\nRATIONALE: x')[0] == 'both'
  assert f('REPORT_MODE: research\nRATIONALE: x')[0] == 'research'
  print('_parse_mode_detector_output: OK')
  "
  ```

- [ ] **D2** — `_get_section5_mode` all cases
  ```bash
  python3 -c "
  import sys; sys.path.insert(0, '.')
  import pathlib
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _get_section5_mode(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_get_section5_mode']
  assert f('research', 3) == 'market_relevance'
  assert f('both', 1) == 'trade_recommendation'
  assert f('both', 2) == 'investment_implications'
  assert f('both', 3) == 'market_relevance'
  assert f('both', 99) == 'market_relevance'  # unknown tier
  print('_get_section5_mode: OK')
  "
  ```

- [ ] **D3** — `parse_synthesis_document` schema check
  ```bash
  python3 -c "
  from tools.deep_research import parse_synthesis_document
  import pathlib
  doc = pathlib.Path('tests/macro/fixtures/synthesis_document_valid.txt').read_text()
  result = parse_synthesis_document(doc)
  assert result['has_all_sections'], f'Missing: {result[\"missing_sections\"]}'
  assert result['sources_added_count'] > 0
  print(f'parse_synthesis_document: OK — {result[\"sources_added_count\"]} sources, all sections present')
  "
  ```

- [ ] **D4** — `_parse_signal_agent_output` tier parsing
  ```bash
  python3 -c "
  import sys; sys.path.insert(0, '.')
  import pathlib
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _parse_signal_agent_output(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_parse_signal_agent_output']
  t1 = f('## Signal Assessment\nConviction tier: 1\nTier rationale: All four conditions met.\n\n### Recommendation\nShort EUR/USD.')
  assert t1['tier'] == 1, f'Expected 1 got {t1[\"tier\"]}'
  t3 = f('GARBAGE')
  assert t3['tier'] == 3, 'Fallback should be tier 3'
  print('_parse_signal_agent_output: OK')
  "
  ```

- [ ] **D5** — Deep Research fallback path (mock timeout)
  ```bash
  python3 -c "
  import asyncio, sys; sys.path.insert(0, '.')
  import tools.deep_research as dr

  async def mock_timeout(*a, **kw):
      raise TimeoutError('mock')

  async def test():
      # We can't call _run_deep_research_agent without a full app context
      # but we can verify the timeout raises correctly from run_deep_research
      original = dr.run_deep_research
      dr.run_deep_research = mock_timeout
      try:
          await dr.run_deep_research('topic', 'src', 'manifest')
      except TimeoutError as e:
          print(f'Timeout raises correctly: {e}')
      finally:
          dr.run_deep_research = original

  asyncio.run(test())
  "
  ```

---

### Section E: Equity Pipeline Regression

Confirm that no equity pipeline code was modified.

- [ ] **E1** — No equity function references new macro-only symbols
  ```bash
  grep -n "mode_detector\|deep_research\|signal_agent\|report_mode\|trade_signal\|deep_dive" main.py \
    | grep -i "equity\|_run_equity\|fundamental\|valuation\|competitive\|earnings_quality\|risk_analyst"
  ```
  Expected: **no output**. Any output = FAIL.

- [ ] **E2** — `_run_equity_pipeline` signature unchanged
  ```bash
  grep -A 3 "async def _run_equity_pipeline" main.py
  ```
  Expected: signature does NOT contain `trade_signal` or `deep_dive`.

- [ ] **E3** — Equity agents unchanged in `agents/team.py`
  ```bash
  python3 -c "
  from agents.team import EQUITY_AGENTS
  expected = ['orchestrator','data_harvester','context_processor','fundamental_analyst',
              'fundamental_analyst_market','fundamental_analyst_financials',
              'competitive_analyst','risk_analyst','valuation_analyst',
              'quant_modeler_equity','earnings_quality','report_compiler',
              'fact_checker','review_agent']
  for key in expected:
      assert key in EQUITY_AGENTS, f'Missing equity agent: {key}'
  print(f'Equity agents OK: {list(EQUITY_AGENTS.keys())}')
  "
  ```

- [ ] **E4** — No new imports added to equity pipeline tools
  ```bash
  grep -n "deep_research\|mode_detector\|signal_agent" tools/*.py | grep -v "deep_research.py"
  ```
  Expected: **no output**.

---

### Section F: Prompt File Quality

- [ ] **F1** — `macro_analyst.md` references Thematic Synthesis Document (not old source package reference)
  ```bash
  grep -i "thematic synthesis\|synthesis document" prompts/macro_analyst.md
  ```
  Expected: at least one match confirming the reference was updated.

- [ ] **F2** — `macro_report_compiler.md` contains Section 5 rendering rules
  ```bash
  grep -i "market.relevance\|trade.recommendation\|investment.implication\|rendering mode\|section 5" prompts/macro_report_compiler.md | head -10
  ```
  Expected: at least 3 matches.

- [ ] **F3** — `macro_report_compiler.md` ADK brace safety (existing file — was already fixed, confirm still clean)
  ```bash
  grep -n "{" prompts/macro_report_compiler.md | grep -v "^\s*\`"
  ```
  Expected: no output.

- [ ] **F4** — `macro_analyst.md` ADK brace safety
  ```bash
  grep -n "{" prompts/macro_analyst.md | grep -v "^\s*\`"
  ```
  Expected: no output.

---

### Section G: Meta Block and Report Structure

- [ ] **G1** — Meta block construction in `main.py` adds report mode lines for macro reports
  ```bash
  grep -n "Report mode\|Mode rationale\|_report_mode\|_mode_rationale" main.py | head -10
  ```
  Expected: lines constructing "Report mode:" and "Mode rationale:" strings in the meta block.

- [ ] **G2** — Synthesis document save uses a distinct GCS path (not overwriting the main report)
  ```bash
  grep -n "_synthesis\|synthesis.*save\|save.*synthesis" main.py | head -10
  ```
  Expected: at least one line showing the `_synthesis` suffix in the GCS save call.

- [ ] **G3** — Review loop guard still intact (compiled placeholder check before review)
  ```bash
  grep -n "_is_placeholder\|skip.*review\|review.*skip" main.py | head -5
  ```
  Expected: the existing placeholder guard is present and unchanged.

---

### Section H: Pre-Deployment Smoke Test (local server)

Start the server locally and send a test request to verify the endpoint accepts new parameters without crashing at the HTTP layer. This does NOT run a full pipeline — it just checks the request parsing and parameter extraction.

- [ ] **H1** — Server starts without import errors
  ```bash
  timeout 10 python3 -c "
  import sys
  try:
      import main  # triggers all imports including new agent definitions
      print('Import OK — all agents load correctly')
  except Exception as e:
      print(f'IMPORT ERROR: {e}')
      sys.exit(1)
  " 2>&1 | grep -v "^$"
  ```
  Expected: `Import OK — all agents load correctly` (may also print SDK warnings — ignore those).

- [ ] **H2** — New request parameters parsed without error (dry run)
  ```bash
  python3 -c "
  # Simulate the parameter extraction logic from submit_research_request
  body = {'topic': 'Test', 'report_type': 'macro', 'trade_signal': True, 'deep_dive': False}

  trade_signal_raw = body.get('trade_signal', None)
  if isinstance(trade_signal_raw, bool):
      trade_signal = trade_signal_raw
  elif isinstance(trade_signal_raw, str):
      trade_signal = trade_signal_raw.lower() == 'true' if trade_signal_raw.lower() in ('true', 'false') else None
  else:
      trade_signal = None

  deep_dive_raw = body.get('deep_dive', False)
  deep_dive = bool(deep_dive_raw) if isinstance(deep_dive_raw, bool) else str(deep_dive_raw).lower() == 'true'

  assert trade_signal is True
  assert deep_dive is False

  body2 = {'topic': 'Test', 'report_type': 'macro'}
  trade_signal2 = body2.get('trade_signal', None)
  assert trade_signal2 is None  # auto-detect

  print('Parameter parsing: OK')
  "
  ```

- [ ] **H3** — `google-generativeai` package is installed (required for Deep Research)
  ```bash
  python3 -c "import google.generativeai; print(f'google-generativeai version: {google.generativeai.__version__}')"
  ```
  Expected: version printed without error. If `ModuleNotFoundError`: run `pip install google-generativeai` and check `requirements.txt`.

- [ ] **H4** — `requirements.txt` contains `google-generativeai`
  ```bash
  grep "google-generativeai" requirements.txt
  ```
  Expected: one line with the package and a version constraint.

---

## Audit Report Format

Save your findings as:
```
docs/superpowers/audit/YYYY-MM-DD-macro-deep-research-audit.md
```

Use this exact structure:

```markdown
# Macro Deep Research Redesign — Pre-Deployment Audit
Date: YYYY-MM-DD
Auditor: GLM 4.7
Branch: main (or current branch)

## Summary
Total checks: N
Passed: N
Failed: N
Blockers (must fix before deploy): N

## Result

[READY FOR DEPLOYMENT / NOT READY — N blockers must be resolved]

## Checklist Results

### Section A: Code Structure
- [PASS/FAIL] A1 — tools/deep_research.py importable
- [PASS/FAIL] A2 — macro_mode_detector.md exists
...

### Section B: Configuration
...

### Section C: Unit Test Suite
...

### Section D: Logic Verification
...

### Section E: Equity Pipeline Regression
...

### Section F: Prompt File Quality
...

### Section G: Meta Block and Report Structure
...

### Section H: Pre-Deployment Smoke Test
...

## Failures Detail

### [FAIL] A4 — ADK brace safety
**File:** prompts/macro_mode_detector.md:23
**Expected:** No `{var}` patterns outside fenced code blocks
**Found:** `{rate_path}` on line 23
**Fix:** Remove the brace pattern or move it inside a fenced code block

[Repeat for each FAIL]

## Blockers

[List only FAILs that would cause a production crash or data loss. Mark as BLOCKER.]

1. BLOCKER: [description] — [file:line] — [fix]

## Recommendations

[Non-blocking observations worth noting but not blocking deployment]

## Deployment Clearance

[CLEARED / NOT CLEARED]
Signed: GLM 4.7 — YYYY-MM-DD
```

---

## What Counts as a Blocker

Flag as a blocker (do NOT clear for deployment) if:
- Any unit test fails (Section C)
- ADK brace pattern found in any new prompt file (A4, F3, F4)
- Import error on server start (H1)
- Equity pipeline function references new macro symbols (E1)
- `_run_macro_pipeline` signature missing `trade_signal` or `deep_dive` (A9)
- `google-generativeai` not installed (H3)
- `google-ai-api-key` not in `config.yaml` secrets (B3)

Flag as a recommendation (non-blocking) if:
- Deep Research pricing is still `0.00` in `config.yaml` (update after confirming Google's pricing)
- Any test in the integration suite is skipped (not failed)
- A prompt file has unusual whitespace or formatting that doesn't affect ADK behavior

## Do Not

- Do not make code changes — this is read-only verification
- Do not run a full pipeline (no live API calls to Vertex AI or Deep Research during audit)
- Do not push or deploy — clearance is advisory, deployment decision stays with the user
- Do not skip any section — partial audits are not acceptable pre-deployment sign-offs
