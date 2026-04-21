# Execution Prompt — Macro Deep Research Redesign
**For:** GLM 4.7
**Session type:** Implementation (write code, run tests, commit)
**Scope:** Macro pipeline ONLY — do not touch any equity pipeline files

---

## What You Are Building

You are implementing a redesign of the macro research pipeline in this repository. The full implementation plan is at:

```
docs/superpowers/plans/2026-04-21-macro-deep-research-redesign.md
```

Read that file first. It contains every task, every file path, and every code block you need. Execute it task by task.

## What the Redesign Does

The macro pipeline currently always emits a trade recommendation, even for purely thematic topics. This redesign adds three things:

1. **Mode Detector (Phase 0)** — classifies each request as `"research"` (thematic, no trade call) or `"both"` (trade signal warranted) before any data gathering. Controlled by new `trade_signal` and `deep_dive` request parameters.

2. **Gemini Deep Research Agent (Phase 1e)** — slots between Source Validator and Macro Analyst. Calls the Gemini Deep Research API (`deep-research-preview-04-2026`) with the validated source package and a compact data manifest. Autonomously expands sources and produces a Thematic Synthesis Document saved to GCS as `{run_id}_synthesis.md`.

3. **Signal Agent (Phase 3b)** — runs in parallel with the Quant Modeler, only when `report_mode = "both"`. Assesses the Macro Analyst's output and produces a tiered recommendation: Tier 1 (full trade call), Tier 2 (directional stance), or Tier 3 (observational). Drives Section 5 rendering in the Report Compiler.

## Critical Constraints

- **Equity pipeline: zero changes.** Do not touch `_run_equity_pipeline`, equity agent definitions, equity prompt files, or equity-only tools. Run `grep` checks after each sprint to verify.
- **ADK brace safety:** The Google ADK crashes if any `{var}` pattern appears outside a fenced code block in a `.md` prompt file. After writing any new prompt, run: `grep -n "{" prompts/NEW_FILE.md | grep -v "^\s*\`"` — expect no output.
- **Deep Research auth:** Uses Gemini Developer API (not Vertex AI). API key is in GCP Secret Manager as `"google-ai-api-key"` — already exists. Load it via `tools/http_client.get_api_key("google-ai-api-key")`. No new secrets to create.
- **Fallback:** If Deep Research times out or errors, `_run_deep_research_agent()` must fall back to passing the Source Validator output directly to the Macro Analyst. Log the fallback in the debug report.

## Repository Context

- **Working directory:** root of the repo (`main.py`, `agents/team.py`, `config.yaml`, `prompts/`, `tools/`)
- **Pipeline entry:** `_run_macro_pipeline()` in `main.py` (~line 1609)
- **Agent pattern:** all agents defined in `agents/team.py`, called via `_run_agent(agent, message, label, run_id, timeout_seconds, run_stats)` in `main.py`
- **Existing macro agents:** `macro_data_agent`, `macro_source_validator`, `macro_analyst`, `quant_modeler_macro`, `macro_report_compiler` — all unchanged
- **Cost tracking:** automatic — new agents tracked by `_run_agent()` → `record_agent_complete()` → `format_cost_summary()`. No infrastructure changes needed.
- **Config timeouts:** loaded as `_TIMEOUTS.get("key", default)` from `config.yaml`
- **Test runner:** `pytest` from repo root

## Execution Instructions

Use the `superpowers:subagent-driven-development` skill to execute the plan task by task.

Before starting each task:
- Mark it `in_progress` in your todo list
- Read the relevant existing files before editing (use the Read tool — do not guess at file contents)

After completing each task:
- Run the tests specified in the plan step
- Verify they pass before moving on
- Commit with the exact commit message in the plan step
- Mark the task `completed`

After completing all 4 sprints:
- Run the full unit test suite: `pytest tests/macro/ -v --tb=short`
- Run the equity regression check from Task 19 Step 4
- Run the config validation from Task 19 Step 3
- Report a summary of all tests passed/failed

## Do Not

- Do not implement anything not in the plan
- Do not touch equity pipeline code
- Do not skip the ADK brace check after writing prompt files
- Do not skip tests — every task has a test step, run it
- Do not amend commits — create new commits if a fix is needed
- Do not push to remote (user will deploy manually via `deploy.sh`)
