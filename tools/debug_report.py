"""
debug_report.py — Per-run statistics tracker and debug report generator.

Records per-agent events throughout the pipeline (timeouts, rate-limit retries,
success/failure, output lengths, durations) and generates a Markdown debug report
saved to Cloud Storage at gs://{bucket}/debug/{run_id}.md after every run.

This report is used to:
  1. Debug failures: which agent timed out, how many retries occurred
  2. Improve efficiency: identify expensive agents, tune timeouts per token budget
  3. Track quality: flag runs where too many agents returned placeholder output
"""

import datetime
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class AgentStats:
    """Statistics for a single agent call within a pipeline run."""
    label: str
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    status: str = "pending"            # pending | success | timeout | rate_limit | http_error | error
    output_length: int = 0             # character count of the final result string
    timeout_count: int = 0             # how many times this agent timed out before final outcome
    rate_limit_retry_count: int = 0    # how many 429 retries occurred
    attempt_count: int = 0             # total attempts made (1 = succeeded first try)
    error_message: Optional[str] = None  # truncated error string if status != success


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
) -> None:
    """
    Mark an agent as complete. Detects final status from result string prefix.

    Args:
        stats: The RunStats object for this run
        label: Agent label (e.g., "fundamental-analyst")
        result: The string returned by _run_agent()
        timeout_count: How many times asyncio.TimeoutError was caught for this agent
        rate_limit_retries: How many 429 RESOURCE_EXHAUSTED retries occurred
        attempt_count: Total loop iterations (attempts) made
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


def count_analyst_placeholders(results: list) -> int:
    """Count how many results in a list are placeholder strings."""
    return sum(1 for r in results if _is_placeholder(str(r)))


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
      ## Run Summary (key-value table)
      ## Per-Agent Status (detailed table)
      ## Recommendations (auto-generated from stats)
    """
    now = stats.pipeline_end or datetime.datetime.utcnow()
    total_duration = _duration_str(stats.pipeline_start, now)

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
        f"",
        f"---",
        f"",
        f"## Per-Agent Status",
        f"",
        f"| Agent | Status | Duration | Output (chars) | Timeouts | Rate-Limit Retries | Attempts | Error |",
        f"|---|---|---|---|---|---|---|---|",
    ]

    # Sort agents by start_time for chronological order
    sorted_agents = sorted(
        stats.agents.values(),
        key=lambda a: a.start_time or datetime.datetime(2000, 1, 1),
    )

    for agent in sorted_agents:
        duration = _duration_str(agent.start_time, agent.end_time)
        output_fmt = f"{agent.output_length:,}" if agent.output_length > 0 else "0"
        error_display = (agent.error_message[:80] + "…" if agent.error_message and len(agent.error_message) > 80
                         else agent.error_message or "—")
        lines.append(
            f"| `{agent.label}` | {_status_emoji(agent.status)} | {duration} | "
            f"{output_fmt} | {agent.timeout_count} | {agent.rate_limit_retry_count} | "
            f"{agent.attempt_count} | {error_display} |"
        )

    lines += ["", "---", "", "## Recommendations", ""]

    # Auto-generate recommendations
    recommendations = []

    # Structured data issues
    if stats.structured_data_status == "timeout":
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

    # Quality gate
    if stats.analyst_placeholder_count >= 3:
        recommendations.append(
            f"🔴 **QUALITY WARNING — {stats.analyst_placeholder_count} analyst(s) returned placeholders.** "
            f"The report is significantly incomplete. Primary causes: rate-limit exhaustion "
            f"(increase `max_rate_limit_retries` in `config.yaml`) or agent timeouts "
            f"(increase per-agent timeouts)."
        )

    # Per-agent recommendations
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

    # Token efficiency note
    total_output = sum(a.output_length for a in sorted_agents)
    if total_output > 0:
        recommendations.append(
            f"📊 **Total output across all agents: {total_output:,} characters** — "
            f"if context-window limits are causing truncation, consider reducing tool budgets "
            f"in individual agent prompts."
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
