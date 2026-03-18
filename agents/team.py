"""
team.py — All 13 research agents defined using the Google Agent Development Kit (ADK).

Each agent is defined with:
  - A system prompt loaded from the prompts/ folder
  - The tools it can call
  - The Gemini model tier assigned (3.0 Pro / 2.5 Pro / 2.5 Flash)

The review loop logic (max 3 passes, section re-runs, Review Notes fallback)
is managed by the Research Orchestrator agent, which coordinates all others.

HOW TO CHANGE AN AGENT'S BEHAVIOR:
  Edit the corresponding .md file in the prompts/ folder.
  Changes take effect immediately — no redeployment needed if using Vertex AI Prompt Management.
  For code-level changes, re-run deploy.sh after editing.
"""

import os
import yaml
from pathlib import Path
from google import adk
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.genai import types as genai_types

# ── Load configuration ─────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
with open(_CONFIG_PATH) as f:
    _CONFIG = yaml.safe_load(f)

MODEL_TIER1 = _CONFIG["models"]["tier1"]   # gemini-2.5-pro (Orchestrator, Fact Checker, Review Agent)
MODEL_TIER2 = _CONFIG["models"]["tier2"]   # gemini-2.5-pro (Valuation, Earnings Quality)
MODEL_TIER3 = _CONFIG["models"]["tier3"]   # gemini-2.5-flash (all other agents)
MODEL_TIER_COMPILER = _CONFIG["models"].get("tier_compiler", MODEL_TIER1)  # gemini-2.5-pro (compiler agents)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a system prompt from the prompts/ directory."""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text()


# ── Import tools ───────────────────────────────────────────────────────────────

from tools.finnhub_data import (
    get_quote_finnhub,
    get_historical_prices_finnhub,
    get_financials_finnhub,
    get_key_metrics_finnhub,
    get_earnings_finnhub,
    get_analyst_ratings_finnhub,
)
from tools.fmp_data import (
    get_income_statement_fmp,
    get_balance_sheet_fmp,
    get_cash_flow_fmp,
    get_key_metrics_fmp,
    get_analyst_estimates_fmp,
)
from tools.sec_filings import (
    get_recent_filings,
    get_company_facts,
    get_specific_fact,
    get_insider_transactions,
)
from tools.macro_data import (
    get_series,
    get_multiple_series,
    get_yield_curve_snapshot,
    get_recession_indicators,
)
from tools.quant_tools import (
    compute_rsi,
    compute_sma,
    compute_ema,
    compute_macd,
    compute_bollinger_bands,
    compute_atr,
    compute_historical_volatility,
    compute_max_drawdown,
    compute_var,
    compute_beta,
    compute_correlation,
    compute_skewness_kurtosis,
    simple_linear_regression,
    compute_yield_spread,
    compute_z_score,
)
from tools.earnings_quality_tools import (
    get_sbc_analysis,
    get_gaap_vs_nongaap_gap,
    get_accruals_analysis,
    get_deferred_revenue_trend,
    get_goodwill_analysis,
    get_debt_analysis,
)
from tools.web_search import (
    search_web,
    search_news,
    search_earnings_transcript,
    search_analyst_reports,
    search_academic_papers,
    search_competitor_filings,
)
from tools.storage import save_report, load_report, list_reports, save_run_metadata
from tools.core_api import search_academic_core
from tools.polygon_data import (
    get_ticker_details_polygon,
    get_historical_ohlcv_polygon,
    get_recent_news_polygon,
)


# ── Wrap functions as ADK FunctionTools ────────────────────────────────────────

def _tool(fn) -> FunctionTool:
    return FunctionTool(fn)


# ── EQUITY PATH AGENTS ─────────────────────────────────────────────────────────

research_orchestrator = Agent(
    name="research_orchestrator",
    model=MODEL_TIER1,
    description=(
        "Writes the Executive Summary for a completed equity research report. "
        "Receives the fully compiled, fact-checked report and produces only the "
        "Executive Summary section at the top."
    ),
    instruction=_load_prompt("orchestrator.md"),
    tools=[],  # Pipeline orchestration is handled by Python (main.py); no tools needed here
)

data_harvester = Agent(
    name="data_harvester",
    model=MODEL_TIER3,
    description=(
        "Research Librarian. Supplements pre-gathered structured data (Finnhub, FMP, "
        "SEC EDGAR, Alpha Vantage) with web-sourced research and builds the coverage log."
    ),
    instruction=_load_prompt("data_harvester.md"),
    tools=[
        # Web search only — structured data is pre-gathered by Python before this agent runs.
        _tool(search_web),
        _tool(search_news),
        _tool(search_earnings_transcript),
        _tool(search_analyst_reports),
        _tool(search_academic_papers),
        _tool(search_competitor_filings),
        _tool(search_academic_core),  # CORE academic papers (no Vertex AI quota)
    ],
)

