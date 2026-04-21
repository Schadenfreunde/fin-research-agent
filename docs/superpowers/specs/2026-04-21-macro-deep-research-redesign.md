# Macro Pipeline: Deep Research & Institutional Report Redesign
**Date:** 2026-04-21
**Author:** Brainstorming session — approved by user
**Scope:** Macro pipeline ONLY. Equity pipeline untouched.
**Execution:** GLM 4.7

---

## 1. Problem Statement

The macro pipeline always emits an investment recommendation regardless of whether the topic warrants one. A purely thematic research request (e.g., "The impact of demographic shifts on EM productivity") is forced through Section 5 "Investment Implications" even when no clear trade exists. The desired behaviour is:

- Every macro run produces a research-grade thematic report
- A trade recommendation (or directional stance) is generated only when conviction warrants it or the user explicitly requests it
- The Gemini Deep Research API is introduced as a Thematic Synthesis Agent to expand source coverage beyond the current 12-call budget

---

## 2. New Request Parameters

```python
topic:        str            # unchanged
trade_signal: bool | None    # None = auto-detect (default)
deep_dive:    bool = False   # True = force Research mode, suppress signal detection
context:      str | None     # unchanged
```

---

## 3. Pipeline Architecture

### 3.1 Full Pipeline Flow

```
REQUEST (topic, trade_signal, deep_dive, context)
        │
        ▼
Phase 0:  Mode Detection                    ← NEW
        │
        ▼
Phase 1a: Pre-Gather (FRED/WB/OECD/IMF/ECB) ← unchanged
        │
        ▼
Phase 1b: Context Processor                 ← unchanged, optional
        │
        ▼
Phase 1c: Macro Data Agent                  ← unchanged
        │
        ▼
Phase 1d: Macro Source Validator            ← unchanged
        │
        ▼
Phase 1e: Gemini Deep Research Agent        ← NEW
        │
        ▼
Phase 2:  Macro Analyst                     ← prompt updated
        │
        ├──────────────────────────────────┐
        ▼                                  ▼ (if report_mode ∈ {signal, both})
Phase 3:  Quant Modeler (unchanged)   Phase 3b: Signal Agent  ← NEW
        │                                  │
        └──────────────┬───────────────────┘
                       ▼
Phase 4:  Report Compiler                   ← prompt updated (Section 5 conditional)
        │
        ▼
Phase 5:  Review Loop                       ← unchanged
        │
        ▼
        OUTPUT (report + synthesis artifact + costs)
```

### 3.2 Unchanged Components

- Pre-Gather (Phase 1a)
- Context Processor (Phase 1b)
- Macro Data Agent (Phase 1c)
- Macro Source Validator (Phase 1d)
- Quant Modeler (Phase 3)
- Review Loop (Phase 5)
- Cost tracking infrastructure (`format_cost_summary`, `record_agent_complete`, `run_stats`)

---

## 4. New Component Specifications

### 4.1 Phase 0: Mode Detection

**Purpose:** Classify the request into a report mode before any data gathering.

**Input:**
- `topic: str`
- `trade_signal: bool | None`
- `deep_dive: bool`

**Logic:**
```
if deep_dive == True        → report_mode = "research"
elif trade_signal == True   → report_mode = "both"
elif trade_signal == False  → report_mode = "research"
else (None):
    → LLM classifier reads topic string
    → If topic contains explicit positioning language, named instruments,
      rate trade framing, or directional asset language → report_mode = "both"
    → Otherwise → report_mode = "research"
```

**Output:**
```python
report_mode:     "research" | "both"   # "signal" reserved for future
mode_rationale:  str                   # one sentence, shown in meta block
```

**Implementation notes:**
- Single LLM call using a lightweight model (flash tier)
- Timeout: 30 seconds
- Tracked in run_stats as agent "mode_detector"
- `report_mode = "signal"` (signal-only, no research) is reserved — not implemented in this sprint

### 4.2 Phase 1e: Gemini Deep Research Agent

**Purpose:** Expand source coverage beyond the Source Validator's budget and produce a structured Thematic Synthesis Document that becomes the Macro Analyst's primary qualitative input.

