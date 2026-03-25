"""
debug_report.py — Per-run statistics tracker and debug report generator.

Records per-agent events throughout the pipeline (timeouts, rate-limit retries,
success/failure, output lengths, durations, token counts, cost) and generates a
Markdown debug report saved to Cloud Storage at gs://{bucket}/debug/{run_id}.md
after every run.

This report is used to:
  1. Debug failures: which agent timed out, how many retries occurred
  2. Improve efficiency: identify expensive agents, tune timeouts per token budget
  3. Track quality: flag runs where too many agents returned placeholder output
  4. Monitor cost: per-agent and total run USD cost (tokens + Vertex AI search calls)
"""

import datetime
import os
from dataclasses import dataclass, field
from typing import Optional

from tools.pricing_lookup import get_vertex_ai_pricing, get_pricing_source


# ── Live pricing (fetched once at startup, cached for the session) ─────────────
# Source priority:
#   1. Google Cloud Billing Catalog API (live, requires roles/billing.viewer on SA)
#   2. config.yaml → pricing.models  (manual fallback)
# get_pricing_source() tells you which was used — shown in every report footer.
VERTEX_AI_PRICING, VERTEX_SEARCH_COST_PER_CALL = get_vertex_ai_pricing()

# Tool names that trigger a Vertex AI grounded search call (billed per invocation)
# search_academic_core uses CORE API directly — no Vertex AI cost, excluded here
SEARCH_TOOL_NAMES: frozenset[str] = frozenset({
    "search_web",
    "search_news",
    "search_earnings_transcript",
    "search_analyst_reports",
    "search_academic_papers",
    "search_competitor_filings",
})


