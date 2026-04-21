# Audit Prompt — Macro Deep Research Redesign
**For:** GLM 4.7
**Session type:** Verification only (read, test, report — no code changes)
**When to run:** After the implementation plan is complete and all unit tests pass, BEFORE deploying to Cloud Run

---

## Skills — Read This First

You have access to the full superpowers skill library. The following skills are REQUIRED at specific gates during this audit session.

| When | Skill | Why |
|---|---|---|
| Session start, before running any checks | `superpowers:subagent-driven-development` | Dispatch independent audit sections as parallel subagents — Sections A–H are mostly independent and can run concurrently |
| When any check returns FAIL | `superpowers:systematic-debugging` | Required before logging the failure — diagnose the root cause and identify the exact file/line/fix before writing the audit entry |
| Before issuing the final CLEARED / NOT CLEARED verdict | `superpowers:verification-before-completion` | Ensures all sections are complete and no check was accidentally skipped before the deployment clearance is signed |
| When structuring the Failures Detail section | `superpowers:requesting-code-review` | Frames each failure as a clear, actionable finding rather than a raw test output dump |

**HARD RULE:** Do not issue a CLEARED verdict without first invoking `superpowers:verification-before-completion`. Do not log a FAIL without first invoking `superpowers:systematic-debugging` to confirm root cause.

---

## Your Job

Verify that the macro pipeline redesign is correctly implemented and ready for deployment. You are the last check before the code goes to production. Be thorough and skeptical — your job is to find problems, not to declare success.