**API:** Gemini Deep Research API (`https://ai.google.dev/gemini-api/docs/deep-research`)
- Polling model: submit job → poll until complete
- Integrated as async function fitting existing asyncio pattern
- Timeout: 600 seconds (10 minutes)

**Input package:**
```
1. topic string
2. report_mode
3. Validated Source Package (full Source Validator output)
4. Quantitative Manifest (compact _macro_data_manifest() output, ~400 words)
5. Expansion directive (see prompt section below)
```

**Quantitative handoff protocol:**
- The compact `_macro_data_manifest()` already used by the Context Processor is reused here verbatim
- This gives Deep Research awareness of what quantitative data exists without bloating the prompt with raw FRED observations
- Deep Research is not given the full pre-gathered data — only the manifest

**Prompt directive (expansion instruction):**
```
You may fetch additional sources beyond those already validated.
Prioritise:
  - Academic papers not yet in the source package
  - Central bank speeches and working papers
  - BIS, IMF, and OECD working papers
  - Recent empirical studies (last 3 years)
  - Historical analog literature for the identified theme
Do NOT re-fetch sources already present in the validated source package.
```

**Output: Thematic Synthesis Document**
```markdown
## Thematic Synthesis Document
Topic: {topic}
Generated by: Gemini Deep Research
Sources added beyond Source Validator: {N}

### Synthesis Narrative
[2–4 paragraphs: thematic framing, key tensions, structural forces]

### Key Claims with Evidence
[Bulleted list: each claim + supporting source(s) + confidence level]

### Quantitative Gaps Identified
[Data points referenced in sources but absent from quantitative manifest —
 passed to Quant Modeler as supplementary fetch targets]

### Additional Sources Retrieved
| # | Title | Source | Date | URL |
|---|---|---|---|---|

### Thematic Threads for Analyst
[3–5 named threads the Macro Analyst should develop,
 e.g., "Thread 1: Demographic headwind vs productivity catch-up in EM Asia"]
```

**Saved artifact:**
```
gs://{bucket}/macro/{identifier}/{run_id}_synthesis.md
```
Saved via existing `save_report()` tool immediately after Deep Research completes, before the Macro Analyst runs. This gives the user an inspectable artifact independent of the final report.

**Auth:** The Gemini Deep Research API uses the Gemini Developer API (not Vertex AI). API key is already in GCP Secret Manager as `"google-ai-api-key"`. Loaded via `tools/http_client.get_api_key("google-ai-api-key")` in `_get_google_ai_api_key()`.

**Pricing:**
- Gemini Deep Research API has its own pricing model (per-query, not per-token)
- Add a new `deep_research_cost_per_query` field to `config.yaml` pricing section
- Track in run_stats under agent label "deep_research"

**Macro Analyst input change:**
- Primary qualitative input: Thematic Synthesis Document (replaces raw source package)
- Secondary input: pre-gathered quant data (unchanged, sliced via `_slice_macro_data()`)
- The existing Macro Analyst prompt is updated to reference "Thematic Synthesis Document" instead of "validated source package"

### 4.3 Phase 3b: Signal Agent

**Purpose:** Assess the Macro Analyst's output for trade conviction and produce a tiered recommendation (trade call, directional stance, or observational commentary).

**Activation:** Only runs when `report_mode ∈ {signal, both}`. Skipped entirely in `report_mode = "research"`.

**Runs in parallel with:** Quant Modeler (Phase 3).

**Input:**
- Macro Analyst output (Sections 1–8)
- report_mode

**Conviction tier assessment:**

| Tier | Conditions | Output |
|---|---|---|
| 1 — Strong | Clear directional driver + near-term catalyst with firm date + quantifiable threshold + tight historical analog | Explicit trade recommendation: instrument, direction, entry rationale, stop condition, time horizon |
| 2 — Directional | Thesis is directional but catalyst timing uncertain, OR multiple competing drivers | Bullish/bearish stance on named instruments, no specific entry or stop |
| 3 — Observational | Purely thematic, no clear instrument mapping, or conviction too low | Asset class relevance commentary only |

