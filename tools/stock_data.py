"""
stock_data.py — Alpha Vantage API tools for equity and macro research agents.

Note: Yahoo Finance (yfinance) was removed — Yahoo Finance blocks Google Cloud
Run IPs, making it unusable in production. Alpha Vantage is used for company
overview, income statements, EPS history, current price data, and commodity prices.

Alpha Vantage free tier: 25 API calls/day (500 calls/day with premium plan).
API key registration: https://www.alphavantage.co/support/#api-key (free)
Secret Manager secret name: alpha-vantage-api-key
Environment variable: ALPHA_VANTAGE_KEY

All functions are synchronous (called via loop.run_in_executor from async code).
All functions return an error dict on failure — the pipeline continues even if
individual sources fail.
"""

import datetime

from tools.http_client import get_api_key, api_get_with_auth, handle_api_errors


_AV_BASE = "https://www.alphavantage.co"


def _av_get(function: str, symbol: str, extra_params: dict = None) -> dict:
    """Make a GET request to the Alpha Vantage API. Returns parsed JSON dict."""
    key = get_api_key("ALPHA_VANTAGE_KEY", "alpha-vantage-api-key")
    params = {"function": function, "symbol": symbol}
    if extra_params:
        params.update(extra_params)
    return api_get_with_auth(
        _AV_BASE, "/query",
        auth_param=("apikey", key),
        params=params,
        timeout=15,
        source_label="AlphaVantage",
    )


def _av_get_no_symbol(function: str, extra_params: dict = None) -> dict:
    """AV request for endpoints that don't take a symbol (commodities, FX)."""
    key = get_api_key("ALPHA_VANTAGE_KEY", "alpha-vantage-api-key")
    params = {"function": function}
    if extra_params:
        params.update(extra_params)
    return api_get_with_auth(
        _AV_BASE, "/query",
        auth_param=("apikey", key),
        params=params,
        timeout=15,
        source_label="AlphaVantage",
    )


# ── Alpha Vantage tools ────────────────────────────────────────────────────────

@handle_api_errors("AlphaVantage get_company_overview_alpha")
def get_company_overview_alpha(ticker: str) -> dict:
    """
    Fetch comprehensive company overview from Alpha Vantage.
    Includes description, sector, industry, employee count, and fundamental ratios.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dictionary with company overview data.
    """
    data = _av_get("OVERVIEW", ticker)
    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Alpha Vantage OVERVIEW",
        "data": data,
    }


@handle_api_errors("AlphaVantage get_income_statement_alpha")
def get_income_statement_alpha(ticker: str) -> dict:
    """
    Fetch annual and quarterly income statements from Alpha Vantage.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with annual and quarterly income statements.
    """
    data = _av_get("INCOME_STATEMENT", ticker)
    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Alpha Vantage INCOME_STATEMENT",
        "annual_reports": data.get("annualReports", [])[:4],       # Last 4 years
        "quarterly_reports": data.get("quarterlyReports", [])[:8], # Last 8 quarters
    }


@handle_api_errors("AlphaVantage get_earnings_per_share_alpha")
def get_earnings_per_share_alpha(ticker: str) -> dict:
    """
    Fetch earnings per share history from Alpha Vantage.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with annual and quarterly EPS data.
    """
    data = _av_get("EARNINGS", ticker)
    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Alpha Vantage EARNINGS",
        "annual_earnings": data.get("annualEarnings", [])[:4],
        "quarterly_earnings": data.get("quarterlyEarnings", [])[:8],
    }


@handle_api_errors("AlphaVantage get_current_price_alpha")
def get_current_price_alpha(ticker: str) -> dict:
    """
    Fetch current stock price from Alpha Vantage GLOBAL_QUOTE.

    Primary price source for Cloud Run deployments (Yahoo Finance blocks GCP IPs).
    Returns current price, previous close, change, and latest trading volume.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with current price data from Alpha Vantage.
    """
    data = _av_get("GLOBAL_QUOTE", ticker)
    quote = data.get("Global Quote", {})
    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Alpha Vantage GLOBAL_QUOTE",
        "current_price": quote.get("05. price"),
        "previous_close": quote.get("08. previous close"),
        "change": quote.get("09. change"),
        "change_percent": quote.get("10. change percent"),
        "volume": quote.get("06. volume"),
        "latest_trading_day": quote.get("07. latest trading day"),
        "note": (
            "Primary price source. yfinance is unavailable from Cloud Run IPs — "
            "use this value for current price. 52-week range available via Finnhub key_metrics."
        ),
    }


# ── Alpha Vantage commodity tools (macro pipeline) ────────────────────────────

# AV commodity endpoints use the commodity name as the "function" parameter
# and return monthly data by default. No "symbol" parameter needed.
_AV_COMMODITIES = {
    "WTI": "WTI Crude Oil (USD/barrel)",
    "BRENT": "Brent Crude Oil (USD/barrel)",
    "NATURAL_GAS": "Henry Hub Natural Gas (USD/MMBtu)",
    "COPPER": "Copper (USD/lb)",
    "WHEAT": "Wheat (USD/bushel)",
    "CORN": "Corn (USD/bushel)",
}


@handle_api_errors("AlphaVantage get_commodity_prices_alpha")
def get_commodity_prices_alpha() -> dict:
    """
    Fetch monthly commodity prices from Alpha Vantage for macro analysis.

    Covers: WTI crude, Brent crude, natural gas, copper, wheat, corn.
    Uses 6 API calls (of 25/day free tier). Returns last 12 months per commodity.

    Returns:
        Dictionary with per-commodity price history and summary stats.
    """
    results = {}
    for func_name, description in _AV_COMMODITIES.items():
        try:
            data = _av_get_no_symbol(func_name, {"interval": "monthly"})
            # AV returns {"name": "...", "interval": "monthly", "data": [...]}
            raw_data = data.get("data", [])[:12]  # Last 12 months
            values = []
            for point in raw_data:
                val = point.get("value", ".")
                if val != ".":
                    try:
                        values.append({"date": point.get("date"), "value": float(val)})
                    except (ValueError, TypeError):
                        pass

            latest = values[0] if values else None
            prior_year = values[-1] if len(values) >= 12 else (values[-1] if values else None)
            numeric = [v["value"] for v in values]

            results[func_name] = {
                "description": description,
                "latest_value": latest["value"] if latest else None,
                "latest_date": latest["date"] if latest else None,
                "prior_year_value": prior_year["value"] if prior_year else None,
                "yoy_change": (
                    round(latest["value"] - prior_year["value"], 4)
                    if latest and prior_year else None
                ),
                "12m_min": min(numeric) if numeric else None,
                "12m_max": max(numeric) if numeric else None,
                "data_points": len(values),
            }
        except Exception as e:
            results[func_name] = {"description": description, "error": str(e)}

    return {
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Alpha Vantage Commodity Endpoints (free tier — 25 calls/day)",
        "commodities": results,
    }
