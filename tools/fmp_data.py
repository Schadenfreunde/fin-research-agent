"""
fmp_data.py — Financial data tools using the Financial Modeling Prep (FMP) REST API.

Provides deep fundamental data: income statements, balance sheets, cash flows,
valuation metrics, and analyst estimates. Works on Cloud Run (REST-based, no IP blocking).

Free tier: 250 API calls/day — ~25 tickers/day at 10 calls/ticker.
API key registration: https://financialmodelingprep.com  (free, no credit card)
Secret Manager secret name: fmp-api-key
Environment variable: FMP_API_KEY

All functions are synchronous (called via loop.run_in_executor from async code).
All functions return an error dict on failure — the pipeline continues even if
individual sources fail.
"""

import datetime

from tools.http_client import get_api_key, api_get_with_auth, handle_api_errors

_FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _fmp_get(path: str, params: dict = None):
    """Make a GET request to the FMP API. Returns parsed JSON (list or dict)."""
    key = get_api_key("FMP_API_KEY", "fmp-api-key")
    return api_get_with_auth(
        _FMP_BASE, path,
        auth_param=("apikey", key),
        params=params,
        timeout=15,
        source_label="FMP",
    )


# ── Income Statement ───────────────────────────────────────────────────────────

@handle_api_errors("FMP get_income_statement_fmp")
def get_income_statement_fmp(ticker: str) -> dict:
    """
    Fetch annual and quarterly income statements from FMP.

    Returns up to 4 annual periods and 8 quarterly periods.
    Includes: Revenue, Gross Profit, EBITDA, Operating Income, Net Income,
    EPS (basic + diluted), R&D, SG&A, interest expense, tax rate.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dictionary with annual and quarterly income statement data.
    """
    annual    = _fmp_get(f"/income-statement/{ticker}", {"limit": 4})
    quarterly = _fmp_get(f"/income-statement/{ticker}", {"period": "quarter", "limit": 8})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "FMP /income-statement",
        "annual": annual if isinstance(annual, list) else [],
        "quarterly": quarterly if isinstance(quarterly, list) else [],
    }


# ── Balance Sheet ──────────────────────────────────────────────────────────────

@handle_api_errors("FMP get_balance_sheet_fmp")
def get_balance_sheet_fmp(ticker: str) -> dict:
    """
    Fetch annual and quarterly balance sheets from FMP.

    Returns up to 4 annual periods and 8 quarterly periods.
    Includes: Total assets, Total liabilities, Shareholders equity,
    Cash & equivalents, Total debt, Net debt, Goodwill, Working capital.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with annual and quarterly balance sheet data.
    """
    annual    = _fmp_get(f"/balance-sheet-statement/{ticker}", {"limit": 4})
    quarterly = _fmp_get(f"/balance-sheet-statement/{ticker}", {"period": "quarter", "limit": 8})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "FMP /balance-sheet-statement",
        "annual": annual if isinstance(annual, list) else [],
        "quarterly": quarterly if isinstance(quarterly, list) else [],
    }


# ── Cash Flow Statement ────────────────────────────────────────────────────────

@handle_api_errors("FMP get_cash_flow_fmp")
def get_cash_flow_fmp(ticker: str) -> dict:
    """
    Fetch annual and quarterly cash flow statements from FMP.

    Returns up to 4 annual periods and 8 quarterly periods.
    Includes: Operating CF, Investing CF, Financing CF, Free Cash Flow,
    CapEx, Dividends paid, Stock buybacks, D&A.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with annual and quarterly cash flow data.
    """
    annual    = _fmp_get(f"/cash-flow-statement/{ticker}", {"limit": 4})
    quarterly = _fmp_get(f"/cash-flow-statement/{ticker}", {"period": "quarter", "limit": 8})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "FMP /cash-flow-statement",
        "annual": annual if isinstance(annual, list) else [],
        "quarterly": quarterly if isinstance(quarterly, list) else [],
    }


# ── Key Metrics / Valuation ────────────────────────────────────────────────────

@handle_api_errors("FMP get_key_metrics_fmp")
def get_key_metrics_fmp(ticker: str) -> dict:
    """
    Fetch valuation multiples and key metrics from FMP.

    Returns up to 4 annual periods + 8 quarterly periods.
    Includes: P/E, EV/EBITDA, EV/Revenue, P/FCF, P/B, P/S, PEG,
    Revenue per share, FCF per share, Net Debt/EBITDA, ROIC, ROE.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with historical valuation metric data.
    """
    annual    = _fmp_get(f"/key-metrics/{ticker}", {"limit": 4})
    quarterly = _fmp_get(f"/key-metrics/{ticker}", {"period": "quarter", "limit": 8})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "FMP /key-metrics",
        "annual": annual if isinstance(annual, list) else [],
        "quarterly": quarterly if isinstance(quarterly, list) else [],
    }


# ── Analyst Estimates ──────────────────────────────────────────────────────────

@handle_api_errors("FMP get_analyst_estimates_fmp")
def get_analyst_estimates_fmp(ticker: str) -> dict:
    """
    Fetch forward EPS and revenue analyst estimates from FMP.

    Returns consensus analyst estimates for the next 4 annual periods,
    including high/low/average EPS estimates, revenue estimates, and EBITDA estimates.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with forward analyst estimate data.
    """
    estimates = _fmp_get(f"/analyst-estimates/{ticker}", {"limit": 4})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "FMP /analyst-estimates",
        "estimates": estimates if isinstance(estimates, list) else [],
        "note": (
            "Forward EPS and revenue consensus estimates. "
            "For current analyst Buy/Sell/Hold ratings, see analyst_ratings_finnhub "
            "or search the web for current consensus."
        ),
    }
