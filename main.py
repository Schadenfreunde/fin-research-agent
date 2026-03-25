"""
main.py — FastAPI entry point for the Stock Research Agent System.

Serves:
  GET  /          → Web form (web/index.html)
  POST /research  → Submit a research request (equity or macro)
  GET  /reports   → List past reports
  GET  /health    → Health check endpoint (used by Cloud Run)

Pipeline orchestration is done in Python — each agent is called explicitly
in the correct sequence, with outputs passed between steps. This ensures
agents actually run rather than an LLM just describing what it would do.

For scheduled runs:
  Cloud Scheduler calls POST /scheduled with no body.
  The endpoint runs analysis on all tickers/topics in config.yaml.
"""

import os
import uuid
import datetime
import asyncio
import logging
import random
import yaml
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ── Suppress known-benign SDK warnings ────────────────────────────────────────
# The google-genai SDK logs a warning via logger 'google_genai.types' whenever
# .text is accessed on a response that contains function_call parts. This is
# normal ADK behaviour during tool-call processing (the ADK inspects intermediate
# function_call events internally). The warning is harmless — tool execution and
# final text responses are handled correctly — but it fills Cloud Run logs with
# noise on every agent step that uses tools. Suppress at WARNING level; ERROR and
# above from this logger are still visible.
logging.getLogger("google_genai.types").setLevel(logging.ERROR)

# ── Load configuration ─────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(_CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

# ── Initialize Google Cloud ────────────────────────────────────────────────────

import vertexai
_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or CONFIG["google_cloud"]["project_id"]
_REGION = CONFIG["google_cloud"]["region"]
_MODEL_REGION = CONFIG["google_cloud"].get("model_region", "global")
_BUCKET = os.environ.get("REPORTS_BUCKET") or CONFIG["google_cloud"]["reports_bucket"]
vertexai.init(project=_PROJECT_ID, location=_MODEL_REGION)

# Tell google-genai SDK (used internally by google-adk) to use Vertex AI,
# not the Gemini Developer API. Without these, the ADK looks for a GOOGLE_API_KEY
# and fails — Vertex AI uses service account auth instead.
# model_region must be "global" for Gemini 3.x models.
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", _MODEL_REGION)

# ── Initialize FastAPI ─────────────────────────────────────────────────────────

app = FastAPI(
    title="FinResearchAgent",
    description="Buy-side equity and macro research powered by Vertex AI",
    version="1.0.0",
)

# ── Import agents and tools ────────────────────────────────────────────────────

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
    macro_analyst,
    quant_modeler_macro,
    macro_report_compiler,
    fact_checker,
    review_agent,
)
from tools.storage import save_report, save_run_metadata, list_reports, save_latex_report
from tools.news_api import get_company_news_newsapi, get_topic_news_newsapi
from tools.openfigi_data import get_figi_mapping
from tools.worldbank_data import get_worldbank_macro_snapshot
from tools.oecd_data import get_oecd_leading_indicators, get_oecd_economic_outlook
from tools.stock_data import get_commodity_prices_alpha
from tools.polygon_data import get_forex_snapshot_polygon
from tools.debug_report import (
    RunStats,
    create_run_stats,
    record_agent_start,
    record_agent_complete,
    count_analyst_placeholders,
    generate_debug_report,
    save_debug_report,
    format_cost_summary,
    SEARCH_TOOL_NAMES,
    _is_placeholder,
)

_MAX_REVIEW_PASSES = CONFIG.get("review", {}).get("max_passes", 3)

_TIMEOUTS = CONFIG.get("timeouts", {})
_DEFAULT_TIMEOUT                 = _TIMEOUTS.get("default",                       720)  # 12 min
_DATA_HARVESTER_TIMEOUT          = _TIMEOUTS.get("data_harvester",               720)  # 12 min
_MACRO_DATA_AGENT_TIMEOUT        = _TIMEOUTS.get("macro_data_agent",             540)  # 9 min
_EARNINGS_QUALITY_TIMEOUT        = _TIMEOUTS.get("earnings_quality",             360)  # 6 min
_COMPETITIVE_ANALYST_TIMEOUT     = _TIMEOUTS.get("competitive_analyst",          480)  # 8 min
_FUNDAMENTAL_ANALYST_TIMEOUT     = _TIMEOUTS.get("fundamental_analyst",          720)  # 12 min (legacy)
_FUNDAMENTAL_MARKET_TIMEOUT      = _TIMEOUTS.get("fundamental_analyst_market",   360)  # 6 min (split)
_FUNDAMENTAL_FINANCIALS_TIMEOUT  = _TIMEOUTS.get("fundamental_analyst_financials", 720)  # 12 min (split)
_RISK_ANALYST_TIMEOUT            = _TIMEOUTS.get("risk_analyst",                 720)  # 12 min
_VALUATION_ANALYST_TIMEOUT       = _TIMEOUTS.get("valuation_analyst",            720)  # 12 min
_QUANT_EQUITY_TIMEOUT            = _TIMEOUTS.get("quant_modeler_equity",         480)  # 8 min
_QUANT_MACRO_TIMEOUT             = _TIMEOUTS.get("quant_modeler_macro",          540)  # 9 min
_MACRO_ANALYST_TIMEOUT           = _TIMEOUTS.get("macro_analyst",                480)  # 8 min
_MACRO_SOURCE_VALIDATOR_TIMEOUT  = _TIMEOUTS.get("macro_source_validator",        300)  # 5 min
_CONTEXT_PROCESSOR_TIMEOUT       = _TIMEOUTS.get("context_processor",            300)  # 5 min

# ── Vertex AI concurrency / rate-limit settings ────────────────────────────────
# Free trial accounts have low QPM quotas. _AGENT_SEMAPHORE limits how many
# agent calls can be in flight simultaneously. On free tier, set max_parallel_agents
# to 1–2. On a paid account you can raise this to 5+ for full parallelism.
_CONCURRENCY_CFG = CONFIG.get("concurrency", {})
_MAX_PARALLEL_AGENTS = _CONCURRENCY_CFG.get("max_parallel_agents", 2)
_AGENT_SEMAPHORE = asyncio.Semaphore(_MAX_PARALLEL_AGENTS)
_MAX_RATE_LIMIT_RETRIES = _CONCURRENCY_CFG.get("max_rate_limit_retries", 6)

# ── Structured logging ──────────────────────────────────────────────────────────
# Explicitly configure the "finresearch" logger so all INFO+ messages appear in
# Cloud Run logs. Without this, uvicorn's dictConfig() sets the root logger to
# WARNING level, which silently drops all logger.info() calls — pipeline
# milestones, agent start/complete events, and data-gathering status vanish.
# Setting propagate=False prevents uvicorn's root-logger configuration from
# filtering our messages after module import.
logger = logging.getLogger("finresearch")
logger.setLevel(logging.INFO)
logger.propagate = False  # bypass root logger (uvicorn sets it to WARNING)
if not logger.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setLevel(logging.INFO)
    _console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_console_handler)

# ── Dedicated thread pool for structured data gathering (Phase 2A) ───────────────
# The default asyncio executor on Cloud Run (2 vCPU) has only ~6 threads. With 18+
# parallel API calls, tasks queue instead of running concurrently. 20 workers
# ensures all data-gathering calls run in true parallel, cutting phase time by ~60%.
from concurrent.futures import ThreadPoolExecutor
_DATA_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="data-api")


# ── Helper: Load the web form HTML ────────────────────────────────────────────

def _load_web_form() -> str:
    html_path = Path(__file__).parent / "web" / "index.html"
    return html_path.read_text()


# ── Startup secrets validation ─────────────────────────────────────────────────

def _validate_secrets_at_startup() -> None:
    """
    Log which API keys are present or missing at container boot time.

    Called once at module load. Does NOT raise — missing keys degrade gracefully
    (each tool returns an error dict). This makes it easy to spot config issues
    in Cloud Run logs without needing to trigger a full pipeline run.
    """
    required_keys = {
        "FINNHUB_API_KEY":          "Finnhub (primary price/financials)",
        "FMP_API_KEY":              "Financial Modeling Prep (deep financials)",
        "ALPHA_VANTAGE_KEY":        "Alpha Vantage (supplemental price/overview)",
        "FRED_API_KEY":             "FRED (macro data series)",
        "POLYGON_API_KEY":          "Polygon.io (sector/ticker details/sector fix)",
        "CORE_API_KEY":             "CORE academic papers API",
        "SEMANTIC_SCHOLAR_API_KEY": "Semantic Scholar (optional fallback)",
        "REPORTS_BUCKET":           "GCS reports storage bucket",
        "GOOGLE_CLOUD_PROJECT":     "GCP project ID",
    }
    present, missing = [], []
    for env_var, description in required_keys.items():
        if os.environ.get(env_var):
            present.append(f"  [OK]      {env_var} — {description}")
        else:
            missing.append(f"  [MISSING] {env_var} — {description}")

    logger.info("[STARTUP] API Key Validation:")
    for line in present:
        logger.info(line)
    for line in missing:
        logger.warning(line)
    if missing:
        logger.warning("[STARTUP] %d key(s) missing — some features may degrade.", len(missing))
    else:
        logger.info("[STARTUP] All API keys present.")


# Run once at import time (executes when Cloud Run starts the container)
_validate_secrets_at_startup()


# ── Core agent runner ──────────────────────────────────────────────────────────