fundamental_analyst = Agent(
    name="fundamental_analyst",
    model=MODEL_TIER3,
    description=(
        "Senior buy-side fundamental analyst. Produces Sections 1, 2, 9, 11, 12, and 13 "
        "covering thesis framing, market structure, monetization, unit economics, "
        "financials, and capital structure. "
        "LEGACY: use fundamental_analyst_market + fundamental_analyst_financials instead."
    ),
    instruction=_load_prompt("fundamental_analyst.md"),
    tools=[
        _tool(get_financials_finnhub),
        _tool(get_key_metrics_fmp),
        _tool(get_income_statement_fmp),
        _tool(get_specific_fact),
        _tool(get_company_facts),
        _tool(search_web),
        _tool(search_earnings_transcript),
    ],
)

# ── SPLIT FUNDAMENTAL ANALYSTS ────────────────────────────────────────────────
# The monolithic fundamental_analyst above was hitting Vertex AI rate limits because
# it made too many API calls in a single agent run. Splitting into two lighter agents
# that run in parallel reduces per-agent token and tool-call budgets significantly.

fundamental_analyst_market = Agent(
    name="fundamental_analyst_market",
    model=MODEL_TIER3,
    description=(
        "Senior buy-side fundamental analyst (market focus). Produces only Sections 1 "
        "(Investment Thesis) and 2 (Market Structure & TAM). Web-search focused with "
        "minimal API calls. Runs in parallel with other analysts."
    ),
    instruction=_load_prompt("fundamental_analyst_market.md"),
    tools=[
        _tool(search_web),
        _tool(search_earnings_transcript),
    ],
)

fundamental_analyst_financials = Agent(
    name="fundamental_analyst_financials",
    model=MODEL_TIER3,
    description=(
        "Senior buy-side fundamental analyst (financials focus). Produces only Sections 9 "
        "(Monetisation & Pricing), 11 (Unit Economics), 12 (Financial Profile), and 13 "
        "(Capital Structure). API-heavy. Runs in parallel with other analysts."
    ),
    instruction=_load_prompt("fundamental_analyst_financials.md"),
    tools=[
        _tool(get_financials_finnhub),
        _tool(get_key_metrics_fmp),
        _tool(get_income_statement_fmp),
        _tool(get_specific_fact),
        _tool(get_company_facts),
        _tool(search_web),
        _tool(search_earnings_transcript),
    ],
)

# ── CONTEXT PROCESSOR (equity and macro paths) ────────────────────────────────

context_processor = Agent(
    name="context_processor",
    model=MODEL_TIER3,
    description=(
        "Context interpreter. Receives user-provided context notes and structured data. "
        "Identifies data gaps relevant to the user focus, fetches missing data within "
        "a strict tool budget, and returns an ENRICHED CONTEXT NOTE for all downstream "
        "analysts to incorporate."
    ),
    instruction=_load_prompt("context_processor.md"),
    tools=[
        _tool(search_web),
        _tool(search_news),
        _tool(search_earnings_transcript),
        _tool(search_analyst_reports),
    ],
)

competitive_analyst = Agent(
    name="competitive_analyst",
    model=MODEL_TIER3,
    description=(
        "Senior buy-side competitive and strategic analyst. Produces Sections 3, 4, 5, 6, "
        "7, and 8 covering customers, product, competition, ecosystem, GTM, and retention."
    ),
    instruction=_load_prompt("competitive_analyst.md"),
    tools=[
        _tool(search_web),
        _tool(search_news),
        _tool(search_competitor_filings),
        _tool(search_analyst_reports),
        _tool(get_recent_filings),
    ],
)

risk_analyst = Agent(
    name="risk_analyst",
    model=MODEL_TIER3,
    description=(
        "Senior buy-side risk and quality analyst. Produces Sections 14–19 covering moat, "
        "AI economics, execution quality, supply chain, risk inventory, and M&A."
    ),
    instruction=_load_prompt("risk_analyst.md"),
    tools=[
        _tool(search_web),
        _tool(search_news),
        _tool(get_specific_fact),
        _tool(get_recent_filings),
        _tool(search_analyst_reports),
    ],
)

