"""
polygon_data.py — Polygon.io free-tier API client.

Primary use: reliable company name, sector, and SIC code via ticker details endpoint.
Fallback OHLCV and news also available.

Free tier limits:
  - Unlimited API calls (with API key)
  - Real-time data delayed 15 minutes
  - Historical data: up to 2 years of daily/weekly bars

Env var required:
  POLYGON_API_KEY — register free at https://polygon.io
"""

import datetime
from typing import Optional

from tools.http_client import get_api_key, api_get_with_auth

_POLYGON_BASE = "https://api.polygon.io"


def _polygon_get(path: str, params: Optional[dict] = None) -> dict:
    """
    GET request to api.polygon.io. Appends apiKey automatically.

    Returns parsed JSON dict on success.
    Returns {"error": "...", "source": "polygon"} on failure — never raises.
    """
    api_key = get_api_key("POLYGON_API_KEY", "polygon-api-key", required=False)
    if not api_key:
        return {
            "error": "POLYGON_API_KEY not set — Polygon data unavailable. "
                     "Add the key to Secret Manager and redeploy.",
            "source": "polygon",
        }

    try:
        return api_get_with_auth(
            _POLYGON_BASE, path,
            auth_param=("apiKey", api_key),
            params=params,
            headers={"User-Agent": "FinResearchAgent/1.0"},
            timeout=15,
            source_label="Polygon",
        )
    except Exception as e:
        return {
            "error": f"Polygon API request failed for path '{path}': {str(e)}",
            "source": "polygon",
        }


# ── Public functions ───────────────────────────────────────────────────────────

def get_ticker_details_polygon(ticker: str) -> dict:
    """
    GET /v3/reference/tickers/{ticker}

    Returns comprehensive company metadata including:
      - name: full legal company name (use for web searches, not the ticker)
      - description: business description
      - sic_code: 4-digit SIC code
      - sic_description: human-readable industry description (sector fix)
      - market_cap: latest market capitalisation
      - total_employees: headcount
      - primary_exchange: NYSE, NASDAQ, etc.
      - list_date: IPO date
      - locale / market: us / stocks
      - homepage_url: company website

    This is the PRIMARY fix for the "sector not found" problem.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")

    Returns:
        dict with "results" key containing the ticker details, or error dict.
    """
    ticker = ticker.upper().strip()
    data = _polygon_get(f"/v3/reference/tickers/{ticker}")

    if "error" in data:
        return {
            "ticker": ticker,
            "error": data["error"],
            "source": "polygon_ticker_details",
        }

    return data


def get_historical_ohlcv_polygon(
    ticker: str,
    multiplier: int = 1,
    timespan: str = "week",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    """
    GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}

    Retrieves historical OHLCV (Open, High, Low, Close, Volume) bars.
    Free tier: up to 2 years of data, 15-minute delay on real-time.

    Args:
        ticker: Stock ticker symbol
        multiplier: Bar size multiplier (default: 1)
        timespan: Bar size unit — "minute", "hour", "day", "week", "month", "quarter", "year"
        from_date: Start date "YYYY-MM-DD" (default: 2 years ago)
        to_date: End date "YYYY-MM-DD" (default: today)

    Returns:
        dict with "results" list of OHLCV bars.
    """
    ticker = ticker.upper().strip()

    if to_date is None:
        to_date = datetime.date.today().isoformat()
    if from_date is None:
        two_years_ago = datetime.date.today() - datetime.timedelta(days=730)
        from_date = two_years_ago.isoformat()

    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 5000,
    }

    data = _polygon_get(path, params)

    if "error" in data:
        return {
            "ticker": ticker,
            "error": data["error"],
            "source": "polygon_ohlcv",
        }

    results = data.get("results", [])
    return {
        "ticker": ticker,
        "timespan": timespan,
        "from": from_date,
        "to": to_date,
        "bars_count": len(results),
        "results": results,
        "source": "polygon_ohlcv",
    }


def get_recent_news_polygon(ticker: str, limit: int = 20) -> dict:
    """
    GET /v2/reference/news?ticker={ticker}&limit={limit}

    Returns recent news articles associated with the ticker.

    Args:
        ticker: Stock ticker symbol
        limit: Max articles to return (default: 20, max: 1000 on free tier)

    Returns:
        dict with "results" list of news article objects.
    """
    ticker = ticker.upper().strip()
    params = {
        "ticker": ticker,
        "limit": min(limit, 50),  # cap at 50 for token efficiency
        "order": "desc",
        "sort": "published_utc",
    }

    data = _polygon_get("/v2/reference/news", params)

    if "error" in data:
        return {
            "ticker": ticker,
            "error": data["error"],
            "source": "polygon_news",
        }

    results = data.get("results", [])
    trimmed = [
        {
            "title": a.get("title"),
            "published_utc": a.get("published_utc"),
            "article_url": a.get("article_url"),
            "description": a.get("description", "")[:300],
            "publisher": a.get("publisher", {}).get("name"),
        }
        for a in results
    ]

    return {
        "ticker": ticker,
        "article_count": len(trimmed),
        "results": trimmed,
        "source": "polygon_news",
    }