async def _run_agent(agent, message: str, label: str, run_id: str,
                     timeout_seconds: int = None,
                     run_stats: RunStats = None) -> str:
    """
    Run a single ADK agent with the given message and return its text output.

    Each call creates its own session so agents are fully independent.
    Progress is logged with the run_id and label for Cloud Run log filtering.

    Rate limiting: a module-level asyncio.Semaphore (_AGENT_SEMAPHORE) limits how
    many agents can call Vertex AI simultaneously. This prevents 429 quota errors
    on free trial GCP accounts. Configured via config.yaml → concurrency.max_parallel_agents.

    Retry logic: 429 RESOURCE_EXHAUSTED errors are caught and retried with
    exponential backoff using full jitter (up to _MAX_RATE_LIMIT_RETRIES attempts).
    Full jitter avoids the thundering-herd problem when multiple agents back off
    simultaneously. On a permanent failure after all retries, a placeholder string
    is returned so the pipeline continues rather than crashing.

    timeout_seconds: hard wall-clock limit per attempt. On timeout the pipeline
    continues with a placeholder string — no data is lost that was already
    gathered by Python before this call.

    run_stats: optional RunStats instance. If provided, per-agent timing and
    retry/timeout counts are recorded for the debug report.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    timeout = timeout_seconds if timeout_seconds is not None else _DEFAULT_TIMEOUT
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=message)],
    )

    # ── Stats tracking counters ────────────────────────────────────────────────
    timeout_count = 0
    rate_limit_count = 0
    attempt_count = 0

    # ── Cost tracking (populated by _collect, read after each attempt) ────────
    # _usage accumulates across ALL attempts (retries + timeouts) so the reported
    # cost reflects total Vertex AI spend for this agent slot, not just the last run.
    _agent_model = getattr(agent, "model", "") or ""
    _usage: dict = {"input_tokens": 0, "output_tokens": 0, "search_calls": 0}
    # Shared mutable container so the timeout handler can read the last
    # usage_metadata even after _collect() is cancelled by asyncio.wait_for.
    _last_meta: dict = {"meta": None}

    # Record agent start in RunStats (before retry loop)
    if run_stats is not None:
        record_agent_start(run_stats, label)

    last_exception = None
    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        attempt_count = attempt + 1

        # Back off before retrying (skip wait on first attempt)
        # Full jitter: wait = random(0, base) to avoid thundering-herd when multiple
        # agents retry simultaneously. Capped at 4 minutes per retry.
        if attempt > 0:
            base = min(180, (2 ** (attempt - 1)) * 15)
            wait = min(240, base + random.uniform(0, base))
            logger.warning(
                "[%s] Rate-limit backoff for %s: attempt %d/%d, waiting %.0fs...",
                run_id, label, attempt + 1, _MAX_RATE_LIMIT_RETRIES, wait,
            )
            await asyncio.sleep(wait)

        # Acquire the semaphore — blocks if _MAX_PARALLEL_AGENTS are already running.
        # Released automatically when the 'async with' block exits (success or error).
        async with _AGENT_SEMAPHORE:
            session_service = InMemorySessionService()
            runner = Runner(
                agent=agent,
                app_name=CONFIG["google_cloud"]["service_name"],
                session_service=session_service,
            )
            # Include attempt number in session_id so retries get fresh sessions
            session_id = f"session-{run_id}-{label}-{attempt}"
            await session_service.create_session(
                app_name=CONFIG["google_cloud"]["service_name"],
                user_id="system",
                session_id=session_id,
            )

            logger.info(
                "[%s] Starting: %s%s", run_id, label,
                f" (retry {attempt})" if attempt > 0 else "",
            )

            def _extract_usage(um) -> None:
                """Accumulate token counts from a usage_metadata object into _usage.

                Uses += so tokens from every attempt (including timed-out and
                rate-limited retries) are summed into the total for this agent slot.
                Each attempt uses a fresh Vertex AI session, so usage_metadata
                always starts from 0 — safe to add without risk of double-counting.
                """
                if um is None:
                    return
                _usage["input_tokens"] += (
                    getattr(um, "prompt_token_count", 0) or 0
                )
                _usage["output_tokens"] += (
                    getattr(um, "candidates_token_count", 0) or 0
                )

            async def _collect() -> str:
                # Collect text from every model response event (not just is_final_response).
                # Some agents (data-harvester, quant-equity) produce their text in
                # intermediate turns that also contain function_call parts — the ADK marks
                # the final turn as is_final_response() even when that final event has no
                # text parts (e.g. when the runner hits max tool-use steps).  By accumulating
                # text from all model turns we capture that output rather than silently losing
                # it and returning an empty string downstream.
                #
                # Priority: if the final event itself has text, use ONLY that text (avoids
                # prepending intermediate narration to working agents whose full output is
                # always in the final event).  If the final event has no text, fall back to
                # whatever was accumulated from earlier model turns.
                intermediate_text: list[str] = []
                # _last_usage_meta: tracks the most recent cumulative usage_metadata
                # for this attempt. Also written into _last_meta["meta"] so the
                # outer timeout handler can read it if this coroutine is cancelled.
                _last_usage_meta = None
                # Note: _usage is NOT reset here — tokens accumulate across all
                # attempts so timed-out and rate-limited runs are counted too.
                logger.info("[%s] %s: Connecting to Vertex AI...", run_id, label)
                async for event in runner.run_async(
                    user_id="system",
                    session_id=session_id,
                    new_message=content,
                ):
                    # ── Token usage tracking ───────────────────────────────────────
                    # Keep the most recent usage_metadata — it is cumulative in
                    # Gemini streaming and represents the full run total.
                    um = getattr(event, "usage_metadata", None)
                    if um is not None:
                        _last_usage_meta = um
                        _last_meta["meta"] = um  # sync to outer scope for timeout handler

                    if hasattr(event, "content") and event.content and event.content.parts:
                        parts = event.content.parts
                        for part in parts:
                            fc = getattr(part, "function_call", None)
                            if fc:
                                logger.debug("[%s] %s calling tool: %s", run_id, label, fc.name)
                                # Count Vertex AI grounded search calls for cost tracking
                                if fc.name in SEARCH_TOOL_NAMES:
                                    _usage["search_calls"] += 1

                        # Accumulate text from model response events (skip pure tool
                        # responses which carry only function_response parts).
                        has_fn_response = any(
                            getattr(p, "function_response", None) is not None for p in parts
                        )
                        if not has_fn_response:
                            for part in parts:
                                if getattr(part, "text", None):
                                    intermediate_text.append(part.text)

                    if event.is_final_response():
                        _extract_usage(_last_usage_meta)
                        # Prefer the final event's own text (normal case for all working agents).
                        if event.content and event.content.parts:
                            final_text_parts = [
                                p.text for p in event.content.parts
                                if getattr(p, "text", None)
                            ]
                            if final_text_parts:
                                return "\n".join(final_text_parts)
                        # Final event had no text — fall back to intermediate accumulation.
                        # This recovers output when the runner stops mid-sequence (e.g. max
                        # tool-use steps reached) before a dedicated text-only final turn.
                        if intermediate_text:
                            logger.info(
                                "[%s] %s: final event had no text — using %d intermediate chunk(s)",
                                run_id, label, len(intermediate_text),
                            )
                        _extract_usage(_last_usage_meta)
                        return "\n".join(filter(None, intermediate_text))
                # Generator exhausted without a final response event
                _extract_usage(_last_usage_meta)
                return "\n".join(filter(None, intermediate_text))

            try:
                result = await asyncio.wait_for(_collect(), timeout=timeout)
                if not result.strip():
                    # ADK returned a final event with no text parts — treat as a
                    # non-retryable error so the pipeline gets a descriptive placeholder
                    # rather than silently propagating an empty string downstream.
                    logger.warning("[%s] %s returned empty response — treating as error", run_id, label)
                    result = (
                        f"[EMPTY RESPONSE: {label} returned no text content from the model. "
                        f"Output unavailable — pipeline continued with partial data.]"
                    )
                from tools.debug_report import cost_for_model as _cost_fn
                _agent_cost = _cost_fn(
                    _agent_model,
                    _usage["input_tokens"],
                    _usage["output_tokens"],
                    _usage["search_calls"],
                )
                logger.info(
                    "[%s] Completed: %s (%d chars | in:%d out:%d tok | %d searches | ~$%.4f)",
                    run_id, label, len(result),
                    _usage["input_tokens"], _usage["output_tokens"],
                    _usage["search_calls"], _agent_cost,
                )
                if run_stats is not None:
                    record_agent_complete(
                        run_stats, label, result,
                        timeout_count, rate_limit_count, attempt_count,
                        model=_agent_model,
                        input_tokens=_usage["input_tokens"],
                        output_tokens=_usage["output_tokens"],
                        search_calls=_usage["search_calls"],
                    )
                return result

            except asyncio.TimeoutError:
                timeout_count += 1
                # Capture whatever tokens were consumed before the timeout.
                # _last_meta["meta"] is updated inside _collect() on every event,
                # so it holds the last-seen usage_metadata even though _collect()
                # was cancelled — giving us partial token counts for cost tracking.
                _extract_usage(_last_meta["meta"])
                from tools.debug_report import cost_for_model as _cost_fn
                _agent_cost = _cost_fn(
                    _agent_model,
                    _usage["input_tokens"],
                    _usage["output_tokens"],
                    _usage["search_calls"],
                )
                logger.warning(
                    "[%s] TIMEOUT: %s exceeded %ds (timeout #%d, ~$%.4f consumed so far) — continuing pipeline.",
                    run_id, label, timeout, timeout_count, _agent_cost,
                )
                result = (
                    f"[AGENT TIMEOUT: {label} did not complete within {timeout}s. "
                    f"Output unavailable — pipeline continued.]"
                )
                if run_stats is not None:
                    record_agent_complete(
                        run_stats, label, result,
                        timeout_count, rate_limit_count, attempt_count,
                        model=_agent_model,
                        input_tokens=_usage["input_tokens"],
                        output_tokens=_usage["output_tokens"],
                        search_calls=_usage["search_calls"],
                    )
                return result

            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                is_http_error = "HTTP Error" in err_str or "HTTPError" in err_str
                # INVALID_ARGUMENT (400) from the Vertex AI model API is typically
                # transient when 6 agents start simultaneously (race/connection issue).
                # Pattern: different agents fail with "model —, 0 tokens" each run.
                # Retry once with backoff — the request succeeds on the second attempt.
                is_invalid_argument = "INVALID_ARGUMENT" in err_str

                if is_rate_limit and attempt < _MAX_RATE_LIMIT_RETRIES - 1:
                    last_exception = e
                    rate_limit_count += 1
                    logger.warning(
                        "[%s] Rate limit (429) on %s (attempt %d/%d) — will retry",
                        run_id, label, attempt + 1, _MAX_RATE_LIMIT_RETRIES,
                    )
                    # Semaphore released here (exit 'async with'), then loop retries
                    continue

                if is_invalid_argument and attempt < _MAX_RATE_LIMIT_RETRIES - 1:
                    last_exception = e
                    rate_limit_count += 1
                    logger.warning(
                        "[%s] INVALID_ARGUMENT (400) on %s (attempt %d/%d) — likely transient, will retry",
                        run_id, label, attempt + 1, _MAX_RATE_LIMIT_RETRIES,
                    )
                    continue

                if is_http_error:
                    # HTTP errors from tool calls (404 = EDGAR concept not found,
                    # 503 = API temporarily down, etc.) are non-retryable but also
                    # non-fatal — return a placeholder so the pipeline continues with
                    # partial data rather than crashing the entire run.
                    logger.warning("[%s] HTTP error on %s: %s — returning placeholder", run_id, label, err_str)
                    result = (
                        f"[HTTP ERROR: {label} encountered {err_str}. "
                        f"Output unavailable — pipeline continued with partial data.]"
                    )
                    if run_stats is not None:
                        record_agent_complete(run_stats, label, result,
                                              timeout_count, rate_limit_count, attempt_count)
                    return result

                # All other errors (auth failures, SDK errors, etc.) — return placeholder
                # so the pipeline continues with partial data instead of crashing.
                # Log the full traceback so diagnostic errors (e.g., 'Context variable not
                # found' in data-harvester) reveal the exact ADK source line on the next run.
                import traceback as _tb
                logger.error("[%s] Unexpected error on %s (attempt %d): %s", run_id, label, attempt+1, err_str)
                logger.error("[%s] Full traceback for %s:\n%s", run_id, label, _tb.format_exc())
                result = (
                    f"[ERROR: {label} failed with unexpected error: {err_str}. "
                    f"Output unavailable — pipeline continued with partial data.]"
                )
                if run_stats is not None:
                    record_agent_complete(run_stats, label, result,
                                          timeout_count, rate_limit_count, attempt_count)
                return result

    # All retries exhausted on rate-limit errors — return placeholder so pipeline continues
    logger.warning(
        "[%s] All %d retries exhausted for %s (persistent rate limit) — returning placeholder",
        run_id, _MAX_RATE_LIMIT_RETRIES, label,
    )
    result = (
        f"[RATE LIMIT: {label} hit Vertex AI quota after {_MAX_RATE_LIMIT_RETRIES} retries. "
        f"Output unavailable. To fix: increase Vertex AI quota in GCP Console or reduce "
        f"config.yaml → concurrency.max_parallel_agents.]"
    )
    if run_stats is not None:
        record_agent_complete(run_stats, label, result,
                              timeout_count, rate_limit_count, attempt_count)
    return result


# ── Structured data pre-gathering ─────────────────────────────────────────────

async def _gather_structured_data(ticker: str, run_id: str) -> tuple:
    """
    Directly call all structured data APIs in parallel and return a tuple of:
      (structured_text: str, company_name: str)

    structured_text is a formatted text block ready to pass to LLM agents.
    company_name is the full legal name (e.g., "Apple Inc.") extracted from
    Polygon or Finnhub — used by agents for web searches instead of the ticker.

    Data sources (all REST-based, Cloud Run compatible):
      Group 1a — Finnhub:        price, historical OHLCV, financials, key metrics,
                                 earnings history, analyst ratings (60 calls/min free)
      Group 1b — FMP:            income statement, balance sheet, cash flow,
                                 key metrics, analyst estimates (250 calls/day free)
      Group 1c — Alpha Vantage:  current price, company overview, income
                                 statement, EPS (25 calls/day; kept as supplement)
      Group 1d — Polygon.io:     ticker details (company name, sector/SIC code),
                                 historical OHLCV fallback, recent news
                                 (free tier, unlimited calls, 15-min delay)
      Group 2  — SEC EDGAR:      recent filings, specific financial facts,
                                 insider transactions (free, rate-limited 10 req/s)

    yfinance was removed: Yahoo Finance blocks Google Cloud Run IP addresses.
    Short interest was removed: no reliable free API source exists.
    Analyst ratings: fetched from Finnhub; if unavailable, agents use web search.
    """
    import json as _json
    from tools.finnhub_data import (
        get_quote_finnhub, get_historical_prices_finnhub, get_financials_finnhub,
        get_key_metrics_finnhub, get_earnings_finnhub, get_analyst_ratings_finnhub,
    )
    from tools.fmp_data import (
        get_income_statement_fmp, get_balance_sheet_fmp, get_cash_flow_fmp,
        get_key_metrics_fmp, get_analyst_estimates_fmp,
    )
    from tools.stock_data import (
        get_current_price_alpha, get_company_overview_alpha,
        get_income_statement_alpha, get_earnings_per_share_alpha,
    )
    from tools.sec_filings import get_recent_filings, get_specific_fact, get_insider_transactions
    from tools.polygon_data import (
        get_ticker_details_polygon, get_historical_ohlcv_polygon, get_recent_news_polygon,
    )

    loop = asyncio.get_running_loop()
    _sd_t0 = datetime.datetime.utcnow()
    logger.info("[%s] STEP 1a: Gathering structured data (18+ APIs in parallel — Finnhub, FMP, AV, Polygon, EDGAR)...", run_id)

    # ── Groups 1a–1d: Launch all parallel API groups at once ──────────────────
    # _DATA_EXECUTOR (20 workers) ensures all 18 calls run in true parallel;
    # the default executor on Cloud Run 2-vCPU has only ~6 threads and would queue.

    # ── Group 1a: Finnhub (primary price/history/fundamentals) ────────────────
    finnhub_labels = [
        "price_finnhub",
        "historical_prices_2y_weekly_finnhub",
        "financials_finnhub",
        "key_metrics_finnhub",
        "earnings_finnhub",
        "analyst_ratings_finnhub",
    ]
    finnhub_coros = [
        loop.run_in_executor(_DATA_EXECUTOR, get_quote_finnhub,             ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_historical_prices_finnhub, ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_financials_finnhub,        ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_key_metrics_finnhub,       ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_earnings_finnhub,          ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_analyst_ratings_finnhub,   ticker),
    ]

    # ── Group 1b: FMP (deep financials + valuation metrics) ───────────────────
    fmp_labels = [
        "income_statement_fmp",
        "balance_sheet_fmp",
        "cash_flow_fmp",
        "key_metrics_fmp",
        "analyst_estimates_fmp",
    ]
    fmp_coros = [
        loop.run_in_executor(_DATA_EXECUTOR, get_income_statement_fmp,   ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_balance_sheet_fmp,      ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_cash_flow_fmp,          ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_key_metrics_fmp,        ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_analyst_estimates_fmp,  ticker),
    ]

    # ── Group 1c: Alpha Vantage (supplement / cross-check) ────────────────────
    av_labels = [
        "current_price_av",
        "company_overview_av",
        "income_statement_av",
        "eps_av",
    ]
    av_coros = [
        loop.run_in_executor(_DATA_EXECUTOR, get_current_price_alpha,      ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_company_overview_alpha,   ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_income_statement_alpha,   ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_earnings_per_share_alpha, ticker),
    ]

    # ── Group 1d: Polygon.io (ticker details + sector fix + news) ─────────────
    # get_ticker_details_polygon: company name, SIC code, sector, description, exchange
    # — this is the PRIMARY fix for the "sector not found" problem.
    polygon_labels = [
        "ticker_details_polygon",
        "historical_ohlcv_polygon",
        "recent_news_polygon",
    ]
    polygon_coros = [
        loop.run_in_executor(_DATA_EXECUTOR, get_ticker_details_polygon,   ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_historical_ohlcv_polygon, ticker),
        loop.run_in_executor(_DATA_EXECUTOR, get_recent_news_polygon,      ticker),
    ]

    # Launch all parallel groups simultaneously
    all_results = await asyncio.gather(
        *finnhub_coros, *fmp_coros, *av_coros, *polygon_coros,
        return_exceptions=True,
    )

    # Phase 1D: Log any exceptions from the gather so they appear in Cloud Run logs
    all_labels = finnhub_labels + fmp_labels + av_labels + polygon_labels
    for _lbl, _res in zip(all_labels, all_results):
        if isinstance(_res, Exception):
            logger.warning("[%s] Data gather exception in %s: %s", run_id, _lbl, _res)

    # Unpack results by group
    n_fh, n_fmp, n_av, n_pg = len(finnhub_labels), len(fmp_labels), len(av_labels), len(polygon_labels)
    finnhub_results = all_results[:n_fh]
    fmp_results     = all_results[n_fh:n_fh + n_fmp]
    av_results      = all_results[n_fh + n_fmp:n_fh + n_fmp + n_av]
    polygon_results = all_results[n_fh + n_fmp + n_av:]

    # ── Group 2: Parallel — SEC EDGAR (thread-safe rate limiter in sec_filings.py) ─
    # The _EDGAR_LOCK in sec_filings.py ensures the 0.11s inter-request delay is
    # respected even when calls run from multiple threads simultaneously.
    sec_calls = [
        ("sec_recent_filings",       lambda: get_recent_filings(ticker)),
        ("sec_sbc",                  lambda: get_specific_fact(ticker, "ShareBasedCompensation")),
        ("sec_revenue",              lambda: get_specific_fact(ticker, "Revenues")),
        ("sec_net_income",           lambda: get_specific_fact(ticker, "NetIncomeLoss")),
        ("sec_long_term_debt",       lambda: get_specific_fact(ticker, "LongTermDebt")),
        ("sec_insider_transactions", lambda: get_insider_transactions(ticker)),
    ]

    async def _sec_call(sec_label: str, fn) -> tuple:
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_DATA_EXECUTOR, fn), timeout=20
            )
        except asyncio.TimeoutError:
            result = {"error": "timeout after 20s — SEC EDGAR did not respond in time"}
        except Exception as e:
            result = {"error": str(e)}
        return sec_label, result

    sec_results = list(await asyncio.gather(
        *[_sec_call(lbl, fn) for lbl, fn in sec_calls]
    ))

    # ── Extract company name and sector from Polygon (primary source) ──────────
    company_name = ticker  # fallback: use ticker if name not resolvable
    sector_info = "Unknown"

    polygon_details_raw = polygon_results[0] if polygon_results else {}
    if not isinstance(polygon_details_raw, Exception) and isinstance(polygon_details_raw, dict):
        polygon_inner = polygon_details_raw.get("results", {})
        if isinstance(polygon_inner, dict):
            if polygon_inner.get("name"):
                company_name = polygon_inner["name"]
            if polygon_inner.get("sic_description"):
                sector_info = polygon_inner["sic_description"]

    # Fallback: try Finnhub quote result for company name
    if company_name == ticker:
        finnhub_quote_raw = finnhub_results[0] if finnhub_results else {}
        if not isinstance(finnhub_quote_raw, Exception) and isinstance(finnhub_quote_raw, dict):
            if finnhub_quote_raw.get("name"):
                company_name = finnhub_quote_raw["name"]

    # Fallback: try Alpha Vantage company overview for sector
    if sector_info == "Unknown":
        av_overview_raw = av_results[1] if len(av_results) > 1 else {}
        if not isinstance(av_overview_raw, Exception) and isinstance(av_overview_raw, dict):
            if av_overview_raw.get("Sector"):
                sector_info = av_overview_raw["Sector"]

    # ── Group 3: NewsAPI + OpenFIGI (run after company name resolved) ──────────
    # NewsAPI uses company_name for precise search; OpenFIGI maps ticker to FIGI.
    # Run in parallel as a second batch so company_name is already available.
    group3_labels = ["company_news_newsapi", "figi_mapping_openfigi"]
    group3_results = await asyncio.gather(
        loop.run_in_executor(
            _DATA_EXECUTOR, get_company_news_newsapi, company_name, ticker
        ),
        loop.run_in_executor(_DATA_EXECUTOR, get_figi_mapping, ticker),
        return_exceptions=True,
    )

    _sd_elapsed = (datetime.datetime.utcnow() - _sd_t0).total_seconds()
    logger.info("[%s] STEP 1a: Data gathering complete (%.1fs) — Company: %s | Sector: %s",
                run_id, _sd_elapsed, company_name, sector_info)

    # ── Format all results into a labelled text block for the LLM ─────────────
    def _fmt(label, result):
        if isinstance(result, Exception):
            return f"## {label}\n[ERROR: {result}]\n"
        if isinstance(result, dict) and "error" in result and len(result) <= 3:
            return f"## {label}\n[ERROR: {result['error']}]\n"
        return f"## {label}\n```json\n{_json.dumps(result, indent=2, default=str)}\n```\n"

    lines = [
        f"# PRE-GATHERED STRUCTURED DATA FOR: {ticker.upper()}",
        f"# COMPANY NAME: {company_name}  ← AGENTS: USE THIS FOR WEB SEARCHES, NOT THE TICKER",
        f"# SECTOR / INDUSTRY: {sector_info}",
        f"# Fetched directly by Python from APIs — DO NOT re-call these data sources.",
        f"# Analyst ratings provided by Finnhub. If [ERROR], search the web for current consensus.\n",
    ]
    for label, result in zip(finnhub_labels, finnhub_results):
        lines.append(_fmt(label, result))
    for label, result in zip(fmp_labels, fmp_results):
        lines.append(_fmt(label, result))
    for label, result in zip(av_labels, av_results):
        lines.append(_fmt(label, result))
    for label, result in zip(polygon_labels, polygon_results):
        lines.append(_fmt(label, result))
    for label, result in sec_results:
        lines.append(_fmt(label, result))
    for label, result in zip(group3_labels, group3_results):
        lines.append(_fmt(label, result))

    structured_text = "\n".join(lines)
    logger.info("[%s] Structured data gathered (%d chars)", run_id, len(structured_text))
    return structured_text, company_name


async def _gather_macro_data(run_id: str, topic: str = "") -> dict[str, str]:
    """
    Pre-fetch core macro background data for the macro pipeline.

    Returns a dict of {label: formatted_text} for use with _slice_macro_data().

    Data sources gathered in parallel:
      - FRED yield curve snapshot, recession indicators, FX rates, commodities,
        PMI, credit spreads (US background + global context)
      - World Bank cross-country macro snapshot (annual, 10 major economies)
      - OECD CLI + Economic Outlook projections (forward-looking)
      - IMF WEO projections (GDP, inflation, unemployment, current account)
      - ECB macro snapshot (policy rates, HICP, M3 — Eurozone)
      - Alpha Vantage commodity prices (WTI, Brent, gas, copper, wheat, corn)
      - Polygon FX snapshot (11 major pairs, previous-day close)
      - NewsAPI topic-specific news (if topic provided)
    """
    import json as _json
    from tools.macro_data import (
        get_yield_curve_snapshot, get_recession_indicators, get_multiple_series,
    )

    loop = asyncio.get_running_loop()
    logger.info("[%s] Gathering core macro data (FRED + WB + OECD + IMF + ECB + AV + Polygon + NewsAPI)...", run_id)

    # FRED FX series — 10 major currency pairs
    _FRED_FX_SERIES = [
        "DEXUSEU", "DEXJPUS", "DEXUSUK", "DTWEXBGS", "DEXSZUS",
        "DEXKOUS", "DEXINUS", "DEXMXUS", "DEXBZUS", "DEXCHUS",
    ]
    # FRED macro indicators — commodities, PMI, credit spreads
    _FRED_MACRO_SERIES = [
        "DCOILWTICO", "DCOILBRENTEU", "GOLDAMGBD228NLBM", "DHHNGSP",  # commodities
        "NAPM", "NMFBAI", "UMCSENT",  # leading indicators
        "BAMLH0A0HYM2", "BAMLC0A0CM",  # credit spreads
    ]

    # Build coroutines list — always include all sources
    coros = [
        loop.run_in_executor(_DATA_EXECUTOR, get_yield_curve_snapshot),
        loop.run_in_executor(_DATA_EXECUTOR, get_recession_indicators),
        loop.run_in_executor(_DATA_EXECUTOR, get_worldbank_macro_snapshot),
        loop.run_in_executor(_DATA_EXECUTOR, get_oecd_leading_indicators),
        loop.run_in_executor(_DATA_EXECUTOR, get_oecd_economic_outlook),
        loop.run_in_executor(_DATA_EXECUTOR, get_multiple_series, _FRED_FX_SERIES),
        loop.run_in_executor(_DATA_EXECUTOR, get_multiple_series, _FRED_MACRO_SERIES),
        loop.run_in_executor(_DATA_EXECUTOR, get_commodity_prices_alpha),
        loop.run_in_executor(_DATA_EXECUTOR, get_forex_snapshot_polygon),
    ]
    labels = [
        "yield_curve_snapshot",
        "recession_indicators",
        "worldbank_macro_snapshot",
        "oecd_leading_indicators",
        "oecd_economic_outlook",
        "fred_fx_rates",
        "fred_macro_indicators",
        "alpha_vantage_commodities",
        "polygon_fx_snapshot",
    ]

    # IMF WEO — try import; skip gracefully if module not yet deployed
    try:
        from tools.imf_data import get_imf_weo_snapshot
        coros.append(loop.run_in_executor(_DATA_EXECUTOR, get_imf_weo_snapshot))
        labels.append("imf_weo_snapshot")
    except ImportError:
        logger.warning("[%s] tools.imf_data not available — skipping IMF WEO", run_id)

    # ECB SDMX — try import; skip gracefully if module not yet deployed
    try:
        from tools.ecb_data import get_ecb_macro_snapshot
        coros.append(loop.run_in_executor(_DATA_EXECUTOR, get_ecb_macro_snapshot))
        labels.append("ecb_macro_snapshot")
    except ImportError:
        logger.warning("[%s] tools.ecb_data not available — skipping ECB data", run_id)

    # NewsAPI — conditional on topic
    if topic:
        coros.append(loop.run_in_executor(_DATA_EXECUTOR, get_topic_news_newsapi, topic))
        labels.append("topic_news_newsapi")

    results = await asyncio.gather(*coros, return_exceptions=True)

    # Build per-label formatted text dict
    macro_sections: dict[str, str] = {}
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            macro_sections[label] = f"## {label}\n[ERROR: {result}]\n"
        else:
            macro_sections[label] = (
                f"## {label}\n```json\n{_json.dumps(result, indent=2, default=str)}\n```\n"
            )

    total_chars = sum(len(v) for v in macro_sections.values())
    logger.info("[%s] Core macro data gathered (%d chars across %d sources)", run_id, total_chars, len(macro_sections))
    return macro_sections


# ── Compiler input sanitisation ───────────────────────────────────────────────

_PLACEHOLDER_PREFIXES_COMPILE = (
    "[AGENT TIMEOUT:", "[RATE LIMIT:", "[HTTP ERROR:", "[ERROR:", "[EMPTY RESPONSE:",
)

def _clean_for_compiler(label: str, output: str) -> str:
    """
    Replace error/timeout placeholders with a neutral note before they reach the
    report compiler. Passing raw placeholders to the compiler causes it to return
    0 output tokens — likely a model safety/coherence refusal when given malformed
    inputs. A clean note allows the compiler to skip the section gracefully.
    """
    if not output or any(output.strip().startswith(p) for p in _PLACEHOLDER_PREFIXES_COMPILE):
        return (
            f"[NOTE: {label} output was unavailable for this run "
            f"(agent timed out or errored). Omit this section from the final report "
            f"and note the gap in the Executive Summary.]"
        )
    return output


# ── Per-agent data slicing (Phase 2C) ──────────────────────────────────────────
# Each analyst receives only the structured-data sections relevant to their role.
# Reduces per-agent input tokens by 40–70% vs passing the full ~100 KB blob.

_ANALYST_SECTIONS: dict[str, list[str]] = {
    "fundamental-market": [
        # Price & market context
        "price_finnhub", "key_metrics_finnhub", "analyst_ratings_finnhub",
        "earnings_finnhub", "analyst_estimates_fmp",
        "current_price_av", "company_overview_av", "eps_av",
        "ticker_details_polygon", "recent_news_polygon",
        "company_news_newsapi", "figi_mapping_openfigi",
        # Financials — added for segment revenue & margin data needed in Section 2
        # (without this the agent searches the web for data that's already in the API)
        "income_statement_fmp",
    ],
    "fundamental-financials": [
        "financials_finnhub",  # quarterly data — complements FMP annual
        "income_statement_fmp", "balance_sheet_fmp", "cash_flow_fmp", "key_metrics_fmp",
        # income_statement_av removed — redundant with income_statement_fmp (FMP is more detailed)
        "sec_revenue", "sec_net_income", "sec_long_term_debt", "sec_sbc",
    ],
    "competitive-analyst": [
        "ticker_details_polygon", "recent_news_polygon",
        "company_overview_av", "analyst_ratings_finnhub",
        "sec_recent_filings", "company_news_newsapi",
        # Key metrics added — lets agent compare target company multiples without searching
        "key_metrics_finnhub", "key_metrics_fmp",
    ],
    "risk-analyst": [
        "key_metrics_finnhub", "financials_finnhub",
        "balance_sheet_fmp", "key_metrics_fmp",
        "sec_recent_filings", "sec_insider_transactions",
        "recent_news_polygon", "company_news_newsapi",
    ],
    "valuation-analyst": [
        "price_finnhub", "current_price_av",
        "historical_prices_2y_weekly_finnhub",
        # historical_ohlcv_polygon removed — duplicate of historical_prices_2y_weekly_finnhub
        "key_metrics_finnhub", "key_metrics_fmp",
        "analyst_estimates_fmp", "analyst_ratings_finnhub",
        "eps_av", "earnings_finnhub",
    ],
    "earnings-quality": [
        "financials_finnhub",
        "income_statement_fmp", "balance_sheet_fmp", "cash_flow_fmp",
        # income_statement_av kept here — earnings quality uses multi-source cross-checking
        "income_statement_av",
        "sec_sbc", "sec_revenue", "sec_net_income", "sec_insider_transactions",
    ],
}


# ── Per-agent macro data slicing ──────────────────────────────────────────────
# Each macro agent receives only the pre-gathered data sections relevant to its role.
# Prevents context bloat as new data sources (IMF, ECB, FX, commodities) are added.

_MACRO_AGENT_SECTIONS: dict[str, list[str]] = {
    # NOTE: "context-processor" is intentionally absent — it receives a compact manifest
    # via _macro_data_manifest() instead of full data, to avoid 700K+ token bloat.
    "macro-data-agent": [
        # Baseline context to know what's already gathered — NOT FX/commodities
        "yield_curve_snapshot", "recession_indicators",
        "worldbank_macro_snapshot", "oecd_leading_indicators", "oecd_economic_outlook",
        "imf_weo_snapshot", "topic_news_newsapi",
    ],
    "macro-analyst": [
        # Needs most data for comprehensive 8-section report
        "yield_curve_snapshot", "recession_indicators",
        "worldbank_macro_snapshot", "oecd_leading_indicators", "oecd_economic_outlook",
        "imf_weo_snapshot", "ecb_macro_snapshot",
        "fred_fx_rates", "fred_macro_indicators",
        "topic_news_newsapi",
    ],
    "quant-modeler-macro": [
        # Numerical data for regressions/models — NOT news, NOT World Bank (annual/lagged)
        "yield_curve_snapshot", "recession_indicators",
        "fred_fx_rates", "fred_macro_indicators",
        "ecb_macro_snapshot", "imf_weo_snapshot",
    ],
}


def _slice_macro_data(macro_data: dict, agent_key: str) -> str:
    """
    Filter the pre-gathered macro data dict by the agent's allowed sections.

    Args:
        macro_data: dict of {label: formatted_text} from _gather_macro_data()
        agent_key: Key into _MACRO_AGENT_SECTIONS (e.g., "macro-analyst")

    Returns:
        Formatted text block with only the relevant sections.
    """
    allowed = _MACRO_AGENT_SECTIONS.get(agent_key)
    if allowed is None:
        # Unknown agent — pass all data
        allowed = list(macro_data.keys())

    lines = [
        "# BACKGROUND MACRO DATA (pre-gathered by Python — DO NOT re-fetch)",
        "# Only sections relevant to your role are included below.\n",
    ]
    for label in allowed:
        if label in macro_data:
            lines.append(macro_data[label])
    return "\n".join(lines)


def _macro_data_manifest(macro_data: dict) -> str:
    """
    Returns a compact inventory of pre-gathered sections for the context processor.

    The context processor only needs to know WHICH sections are available (to identify gaps
    relative to the user's topic), not their full content. Full data is passed to downstream
    analysis agents via _slice_macro_data(). Passing the full data to the context processor
    caused 700K+ input tokens and consistent timeouts.
    """
    _SECTION_DESCRIPTIONS: dict[str, str] = {
        "yield_curve_snapshot":      "US Treasury yield curve (3M, 2Y, 5Y, 10Y, 30Y) + 2s10s and 3m10y spreads",
        "recession_indicators":      "FRED recession indicators: T10Y3M, Sahm Rule, UNRATE, PAYEMS, INDPRO, NFCI, STLFSI4",
        "worldbank_macro_snapshot":  "World Bank annual data: GDP growth, inflation, unemployment, current account, gov debt, trade — 10 major economies",
        "oecd_leading_indicators":   "OECD Composite Leading Indicators — 20 countries (monthly, 6-month horizon)",
        "oecd_economic_outlook":     "OECD GDP growth projections — major economies",
        "imf_weo_snapshot":          "IMF WEO forecasts: GDP, inflation, unemployment, current account — major economies",
        "ecb_macro_snapshot":        "ECB deposit rate, main refinancing rate, Eurozone HICP (headline + core), M3 money supply",
        "fred_fx_rates":             "FRED FX rates: EUR, JPY, GBP, CHF, KRW, INR, MXN, BRL, CNY, Trade-weighted USD",
        "fred_macro_indicators":     "FRED commodities (WTI, Brent, gold, gas), ISM Manufacturing + Services PMI, consumer sentiment, HY + IG credit spreads",
        "alpha_vantage_commodities": "AV monthly prices: WTI, Brent, natural gas, copper, wheat, corn (12-month history + YoY change)",
        "polygon_fx_snapshot":       "Polygon previous-day OHLC: 11 major currency pairs",
        "topic_news_newsapi":        "NewsAPI recent news articles for the topic",
    }
    available = [k for k in _SECTION_DESCRIPTIONS if k in macro_data]
    missing   = [k for k in _SECTION_DESCRIPTIONS if k not in macro_data]

    lines = [
        "# PRE-GATHERED MACRO DATA — AVAILABLE SECTIONS",
        "The following data sections are already gathered and will be passed to all downstream analysis agents.",
        "DO NOT search for any data already covered below. Focus tool calls on topic-specific gaps only.\n",
    ]
    for key in available:
        lines.append(f"- **{key}**: {_SECTION_DESCRIPTIONS[key]}")
    if missing:
        lines.append(f"\n_Sections not fetched this run (API failure or not applicable): {', '.join(missing)}_")
    return "\n".join(lines)


def _slice_structured_data(structured_data: str, include_sections: list[str]) -> str:
    """
    Extract only the specified ## sections from a structured_data block.
    The preamble (# header lines before the first ## section) is always preserved.
    Sections missing from structured_data are silently skipped.
    """
    # Split on "\n## " — first element is the preamble; rest are section blobs
    parts = structured_data.split("\n## ")
    preamble = parts[0]

    # Map section name → content (everything after the first newline in each blob)
    sections: dict[str, str] = {}
    for part in parts[1:]:
        nl = part.find("\n")
        if nl > 0:
            sections[part[:nl].strip()] = part[nl:]

    # Reassemble: preamble + only the requested sections (in the requested order)
    sliced = preamble
    for name in include_sections:
        if name in sections:
            sliced += f"\n## {name}{sections[name]}"
    return sliced


def _make_analyst_context(
    agent_key: str,
    ticker: str,
    company_name: str,
    run_id: str,
    structured_data: str,
    data_output: str,
    enriched_context_note: str,
) -> str:
    """Build a per-agent context string with only the relevant structured-data sections."""
    sections = _ANALYST_SECTIONS.get(agent_key, [])
    sliced = _slice_structured_data(structured_data, sections) if sections else structured_data
    return (
        f"EQUITY ANALYSIS REQUEST\n"
        f"Ticker: {ticker}\n"
        f"Company Name: {company_name} — USE THIS NAME FOR WEB SEARCHES, NOT THE TICKER\n"
        f"Run ID: {run_id}\n\n"
        f"STRUCTURED DATA FROM APIs (sections relevant to your role):\n{sliced}\n\n"
        f"COVERAGE LOG AND WEB SOURCES:\n{data_output}\n\n"
        + (f"ENRICHED USER CONTEXT (incorporate these focus areas):\n{enriched_context_note}\n\n"
           if enriched_context_note else "")
        + f"SECTOR NOTE: If sector/industry is not in the structured data above, "
        f"use search_web to find it — do not leave sector blank.\n\n"
        f"Please produce your assigned sections of the equity research memo."
    )


# ── Equity pipeline ────────────────────────────────────────────────────────────

async def _run_equity_pipeline(topic: str, run_id: str,
                               user_context: str = "",
                               run_stats: RunStats = None) -> str:
    """
    Run the full equity research pipeline and return the assembled report text.

    Sequence:
      1a. Structured data (Python — Finnhub + FMP + AV + Polygon + EDGAR in parallel)
      1b. Context Processor (optional — only if user_context provided; runs FIRST so
          it can pass Data Harvester Guidance to the next step)
      1c. Data Harvester (serial — web search + coverage log; receives enriched context
          note so searches target gaps identified by the Context Processor)
      2.  6 analyst agents in parallel (fundamental-market, fundamental-financials,
          competitive, risk, valuation, earnings-quality)
      3.  Quant Modeler (after all 6 analysts complete)
      4.  Report Compiler (only after quant completes)
      5.  Review loop (fact checker + review agent, up to max_passes)
      6.  Executive Summary (orchestrator)
    """

    # ── Step 1a: Structured data — Python calls APIs directly ─────────────────
    # Replaces ~30 sequential LLM tool calls with parallel Python calls.
    # Top-level 120s timeout ensures the pipeline never stalls here.
    _sd_start = datetime.datetime.utcnow()
    try:
        structured_data, company_name = await asyncio.wait_for(
            _gather_structured_data(topic, run_id), timeout=120
        )
        if run_stats is not None:
            run_stats.structured_data_duration_s = (
                datetime.datetime.utcnow() - _sd_start).total_seconds()
            run_stats.structured_data_status = "ok"
    except asyncio.TimeoutError:
        logger.warning("[%s] TIMEOUT: _gather_structured_data exceeded 120s — continuing with placeholder", run_id)
        structured_data = (
            f"# PRE-GATHERED STRUCTURED DATA FOR: {topic.upper()}\n"
            f"[TIMEOUT: Structured data APIs did not respond within 120s. "
            f"All agents should use web_search to source financial data for this run.]\n"
        )
        company_name = topic  # fallback: use ticker
        if run_stats is not None:
            run_stats.structured_data_duration_s = 120.0
            run_stats.structured_data_status = "timeout"

    # ── Compact source summary — shared by Context Processor and Data Harvester ──
    # Both agents need to know what structured data has been gathered, but neither
    # needs the raw JSON values (~500K tokens for large-cap stocks like AAPL).
    # Passing the full structured_data causes 0 output tokens and an error.
    # This compact description is sufficient: it tells agents what's available
    # so they can identify gaps and avoid re-calling APIs that already ran.
    _harvester_sources_summary = (
        "Pre-gathered structured data sources (DO NOT re-call any of these APIs):\n"
        "  • Finnhub:       current quote, 2yr weekly OHLCV, financials, key metrics, "
        "earnings history, analyst ratings\n"
        "  • FMP:           income statement (5yr), balance sheet (5yr), cash flow (5yr), "
        "key metrics, analyst estimates\n"
        "  • Alpha Vantage: current price, company overview, income statement, EPS\n"
        "  • Polygon:       ticker details (name/sector/SIC), historical OHLCV, recent news\n"
        "  • SEC EDGAR:     recent filings, SBC, revenues, net income, LT debt, "
        "insider transactions\n"
        "  • NewsAPI:       recent news articles from WSJ, FT, Bloomberg, Reuters, Barron's "
        "(last 30 days — premium financial outlets only)\n"
        "  • OpenFIGI:      FIGI instrument mapping — security type, exchange, market sector\n"
        "Total: ~22 pre-gathered structured data sources available to all analysts."
    )

    # ── Step 1b: Context Processor — only runs if user provided context ──────────
    # Runs FIRST (before Data Harvester) so its Data Harvester Guidance section
    # can direct the harvester's searches toward gaps relevant to the user's focus.
    # Uses the compact source summary (not full structured_data) — passing the full
    # raw JSON payload (~500K tokens for large-cap stocks) causes 0 output tokens.
    # The context processor identifies WHAT to research, not the raw values themselves.
    enriched_context_note = ""
    context_stripped = user_context.strip() if user_context else ""
    if context_stripped and context_stripped.lower() not in ("none", "n/a", ""):
        logger.info("[%s] STEP 1b: Context Processor (enriching user context — runs before Data Harvester)...", run_id)
        enriched_context_note = await _run_agent(
            context_processor,
            (
                f"USER CONTEXT:\n{user_context}\n\n"
                f"Company Name: {company_name} | Ticker: {topic}\n"
                f"Run ID: {run_id}\n\n"
                f"STRUCTURED DATA ALREADY GATHERED (compact summary — full JSON available to analysts):\n"
                f"{_harvester_sources_summary}\n\n"
                f"Identify gaps relevant to the user context. Fetch missing data within "
                f"your tool budget. Return the ENRICHED CONTEXT NOTE including a "
                f"DATA HARVESTER GUIDANCE section with specific searches the Data Harvester "
                f"should prioritize to fill gaps relevant to the user's focus."
            ),
            "context-processor",
            run_id,
            timeout_seconds=_CONTEXT_PROCESSOR_TIMEOUT,
            run_stats=run_stats,
        )
        logger.info("[%s] Context processor complete (%d chars)", run_id, len(enriched_context_note))
    else:
        logger.info("[%s] No user context provided — skipping context_processor", run_id)

    # ── Step 1c: Data Harvester LLM — web search + Coverage Log only ─────────
    logger.info("[%s] STEP 1c: Data Harvester (web search + coverage log — ~5-10 min)...", run_id)
    data_output = await _run_agent(
        data_harvester,
        (
            f"Company Name: {company_name} | Ticker: {topic}\n"
            f"NOTE: For all searches and queries, use '{company_name}' not the ticker '{topic}'.\n\n"
            f"STRUCTURED DATA ALREADY GATHERED (compact summary — full JSON available to analysts):\n"
            f"{_harvester_sources_summary}\n\n"
            + (
                f"ENRICHED CONTEXT NOTE FROM CONTEXT PROCESSOR:\n{enriched_context_note}\n\n"
                f"IMPORTANT: Read the 'Data Harvester Guidance' section above and prioritize "
                f"those searches first before running general coverage searches.\n\n"
                if enriched_context_note else ""
            )
            + f"---\n\n"
            f"Your tasks (web search + Coverage Log only):\n"
            f"1. Use search_academic_core, search_web, search_news, and search_analyst_reports "
            f"to find unstructured sources. Stop each category once the target count is reached:\n"
            f"   - News/media articles: target ≥10 (WSJ, FT, Bloomberg, Reuters, Barron's)\n"
            f"   - Academic/expert sources: target ≥5\n"
            f"   - Competitor primary sources: target ≥5\n"
            f"   Once all three categories reach their targets, STOP searching immediately.\n"
            f"2. Build the Coverage Log listing the ~20 pre-gathered structured sources above "
            f"PLUS all web sources you found.\n"
            f"3. Run the Coverage Validator on the combined source list.\n"
            f"4. Build the Source Discrepancies table (if source counts differ materially across "
            f"databases, note it; otherwise write 'No source discrepancies identified.').\n"
            f"5. Return: Coverage Log, Coverage Validator, Source Discrepancies, Coverage Note.\n\n"
            f"Run ID: {run_id}"
        ),
        "data-harvester",
        run_id,
        timeout_seconds=_DATA_HARVESTER_TIMEOUT,
        run_stats=run_stats,
    )

    # ── Step 2: Six analyst agents in parallel (per-agent data slicing) ────────
    # PIPELINE GATE: asyncio.gather() blocks until ALL 6 agents have responded
    # (or timed out/errored). The report compiler NEVER runs until this gate clears.
    # Each agent receives only the structured-data sections relevant to its role
    # (Phase 2C), reducing input tokens by 40–70% vs passing the full ~100 KB blob.
    _ctx = dict(
        ticker=topic, company_name=company_name, run_id=run_id,
        structured_data=structured_data, data_output=data_output,
        enriched_context_note=enriched_context_note,
    )

    logger.info("[%s] STEP 2: Running 6 analyst agents in parallel (~5-12 min)...", run_id)
    _analyst_results = await asyncio.gather(
        _run_agent(fundamental_analyst_market,    _make_analyst_context("fundamental-market",     **_ctx), "fundamental-market",     run_id, timeout_seconds=_FUNDAMENTAL_MARKET_TIMEOUT,     run_stats=run_stats),
        _run_agent(fundamental_analyst_financials, _make_analyst_context("fundamental-financials", **_ctx), "fundamental-financials", run_id, timeout_seconds=_FUNDAMENTAL_FINANCIALS_TIMEOUT, run_stats=run_stats),
        _run_agent(competitive_analyst,            _make_analyst_context("competitive-analyst",    **_ctx), "competitive-analyst",    run_id, timeout_seconds=_COMPETITIVE_ANALYST_TIMEOUT,    run_stats=run_stats),
        _run_agent(risk_analyst,                   _make_analyst_context("risk-analyst",           **_ctx), "risk-analyst",           run_id, timeout_seconds=_RISK_ANALYST_TIMEOUT,            run_stats=run_stats),
        _run_agent(valuation_analyst,              _make_analyst_context("valuation-analyst",      **_ctx), "valuation-analyst",      run_id, timeout_seconds=_VALUATION_ANALYST_TIMEOUT,       run_stats=run_stats),
        _run_agent(earnings_quality_agent,         _make_analyst_context("earnings-quality",       **_ctx), "earnings-quality",       run_id, timeout_seconds=_EARNINGS_QUALITY_TIMEOUT,        run_stats=run_stats),
        return_exceptions=True,
    )
    logger.info("[%s] STEP 2: All 6 analysts complete — proceeding to quant modeler", run_id)

    # Convert any exception results to placeholder strings (defense-in-depth)
    _analyst_labels = ["fundamental-market", "fundamental-financials", "competitive-analyst",
                       "risk-analyst", "valuation-analyst", "earnings-quality"]
    _processed = []
    for _lbl, _res in zip(_analyst_labels, _analyst_results):
        if isinstance(_res, Exception):
            logger.error("[%s] %s raised %s: %s", run_id, _lbl, type(_res).__name__, _res)
            _processed.append(f"[ERROR: {_lbl} failed: {_res}]")
        else:
            _processed.append(_res)
    # ── Retry failed analysts (up to _MAX_ANALYST_RETRIES rounds) ────────────
    _MAX_ANALYST_RETRIES = CONFIG.get("retry", {}).get("max_analyst_retries", 3)
    if _MAX_ANALYST_RETRIES > 0:
        _agent_retry_map = {
            0: (fundamental_analyst_market,    _FUNDAMENTAL_MARKET_TIMEOUT),
            1: (fundamental_analyst_financials, _FUNDAMENTAL_FINANCIALS_TIMEOUT),
            2: (competitive_analyst,           _COMPETITIVE_ANALYST_TIMEOUT),
            3: (risk_analyst,                  _RISK_ANALYST_TIMEOUT),
            4: (valuation_analyst,             _VALUATION_ANALYST_TIMEOUT),
            5: (earnings_quality_agent,        _EARNINGS_QUALITY_TIMEOUT),
        }
        for _retry_round in range(1, _MAX_ANALYST_RETRIES + 1):
            _failed_indices = [i for i, out in enumerate(_processed) if _is_placeholder(out)]
            if not _failed_indices:
                break
            logger.info(
                "[%s] ANALYST RETRY round %d/%d: %d failed — retrying: %s",
                run_id, _retry_round, _MAX_ANALYST_RETRIES,
                len(_failed_indices),
                [_analyst_labels[i] for i in _failed_indices],
            )
            _retry_tasks = []
            for i in _failed_indices:
                _agent, _timeout = _agent_retry_map[i]
                _retry_tasks.append(
                    _run_agent(
                        _agent,
                        _make_analyst_context(_analyst_labels[i], **_ctx),
                        f"{_analyst_labels[i]}-retry-{_retry_round}",
                        run_id,
                        timeout_seconds=_timeout,
                        run_stats=run_stats,
                    )
                )
            _retry_results = await asyncio.gather(*_retry_tasks, return_exceptions=True)
            for j, i in enumerate(_failed_indices):
                _rr = _retry_results[j]
                _rlbl = _analyst_labels[i]
                if isinstance(_rr, Exception):
                    logger.error("[%s] %s retry-%d failed: %s", run_id, _rlbl, _retry_round, _rr)
                elif not _is_placeholder(_rr):
                    logger.info("[%s] %s retry-%d succeeded (%d chars)", run_id, _rlbl, _retry_round, len(_rr))
                    _processed[i] = _rr
                else:
                    logger.warning("[%s] %s retry-%d still placeholder", run_id, _rlbl, _retry_round)

    (fundamental_market_out, fundamental_financials_out,
     competitive_out, risk_out, valuation_out, earnings_out) = _processed

    # Quality gate: flag runs where too many analysts returned placeholder output
    _placeholder_count = count_analyst_placeholders(_processed)
    if run_stats is not None:
        run_stats.analyst_placeholder_count = _placeholder_count
    _quality_warning = ""
    if _placeholder_count > 3:
        _quality_warning = (
            f"\n\n> ⚠️ **QUALITY WARNING**: {_placeholder_count}/6 analyst agents returned "
            f"placeholder outputs (timeout/rate-limit/error). This report is significantly "
            f"incomplete. Check the debug report for details.\n\n"
        )
        logger.warning("[%s] %d/6 analysts returned placeholders", run_id, _placeholder_count)

    # ── Step 3: Quant Modeler ──────────────────────────────────────────────────
    # IMPORTANT: Do NOT pass the full structured_data here.
    # For large-cap stocks the full payload (20+ raw API JSON blobs, financial
    # statements, SEC filings, news articles) can exceed 500K input tokens,
    # which causes the model to return 0 output tokens and error.
    # The quant agent only needs price/volume/metrics data — extract those sections.
    logger.info("[%s] STEP 3: Quant Modeler (technical indicators, beta, VaR, risk metrics)...", run_id)

    # Sections the quant modeler actually needs (price, OHLCV, metrics, earnings)
    # Everything else (income statements, balance sheets, SEC filings, news, FIGI)
    # is not used for technical / statistical analysis and bloats the context.
    _QUANT_SECTIONS = {
        "price_finnhub",
        "historical_prices_2y_weekly_finnhub",
        "key_metrics_finnhub",
        "earnings_finnhub",
        "analyst_ratings_finnhub",
        "historical_ohlcv_polygon",
        "ticker_details_polygon",
        "key_metrics_fmp",
    }

    def _extract_structured_sections(text: str, keep: set) -> str:
        """
        Extract only the named ## sections from a structured data block.
        Preserves the header lines (everything before the first ## section).
        """
        import re as _re
        parts = _re.split(r'\n(?=## )', text)
        header_parts = [p for p in parts if not p.startswith("## ")]
        kept_parts   = [
            p for p in parts
            if p.startswith("## ") and any(
                p.startswith(f"## {name}") for name in keep
            )
        ]
        result = "\n".join(header_parts + kept_parts)
        original_len = len(text)
        kept_len     = len(result)
        logger.info(
            "[%s] quant structured data: %d → %d chars (%.0f%% of full payload)",
            run_id, original_len, kept_len,
            100.0 * kept_len / original_len if original_len else 0,
        )
        return result

    quant_structured_data = _extract_structured_sections(structured_data, _QUANT_SECTIONS)

    quant_context = (
        f"EQUITY QUANT ANALYSIS REQUEST\n"
        f"Ticker / Company: {topic} ({company_name})\n"
        f"Run ID: {run_id}\n\n"
        f"PRICE & METRICS DATA FROM APIs:\n{quant_structured_data}\n\n"
        f"FUNDAMENTAL ANALYSIS (THESIS & MARKET):\n{fundamental_market_out}\n\n"
        f"FUNDAMENTAL ANALYSIS (FINANCIALS):\n{fundamental_financials_out}\n\n"
        f"VALUATION & SCENARIOS ANALYSIS:\n{valuation_out}\n\n"
        f"Produce the Quant Dashboard (technical indicators, statistical models, risk metrics)."
    )
    quant_out = await _run_agent(quant_modeler_equity, quant_context, "quant-equity",
                                  run_id, timeout_seconds=_QUANT_EQUITY_TIMEOUT,
                                  run_stats=run_stats)

    # ── Step 4: Report Compiler ────────────────────────────────────────────────
    # PIPELINE GATE: only reached after all 6 analysts AND quant modeler have completed.
    # Sanitise any error/timeout placeholders before passing to the compiler.
    # Raw placeholders cause the compiler to return 0 output tokens (model refusal).
    logger.info("[%s] STEP 4: Report Compiler (assembling 21-section memo)...", run_id)
    compile_context = (
        f"REPORT COMPILATION REQUEST\n"
        f"Ticker / Company: {topic} ({company_name})\n"
        f"Run ID: {run_id}\n\n"
        + _quality_warning
        + f"Assemble the following analyst outputs into the final 21-section equity research memo "
        f"in the exact required output sequence. Do not add new analysis — compile only.\n\n"
        f"--- FUNDAMENTAL ANALYSIS (SECTIONS 1-2: THESIS & MARKET) ---\n"
        f"{_clean_for_compiler('Fundamental Market', fundamental_market_out)}\n\n"
        f"--- FUNDAMENTAL ANALYSIS (SECTIONS 9,11,12,13: FINANCIALS) ---\n"
        f"{_clean_for_compiler('Fundamental Financials', fundamental_financials_out)}\n\n"
        f"--- COMPETITIVE & STRATEGIC ANALYSIS ---\n"
        f"{_clean_for_compiler('Competitive Analyst', competitive_out)}\n\n"
        f"--- RISK & QUALITY ANALYSIS ---\n"
        f"{_clean_for_compiler('Risk Analyst', risk_out)}\n\n"
        f"--- VALUATION & SCENARIOS ANALYSIS ---\n"
        f"{_clean_for_compiler('Valuation Analyst', valuation_out)}\n\n"
        f"--- EARNINGS QUALITY & ALPHA SIGNALS ---\n"
        f"{_clean_for_compiler('Earnings Quality', earnings_out)}\n\n"
        f"--- QUANT DASHBOARD ---\n"
        f"{_clean_for_compiler('Quant Modeler', quant_out)}\n"
    )
    compiled = await _run_agent(report_compiler, compile_context, "report-compiler",
                                 run_id, run_stats=run_stats)

    # ── Step 5: Review loop ────────────────────────────────────────────────────
    review_notes_text = ""
    try:
        # Guard: skip the review loop if the compiled report is empty or a placeholder.
        # An empty/placeholder compiled report means the report-compiler itself failed;
        # running the fact-checker and review-agent against it only produces "content
        # is missing" apologies that get fed back to a compiler that still has nothing
        # to work with — wasting 3 passes and producing the wrong exec-summary output.
        if _is_placeholder(compiled):
            logger.warning("[%s] compiled report is empty/placeholder — skipping review loop", run_id)
            review_notes_text = (
                f"\n\n---\n\n## Review Notes\n\n"
                f"*The review loop was skipped because the compiled report was empty or "
                f"contained only placeholder content (the report-compiler agent did not "
                f"return usable output). Check the debug report for which agents failed.*\n"
            )
        else:
            for pass_num in range(1, _MAX_REVIEW_PASSES + 1):
                logger.info("[%s] STEP 5: Review pass %d/%d — fact-checker + review-agent running in parallel...",
                            run_id, pass_num, _MAX_REVIEW_PASSES)
                # Phase 2E: run fact checker and review agent in parallel — they are independent
                _review_msg = f"Review the following equity research report for {topic} ({company_name}):\n\n{compiled}"
                fact_result, review_result = await asyncio.gather(
                    _run_agent(fact_checker,  _review_msg, f"fact-checker-pass-{pass_num}",  run_id, run_stats=run_stats),
                    _run_agent(review_agent,  _review_msg, f"review-agent-pass-{pass_num}",  run_id, run_stats=run_stats),
                )

                fact_passed = "PASS" in fact_result.upper()
                review_passed = "PASS" in review_result.upper()

                if fact_passed and review_passed:
                    logger.info("[%s] STEP 5: Review pass %d/%d PASSED ✓ — proceeding to executive summary",
                                run_id, pass_num, _MAX_REVIEW_PASSES)
                    break
                logger.info("[%s] STEP 5: Review pass %d/%d verdict — fact-checker: %s | review-agent: %s — re-compiling...",
                            run_id, pass_num, _MAX_REVIEW_PASSES,
                            "PASS" if fact_passed else "FAIL",
                            "PASS" if review_passed else "FAIL")

                if pass_num < _MAX_REVIEW_PASSES:
                    # Re-compile with feedback so the next pass reviews the improved version.
                    # Only append original analyst outputs when the compiled report is
                    # empty/placeholder — if it is already a full report (> 1 000 chars),
                    # adding ~120 K of analyst text doubles the context size and can push
                    # the model past its effective output range, causing it to return a
                    # tiny stub (< 200 chars) that silently replaces the good compiled report.
                    if _is_placeholder(compiled) or len(compiled) < 1_000:
                        analyst_rebuild = (
                            f"--- ORIGINAL ANALYST OUTPUTS (use to rebuild missing sections) ---\n"
                            f"--- FUNDAMENTAL ANALYSIS (SECTIONS 1-2: THESIS & MARKET) ---\n{fundamental_market_out}\n\n"
                            f"--- FUNDAMENTAL ANALYSIS (SECTIONS 9,10,11,12,13: FINANCIALS) ---\n{fundamental_financials_out}\n\n"
                            f"--- COMPETITIVE & STRATEGIC ANALYSIS ---\n{competitive_out}\n\n"
                            f"--- RISK & QUALITY ANALYSIS ---\n{risk_out}\n\n"
                            f"--- VALUATION & SCENARIOS ANALYSIS ---\n{valuation_out}\n\n"
                            f"--- EARNINGS QUALITY & ALPHA SIGNALS ---\n{earnings_out}\n\n"
                            f"--- QUANT DASHBOARD ---\n{quant_out}\n"
                        )
                        logger.info("[%s] Pass %d: compiled is placeholder — including original analyst outputs for rebuild", run_id, pass_num)
                    else:
                        analyst_rebuild = ""
                        logger.info("[%s] Pass %d: compiled is %d chars — omitting analyst outputs from revise context", run_id, pass_num, len(compiled))
                    revise_context = (
                        f"REVISION REQUEST — Pass {pass_num}\n"
                        f"Ticker / Company: {topic} ({company_name})\n\n"
                        f"The following issues were found in the compiled report. "
                        f"Revise and reassemble the full report addressing all issues.\n\n"
                        f"FACT CHECKER FEEDBACK:\n{fact_result}\n\n"
                        f"REVIEW AGENT FEEDBACK:\n{review_result}\n\n"
                        f"COMPILED REPORT:\n{compiled}\n\n"
                        f"{analyst_rebuild}"
                    )
                    new_compiled = await _run_agent(
                        report_compiler, revise_context, f"report-compiler-pass-{pass_num}",
                        run_id, run_stats=run_stats,
                    )
                    # Guard: if the revised output is tiny (model hit context/output limits
                    # or returned a "no changes needed" stub), keep the previous compiled
                    # report rather than replacing it with a stub and breaking the pipeline.
                    if _is_placeholder(new_compiled) or len(new_compiled) < 1_000:
                        logger.warning(
                            "[%s] Pass %d: compiler returned tiny output (%d chars) — keeping previous (%d chars) and exiting loop",
                            run_id, pass_num, len(new_compiled), len(compiled),
                        )
                        break
                    compiled = new_compiled
                else:
                    # Max passes reached — append Review Notes to final report
                    logger.info("[%s] Max review passes reached — appending Review Notes", run_id)
                    review_notes_text = (
                        f"\n\n---\n\n## Review Notes\n\n"
                        f"*This report reached the maximum number of review passes ({_MAX_REVIEW_PASSES}). "
                        f"The following issues were flagged but not fully resolved:*\n\n"
                        f"**Fact Checker (Pass {pass_num}):**\n{fact_result}\n\n"
                        f"**Review Agent (Pass {pass_num}):**\n{review_result}\n"
                    )
    except Exception as review_err:
        logger.error("[%s] Review loop error: %s — using unreviewed report", run_id, review_err)
        review_notes_text = (
            f"\n\n---\n\n## Review Notes\n\n"
            f"*The review loop encountered an error and could not complete: {review_err}. "
            f"This report has not been fully fact-checked or reviewed.*\n"
        )

    # ── Step 6: Executive Summary (orchestrator writes it last, up to 3 attempts) ─
    logger.info("[%s] STEP 6: Executive Summary (orchestrator)...", run_id)
    exec_context = (
        f"EXECUTIVE SUMMARY REQUEST\n"
        f"Ticker / Company: {topic} ({company_name})\n\n"
        f"The full equity research report is below. Write only the Executive Summary "
        f"section to appear at the very top of the final document.\n\n"
        f"COMPILED REPORT:\n{compiled}"
    )
    _MAX_EXEC_RETRIES = CONFIG.get("retry", {}).get("max_exec_summary_retries", 3)
    exec_summary = ""
    for _exec_attempt in range(1, _MAX_EXEC_RETRIES + 1):
        _exec_label = "exec-summary" if _exec_attempt == 1 else f"exec-summary-attempt-{_exec_attempt}"
        exec_summary = await _run_agent(
            research_orchestrator, exec_context, _exec_label, run_id, run_stats=run_stats
        )
        if not _is_placeholder(exec_summary):
            if _exec_attempt > 1:
                logger.info("[%s] exec-summary succeeded on attempt %d", run_id, _exec_attempt)
            break
        logger.warning(
            "[%s] exec-summary attempt %d/%d failed — %s",
            run_id, _exec_attempt, _MAX_EXEC_RETRIES,
            "retrying" if _exec_attempt < _MAX_EXEC_RETRIES else "giving up",
        )
    if _is_placeholder(exec_summary):
        logger.error("[%s] exec-summary failed after %d attempts — sending report without executive summary",
                     run_id, _MAX_EXEC_RETRIES)
        exec_summary = (
            "## Executive Summary\n\n"
            "*Executive summary unavailable — the orchestrator could not produce a summary "
            f"after {_MAX_EXEC_RETRIES} attempts. Refer to individual sections below.*\n"
        )

    # ── Assemble final report ──────────────────────────────────────────────────
    cost_section = format_cost_summary(run_stats) if run_stats is not None else ""
    return f"{exec_summary}\n\n---\n\n{compiled}{review_notes_text}{cost_section}"


# ── Macro pipeline ─────────────────────────────────────────────────────────────

async def _run_macro_pipeline(topic: str, run_id: str,
                              user_context: str = "",
                              run_stats: "RunStats" = None) -> str:
    """
    Run the full macro/thematic research pipeline and return the assembled report text.

    Sequence:
      1a. Core FRED data (Python direct)
      1b. Context Processor (optional — runs FIRST so Data Harvester Guidance
          can direct the Macro Data Agent's searches toward the user's focus)
      1c. Macro Data Agent (LLM — receives enriched context note; topic-specific
          data + web search guided by user focus)
      1d. Macro Source Validator (validates source-topic alignment; fills gaps)
      2.  Macro Analyst (8-section report including Literature Review)
      3.  Quant Modeler (Macro) (geography-aware econometric models)
      4.  Macro Report Compiler (assembles 8 sections + appendices)
      5.  Review loop (fact checker + review agent, up to max_passes)
    """

    # ── Step 1a: Core macro data — Python fetches directly ──────────────────
    # 90s top-level timeout: covers FRED + World Bank + OECD + IMF + ECB + AV + Polygon.
    logger.info("[%s] STEP 1a: Fetching macro data (FRED + WB + OECD + IMF + ECB + AV + Polygon)...", run_id)
    _macro_gather_start = datetime.datetime.utcnow()
    try:
        macro_data_dict = await asyncio.wait_for(
            _gather_macro_data(run_id, topic=topic), timeout=90
        )
        if run_stats is not None:
            run_stats.structured_data_duration_s = (
                datetime.datetime.utcnow() - _macro_gather_start
            ).total_seconds()
            run_stats.structured_data_status = "ok"
    except asyncio.TimeoutError:
        logger.warning("[%s] TIMEOUT: _gather_macro_data exceeded 90s — continuing with placeholder", run_id)
        if run_stats is not None:
            run_stats.structured_data_duration_s = 90.0
            run_stats.structured_data_status = "timeout"
        macro_data_dict = {
            "_timeout": (
                "## _timeout\n[TIMEOUT: Background macro data APIs did not respond within 90s. "
                "Use web_search to source macro data for this run.]\n"
            ),
        }
    logger.info("[%s] STEP 1a: Macro data complete (%d sources)", run_id, len(macro_data_dict))

    # ── Step 1b: Context Processor — enrich user context (optional) ──────────
    # Runs FIRST so the Data Harvester Guidance section can direct the Macro Data
    # Agent's searches toward gaps relevant to the user's focus.
    enriched_context_note = ""
    if user_context and user_context.strip().lower() not in ("", "none", "n/a"):
        logger.info("[%s] STEP 1b: Context Processor (enriching user context — runs before Macro Data Agent)...", run_id)
        enriched_context_note = await _run_agent(
            context_processor,
            (
                f"USER CONTEXT:\n{user_context}\n\n"
                f"PRE-GATHERED MACRO DATA INVENTORY:\n{_macro_data_manifest(macro_data_dict)}\n\n"
                f"Topic: {topic}\nRun ID: {run_id}\n\n"
                f"Identify gaps in the data relative to the user's focus. "
                f"Fetch missing data within your budget. Return the ENRICHED CONTEXT NOTE "
                f"including a DATA HARVESTER GUIDANCE section with specific searches the "
                f"Macro Data Agent should prioritize to fill gaps relevant to the user's focus."
            ),
            "context-processor",
            run_id,
            timeout_seconds=_CONTEXT_PROCESSOR_TIMEOUT,
            run_stats=run_stats,
        )
        logger.info("[%s] Context processor complete (%d chars)", run_id, len(enriched_context_note))
    else:
        logger.info("[%s] STEP 1b: No user context — skipping context processor", run_id)

    # ── Step 1c: Macro Data Agent LLM — topic-specific data + web search ────
    logger.info("[%s] STEP 1c: Macro Data Agent (web search, topic-specific data + sources — ~5-10 min)...", run_id)
    data_output = await _run_agent(
        macro_data_agent,
        (
            f"PRE-GATHERED MACRO DATA (background context — DO NOT re-fetch):\n\n"
            f"{_slice_macro_data(macro_data_dict, 'macro-data-agent')}\n\n"
            f"---\n\n"
            f"Macro research topic: {topic}\n\n"
            + (
                f"ENRICHED CONTEXT NOTE FROM CONTEXT PROCESSOR:\n{enriched_context_note}\n\n"
                f"IMPORTANT: Read the 'Data Harvester Guidance' section above and prioritize "
                f"those searches first before running general coverage searches.\n\n"
                if enriched_context_note else ""
            )
            + f"IMPORTANT: Focus all data gathering on the topic's primary geography and theme. "
            f"Do NOT expand on the US data above unless it is a direct driver of this topic. "
            f"For non-US topics (e.g., European, German, UK, EM), source data from the relevant "
            f"national/regional statistics agencies, central banks, and research institutions "
            f"(Eurostat, ECB, Bundesbank, ONS, IMF, OECD, BIS) via web search — not FRED.\n\n"
            f"Your tasks:\n"
            f"1. If the topic is US-focused: fetch relevant FRED series not already in the data above.\n"
            f"   If the topic is non-US: use web_search to find the equivalent local data (GDP, CPI, "
            f"   policy rate, employment, PMI) from authoritative sources for the correct geography.\n"
            f"2. Use web_search/search_news to find sources. Stop each category when target is reached:\n"
            f"   - Recent news articles: target ≥5 (FT, WSJ, Bloomberg, Reuters, The Economist)\n"
            f"   - Relevant central bank documents (ECB, BoE, Fed, BoJ — match the topic's geography): target ≥2\n"
            f"   - Academic/research institution papers: target ≥2\n"
            f"   Once all categories reach their targets, STOP searching.\n"
            f"3. Return: Data Package (topic-relevant data table), Source Log, Coverage Summary.\n\n"
            f"Run ID: {run_id}"
        ),
        "macro-data-agent",
        run_id,
        timeout_seconds=_MACRO_DATA_AGENT_TIMEOUT,
        run_stats=run_stats,
    )

    # ── Step 1d: Macro Source Validator — validate + augment source package ──
    logger.info("[%s] STEP 1d: Macro Source Validator (validating + augmenting source package)...", run_id)
    source_validator_out = await _run_agent(
        macro_source_validator,
        (
            f"Macro research topic: {topic}\n\n"
            f"MACRO DATA AGENT OUTPUT (sources to validate):\n{data_output}\n\n"
            f"Validate whether the gathered sources match the topic's primary geography and theme. "
            f"Issue targeted additional searches for any significant gaps in academic coverage, "
            f"historical analog papers, or central bank publications. "
            f"Return the Augmented Source Package and Validation Summary.\n\n"
            f"Run ID: {run_id}"
        ),
        "macro-source-validator",
        run_id,
        timeout_seconds=_MACRO_SOURCE_VALIDATOR_TIMEOUT,
        run_stats=run_stats,
    )

    # ── Step 2: Macro Analyst ──────────────────────────────────────────────────
    analysis_context = (
        f"MACRO RESEARCH REQUEST\n"
        f"Topic: {topic}\n"
        f"Run ID: {run_id}\n\n"
        f"IMPORTANT SCOPE: Stay focused on the topic's primary geography and theme. "
        f"The US core macro data below is background context — include it only where a direct "
        f"cross-market transmission mechanism to the topic can be stated.\n\n"
        f"PRE-GATHERED MACRO DATA (background context — DO NOT re-fetch):\n"
        f"{_slice_macro_data(macro_data_dict, 'macro-analyst')}\n\n"
        f"MACRO DATA AGENT OUTPUT (topic-specific data + web sources):\n{data_output}\n\n"
        f"MACRO SOURCE VALIDATOR OUTPUT (validated + augmented source package):\n"
        f"{source_validator_out}\n\n"
        + (f"ENRICHED USER CONTEXT:\n{enriched_context_note}\n\n" if enriched_context_note else "")
        + f"Produce the 8-section macro research report (Sections 1–7 as specified, plus "
        f"Section 8 Literature Review synthesizing the academic sources in the validated package)."
    )
    logger.info("[%s] STEP 2: Macro Analyst (8-section research report — ~8-15 min)...", run_id)
    analysis_out = await _run_agent(
        macro_analyst, analysis_context, "macro-analyst", run_id,
        timeout_seconds=_MACRO_ANALYST_TIMEOUT, run_stats=run_stats,
    )

    # ── Step 3: Quant Modeler (Macro) ──────────────────────────────────────────
    # Sliced data: yields, FX, commodities, spreads, ECB, IMF — numerical data
    # for regressions. News and World Bank (lagged annual) excluded.
    quant_context = (
        f"MACRO QUANT ANALYSIS REQUEST\n"
        f"Topic: {topic}\n"
        f"Run ID: {run_id}\n\n"
        f"IMPORTANT: Identify the primary geography from the topic and the Macro Analyst's "
        f"output. Use geography-appropriate yield curve series, recession models, and "
        f"regression benchmarks — NOT US indicators unless the topic is explicitly US-focused.\n\n"
        f"PRE-GATHERED MACRO DATA (numerical — DO NOT re-fetch):\n"
        f"{_slice_macro_data(macro_data_dict, 'quant-modeler-macro')}\n\n"
        f"MACRO ANALYST OUTPUT (already synthesises all topic-specific data and sources):\n{analysis_out}\n\n"
        f"Produce geography-aware econometric models, yield curve analysis (using indicators "
        f"for the topic's geography), and source credibility evaluation."
    )
    logger.info("[%s] STEP 3: Quant Modeler — macro (econometric models, yield curve analysis)...", run_id)
    quant_out = await _run_agent(
        quant_modeler_macro, quant_context, "quant-macro", run_id,
        timeout_seconds=_QUANT_MACRO_TIMEOUT, run_stats=run_stats,
    )

    # ── Step 4: Macro Report Compiler ─────────────────────────────────────────
    # Sanitise placeholders before passing — raw [ERROR:] strings cause 0 output tokens.
    compile_context = (
        f"MACRO REPORT COMPILATION REQUEST\n"
        f"Topic: {topic}\n"
        f"Run ID: {run_id}\n\n"
        f"Assemble the following outputs into the final 8-section macro research report "
        f"(Sections 1–7 + Section 8 Literature Review). "
        f"Merge the quant findings into the appropriate sections (2, 4, 5, 6). "
        f"Paste Section 8 (Literature Review) verbatim from the Macro Analyst output.\n\n"
        f"--- MACRO ANALYST OUTPUT ---\n"
        f"{_clean_for_compiler('Macro Analyst', analysis_out)}\n\n"
        f"--- QUANT MODELER OUTPUT ---\n"
        f"{_clean_for_compiler('Quant Modeler', quant_out)}\n\n"
        f"--- MACRO SOURCE VALIDATOR OUTPUT (for Source Log) ---\n"
        f"{_clean_for_compiler('Source Validator', source_validator_out)}\n"
    )
    logger.info("[%s] STEP 4: Macro Report Compiler (assembling 8-section report)...", run_id)
    compiled = await _run_agent(
        macro_report_compiler, compile_context, "macro-compiler", run_id, run_stats=run_stats,
    )

    # ── Step 5: Review loop ────────────────────────────────────────────────────
    review_notes_text = ""
    try:
        if _is_placeholder(compiled):
            logger.warning("[%s] macro compiled report is empty/placeholder — skipping review loop", run_id)
            review_notes_text = (
                f"\n\n---\n\n## Review Notes\n\n"
                f"*The review loop was skipped because the compiled report was empty or "
                f"contained only placeholder content (the macro report-compiler did not "
                f"return usable output). Check the debug report for which agents failed.*\n"
            )
        else:
            for pass_num in range(1, _MAX_REVIEW_PASSES + 1):
                # Phase 2E: run fact checker and review agent in parallel — they are independent
                logger.info("[%s] STEP 5: Review pass %d/%d — fact-checker + review-agent running in parallel...",
                            run_id, pass_num, _MAX_REVIEW_PASSES)
                _macro_review_msg = f"Review the following macro research report on '{topic}':\n\n{compiled}"
                fact_result, review_result = await asyncio.gather(
                    _run_agent(fact_checker,  _macro_review_msg, f"fact-checker-pass-{pass_num}",  run_id, run_stats=run_stats),
                    _run_agent(review_agent,  _macro_review_msg, f"review-agent-pass-{pass_num}",  run_id, run_stats=run_stats),
                )

                fact_passed = "PASS" in fact_result.upper()
                review_passed = "PASS" in review_result.upper()

                if fact_passed and review_passed:
                    logger.info("[%s] STEP 5: Review pass %d/%d PASSED ✓ — report complete",
                                run_id, pass_num, _MAX_REVIEW_PASSES)
                    break

                logger.info("[%s] STEP 5: Review pass %d/%d verdict — fact-checker: %s | review-agent: %s — re-compiling...",
                            run_id, pass_num, _MAX_REVIEW_PASSES,
                            "PASS" if fact_passed else "FAIL",
                            "PASS" if review_passed else "FAIL")
                if pass_num < _MAX_REVIEW_PASSES:
                    revise_context = (
                        f"REVISION REQUEST — Pass {pass_num}\n"
                        f"Topic: {topic}\n\n"
                        f"FACT CHECKER FEEDBACK:\n{fact_result}\n\n"
                        f"REVIEW AGENT FEEDBACK:\n{review_result}\n\n"
                        f"CURRENT REPORT:\n{compiled}\n\n"
                        f"NOTE: The report must retain all 8 sections including Section 8 "
                        f"(Literature Review). If Section 8 is missing from the current report, "
                        f"restore it verbatim from the original analyst output below:\n"
                        f"--- ORIGINAL MACRO ANALYST SECTION 8 (Literature Review) ---\n"
                        f"{analysis_out[-8000:] if len(analysis_out) > 8000 else analysis_out}"
                    )
                    new_compiled = await _run_agent(
                        macro_report_compiler, revise_context,
                        f"macro-compiler-pass-{pass_num}", run_id, run_stats=run_stats,
                    )
                    # Guard against tiny revision output replacing a good report
                    if _is_placeholder(new_compiled) or len(new_compiled) < 1_000:
                        logger.warning(
                            "[%s] Macro pass %d: compiler returned tiny output (%d chars) — keeping previous (%d chars)",
                            run_id, pass_num, len(new_compiled), len(compiled),
                        )
                        break
                    compiled = new_compiled
                else:
                    logger.info("[%s] STEP 5: Max review passes (%d) reached — appending Review Notes", run_id, _MAX_REVIEW_PASSES)
                    review_notes_text = (
                        f"\n\n---\n\n## Review Notes\n\n"
                        f"*This report reached the maximum number of review passes ({_MAX_REVIEW_PASSES}). "
                        f"The following issues were flagged but not fully resolved:*\n\n"
                        f"**Fact Checker (Pass {pass_num}):**\n{fact_result}\n\n"
                        f"**Review Agent (Pass {pass_num}):**\n{review_result}\n"
                    )
    except Exception as review_err:
        logger.error("[%s] Macro review loop error: %s — using unreviewed report", run_id, review_err)
        review_notes_text = (
            f"\n\n---\n\n## Review Notes\n\n"
            f"*The review loop encountered an error and could not complete: {review_err}. "
            f"This report has not been fully fact-checked or reviewed.*\n"
        )

    cost_section = format_cost_summary(run_stats) if run_stats is not None else ""
    return f"{compiled}{review_notes_text}{cost_section}"


# ── Top-level pipeline runner ──────────────────────────────────────────────────

async def run_research_pipeline(topic: str, report_type: str, run_id: str,
                                user_context: str = ""):
    """
    Entry point for background pipeline runs.
    Dispatches to the equity or macro pipeline and saves the result to GCS.
    Also saves a per-run debug report to gs://{bucket}/debug/{run_id}.md.
    """
    start_time = datetime.datetime.utcnow()
    run_metadata = {
        "run_id": run_id,
        "topic": topic,
        "report_type": report_type,
        "start_time": start_time.isoformat(),
        "status": "running",
    }

    # Create per-run stats tracker (used by debug report)
    run_stats = create_run_stats(run_id, topic, report_type)

    logger.info("[%s] ═══ PIPELINE START: %s for '%s' ═══", run_id, report_type.upper(), topic)
    if user_context:
        logger.info("[%s]     Additional context provided (%d chars)", run_id, len(user_context))

    try:
        if report_type == "equity":
            final_report = await _run_equity_pipeline(
                topic, run_id, user_context=user_context, run_stats=run_stats
            )
        else:
            final_report = await _run_macro_pipeline(
                topic, run_id, user_context=user_context, run_stats=run_stats
            )

        # ── Prepend pandoc/LaTeX YAML front matter ────────────────────────────
        # This block is the FIRST thing in the saved .md file so pandoc treats
        # it as document metadata.  It sets margins, font size, TOC, section
        # numbering, and loads the LaTeX packages required for the tables and
        # math that appear in the report body.
        #
        # Quick conversion commands:
        #   PDF:  pandoc report.md --pdf-engine=xelatex -o report.pdf
        #   .tex: pandoc report.md --standalone -o report.tex
        #
        # The title is built from the topic; the date comes from this run's
        # start_time so it is always accurate regardless of how long the
        # pipeline took.
        _yaml_date = start_time.strftime("%Y-%m-%d")
        if report_type == "equity":
            _yaml_title = f"{topic.upper()} — Equity Research Memo"
        else:
            _yaml_title = topic.replace("-", " ").title() + " — Macro Research Report"

        # Build the YAML front-matter block.
        # header-includes must be a YAML list, not a literal block scalar (|).
        # Pandoc 3.x (Debian Bookworm) switched to HsYAML which is YAML-1.2
        # strict and rejects the block-scalar form with a "parse exception at
        # line 1, column 1" error.  Single-quoted YAML strings are used for
        # the LaTeX commands so that backslashes are treated as literals (YAML
        # double-quoted strings would interpret \t, \f, \r, \u, etc.).
        _yaml_title_escaped = _yaml_title.replace('"', '\\"')
        _yaml_lines = [
            "---",
            f'title: "{_yaml_title_escaped}"',
            f'date: "{_yaml_date}"',
            'geometry: "margin=1in, top=1.5in, bottom=1.2in"',
            'fontsize: "11pt"',
            'numbersections: true',
            'toc: true',
            'toc-depth: 3',
            'colorlinks: true',
            'linkcolor: "blue"',
            'urlcolor: "blue"',
            'header-includes:',
            r"  - '\usepackage{booktabs}'",
            r"  - '\usepackage{longtable}'",
            r"  - '\usepackage{array}'",
            r"  - '\usepackage{float}'",
            r"  - '\floatplacement{figure}{H}'",
            r"  - '\floatplacement{table}{H}'",
            r"  - '\usepackage{fancyhdr}'",
            r"  - '\pagestyle{fancy}'",
            r"  - '\fancyhf{}'",
            r"  - '\fancyhead[L]{\small AI Research Pipeline}'",
            r"  - '\fancyhead[R]{\small \thepage}'",
            r"  - '\renewcommand{\headrulewidth}{0.4pt}'",
            "---",
            "",
        ]
        _yaml_header = "\n".join(_yaml_lines) + "\n"
        final_report = _yaml_header + final_report

        identifier = topic.replace(" ", "-").replace("/", "-").lower()
        storage_result = save_report(
            content=final_report,
            report_type=report_type,
            identifier=identifier,
        )
        if not storage_result.get("saved"):
            raise RuntimeError(
                f"Failed to save report to GCS: {storage_result.get('error', 'unknown error')}"
            )
        gcs_uri = storage_result.get("gcs_uri", "")

        # ── Save LaTeX (.tex) version alongside the Markdown ──────────────────
        latex_result = save_latex_report(
            md_content=final_report,
            report_type=report_type,
            identifier=identifier,
            timestamp=storage_result["timestamp"],
        )
        if latex_result.get("saved"):
            logger.info("[%s] LaTeX report saved to %s", run_id, latex_result["gcs_uri"])
        else:
            logger.warning("[%s] LaTeX save failed (non-fatal): %s", run_id, latex_result.get("error"))

        run_metadata.update({
            "status": "completed",
            "end_time": datetime.datetime.utcnow().isoformat(),
            "storage_path": gcs_uri,
        })
        _elapsed = (datetime.datetime.utcnow() - start_time).total_seconds()
        logger.info("[%s] Report saved to Cloud Storage: %s", run_id, gcs_uri)
        logger.info("[%s] ═══ PIPELINE COMPLETE: '%s' — %.0fs total (%.0f min) ═══",
                    run_id, topic, _elapsed, _elapsed / 60)

    except Exception as e:
        run_metadata.update({
            "status": "error",
            "error": str(e),
            "end_time": datetime.datetime.utcnow().isoformat(),
        })
        logger.error("[%s] Pipeline error: %s", run_id, e)
        raise  # Re-raise so the calling endpoint can detect the failure

    finally:
        try:
            save_run_metadata(run_metadata, run_id)
        except Exception as meta_err:
            logger.warning("[%s] Failed to save run metadata: %s", run_id, meta_err)

        # Save per-run debug report to GCS (best-effort — never blocks the pipeline)
        try:
            run_stats.pipeline_end = datetime.datetime.utcnow()
            debug_result = save_debug_report(run_stats, _BUCKET)
            if debug_result.get("saved"):
                logger.info("[%s] Debug report saved to %s", run_id, debug_result["gcs_uri"])
            else:
                logger.warning("[%s] Debug report not saved: %s", run_id, debug_result.get("error"))
        except Exception as debug_err:
            logger.warning("[%s] Failed to save debug report: %s", run_id, debug_err)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_form():
    """Serve the research request web form."""
    return _load_web_form()


@app.get("/health")
async def health_check():
    """Health check endpoint — used by Cloud Run to verify the service is running."""
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


@app.post("/research")
async def submit_research_request(request: Request):
    """
    Accept a research request from the web form or API.
    Blocks until the pipeline completes (report saved to GCS), then returns.
    Cloud Run keeps the instance alive for the duration of this request and scales
    to zero automatically after the response is sent — no always-on cost.

    Expected form data or JSON body:
      - topic: Stock ticker (e.g., "AAPL") or macro topic (e.g., "US interest rates")
      - report_type: "equity" or "macro"
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    topic = body.get("topic", "").strip()
    report_type = body.get("report_type", "equity").strip().lower()
    user_context = body.get("context", "").strip()  # Optional user guidance / focus areas

    if not topic:
        return JSONResponse(
            {"error": "Topic is required. Please enter a ticker symbol or research topic."},
            status_code=400,
        )

    if report_type not in ("equity", "macro"):
        return JSONResponse(
            {"error": "report_type must be 'equity' or 'macro'"},
            status_code=400,
        )

    run_id = (
        f"{report_type}-{topic.replace(' ', '-').lower()}"
        f"-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        f"-{str(uuid.uuid4())[:8]}"
    )

    # Run the pipeline synchronously — keeps the HTTP request active so Cloud Run
    # does not scale down the instance mid-pipeline. Hard 1-hour limit as a safety
    # net against infinite loops (Cloud Run --timeout 3600 also enforces this).
    # Note: Cloud Run's maximum request timeout is 3600s; it cannot be set higher.
    try:
        await asyncio.wait_for(
            run_research_pipeline(
                topic=topic, report_type=report_type,
                run_id=run_id, user_context=user_context,
            ),
            timeout=3600,
        )
    except asyncio.TimeoutError:
        logger.warning("[%s] HARD TIMEOUT: pipeline exceeded 1 hour — aborting", run_id)
        return JSONResponse(
            {
                "status": "timeout",
                "message": (
                    f"Pipeline for '{topic}' exceeded the 1-hour hard limit and was stopped. "
                    f"Any partial results may have been saved to gs://{_BUCKET}/."
                ),
                "run_id": run_id,
                "storage_bucket": _BUCKET,
            },
            status_code=504,
        )
    except Exception as e:
        logger.error("[%s] Pipeline failed with error: %s", run_id, e)
        return JSONResponse(
            {
                "status": "error",
                "message": (
                    f"Research pipeline for '{topic}' ({report_type}) failed: {str(e)}. "
                    f"Check Cloud Run logs for run_id '{run_id}' for details."
                ),
                "run_id": run_id,
                "storage_bucket": _BUCKET,
            },
            status_code=500,
        )

    return JSONResponse({
        "status": "complete",
        "message": (
            f"Research report for '{topic}' ({report_type}) has been saved "
            f"to your Cloud Storage bucket ({_BUCKET})."
        ),
        "run_id": run_id,
        "storage_bucket": _BUCKET,
    })


@app.post("/scheduled")
async def run_scheduled_analyses():
    """
    Triggered by Cloud Scheduler for automated runs.
    Runs analysis on all tickers and macro topics in config.yaml.
    Blocks until all pipelines complete (Cloud Run stays alive).
    """
    scheduled = CONFIG.get("scheduled_runs", {})
    equity_tickers = scheduled.get("equity_tickers", [])
    macro_topics = scheduled.get("macro_topics", [])

    results = []
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    for ticker in equity_tickers:
        run_id = f"equity-{ticker.lower()}-scheduled-{timestamp}"
        try:
            await asyncio.wait_for(
                run_research_pipeline(topic=ticker, report_type="equity", run_id=run_id),
                timeout=3600,
            )
            results.append({"topic": ticker, "type": "equity", "run_id": run_id, "status": "complete"})
        except asyncio.TimeoutError:
            logger.warning("[%s] HARD TIMEOUT: scheduled equity pipeline exceeded 1 hour", run_id)
            results.append({"topic": ticker, "type": "equity", "run_id": run_id, "status": "timeout"})
        except Exception as e:
            logger.error("[%s] Scheduled equity pipeline failed: %s", run_id, e)
            results.append({"topic": ticker, "type": "equity", "run_id": run_id, "status": "error", "error": str(e)})

    for topic in macro_topics:
        run_id = f"macro-{topic.replace(' ', '-').lower()}-scheduled-{timestamp}"
        try:
            await asyncio.wait_for(
                run_research_pipeline(topic=topic, report_type="macro", run_id=run_id),
                timeout=3600,
            )
            results.append({"topic": topic, "type": "macro", "run_id": run_id, "status": "complete"})
        except asyncio.TimeoutError:
            logger.warning("[%s] HARD TIMEOUT: scheduled macro pipeline exceeded 1 hour", run_id)
            results.append({"topic": topic, "type": "macro", "run_id": run_id, "status": "timeout"})
        except Exception as e:
            logger.error("[%s] Scheduled macro pipeline failed: %s", run_id, e)
            results.append({"topic": topic, "type": "macro", "run_id": run_id, "status": "error", "error": str(e)})

    return JSONResponse({
        "status": "complete",
        "results": results,
        "message": (
            f"Completed {len(results)} scheduled research runs. "
            f"Reports saved to gs://{_BUCKET}/."
        ),
    })


@app.get("/reports")
async def list_past_reports(report_type: Optional[str] = None, limit: int = 20):
    """
    List past research reports saved in Cloud Storage.

    Query parameters:
      - report_type: Filter by "equity" or "macro" (optional)
      - limit: Maximum number of results (default: 20)
    """
    reports = list_reports(report_type=report_type, limit=limit)
    return JSONResponse({"reports": reports, "count": len(reports)})


# ── Entry point (for local testing) ───────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("Starting FinResearchAgent locally...")
    print("Open http://localhost:8080 in your browser to submit research requests.")
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
