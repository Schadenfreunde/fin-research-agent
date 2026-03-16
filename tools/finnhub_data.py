"""
finnhub_data.py — Financial data tools using the Finnhub REST API.

Replaces yfinance for Cloud Run deployments (Yahoo Finance blocks GCP IPs).
Finnhub free tier: 60 API calls/minute — sufficient for per-ticker gathering.

API key registration: https://finnhub.io  (free, no credit card)
Secret Manager secret name: finnhub-api-key
Environment variable: FINNHUB_API_KEY

All functions are synchronous (called via loop.run_in_executor from async code).
All functions return an error dict on failure — the pipeline continues even if
individual sources fail.
"""

import datetime
import time

from tools.http_client import get_api_key, api_get_with_auth, handle_api_errors

_FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub_get(path: str, params: dict = None) -> dict:
    """Make a GET request to the Finnhub API. Returns parsed JSON dict."""
    key = get_api_key("FINNHUB_API_KEY", "finnhub-api-key")
    return api_get_with_auth(
        _FINNHUB_BASE, path,
        auth_param=("token", key),
        params=params,
        timeout=15,
        source_label="Finnhub",
    )


# ── Quote / Price ──────────────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_quote_finnhub")
def get_quote_finnhub(ticker: str) -> dict:
    """
    Fetch current price, 52-week range, and company profile.

    Calls /quote (real-time price) and /stock/profile2 (market cap, shares, sector).

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Dictionary with price and company profile data.
    """
    quote = _finnhub_get("/quote", {"symbol": ticker})
    profile = _finnhub_get("/stock/profile2", {"symbol": ticker})
    today = datetime.date.today().isoformat()

    return {
        "ticker": ticker,
        "date": today,
        "source": "Finnhub /quote + /stock/profile2",
        "current_price": quote.get("c"),          # current price
        "open": quote.get("o"),                   # open price of the day
        "high": quote.get("h"),                   # high price of the day
        "low": quote.get("l"),                    # low price of the day
        "previous_close": quote.get("pc"),        # previous close
        "change": quote.get("d"),                 # change
        "change_percent": quote.get("dp"),        # change %
        "52_week_high": quote.get("t") and None,  # not in /quote; use profile
        "market_cap": profile.get("marketCapitalization"),  # in millions
        "shares_outstanding": profile.get("shareOutstanding"),  # in millions
        "currency": profile.get("currency", "USD"),
        "exchange": profile.get("exchange"),
        "industry": profile.get("finnhubIndustry"),
        "name": profile.get("name"),
        "country": profile.get("country"),
        "logo": profile.get("logo"),
        "weburl": profile.get("weburl"),
    }


# ── Historical Prices ──────────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_historical_prices_finnhub")
def get_historical_prices_finnhub(ticker: str, days: int = 730) -> dict:
    """
    Fetch daily OHLCV candle data for the past N days.

    Uses the Finnhub /stock/candle endpoint with resolution 'W' (weekly bars).
    Default: 730 days (~2 years) of weekly bars.

    Args:
        ticker: Stock ticker symbol
        days: Number of calendar days of history to fetch (default 730 = ~2 years)

    Returns:
        Dictionary with OHLCV records list.
    """
    now = int(time.time())
    from_ts = now - (days * 86400)

    data = _finnhub_get("/stock/candle", {
        "symbol": ticker,
        "resolution": "W",
        "from": from_ts,
        "to": now,
    })

    status = data.get("s", "no_data")
    if status != "ok":
        return {
            "ticker": ticker,
            "period": f"{days}d",
            "interval": "1wk",
            "error": f"Finnhub returned status '{status}' — no data available",
            "source": "Finnhub /stock/candle",
        }

    closes = data.get("c", [])
    opens  = data.get("o", [])
    highs  = data.get("h", [])
    lows   = data.get("l", [])
    vols   = data.get("v", [])
    ts     = data.get("t", [])

    records = []
    for i in range(len(ts)):
        records.append({
            "date": datetime.datetime.utcfromtimestamp(ts[i]).date().isoformat(),
            "open":   round(opens[i],  4) if opens[i]  is not None else None,
            "high":   round(highs[i],  4) if highs[i]  is not None else None,
            "low":    round(lows[i],   4) if lows[i]   is not None else None,
            "close":  round(closes[i], 4) if closes[i] is not None else None,
            "volume": int(vols[i]) if vols[i] is not None else None,
        })

    return {
        "ticker": ticker,
        "period": f"{days}d",
        "interval": "1wk",
        "source": "Finnhub /stock/candle",
        "records": records,
        "count": len(records),
    }


# ── Financial Statements ───────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_financials_finnhub")
def get_financials_finnhub(ticker: str) -> dict:
    """
    Fetch reported financial statements (income, balance sheet, cash flow).

    Uses /stock/financials-reported which returns XBRL-parsed data from SEC filings.
    Returns up to 4 annual + 8 quarterly periods.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with annual and quarterly financial statement data.
    """
    annual = _finnhub_get("/stock/financials-reported", {
        "symbol": ticker,
        "freq": "annual",
    })
    quarterly = _finnhub_get("/stock/financials-reported", {
        "symbol": ticker,
        "freq": "quarterly",
    })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Finnhub /stock/financials-reported",
        "annual": annual.get("data", [])[:4],        # last 4 fiscal years
        "quarterly": quarterly.get("data", [])[:8],  # last 8 quarters
    }


