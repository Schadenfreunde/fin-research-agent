# Execution Prompt — Macro Deep Research Redesign
**For:** GLM 4.7
**Session type:** Implementation (write code, run tests, commit)
**Scope:** Macro pipeline ONLY — do not touch any equity pipeline files

---

## Skills — Read This First

You have access to the full superpowers skill library. The following skills are REQUIRED at specific gates during this session. Do not skip them.

| When | Skill | Why |
|---|---|---|
| Session start, before any work | `superpowers:subagent-driven-development` | Primary execution mode — dispatches a fresh subagent per task with review between each |
| Before writing ANY implementation code in each task | `superpowers:test-driven-development` | Enforces write-test-first discipline; the plan already has test steps, this skill governs how to execute them |
| Whenever a test fails or unexpected behaviour occurs | `superpowers:systematic-debugging` | Required before proposing any fix — diagnose root cause first, do not patch blindly |
| Before marking any task `completed` | `superpowers:verification-before-completion` | Runs the verification commands specified in each task step before declaring done |
| After completing each full sprint (1, 2, 3, 4) | `superpowers:requesting-code-review` | Sprint-level review checkpoint before starting the next sprint |
| When two or more tasks in a sprint are fully independent | `superpowers:dispatching-parallel-agents` | Parallelise where the plan allows — e.g. Sprint 3 Tasks 11 + 12 can run in parallel |
| After all 4 sprints are complete and all tests pass | `superpowers:finishing-a-development-branch` | Final integration check before handing off for audit |

**HARD RULE:** Do not proceed past a sprint boundary without invoking `superpowers:requesting-code-review`. Do not declare the implementation complete without invoking `superpowers:finishing-a-development-branch`.

---

## What You Are Building

You are implementing a redesign of the macro research pipeline in this repository. The full implementation plan is at:

```
docs/superpowers/plans/2026-04-21-macro-deep-research-redesign.md
```

Read that file first. It contains every task, every file path, and every code block you need. Execute it task by task using `superpowers:subagent-driven-development`.

## What the Redesign Does

The macro pipeline currently always emits a trade recommendation, even for purely thematic topics. This redesign adds three things:

1. **Mode Detector (Phase 0)** — classifies each request as `"research"` (thematic, no trade call) or `"both"` (trade signal warranted) before any data gathering. Controlled by new `trade_signal` and `deep_dive` request parameters on the `/research` endpoint.

2. **Gemini Deep Research Agent (Phase 1e)** — slots between Source Validator and Macro Analyst. Calls the real Gemini Deep Research API (`deep-research-preview-04-2026`) via `tools/deep_research.py`. Produces a Thematic Synthesis Document saved to GCS as `{run_id}_synthesis.md` before the Macro Analyst runs. On timeout or API error, falls back gracefully to the Source Validator output.

3. **Signal Agent (Phase 3b)** — runs in parallel with the Quant Modeler, only when `report_mode = "both"`. Assesses the Macro Analyst's output and produces a tiered recommendation: Tier 1 (full trade call), Tier 2 (directional stance), or Tier 3 (observational). Drives Section 5 rendering in the Report Compiler.

## Critical Constraints

- **Equity pipeline: zero changes.** Do not touch `_run_equity_pipeline`, equity agent definitions, equity prompt files, or equity-only tools. Run `grep` checks after each sprint to verify.
- **ADK brace safety:** The Google ADK crashes if any `{var}` pattern appears outside a fenced code block in a `.md` prompt file. After writing any new prompt, run: `grep -n "{" prompts/NEW_FILE.md | grep -v "^\s*\`"` — expect no output. This is a blocker if missed.
- **Deep Research auth:** Uses Gemini Developer API (not Vertex AI). API key is in GCP Secret Manager as `"google-ai-api-key"` — already exists. Load it via `tools/http_client.get_api_key("google-ai-api-key")`. No new secrets to create.
- **Fallback:** If Deep Research times out or errors, `_run_deep_research_agent()` must fall back to passing the Source Validator output directly to the Macro Analyst. Log the fallback in the debug report as `deep-research: timeout` or `deep-research: error`.
- **No forced trade calls:** The redesign's entire point is that trade recommendations are conditional. Never write code that forces a Section 5 trade call regardless of mode.