valuation_analyst = Agent(
    name="valuation_analyst",
    model=MODEL_TIER2,
    description=(
        "Lead valuation analyst. Produces Sections 20–21, the Quality Scorecard, and "
        "applies Decision Rules to determine the investment rating. "
        "Uses Gemini 2.5 Pro for complex DCF and probability-weighted scenario modeling."
    ),
    instruction=_load_prompt("valuation_analyst.md"),
    tools=[
        _tool(get_quote_finnhub),
        _tool(get_financials_finnhub),
        _tool(get_key_metrics_fmp),
        _tool(get_historical_prices_finnhub),
        _tool(get_analyst_estimates_fmp),
        _tool(search_web),
        _tool(search_analyst_reports),
        _tool(get_series),
    ],
)

quant_modeler_equity = Agent(
    name="quant_modeler_equity",
    model=MODEL_TIER3,
    description=(
        "Quantitative analyst (equity). Runs technical indicators (RSI, MACD, Bollinger "
        "Bands, MAs) and statistical models (beta, volatility, drawdown, VaR). "
        "Outputs feed Sections 18, 20, and 21."
    ),
    instruction=_load_prompt("quant_modeler_equity.md"),
    tools=[
        _tool(get_historical_prices_finnhub),
        _tool(get_earnings_finnhub),
        _tool(get_series),  # For macro factor correlations
        _tool(compute_rsi),
        _tool(compute_sma),
        _tool(compute_ema),
        _tool(compute_macd),
        _tool(compute_bollinger_bands),
        _tool(compute_atr),
        _tool(compute_historical_volatility),
        _tool(compute_max_drawdown),
        _tool(compute_var),
        _tool(compute_beta),
        _tool(compute_correlation),
        _tool(compute_skewness_kurtosis),
    ],
)

earnings_quality_agent = Agent(
    name="earnings_quality_agent",
    model=MODEL_TIER3,
    description=(
        "Forensic accounting and alpha signals specialist. Analyzes GAAP vs non-GAAP gaps, "
        "SBC burden, insider activity, accruals quality, off-balance-sheet items, and "
        "aggressive accounting flags. "
        "Uses Gemini 2.5 Flash for fast structured forensic analysis."
    ),
    instruction=_load_prompt("earnings_quality_agent.md"),
    tools=[
        _tool(get_sbc_analysis),
        _tool(get_gaap_vs_nongaap_gap),
        _tool(get_accruals_analysis),
        _tool(get_deferred_revenue_trend),
        _tool(get_goodwill_analysis),
        _tool(get_debt_analysis),
        # get_short_interest removed: Yahoo Finance blocks Cloud Run IPs.
        # Short interest data is available via search_web if the agent needs it.
        _tool(get_insider_transactions),
        _tool(search_web),
        _tool(search_news),
        _tool(get_recent_filings),
        _tool(get_specific_fact),
    ],
)

report_compiler = Agent(
    name="report_compiler",
    model=MODEL_TIER_COMPILER,
    description=(
        "Document editor for equity reports. Assembles all analyst outputs into the "
        "final structured 21-section investment memo in the exact required sequence. "
        "Does not add analysis — compiles only."
    ),
    instruction=_load_prompt("report_compiler.md"),
    tools=[
        _tool(save_report),
    ],
    # A full 21-section equity memo requires 15K–30K output tokens.
    # Without this, the ADK default (8192) silently truncates or produces empty output.
    generate_content_config=genai_types.GenerateContentConfig(
        max_output_tokens=65536,
    ),
)


# ── MACRO PATH AGENTS ──────────────────────────────────────────────────────────

macro_data_agent = Agent(
    name="macro_data_agent",
    model=MODEL_TIER3,
    description=(
        "Macro data librarian. Gathers macroeconomic data from FRED and web sources "
        "before any macro analysis begins. Builds the source log."
    ),
    instruction=_load_prompt("macro_data_agent.md"),
    tools=[
        _tool(get_series),
        _tool(get_multiple_series),
        _tool(get_yield_curve_snapshot),
        _tool(get_recession_indicators),
        _tool(search_web),
        _tool(search_news),
        _tool(search_academic_papers),
        _tool(search_academic_core),  # CORE academic papers (no Vertex AI quota)
    ],
)

macro_analyst = Agent(
    name="macro_analyst",
    model=MODEL_TIER3,
    description=(
        "Senior macro strategist. Produces the 8-section macro research report covering "
        "current state, drivers, scenarios, investment implications, risks, monitoring plan, "
        "and literature review. Geography-aware: uses topic-specific indicators, not US defaults."
    ),
    instruction=_load_prompt("macro_analyst.md"),
    tools=[
        _tool(get_series),
        _tool(get_multiple_series),
        _tool(get_yield_curve_snapshot),
        _tool(get_recession_indicators),
        _tool(search_web),
        _tool(search_news),
        _tool(search_academic_papers),
        _tool(search_academic_core),  # CORE academic papers (no Vertex AI quota)
    ],
)