# ── Key Metrics / Valuation ────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_key_metrics_finnhub")
def get_key_metrics_finnhub(ticker: str) -> dict:
    """
    Fetch valuation multiples and key financial metrics.

    Uses /stock/metric?metric=all which returns P/E, EV/EBITDA, P/B, P/S,
    52-week price range, revenue growth, margin data, and more.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with valuation and quality metrics.
    """
    data = _finnhub_get("/stock/metric", {
        "symbol": ticker,
        "metric": "all",
    })

    m = data.get("metric", {})

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Finnhub /stock/metric",
        # Valuation
        "pe_ttm":              m.get("peTTM"),
        "pe_excluding_xor":    m.get("peExclTTM"),
        "pb_quarterly":        m.get("pbQuarterly"),
        "ps_ttm":              m.get("psTTM"),
        "ev_ebitda_ttm":       m.get("evEbitdaTTM"),
        "peg_5yr":             m.get("pegRatio5Y"),
        "price_to_fcf_ttm":   m.get("priceToFreeCashFlowTTM"),
        # 52-week range
        "52_week_high":        m.get("52WeekHigh"),
        "52_week_low":         m.get("52WeekLow"),
        "52_week_high_date":   m.get("52WeekHighDate"),
        "52_week_low_date":    m.get("52WeekLowDate"),
        "52_week_return":      m.get("52WeekPriceReturnDaily"),
        # Growth
        "revenue_growth_3y_cagr":     m.get("revenueGrowth3Y"),
        "eps_growth_3y_cagr":         m.get("epsGrowth3Y"),
        "eps_growth_ttm_yoy":         m.get("epsGrowthTTMYoy"),
        # Margins
        "gross_margin_ttm":    m.get("grossMarginTTM"),
        "ebitda_margin_ttm":   m.get("ebitdaPerRevenueTTM"),
        "net_margin_ttm":      m.get("netMarginTTM"),
        "fcf_margin_ttm":      m.get("freeCashFlowMarginTTM"),
        # Returns
        "roe_ttm":             m.get("roeTTM"),
        "roic_ttm":            m.get("roicTTM"),
        # Leverage
        "total_debt_to_equity":    m.get("totalDebt/totalEquityAnnual"),
        "net_debt_to_ebitda":      m.get("netDebt/ebitdaAnnual"),
        "current_ratio":           m.get("currentRatioQuarterly"),
        # Per share
        "book_value_per_share":    m.get("bookValuePerShareQuarterly"),
        "fcf_per_share_ttm":       m.get("freeCashFlowPerShareTTM"),
        "revenue_per_share_ttm":   m.get("revenuePerShareTTM"),
    }


# ── Earnings History ───────────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_earnings_finnhub")
def get_earnings_finnhub(ticker: str) -> dict:
    """
    Fetch quarterly earnings history: EPS actual vs estimate, surprise %.

    Uses /stock/earnings which returns the last N quarters of earnings releases.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with earnings surprise data for the last 8 quarters.
    """
    data = _finnhub_get("/stock/earnings", {
        "symbol": ticker,
        "limit": 8,
    })

    records = []
    for item in (data if isinstance(data, list) else []):
        records.append({
            "period":        item.get("period"),
            "date":          item.get("date"),
            "quarter":       item.get("quarter"),
            "year":          item.get("year"),
            "eps_estimate":  item.get("estimate"),
            "eps_actual":    item.get("actual"),
            "eps_surprise":  item.get("surprisePercent"),
            "revenue_estimate": item.get("revenueEstimate"),
            "revenue_actual":   item.get("revenueActual"),
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Finnhub /stock/earnings",
        "earnings_history": records,
        "count": len(records),
    }


# ── Analyst Ratings ────────────────────────────────────────────────────────────

@handle_api_errors("Finnhub get_analyst_ratings_finnhub")
def get_analyst_ratings_finnhub(ticker: str) -> dict:
    """
    Fetch analyst recommendation trends (strong buy / buy / hold / sell / strong sell counts).

    Uses /stock/recommendation which returns monthly aggregated analyst consensus.
    Returns the 4 most recent months. Has its own 15s timeout baked in via urlopen.

    If this call fails, agents are instructed to use web search for current consensus.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with analyst recommendation trend data.
    """
    data = _finnhub_get("/stock/recommendation", {"symbol": ticker})

    records = []
    for item in (data if isinstance(data, list) else [])[:4]:
        total = (
            (item.get("strongBuy") or 0) +
            (item.get("buy") or 0) +
            (item.get("hold") or 0) +
            (item.get("sell") or 0) +
            (item.get("strongSell") or 0)
        )
        records.append({
            "period":       item.get("period"),
            "strong_buy":   item.get("strongBuy"),
            "buy":          item.get("buy"),
            "hold":         item.get("hold"),
            "sell":         item.get("sell"),
            "strong_sell":  item.get("strongSell"),
            "total_analysts": total,
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "Finnhub /stock/recommendation",
        "recommendation_trends": records,
        "note": (
            "Counts by category for the last 4 months. "
            "If any field shows [ERROR], search the web for current analyst consensus and price targets."
        ),
    }