Save your full audit report to:
```
docs/superpowers/audit/YYYY-MM-DD-macro-deep-research-audit.md
```
(create the `docs/superpowers/audit/` directory if it doesn't exist — `mkdir -p docs/superpowers/audit/`)

## Skill Invocation Sequence

```
SESSION START
  → invoke superpowers:subagent-driven-development
  → dispatch Sections A, B, C, D, E, F, G, H as parallel subagents where independent

ON ANY FAIL
  → invoke superpowers:systematic-debugging
  → record: expected vs actual, file:line, root cause, recommended fix

BEFORE WRITING FAILURES DETAIL SECTION
  → invoke superpowers:requesting-code-review
  → structure each finding as a clear actionable entry

BEFORE ISSUING VERDICT
  → invoke superpowers:verification-before-completion
  → confirm all 32+ checks have a recorded PASS or FAIL before signing off
```

## What Was Implemented

The macro pipeline was extended with:

1. **Mode Detector (`macro_mode_detector` agent)** — classifies requests as `"research"` or `"both"` using new `trade_signal` and `deep_dive` parameters on the `/research` endpoint. Stores `report_mode` and `mode_rationale` on `run_stats` as dynamic attributes. Adds two lines to the meta block of every macro report.

2. **Gemini Deep Research Agent** — called via `tools/deep_research.py` → `run_deep_research()`. Uses Gemini Developer API (`deep-research-preview-04-2026`), auth via Secret Manager key `"google-ai-api-key"` (already exists — no new secret). Is NOT an ADK agent and does NOT appear in `MACRO_AGENTS`. Output (Thematic Synthesis Document) saved to GCS as `{run_id}_synthesis.md` before the Macro Analyst runs. On timeout/error, falls back to Source Validator output.

3. **Signal Agent (`macro_signal_agent` agent)** — runs in parallel with Quant Modeler when `report_mode = "both"`. Returns a conviction tier (1/2/3) that drives Section 5 rendering. Skipped entirely in `"research"` mode.

4. **Section 5 conditional rendering** — Report Compiler renders Section 5 based on `signal_tier`: Tier 1 → "Investment Implications & Trade Recommendation", Tier 2 → "Investment Implications", Tier 3 or research mode → "Market Relevance".

5. **New helpers in `main.py`**: `_parse_mode_detector_output()`, `_parse_signal_agent_output()`, `_get_section5_mode()`, `_get_google_ai_api_key()`, `_run_deep_research_agent()`.

## Audit Checklist

Work through each section. Record PASS or FAIL for every item. For FAILs, invoke `superpowers:systematic-debugging` before logging — record root cause, not just the symptom.

---

### Section A: Code Structure Verification

- [ ] **A1** — `tools/deep_research.py` exists and exports the required symbols
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

- [ ] **A4** — ADK brace safety on all new prompt files (BLOCKER if failed)
  ```bash
  for f in prompts/macro_mode_detector.md prompts/macro_signal_agent.md; do
    echo "=== $f ===" && grep -n "{" "$f" | grep -v "^\s*\`" || echo "CLEAN"
  done
  ```
  Expected: `CLEAN` for both files. Any `{var}` outside a fenced code block = production crash at agent load time.

- [ ] **A5** — `macro_mode_detector` importable and registered
  ```bash
  python3 -c "
  from agents.team import macro_mode_detector, MACRO_AGENTS
  assert macro_mode_detector.name == 'macro_mode_detector'
  assert 'macro_mode_detector' in MACRO_AGENTS
  print('OK')
  "
  ```

- [ ] **A6** — `macro_signal_agent` importable and registered
  ```bash
  python3 -c "
  from agents.team import macro_signal_agent, MACRO_AGENTS
  assert macro_signal_agent.name == 'macro_signal_agent'
  assert 'macro_signal_agent' in MACRO_AGENTS
  print('OK')
  "
  ```

- [ ] **A7** — Deep Research correctly absent from `MACRO_AGENTS` (it's not an ADK agent)
  ```bash
  python3 -c "
  from agents.team import MACRO_AGENTS
  assert 'deep_research' not in MACRO_AGENTS, 'deep_research should not be in MACRO_AGENTS'
  print('OK — Deep Research absent from MACRO_AGENTS as expected')
  "
  ```

- [ ] **A8** — All five new helper functions present in `main.py`
  ```bash
  grep -n "def _parse_mode_detector_output\|def _parse_signal_agent_output\|def _get_section5_mode\|def _get_google_ai_api_key\|def _run_deep_research_agent" main.py
  ```
  Expected: 5 lines, one per function.

- [ ] **A9** — `_run_macro_pipeline` signature has both new parameters (BLOCKER if failed)
  ```bash
  grep -A 4 "async def _run_macro_pipeline" main.py
  ```
  Expected: `trade_signal: bool | None = None` and `deep_dive: bool = False` in signature.

- [ ] **A10** — `/research` endpoint extracts and forwards both new parameters
  ```bash
  grep -n "trade_signal\|deep_dive" main.py | head -25
  ```
  Expected: extraction in `submit_research_request` AND forwarding in the `run_research_pipeline` call.

---

### Section B: Configuration Verification

- [ ] **B1** — All three new timeouts present with correct values
  ```bash
  python3 -c "
  import yaml
  c = yaml.safe_load(open('config.yaml'))
  t = c['timeouts']
  assert 'mode_detector' in t and t['mode_detector'] == 30, f'mode_detector: {t.get(\"mode_detector\")}'
  assert 'deep_research' in t and t['deep_research'] == 600, f'deep_research: {t.get(\"deep_research\")}'
  assert 'signal_agent' in t and t['signal_agent'] == 180, f'signal_agent: {t.get(\"signal_agent\")}'
  print(f'Timeouts OK: mode_detector={t[\"mode_detector\"]}s, deep_research={t[\"deep_research\"]}s, signal_agent={t[\"signal_agent\"]}s')
  "
  ```

- [ ] **B2** — Deep Research pricing entry present
  ```bash
  python3 -c "
  import yaml
  c = yaml.safe_load(open('config.yaml'))
  assert 'deep_research_cost_per_query' in c['pricing'], 'Missing pricing.deep_research_cost_per_query'
  print(f'Deep Research pricing: {c[\"pricing\"][\"deep_research_cost_per_query\"]} USD/query')
  "
  ```

- [ ] **B3** — `google-ai-api-key` referenced in secrets section (BLOCKER if missing)
  ```bash
  grep "google-ai-api-key" config.yaml
  ```
  Expected: one matching line. If absent, `_get_google_ai_api_key()` will silently return None and all Deep Research calls will fail.

- [ ] **B4** — `config.yaml` parses cleanly
  ```bash
  python3 -c "import yaml; yaml.safe_load(open('config.yaml')); print('YAML OK')"
  ```

---

### Section C: Unit Test Suite

Every test must pass before deployment. Zero tolerance for failures.

- [ ] **C1** — Mode detection unit tests
  ```bash
  pytest tests/macro/test_mode_detection.py -v --tb=short
  ```

- [ ] **C2** — Signal Agent unit tests
  ```bash
  pytest tests/macro/test_signal_agent.py -v --tb=short
  ```

- [ ] **C3** — Deep Research handoff unit tests
  ```bash
  pytest tests/macro/test_deep_research_handoff.py -v --tb=short
  ```

- [ ] **C4** — Section 5 rendering unit tests
  ```bash
  pytest tests/macro/test_section5_rendering.py -v --tb=short
  ```

- [ ] **C5** — Integration tests (non-API subset, no live calls)
  ```bash
  pytest tests/macro/test_pipeline_integration.py -v --tb=short -k "not asyncio"
  ```

- [ ] **C6** — Full macro test suite (all at once)
  ```bash
  pytest tests/macro/ -v --tb=short 2>&1 | tail -20
  ```
  Expected: final summary line shows `N passed, 0 failed`.

If any test in C1–C6 fails: **immediately invoke `superpowers:systematic-debugging`** before proceeding. Do not log a vague "test failed" — identify the file, line, and root cause.

---

### Section D: Logic Verification (pure Python, no API calls)

- [ ] **D1** — `_parse_mode_detector_output` fallback and happy paths
  ```bash
  python3 -c "
  import pathlib
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _parse_mode_detector_output(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_parse_mode_detector_output']
  assert f('GARBAGE')[0] == 'research', 'Fallback must be research'
  assert f('REPORT_MODE: both\nRATIONALE: x')[0] == 'both'
  assert f('REPORT_MODE: research\nRATIONALE: x')[0] == 'research'
  assert len(f('REPORT_MODE: research\nRATIONALE: Thematic.')[1]) > 5, 'Rationale must be non-empty'
  print('_parse_mode_detector_output: OK')
  "
  ```

- [ ] **D2** — `_get_section5_mode` all five cases
  ```bash
  python3 -c "
  import pathlib
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _get_section5_mode(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_get_section5_mode']
  assert f('research', 3) == 'market_relevance', f'Got {f(\"research\", 3)}'
  assert f('both', 1) == 'trade_recommendation', f'Got {f(\"both\", 1)}'
  assert f('both', 2) == 'investment_implications', f'Got {f(\"both\", 2)}'
  assert f('both', 3) == 'market_relevance', f'Got {f(\"both\", 3)}'
  assert f('both', 99) == 'market_relevance', 'Unknown tier must fall back to market_relevance'
  print('_get_section5_mode: OK')
  "
  ```

- [ ] **D3** — `parse_synthesis_document` validates the fixture correctly
  ```bash
  python3 -c "
  from tools.deep_research import parse_synthesis_document
  import pathlib
  doc = pathlib.Path('tests/macro/fixtures/synthesis_document_valid.txt').read_text()
  result = parse_synthesis_document(doc)
  assert result['has_all_sections'], f'Missing sections: {result[\"missing_sections\"]}'
  assert result['sources_added_count'] > 0, 'sources_added_count should be > 0'
  assert result['full_text'] == doc, 'full_text must be the raw input unchanged'
  print(f'parse_synthesis_document: OK — {result[\"sources_added_count\"]} sources, all 5 sections present')
  "
  ```

- [ ] **D4** — `_parse_signal_agent_output` tier extraction and fallback
  ```bash
  python3 -c "
  import pathlib
  src = pathlib.Path('main.py').read_text()
  start = src.index('def _parse_signal_agent_output(')
  end = src.index('\ndef ', start + 1)
  ns = {}; exec(src[start:end], ns)
  f = ns['_parse_signal_agent_output']
  t1 = f('## Signal Assessment\nConviction tier: 1\nTier rationale: All four conditions met.\n\n### Recommendation\nShort EUR/USD.')
  assert t1['tier'] == 1, f'Expected tier 1, got {t1[\"tier\"]}'
  assert len(t1['recommendation']) > 5, 'Tier 1 recommendation must be non-empty'
  t_bad = f('GARBAGE OUTPUT')
  assert t_bad['tier'] == 3, f'Fallback must be tier 3, got {t_bad[\"tier\"]}'
  print('_parse_signal_agent_output: OK')
  "
  ```

- [ ] **D5** — Deep Research timeout raises `TimeoutError` (not swallowed silently)
  ```bash
  python3 -c "
  import asyncio, sys; sys.path.insert(0, '.')
  import tools.deep_research as dr

  async def mock_timeout(*a, **kw):
      raise TimeoutError('mock timeout')

  async def test():
      original = dr.run_deep_research
      dr.run_deep_research = mock_timeout
      try:
          await dr.run_deep_research('topic', 'src', 'manifest')
          print('FAIL — TimeoutError was swallowed')
      except TimeoutError as e:
          print(f'OK — TimeoutError raised correctly: {e}')
      finally:
          dr.run_deep_research = original

  asyncio.run(test())
  "
  ```

---

### Section E: Equity Pipeline Regression (BLOCKER if any check fails)

- [ ] **E1** — No equity function references new macro-only symbols
  ```bash
  grep -n "mode_detector\|deep_research\|signal_agent\|report_mode\|trade_signal\|deep_dive" main.py \
    | grep -i "equity\|_run_equity\|fundamental\|valuation\|competitive\|earnings_quality\|risk_analyst"
  ```
  Expected: **no output**. Any match = BLOCKER.

- [ ] **E2** — `_run_equity_pipeline` signature is unchanged
  ```bash
  grep -A 4 "async def _run_equity_pipeline" main.py
  ```
  Expected: signature contains only `topic`, `run_id`, `user_context`, `run_stats` — no `trade_signal` or `deep_dive`.

- [ ] **E3** — All equity agents still present in `EQUITY_AGENTS`
  ```bash
  python3 -c "
  from agents.team import EQUITY_AGENTS
  required = ['orchestrator','data_harvester','context_processor','fundamental_analyst',
              'fundamental_analyst_market','fundamental_analyst_financials',
              'competitive_analyst','risk_analyst','valuation_analyst',
              'quant_modeler_equity','earnings_quality','report_compiler',
              'fact_checker','review_agent']
  missing = [k for k in required if k not in EQUITY_AGENTS]
  assert not missing, f'Missing equity agents: {missing}'
  print(f'All {len(required)} equity agents present — OK')
  "
  ```

- [ ] **E4** — New symbols not leaked into equity tool files
  ```bash
  grep -rn "deep_research\|mode_detector\|signal_agent" tools/*.py | grep -v "deep_research.py"
  ```
  Expected: **no output**.

---

### Section F: Prompt File Quality

- [ ] **F1** — `macro_analyst.md` updated to reference Thematic Synthesis Document
  ```bash
  grep -i "thematic synthesis\|synthesis document" prompts/macro_analyst.md
  ```
  Expected: at least one match.

- [ ] **F2** — `macro_report_compiler.md` contains Section 5 rendering rules
  ```bash
  grep -ic "market.relevance\|trade.recommendation\|investment.implication\|rendering mode\|section 5" prompts/macro_report_compiler.md
  ```
  Expected: 3 or more matches.

- [ ] **F3** — `macro_report_compiler.md` ADK brace safety (previously fixed — confirm still clean)
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

- [ ] **G1** — Meta block adds mode lines for macro reports (not equity)
  ```bash
  grep -n "Report mode\|Mode rationale\|_report_mode\|_mode_rationale\|_mode_display" main.py | head -10
  ```
  Expected: mode lines constructed only inside the `if report_type == "macro":` branch.

- [ ] **G2** — Synthesis document saved to GCS with `_synthesis` suffix before Macro Analyst runs
  ```bash
  grep -n "_synthesis\|synthesis.*save\|save.*synthesis" main.py | head -10
  ```
  Expected: at least one line with `_synthesis` in the GCS path; must appear before the `analysis_context` variable construction in the source.

- [ ] **G3** — Review loop guard still intact (regression check)
  ```bash
  grep -n "_is_placeholder\|skip.*review\|review.*skip" main.py | head -5
  ```
  Expected: existing guard lines still present and unchanged.

- [ ] **G4** — Signal Agent runs AFTER Macro Analyst, BEFORE Report Compiler
  ```bash
  grep -n "signal.agent\|signal_raw\|signal_parsed\|STEP 3b\|Phase 3b" main.py | head -10
  ```
  Expected: signal agent call appears between the Macro Analyst call (Step 2) and the compile_context construction (Step 4).

---

### Section H: Pre-Deployment Smoke Tests (no live API calls)

- [ ] **H1** — Full import succeeds without errors
  ```bash
  timeout 15 python3 -c "
  import sys
  try:
      import main
      print('Import OK — all agents and tools load without error')
  except Exception as e:
      print(f'IMPORT ERROR: {e}')
      sys.exit(1)
  " 2>&1 | grep -v "WARNING\|INFO\|^$"
  ```
  Expected: `Import OK` line present, no `ERROR` lines. Ignore SDK INFO/WARNING lines.

- [ ] **H2** — Request parameter parsing works for all four input combinations
  ```bash
  python3 -c "
  cases = [
      ({'trade_signal': True,  'deep_dive': False}, True,  False),
      ({'trade_signal': False, 'deep_dive': False}, False, False),
      ({'deep_dive': True},                         None,  True),
      ({},                                          None,  False),
  ]
  for body, expected_ts, expected_dd in cases:
      trade_signal_raw = body.get('trade_signal', None)
      if isinstance(trade_signal_raw, bool):
          trade_signal = trade_signal_raw
      elif isinstance(trade_signal_raw, str):
          trade_signal = trade_signal_raw.lower() == 'true' if trade_signal_raw.lower() in ('true','false') else None
      else:
          trade_signal = None
      deep_dive_raw = body.get('deep_dive', False)
      deep_dive = bool(deep_dive_raw) if isinstance(deep_dive_raw, bool) else str(deep_dive_raw).lower() == 'true'
      assert trade_signal == expected_ts, f'trade_signal: expected {expected_ts} got {trade_signal} for {body}'
      assert deep_dive == expected_dd, f'deep_dive: expected {expected_dd} got {deep_dive} for {body}'
  print('Parameter parsing: all 4 cases OK')
  "
  ```

- [ ] **H3** — `google-generativeai` package installed and importable (BLOCKER if failed)
  ```bash
  python3 -c "import google.generativeai as genai; print(f'google-generativeai {genai.__version__} — OK')"
  ```

- [ ] **H4** — `requirements.txt` includes `google-generativeai`
  ```bash
  grep "google-generativeai" requirements.txt
  ```
  Expected: one line with version constraint. If absent, the Docker build will fail on deploy.

- [ ] **H5** — No circular imports or import-time side effects from new files
  ```bash
  python3 -c "
  import tools.deep_research
  import agents.team
  print('No circular import issues detected')
  "
  ```

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
Branch: [output of: git branch --show-current]
Commit: [output of: git log --oneline -1]

## Summary
Total checks: 32
Passed: N
Failed: N
Blockers (deploy-stopping): N

## Verdict

[READY FOR DEPLOYMENT / NOT READY — N blockers must be resolved first]

## Checklist Results

### A: Code Structure (A1–A10)
- [PASS/FAIL] A1 — tools/deep_research.py importable
- [PASS/FAIL] A2 — macro_mode_detector.md exists
[... one line per check ...]

### B: Configuration (B1–B4)
[...]

### C: Unit Test Suite (C1–C6)
[...]

### D: Logic Verification (D1–D5)
[...]

### E: Equity Regression (E1–E4)
[...]

### F: Prompt Quality (F1–F4)
[...]

### G: Report Structure (G1–G4)
[...]

### H: Smoke Tests (H1–H5)
[...]

## Failures Detail

### [FAIL] A4 — ADK brace safety
**File:** prompts/macro_mode_detector.md:23
**Expected:** No bare {var} patterns outside fenced code blocks
**Found:** `{rate_path}` on line 23
**Root cause:** Brace left over from template example in prompt body
**Fix:** Wrap the example in a fenced code block or remove the brace
**Blocker:** YES — will crash the ADK agent at load time

[One entry per FAIL, populated after superpowers:systematic-debugging]

## Blockers

[Empty if none]

1. BLOCKER: [description] — [file:line] — [exact fix required]

## Non-Blocking Recommendations

[Observations that don't block deployment but should be addressed soon]

## Deployment Clearance

[CLEARED / NOT CLEARED]
Auditor: GLM 4.7 — YYYY-MM-DD
```

---

## What Counts as a Blocker

**BLOCKER — do not clear for deployment:**
- Any unit test fails (Section C)
- ADK brace pattern found in any new prompt file (A4, F3, F4)
- Import error on `import main` (H1)
- Any equity pipeline function references new macro symbols (E1)
- `_run_macro_pipeline` signature missing `trade_signal` or `deep_dive` (A9)
- `google-generativeai` not installed (H3) — Docker deploy will fail
- `google-ai-api-key` absent from `config.yaml` secrets (B3)
- `_run_deep_research_agent` absent from `main.py` (A8)

**Recommendation — non-blocking:**
- `deep_research_cost_per_query` is `0.00` — confirm actual price from Google and update
- Integration tests skipped (not failed) due to missing credentials in local env
- Prompt file has unusual whitespace that doesn't affect ADK behaviour

## Do Not

- Do not make any code changes — this session is read-only verification
- Do not run full pipeline calls (no live Vertex AI or Deep Research API calls)
- Do not push or deploy — clearance is advisory; the user deploys via `./deploy.sh`
- Do not skip any section — a partial audit does not constitute a deployment sign-off
- Do not issue CLEARED if any blocker is unresolved