## Repository Context

- **Working directory:** root of the repo (`main.py`, `agents/team.py`, `config.yaml`, `prompts/`, `tools/`)
- **Pipeline entry:** `_run_macro_pipeline()` in `main.py` (~line 1609)
- **Agent pattern:** all agents defined in `agents/team.py`, called via `_run_agent(agent, message, label, run_id, timeout_seconds, run_stats)` in `main.py`
- **Existing macro agents:** `macro_data_agent`, `macro_source_validator`, `macro_analyst`, `quant_modeler_macro`, `macro_report_compiler` — all unchanged
- **Cost tracking:** automatic — new agents tracked by `_run_agent()` → `record_agent_complete()` → `format_cost_summary()`. No infrastructure changes needed.
- **Config timeouts:** loaded as `_TIMEOUTS.get("key", default)` from `config.yaml`
- **Deep Research is NOT an ADK agent** — it is called directly via `tools/deep_research.py` using the `google-generativeai` SDK. It does not appear in `MACRO_AGENTS`.
- **Test runner:** `pytest` from repo root

## Sprint Breakdown

| Sprint | Tasks | Key deliverable |
|---|---|---|
| 1 | 1–6 | Mode Detector, config, request schema, unit tests |
| 2 | 7–10 | `tools/deep_research.py`, Secret Manager wiring, `_run_deep_research_agent()`, unit tests |
| 3 | 11–15 | Signal Agent, Section 5 conditional rendering, unit tests |
| 4 | 16–20 | Integration tests, storage compatibility, equity regression, audit task, documentation |

## Skill Invocation Sequence

```
SESSION START
  → invoke superpowers:subagent-driven-development

SPRINT 1 (Tasks 1–6)
  → Before each implementation task: invoke superpowers:test-driven-development
  → On any test failure: invoke superpowers:systematic-debugging before fixing
  → Before marking each task complete: invoke superpowers:verification-before-completion
  → After Task 6 (sprint complete): invoke superpowers:requesting-code-review

SPRINT 2 (Tasks 7–10)
  → Tasks 7 and 8 are independent — invoke superpowers:dispatching-parallel-agents
  → On any test failure: invoke superpowers:systematic-debugging
  → Before marking each task complete: invoke superpowers:verification-before-completion
  → After Task 10 (sprint complete): invoke superpowers:requesting-code-review

SPRINT 3 (Tasks 11–15)
  → Tasks 11 and 12 are independent (prompt + agent def) — invoke superpowers:dispatching-parallel-agents
  → On any test failure: invoke superpowers:systematic-debugging
  → Before marking each task complete: invoke superpowers:verification-before-completion
  → After Task 15 (sprint complete): invoke superpowers:requesting-code-review

SPRINT 4 (Tasks 16–20)
  → Sections A–E of Task 19 can be parallelised — invoke superpowers:dispatching-parallel-agents
  → Before marking each task complete: invoke superpowers:verification-before-completion
  → After Task 20 (sprint complete): invoke superpowers:requesting-code-review

IMPLEMENTATION COMPLETE
  → Run full test suite: pytest tests/macro/ -v --tb=short
  → Invoke superpowers:finishing-a-development-branch
```

## After All Sprints

When all 4 sprints are done:

1. Run `pytest tests/macro/ -v --tb=short` — zero failures required
2. Run equity regression check (Task 19 Step 4) — zero matches required
3. Run config validation (Task 19 Step 3) — all new keys present
4. Invoke `superpowers:finishing-a-development-branch`
5. Report a complete summary: tests passed/failed, commits made, files changed

## Do Not

- Do not implement anything not in the plan
- Do not touch equity pipeline code
- Do not skip the ADK brace check after writing any prompt file
- Do not skip tests — every task has a test step, run it before marking done
- Do not skip skill invocations at sprint boundaries — they are checkpoints, not suggestions
- Do not amend commits — create new commits if a fix is needed after a commit
- Do not push to remote (user deploys manually via `./deploy.sh`)
