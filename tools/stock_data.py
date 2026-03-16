"""
stock_data.py — Alpha Vantage API tools for the equity research agents.

Note: Yahoo Finance (yfinance) was removed — Yahoo Finance blocks Google Cloud
Run IPs, making it unusable in production. Alpha Vantage is used for company
overview, income statements, EPS history, and current price data.

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