**Output format:**
```markdown
## Signal Assessment
Conviction tier: {1 | 2 | 3}
Tier rationale: {one sentence}

### Recommendation
{Trade call / directional stance / observational commentary per tier}
```

**Agent settings:**
- Model: flash tier (conviction assessment, not synthesis)
- Timeout: 180 seconds
- Max output tokens: 4,096
- Tracked in run_stats as agent "signal_agent"

---

## 5. Modified Components

### 5.1 Report Compiler — Section 5 Conditional Rendering

The compiler receives `report_mode` and Signal Agent tier. Section 5 is rendered as follows:

| Condition | Section 5 Title | Content |
|---|---|---|
| `report_mode = "research"` (no Signal Agent) | Market Relevance | Observational: which asset classes this theme touches, why it matters structurally — no stance |
| Signal Agent tier 1 | Investment Implications & Trade Recommendation | Full trade call + asset class framing from Analyst |
| Signal Agent tier 2 | Investment Implications | Directional stance + asset class framing — no specific entry/stop |
| Signal Agent tier 3 | Market Relevance | Observational commentary from Signal Agent |

The compiler prompt is updated to include this conditional rendering rule explicitly.

### 5.2 Report Meta Block — Mode Visibility

Two new lines added to the existing meta block:

```
*Run ID: `{run_id}` · macro report · {date}*
*Report mode: {Research | Research + Signal}*        ← NEW
*Mode rationale: {mode_rationale from Mode Detector}* ← NEW
```

### 5.3 Macro Analyst Prompt

Single change: the prompt's reference to "validated source package" is updated to "Thematic Synthesis Document from Deep Research Agent". All 8-section structure and labelling rules (Fact/Analysis/Inference, citation requirements, geography scope) are unchanged.

---

## 6. Output Artifacts

| Artifact | Path | When saved |
|---|---|---|
| Final report (markdown) | `gs://{bucket}/macro/{identifier}/{run_id}.md` | End of pipeline (unchanged) |
| Final report (LaTeX) | `gs://{bucket}/macro/{identifier}/{run_id}.tex` | End of pipeline (unchanged) |
| Thematic Synthesis Document | `gs://{bucket}/macro/{identifier}/{run_id}_synthesis.md` | After Phase 1e, before Macro Analyst |
| Debug report | `gs://{bucket}/debug/{run_id}.md` | End of pipeline (unchanged) |

---

## 7. Cost Tracking

No changes to cost infrastructure. The three new agents (mode_detector, deep_research, signal_agent) are automatically tracked by the existing `_run_agent()` → `record_agent_complete()` flow.

**config.yaml additions:**
```yaml
pricing:
  deep_research_cost_per_query: TBD   # populate from Google pricing page at implementation time
  models:
    # existing entries unchanged
    # no new model entries needed — Mode Detector uses flash, Signal Agent uses flash
```

**Timeouts in config.yaml (new entries):**
```yaml
timeouts:
  mode_detector:    30
  deep_research:   600
  signal_agent:    180
```

---

## 8. Test Suite

The test suite is self-contained and executable by GLM 4.7 without external dependencies beyond the existing pipeline environment.

### 8.1 Unit Tests

**File:** `tests/macro/test_mode_detection.py`
- `test_explicit_trade_signal_true` — `trade_signal=True` always returns `report_mode="both"`
- `test_explicit_trade_signal_false` — `trade_signal=False` always returns `report_mode="research"`
- `test_deep_dive_override` — `deep_dive=True` overrides any `trade_signal=True`, returns `report_mode="research"`
- `test_auto_detect_signal_topic` — "Should I be long EUR/USD into ECB meeting?" → `report_mode="both"`
- `test_auto_detect_research_topic` — "Impact of demographic shifts on EM productivity" → `report_mode="research"`
- `test_mode_rationale_present` — output always contains non-empty `mode_rationale`