def cost_for_model(
    model: str,
    input_tokens: int,
    output_tokens: int,
    search_calls: int,
) -> float:
    """
    Estimate USD cost for one agent call.

    Matches model string by substring (handles versioned names like
    "gemini-2.5-flash-001"). Returns 0.0 if model is not in the pricing table.

    Args:
        model:         Model ID (e.g. "gemini-2.5-flash", "claude-sonnet-4-6")
        input_tokens:  Prompt token count
        output_tokens: Candidates/completion token count
        search_calls:  Number of Vertex AI grounded search tool calls

    Returns:
        Estimated cost in USD.
    """
    pricing = None
    for key, prices in VERTEX_AI_PRICING.items():
        if key in model or model in key:
            pricing = prices
            break

    token_cost = 0.0
    if pricing and (input_tokens or output_tokens):
        token_cost = (
            (input_tokens  / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
        )

    search_cost = search_calls * VERTEX_SEARCH_COST_PER_CALL
    return round(token_cost + search_cost, 6)


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class AgentStats:
    """Statistics for a single agent call within a pipeline run."""
    label: str
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    status: str = "pending"            # pending | running | success | timeout | rate_limit | http_error | error
    output_length: int = 0             # character count of the final result string
    timeout_count: int = 0             # how many times this agent timed out before final outcome
    rate_limit_retry_count: int = 0    # how many 429 retries occurred
    attempt_count: int = 0             # total attempts made (1 = succeeded first try)
    error_message: Optional[str] = None  # truncated error string if status != success
    # Cost tracking
    model: str = ""                    # model ID used for this agent
    input_tokens: int = 0              # prompt token count (from usage_metadata)
    output_tokens: int = 0             # candidates token count (from usage_metadata)
    search_calls: int = 0              # Vertex AI grounded search tool calls made
    cost_usd: float = 0.0              # estimated USD cost (tokens + search)


@dataclass
class RunStats:
    """Aggregated statistics for an entire pipeline run."""
    run_id: str
    topic: str
    report_type: str
    pipeline_start: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    pipeline_end: Optional[datetime.datetime] = None
    agents: dict = field(default_factory=dict)   # label (str) → AgentStats
    structured_data_duration_s: float = 0.0      # seconds taken to gather structured data
    structured_data_status: str = "ok"           # ok | timeout | error
    analyst_placeholder_count: int = 0           # how many of the parallel analysts returned placeholders
    # Run-level cost totals (summed across all agents after pipeline completes)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_search_calls: int = 0
    total_cost_usd: float = 0.0


# ── Placeholder prefixes (must match _run_agent() return strings in main.py) ──

_PLACEHOLDER_PREFIXES = (
    "[AGENT TIMEOUT:",
    "[RATE LIMIT:",
    "[HTTP ERROR:",
    "[ERROR:",
    "[EMPTY RESPONSE:",  # injected by _run_agent when model returns no text parts
)


# ── Helper functions ───────────────────────────────────────────────────────────

def _is_placeholder(result: str) -> bool:
    """Return True if result is a placeholder string (timeout / rate-limit / error)."""
    if not result:
        return True
    stripped = result.strip()
    return any(stripped.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def _detect_status(result: str) -> tuple[str, Optional[str]]:
    """
    Detect the status and error message from a result string.

    Returns (status, error_message) where status is one of:
      success | timeout | rate_limit | http_error | error
    """
    stripped = result.strip() if result else ""
    if not stripped:
        return "error", "[EMPTY RESPONSE: agent returned no text content]"
    if stripped.startswith("[AGENT TIMEOUT:"):
        return "timeout", stripped[:200]
    if stripped.startswith("[RATE LIMIT:"):
        return "rate_limit", stripped[:200]
    if stripped.startswith("[HTTP ERROR:"):
        return "http_error", stripped[:200]
    if stripped.startswith("[ERROR:"):
        return "error", stripped[:200]
    if stripped.startswith("[EMPTY RESPONSE:"):
        return "error", stripped[:200]
    return "success", None


# ── Factory + recording functions ──────────────────────────────────────────────

def create_run_stats(run_id: str, topic: str, report_type: str) -> RunStats:
    """Create a fresh RunStats instance for a new pipeline run."""
    return RunStats(run_id=run_id, topic=topic, report_type=report_type)


def record_agent_start(stats: RunStats, label: str) -> None:
    """
    Mark an agent as started. Creates the AgentStats entry if not yet present.
    Safe to call multiple times — only sets start_time on first call.
    """
    if label not in stats.agents:
        stats.agents[label] = AgentStats(label=label)
    if stats.agents[label].start_time is None:
        stats.agents[label].start_time = datetime.datetime.utcnow()
        stats.agents[label].status = "running"


def record_agent_complete(
    stats: RunStats,
    label: str,
    result: str,
    timeout_count: int = 0,
    rate_limit_retries: int = 0,
    attempt_count: int = 1,
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    search_calls: int = 0,
) -> None:
    """
    Mark an agent as complete. Detects final status from result string prefix.
    Calculates and stores cost estimate from token usage and search calls.

    Args:
        stats:              The RunStats object for this run
        label:              Agent label (e.g., "fundamental-analyst")
        result:             The string returned by _run_agent()
        timeout_count:      How many times asyncio.TimeoutError was caught
        rate_limit_retries: How many 429 RESOURCE_EXHAUSTED retries occurred
        attempt_count:      Total loop iterations (attempts) made
        model:              Model ID string (e.g., "gemini-2.5-flash")
        input_tokens:       Prompt token count from usage_metadata
        output_tokens:      Candidates token count from usage_metadata
        search_calls:       Number of Vertex AI grounded search tool calls made
    """
    if label not in stats.agents:
        stats.agents[label] = AgentStats(label=label)

    agent = stats.agents[label]
    agent.end_time = datetime.datetime.utcnow()
    agent.timeout_count = timeout_count
    agent.rate_limit_retry_count = rate_limit_retries
    agent.attempt_count = attempt_count
    agent.output_length = len(result) if result else 0

    status, error_msg = _detect_status(result)
    agent.status = status
    agent.error_message = error_msg

    # Cost tracking
    agent.model = model
    agent.input_tokens = input_tokens
    agent.output_tokens = output_tokens
    agent.search_calls = search_calls
    agent.cost_usd = cost_for_model(model, input_tokens, output_tokens, search_calls)

    # Accumulate into run totals
    stats.total_input_tokens += input_tokens
    stats.total_output_tokens += output_tokens
    stats.total_search_calls += search_calls
    stats.total_cost_usd = round(stats.total_cost_usd + agent.cost_usd, 6)


def count_analyst_placeholders(results: list) -> int:
    """Count how many results in a list are placeholder strings."""
    return sum(1 for r in results if _is_placeholder(str(r)))


def format_cost_summary(stats: RunStats) -> str:
    """
    Return a concise cost summary block for appending to research reports.

    Includes all agents that ran (success, timeout, error) with their status
    clearly labelled. Timed-out agents show partial token costs — i.e., whatever
    was consumed before the wall-clock limit was hit — so the total reflects true
    Vertex AI spend regardless of whether an agent completed successfully.

    Example output:
        ## Run Cost & Token Usage
        | Metric | Value |
        ...
    """
    token_note = (
        "(token counts unavailable — usage_metadata not returned by this model)"
        if stats.total_input_tokens == 0 and stats.total_output_tokens == 0
        else ""
    )

    # Agents that have a recorded final status (exclude still-pending/running)
    completed = [
        a for a in stats.agents.values()
        if a.status not in ("pending", "running")
    ]

    # Check for any timed-out agents that incurred costs
    timeout_with_cost = [
        a for a in completed
        if a.status == "timeout" and (a.cost_usd > 0 or a.input_tokens > 0)
    ]
    timeout_zero_cost = [
        a for a in completed
        if a.status == "timeout" and a.cost_usd == 0 and a.input_tokens == 0
    ]

    timeout_note = ""
    if timeout_with_cost:
        names = ", ".join(f"`{a.label}`" for a in timeout_with_cost)
        timeout_note = (
            f" Partial costs included for timed-out agents ({names}) — "
            f"reflects tokens consumed before the timeout limit was reached."
        )
    elif timeout_zero_cost:
        names = ", ".join(f"`{a.label}`" for a in timeout_zero_cost)
        timeout_note = (
            f" Agents {names} timed out before any usage_metadata was returned "
            f"(zero tokens billed — timed out during connection/startup)."
        )

    lines = [
        "",
        "---",
        "",
        "## Run Cost & Token Usage",
        "",
        f"*Estimates based on Vertex AI list pricing.{' ' + token_note if token_note else ''}"
        f"{timeout_note}*",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| **Total estimated cost** | **\\${stats.total_cost_usd:.4f}** |",
        f"| Input tokens (all agents) | {stats.total_input_tokens:,} |",
        f"| Output tokens (all agents) | {stats.total_output_tokens:,} |",
        f"| Vertex AI search calls | {stats.total_search_calls} "
        f"(~\\${stats.total_search_calls * VERTEX_SEARCH_COST_PER_CALL:.3f}) |",
        f"| Run ID | `{stats.run_id}` |",
        "",
    ]

    # Per-agent cost breakdown — show all completed agents, sorted by cost desc
    if completed:
        lines += [
            "**Per-agent breakdown:**",
            "",
            "| Agent | Status | Model | Input tok | Output tok | Searches | Cost |",
            "|---|---|---|---|---|---|---|",
        ]
        status_labels = {
            "success":    "✅ ok",
            "timeout":    "⏱ timeout",
            "rate_limit": "🔴 rate_limit",
            "http_error": "🟡 http_error",
            "error":      "❌ error",
        }
        for a in sorted(completed, key=lambda x: x.cost_usd, reverse=True):
            model_short = a.model.split("/")[-1] if "/" in a.model else (a.model or "—")
            status_label = status_labels.get(a.status, a.status)
            cost_str = f"\\${a.cost_usd:.4f}" if a.cost_usd > 0 else "—"
            # Mark timed-out agents that had non-zero cost as partial
            if a.status == "timeout" and a.cost_usd > 0:
                cost_str = f"~\\${a.cost_usd:.4f} (partial)"
            lines.append(
                f"| `{a.label}` | {status_label} | {model_short} | "
                f"{a.input_tokens:,} | {a.output_tokens:,} | "
                f"{a.search_calls} | {cost_str} |"
            )
        lines.append("")

    lines.append(
        f"*Prices are estimates sourced from: {get_pricing_source()}. "
        f"Token counts from `usage_metadata` in Vertex AI responses. "
        f"Search cost: \\${VERTEX_SEARCH_COST_PER_CALL:.3f} per grounded search call. "
        f"Timed-out agent costs reflect tokens consumed before cancellation.*"
    )
    return "\n".join(lines)


# ── Report generation ──────────────────────────────────────────────────────────

def _duration_str(start: Optional[datetime.datetime],
                  end: Optional[datetime.datetime]) -> str:
    """Format a duration as '123s' or '—' if either timestamp is missing."""
    if start is None or end is None:
        return "—"
    delta = (end - start).total_seconds()
    return f"{delta:.0f}s"


def _status_emoji(status: str) -> str:
    """Return a short status indicator for the Markdown table."""
    return {
        "success":    "✅ success",
        "timeout":    "⏱ timeout",
        "rate_limit": "🔴 rate_limit",
        "http_error": "🟡 http_error",
        "error":      "❌ error",
        "pending":    "⬜ pending",
        "running":    "🔄 running",
    }.get(status, status)


def generate_debug_report(stats: RunStats) -> str:
    """
    Produce a Markdown debug report from a completed RunStats object.

    Structure:
      # Debug Report — {run_id}
      ## Run Summary (key-value table, includes cost totals)
      ## Per-Agent Status (detailed table with token counts and cost)
      ## Recommendations (auto-generated from stats)
    """
    now = stats.pipeline_end or datetime.datetime.utcnow()
    total_duration = _duration_str(stats.pipeline_start, now)

    token_note = (
        "*(token counts unavailable)*"
        if stats.total_input_tokens == 0 and stats.total_output_tokens == 0
        else ""
    )

    lines = [
        f"# Debug Report — {stats.run_id}",
        f"",
        f"*Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}*",
        f"",
        f"---",
        f"",
        f"## Run Summary",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| **Run ID** | `{stats.run_id}` |",
        f"| **Topic** | {stats.topic} |",
        f"| **Report Type** | {stats.report_type} |",
        f"| **Pipeline Start** | {stats.pipeline_start.strftime('%Y-%m-%d %H:%M:%S UTC')} |",
        f"| **Pipeline End** | {now.strftime('%Y-%m-%d %H:%M:%S UTC')} |",
        f"| **Total Duration** | {total_duration} |",
        f"| **Structured Data** | {stats.structured_data_status} ({stats.structured_data_duration_s:.0f}s) |",
        f"| **Analyst Placeholders** | {stats.analyst_placeholder_count} |",
        f"| **Total Input Tokens** | {stats.total_input_tokens:,} {token_note} |",
        f"| **Total Output Tokens** | {stats.total_output_tokens:,} {token_note} |",
        f"| **Total Search Calls** | {stats.total_search_calls} (~${stats.total_search_calls * VERTEX_SEARCH_COST_PER_CALL:.3f}) |",
        f"| **Estimated Total Cost** | **${stats.total_cost_usd:.4f}** |",
        f"",
        f"---",
        f"",
        f"## Per-Agent Status",
        f"",
        f"| Agent | Model | Status | Duration | Output (chars) | Input tok | Output tok | Searches | Cost | Retries |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ]

    # Sort agents by start_time for chronological order
    sorted_agents = sorted(
        stats.agents.values(),
        key=lambda a: a.start_time or datetime.datetime(2000, 1, 1),
    )

    for agent in sorted_agents:
        duration = _duration_str(agent.start_time, agent.end_time)
        output_fmt = f"{agent.output_length:,}" if agent.output_length > 0 else "0"
        model_short = agent.model.split("/")[-1] if "/" in agent.model else (agent.model or "—")
        retries = f"rl:{agent.rate_limit_retry_count} t:{agent.timeout_count}"
        lines.append(
            f"| `{agent.label}` | {model_short} | {_status_emoji(agent.status)} | {duration} | "
            f"{output_fmt} | {agent.input_tokens:,} | {agent.output_tokens:,} | "
            f"{agent.search_calls} | ${agent.cost_usd:.4f} | {retries} |"
        )

    lines += ["", "---", "", "## Recommendations", ""]

    # Auto-generate recommendations
    recommendations = []

    if stats.structured_data_status == "timeout":
        if stats.report_type == "macro":
            recommendations.append(
                "⚠️ **Macro pre-gather timed out** — FRED/WorldBank/OECD/IMF/ECB/AV/Polygon APIs did not "
                "respond within the 90-second window. Check API availability and consider increasing the "
                "`_gather_macro_data` timeout in `main.py` if this recurs."
            )
        else:
            recommendations.append(
                "⚠️ **Structured data timed out** — Finnhub/FMP/EDGAR APIs did not respond within the "
                "120-second window. Check API availability and consider increasing the `_gather_structured_data` "
                "timeout in `main.py` if this recurs."
            )
    elif stats.structured_data_status == "error":
        recommendations.append(
            "⚠️ **Structured data errored** — one or more API calls failed at the data-gathering stage. "
            "Agents fell back to web search. Check API keys and quotas."
        )

    if stats.analyst_placeholder_count >= 3:
        recommendations.append(
            f"🔴 **QUALITY WARNING — {stats.analyst_placeholder_count} analyst(s) returned placeholders.** "
            f"The report is significantly incomplete. Primary causes: rate-limit exhaustion "
            f"(increase `max_rate_limit_retries` in `config.yaml`) or agent timeouts "
            f"(increase per-agent timeouts)."
        )

    for agent in sorted_agents:
        if agent.timeout_count > 0:
            recommendations.append(
                f"⏱ **`{agent.label}` timed out {agent.timeout_count}x** — consider increasing "
                f"`timeouts.{agent.label.replace('-', '_')}` in `config.yaml`."
            )
        if agent.rate_limit_retry_count > 3:
            recommendations.append(
                f"🔴 **`{agent.label}` hit {agent.rate_limit_retry_count} rate-limit retries** — "
                f"reduce `concurrency.max_parallel_agents` or increase `max_rate_limit_retries` "
                f"in `config.yaml`."
            )

    total_output = sum(a.output_length for a in sorted_agents)
    if total_output > 0:
        recommendations.append(
            f"📊 **Total output across all agents: {total_output:,} characters** — "
            f"if context-window limits are causing truncation, consider reducing tool budgets "
            f"in individual agent prompts."
        )

    if stats.total_cost_usd > 0:
        most_expensive = max(sorted_agents, key=lambda a: a.cost_usd, default=None)
        if most_expensive:
            recommendations.append(
                f"💰 **Estimated run cost: ${stats.total_cost_usd:.4f}** — "
                f"most expensive agent: `{most_expensive.label}` (${most_expensive.cost_usd:.4f}). "
                f"Search calls: {stats.total_search_calls} × ${VERTEX_SEARCH_COST_PER_CALL:.3f} = "
                f"${stats.total_search_calls * VERTEX_SEARCH_COST_PER_CALL:.3f}. "
                f"Pricing source: {get_pricing_source()}."
            )

    if not recommendations:
        recommendations.append(
            "✅ **No issues detected** — all agents completed successfully with no timeouts or rate-limit retries."
        )

    for rec in recommendations:
        lines.append(f"- {rec}")
        lines.append("")

    lines += [
        "---",
        "",
        f"*Debug report generated automatically by `tools/debug_report.py`. "
        f"To disable, set `debug.save_debug_report: false` in `config.yaml`.*",
    ]

    return "\n".join(lines)


# ── Cloud Storage save ─────────────────────────────────────────────────────────

def save_debug_report(stats: RunStats, bucket: str) -> dict:
    """
    Save the debug report Markdown to GCS at gs://{bucket}/debug/{run_id}.md.

    Uses google.cloud.storage directly (same pattern as tools/storage.py).

    Returns:
        dict with keys: saved (bool), gcs_uri (str), error (str or None)
    """
    try:
        from google.cloud import storage as gcs_storage

        content = generate_debug_report(stats)
        blob_path = f"debug/{stats.run_id}.md"

        client = gcs_storage.Client()
        bucket_obj = client.bucket(bucket)
        blob = bucket_obj.blob(blob_path)
        blob.upload_from_string(content, content_type="text/markdown; charset=utf-8")

        gcs_uri = f"gs://{bucket}/{blob_path}"
        return {"saved": True, "gcs_uri": gcs_uri, "error": None}

    except Exception as e:
        return {
            "saved": False,
            "gcs_uri": None,
            "error": str(e),
        }