quant_modeler_macro = Agent(
    name="quant_modeler_macro",
    model=MODEL_TIER3,
    description=(
        "Quantitative analyst (macro). Builds econometric models (regressions, yield curve "
        "analysis, time series decomposition) and evaluates credibility of quantitative "
        "claims in cited sources."
    ),
    instruction=_load_prompt("quant_modeler_macro.md"),
    tools=[
        _tool(get_series),
        _tool(get_multiple_series),
        _tool(get_yield_curve_snapshot),
        _tool(get_recession_indicators),
        _tool(simple_linear_regression),
        _tool(compute_yield_spread),
        _tool(compute_z_score),
        _tool(compute_correlation),
        _tool(compute_historical_volatility),
    ],
)

macro_source_validator = Agent(
    name="macro_source_validator",
    model=MODEL_TIER3,
    description=(
        "Source quality gatekeeper for macro research. Validates whether gathered sources "
        "match the topic's geography and theme. Issues targeted additional searches to fill "
        "gaps in academic coverage, historical analogs, or central bank publications. "
        "Runs between the Macro Data Agent and the Macro Analyst."
    ),
    instruction=_load_prompt("macro_source_validator.md"),
    tools=[
        _tool(search_academic_core),  # CORE academic search (no Vertex AI quota)
        _tool(search_web),
        _tool(search_news),
        _tool(search_academic_papers),  # Fallback for Vertex AI search
    ],
)

macro_report_compiler = Agent(
    name="macro_report_compiler",
    model=MODEL_TIER_COMPILER,
    description=(
        "Document editor for macro reports. Assembles all macro analyst and quant outputs "
        "into the final 8-section macro research report (including Literature Review). "
        "Does not add analysis — compiles only."
    ),
    instruction=_load_prompt("macro_report_compiler.md"),
    tools=[
        _tool(save_report),
    ],
    # Same as equity compiler: large output required for a full macro report.
    generate_content_config=genai_types.GenerateContentConfig(
        max_output_tokens=65536,
    ),
)


# ── REVIEW LOOP AGENTS (both paths) ───────────────────────────────────────────

fact_checker = Agent(
    name="fact_checker",
    model=MODEL_TIER1,
    description=(
        "Quality assurance analyst. Verifies Fact/Analysis/Inference labels, citations, "
        "60-source coverage gate, math consistency, decision gate compliance, and "
        "quant model validity. Returns PASS or FAIL with specific issue list. "
        "Uses Gemini 3.0 Pro to out-reason the agents it is checking."
    ),
    instruction=_load_prompt("fact_checker.md"),
    tools=[],  # Reads the compiled report — no external tool calls needed
)

review_agent = Agent(
    name="review_agent",
    model=MODEL_TIER1,
    description=(
        "Senior editor and internal review committee. Checks coherence, consistency, "
        "executive summary vs body alignment, rating logic, and actionability. "
        "Returns PASS or FAIL with specific issue list. "
        "Uses Gemini 3.0 Pro to catch errors in the highest-quality sections."
    ),
    instruction=_load_prompt("review_agent.md"),
    tools=[],  # Reads the compiled report — no external tool calls needed
)


# ── AGENT REGISTRY (used by main.py and Orchestrator) ─────────────────────────

EQUITY_AGENTS = {
    "orchestrator": research_orchestrator,
    "data_harvester": data_harvester,
    "context_processor": context_processor,
    "fundamental_analyst": fundamental_analyst,              # legacy — kept for fallback
    "fundamental_analyst_market": fundamental_analyst_market,
    "fundamental_analyst_financials": fundamental_analyst_financials,
    "competitive_analyst": competitive_analyst,
    "risk_analyst": risk_analyst,
    "valuation_analyst": valuation_analyst,
    "quant_modeler_equity": quant_modeler_equity,
    "earnings_quality": earnings_quality_agent,
    "report_compiler": report_compiler,
    "fact_checker": fact_checker,
    "review_agent": review_agent,
}

MACRO_AGENTS = {
    "orchestrator": research_orchestrator,
    "context_processor": context_processor,
    "macro_data_agent": macro_data_agent,
    "macro_source_validator": macro_source_validator,
    "macro_analyst": macro_analyst,
    "quant_modeler_macro": quant_modeler_macro,
    "macro_report_compiler": macro_report_compiler,
    "fact_checker": fact_checker,
    "review_agent": review_agent,
}

ALL_AGENTS = {**EQUITY_AGENTS, **MACRO_AGENTS}