**File:** `tests/macro/test_signal_agent.py`
- `test_tier1_output_schema` — tier 1 output contains instrument, direction, entry_rationale, stop_condition, time_horizon
- `test_tier2_output_no_entry_stop` — tier 2 output has named instruments but no entry/stop fields
- `test_tier3_output_observational` — tier 3 output has no instrument, direction, or stance fields
- `test_signal_agent_skipped_in_research_mode` — when `report_mode="research"`, signal agent is not called
- `test_conviction_tier_rationale_present` — every tier output has non-empty `tier_rationale`

**File:** `tests/macro/test_deep_research_handoff.py`
- `test_synthesis_document_schema` — output contains all 5 required sections (Synthesis Narrative, Key Claims, Quantitative Gaps, Additional Sources, Thematic Threads)
- `test_synthesis_saved_to_gcs` — `save_report()` called with `_synthesis` suffix before Macro Analyst runs
- `test_manifest_not_full_data` — handoff package to Deep Research contains manifest (~400 words), not full pre-gathered data (>10K words)
- `test_additional_sources_logged` — sources added by Deep Research appear in the final report's Source Log

**File:** `tests/macro/test_section5_rendering.py`
- `test_research_mode_renders_market_relevance` — Section 5 title is "Market Relevance" when no signal agent ran
- `test_tier1_renders_trade_recommendation` — Section 5 title contains "Trade Recommendation"
- `test_tier2_renders_investment_implications` — Section 5 title is "Investment Implications" (no "Trade Recommendation")
- `test_tier3_renders_market_relevance` — Section 5 title is "Market Relevance"
- `test_meta_block_contains_mode` — compiled report meta block contains "Report mode:" line
- `test_meta_block_contains_rationale` — compiled report meta block contains "Mode rationale:" line

### 8.2 Integration Tests

**File:** `tests/macro/test_pipeline_integration.py`
- `test_research_mode_end_to_end` — full pipeline run with `trade_signal=False`; assert signal agent not in run_stats, Section 5 title is "Market Relevance", synthesis artifact saved
- `test_signal_mode_end_to_end` — full pipeline run with `trade_signal=True`; assert signal agent in run_stats, Section 5 title contains "Investment Implications" or "Trade Recommendation"
- `test_auto_detect_thematic_topic` — full pipeline run with no flags, thematic topic; assert `report_mode="research"`
- `test_auto_detect_signal_topic` — full pipeline run with no flags, signal-language topic; assert `report_mode="both"`
- `test_synthesis_artifact_independent_of_report` — synthesis file saved to GCS even if report compiler fails
- `test_cost_summary_includes_new_agents` — cost summary in final report includes rows for mode_detector, deep_research, signal_agent (when applicable)
- `test_deep_research_timeout_graceful` — when Deep Research times out, pipeline continues with Source Validator output as fallback input to Macro Analyst; timeout logged in debug report

### 8.3 Test Fixtures

**File:** `tests/macro/fixtures/`
- `validated_source_package.txt` — representative Source Validator output for a non-US topic
- `data_manifest.txt` — representative `_macro_data_manifest()` output
- `analyst_output_high_conviction.txt` — Macro Analyst output with clear directional driver + firm catalyst date
- `analyst_output_low_conviction.txt` — Macro Analyst output with competing drivers, no firm catalyst
- `analyst_output_thematic.txt` — purely thematic Macro Analyst output with no instrument mapping
- `synthesis_document_valid.txt` — valid Thematic Synthesis Document for schema validation tests

---

## 9. Audit Plan (GLM 4.7)

To be executed by GLM 4.7 after implementation is merged and deployed.

### 9.1 Audit Checklist

**A. Mode Detection Audit**
- [ ] Run 10 topics: 5 thematic, 5 signal-language. Verify mode_rationale in each report meta block matches expected classification
- [ ] Verify `deep_dive=True` suppresses signal mode in all 5 signal-language topics
- [ ] Confirm mode_rationale is never empty in any report

**B. Deep Research Integration Audit**
- [ ] Verify `{run_id}_synthesis.md` exists in GCS for every macro run
- [ ] Verify synthesis document contains all 5 required sections
- [ ] Verify sources in "Additional Sources Retrieved" appear in the final report's Source Log (no orphan sources)
- [ ] Verify Macro Analyst prompt references Thematic Synthesis Document (not raw source package) — grep `prompts/macro_analyst.md`
- [ ] Verify Deep Research timeout (600s) is present in config.yaml
- [ ] Verify `deep_research_cost_per_query` is populated in config.yaml (not TBD)

**C. Signal Agent Audit**
- [ ] Run 3 high-conviction topics with `trade_signal=True`. Verify tier 1 output in all 3 Section 5s
- [ ] Run 3 low-conviction topics with `trade_signal=True`. Verify tier 2 or 3 output (not tier 1 forced)
- [ ] Run 3 thematic topics with `trade_signal=False`. Verify signal agent row absent from cost summary
- [ ] Verify signal_agent timeout (180s) present in config.yaml

**D. Section 5 Rendering Audit**
- [ ] For each of 5 research-mode reports: Section 5 title is "Market Relevance", no trade call language present
- [ ] For each of 5 signal-mode tier 1 reports: Section 5 title contains "Trade Recommendation"
- [ ] For each of 5 signal-mode tier 2 reports: Section 5 contains named instruments + directional bias, no entry/stop levels
- [ ] Verify meta block in all 10 reports contains "Report mode:" and "Mode rationale:"

**E. Cost Tracking Audit**
- [ ] Verify mode_detector appears in cost summary of every macro run
- [ ] Verify deep_research appears in cost summary of every macro run (with cost > 0)
- [ ] Verify signal_agent appears in cost summary only when `report_mode ∈ {signal, both}`
- [ ] Verify total_cost_usd includes Deep Research query cost (not just token costs)

**F. Regression Audit (Equity Pipeline Isolation)**
- [ ] Run 3 equity reports and verify zero changes to equity output format, cost summary, or agent chain
- [ ] Grep codebase for any equity pipeline function that references new macro-only symbols (mode_detector, deep_research, signal_agent) — expect zero matches

### 9.2 Audit Report Format

GLM 4.7 saves audit results to:
```
gs://{bucket}/audit/{YYYY-MM-DD}_macro_deep_research_audit.md
```

Format:
```markdown
# Macro Deep Research Redesign — Audit Report
Date: {date}
Auditor: GLM 4.7
Sprint: {sprint}

## Summary
Pass: {N} / {total}
Fail: {N} / {total}

## Checklist Results
[Each item: PASS / FAIL + one-line note]

## Failures Detail
[For each FAIL: expected, actual, file/line if applicable]

## Recommendations
[Actionable fixes for any FAILs]
```

---

## 10. Implementation Plan: Jira Tickets by Sprint

### Sprint 1 — Mode Detection & Request Schema (GLM 4.7)

**MACRO-001: Add trade_signal and deep_dive parameters to macro pipeline entry point**
- File: `main.py`
- Add `trade_signal: bool | None = None` and `deep_dive: bool = False` to macro pipeline function signature
- Pass both through to Mode Detector

**MACRO-002: Implement Mode Detector agent**
- File: `main.py` (new `_run_mode_detector()` async function)
- File: `prompts/macro_mode_detector.md` (new prompt)
- File: `agents/team.py` (new agent definition: flash model, 30s timeout, 512 output tokens)
- File: `config.yaml` (add `timeouts.mode_detector: 30`)
- Logic: explicit flag handling + LLM classifier for None case
- Output: `{report_mode, mode_rationale}` dict

**MACRO-003: Unit tests for Mode Detector**
- File: `tests/macro/test_mode_detection.py`
- All 6 unit tests from Section 8.1
- Fixtures: none needed (pure logic tests)

**MACRO-004: Update meta block to include report_mode and mode_rationale**
- File: `main.py` (meta block construction)
- Add two new lines to existing meta block format
- No template changes needed — meta block is built in Python

---

### Sprint 2 — Gemini Deep Research Agent (GLM 4.7)

**MACRO-005: Add Deep Research API client**
- File: `tools/deep_research.py` (new file)
- Async function: `run_deep_research(prompt, timeout) -> str`
- Implements: job submission, polling loop, timeout handling, result extraction
- Auth: uses existing Google credentials / Secret Manager pattern

**MACRO-006: Add Deep Research pricing to config.yaml**
- File: `config.yaml`
- Add `pricing.deep_research_cost_per_query` (populate from Google pricing page)
- Add `timeouts.deep_research: 600`

**MACRO-007: Implement Deep Research agent orchestration**
- File: `main.py` (new `_run_deep_research_agent()` async function)
- Builds handoff package: topic + report_mode + source package + `_macro_data_manifest()`
- Calls `tools/deep_research.py`
- Parses Thematic Synthesis Document from response
- Saves synthesis artifact to GCS (`{run_id}_synthesis.md`) via `save_report()`
- Registers in run_stats as agent "deep_research"
- Graceful fallback: on timeout, log in debug report and pass Source Validator output directly to Macro Analyst

**MACRO-008: Update Macro Analyst prompt**
- File: `prompts/macro_analyst.md`
- Single change: replace reference to "validated source package" with "Thematic Synthesis Document from Deep Research Agent"
- All other prompt content unchanged

**MACRO-009: Unit and integration tests for Deep Research**
- File: `tests/macro/test_deep_research_handoff.py`
- All 4 unit tests from Section 8.1
- File: `tests/macro/fixtures/` (add synthesis_document_valid.txt, validated_source_package.txt, data_manifest.txt)

---

### Sprint 3 — Signal Agent & Section 5 Conditional Rendering (GLM 4.7)

**MACRO-010: Implement Signal Agent**
- File: `main.py` (new `_run_signal_agent()` async function)
- File: `prompts/macro_signal_agent.md` (new prompt)
- File: `agents/team.py` (new agent definition: flash model, 180s timeout, 4096 output tokens)
- File: `config.yaml` (add `timeouts.signal_agent: 180`)
- Activation guard: skip entirely when `report_mode = "research"`
- Runs in parallel with Quant Modeler (Phase 3) using existing asyncio gather pattern

**MACRO-011: Update Report Compiler prompt for Section 5 conditional rendering**
- File: `prompts/macro_report_compiler.md`
- Add conditional rendering rule table (from Section 5.1 of this spec)
- Compiler receives `report_mode` and Signal Agent tier as context variables

**MACRO-012: Unit tests for Signal Agent and Section 5**
- File: `tests/macro/test_signal_agent.py` (5 tests)
- File: `tests/macro/test_section5_rendering.py` (6 tests)
- Fixtures: analyst_output_high_conviction.txt, analyst_output_low_conviction.txt, analyst_output_thematic.txt

---

### Sprint 4 — Integration, Cost Tracking, and Audit (GLM 4.7)

**MACRO-013: Integration tests**
- File: `tests/macro/test_pipeline_integration.py`
- All 7 integration tests from Section 8.2
- Requires: test GCS bucket, mock Deep Research API responses for CI

**MACRO-014: Verify cost summary includes new agents**
- File: `debug_report.py`
- Verify `format_cost_summary()` renders correctly for new agent labels (mode_detector, deep_research, signal_agent)
- No code changes expected — auto-tracked by existing infrastructure
- If gaps found: add agent label display fixes

**MACRO-015: Equity pipeline regression check**
- Manual + automated: run 3 equity reports, compare output format against pre-sprint baseline
- Automated grep: confirm no equity pipeline function references new macro symbols

**MACRO-016: Run audit plan**
- Execute all checklist items from Section 9
- Save audit report to GCS in format specified in Section 9.2
- File any FAILs as new Jira tickets in a follow-on sprint

---

## 11. Constraints & Notes

- **Equity pipeline**: zero changes. All new files and prompt modifications are macro-only.
- **Deep Research fallback**: if Deep Research API is unavailable or times out, the pipeline continues using the Source Validator output as the Macro Analyst's primary qualitative input. This is logged in the debug report as a degraded-mode run.
- **`report_mode = "signal"`** (signal-only, no research): reserved for future implementation. Not in scope for these sprints.
- **Cost tracking**: no infrastructure changes. New agents auto-tracked by existing `_run_agent()` → `record_agent_complete()` flow.
- **Geography awareness**: all existing geography rules (non-US sources, cross-market spillover labelling) apply unchanged to the Deep Research agent's expansion directive.
