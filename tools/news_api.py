"""
news_api.py — NewsAPI (newsapi.org) integration for real-time financial news.

API key is loaded from Google Secret Manager via environment variable NEWS_API_KEY.
Register free at https://newsapi.org (100 requests/day free; real-time on paid plans).

Two entry points:
  get_company_news_newsapi()  — used by the equity pipeline for company-specific news
  get_topic_news_newsapi()    — used by the macro pipeline for topic-specific news
"""

import datetime
import urllib.parse

from tools.http_client import get_api_key, api_get, handle_api_errors

_NEWS_API_BASE = "https://newsapi.org/v2"

# Premium financial news domains — prioritized over general media
_FINANCIAL_DOMAINS = (
    "wsj.com,bloomberg.com,ft.com,reuters.com,"
    "barrons.com,cnbc.com,marketwatch.com,businessinsider.com,"
    "seekingalpha.com,thestreet.com,investing.com"
)

# Load key at module level — required=False so the pipeline continues if not set
_NEWS_API_KEY = get_api_key("NEWS_API_KEY", "news-api-key", required=False)


def _news_api_get(endpoint: str, params: dict) -> dict:
    """
    Internal helper: build a NewsAPI URL with the API key header and call api_get.

    NewsAPI supports both `apiKey=` query param and `X-Api-Key` header.
    We use the query-param approach to stay compatible with api_get().
    """
    params["apiKey"] = _NEWS_API_KEY
    url = f"{_NEWS_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    return api_get(url, timeout=15, source_label=f"NewsAPI/{endpoint}")


@handle_api_errors("NewsAPI get_company_news_newsapi")
def get_company_news_newsapi(
    company_name: str,
    ticker: str,
    days_back: int = 30,
) -> dict:
    """
    Fetch recent news articles about a company from NewsAPI.

    Searches premium financial outlets for the company name and ticker.
    Returns up to 15 articles with title, source, URL, and publication date.

    Args:
        company_name: Full legal company name (e.g., "Apple Inc.")
        ticker:       Stock ticker (e.g., "AAPL") — used as fallback search term
        days_back:    How many days of articles to retrieve (default 30)

    Returns:
        Dictionary with article list and metadata, or error dict on failure.
    """
    if not _NEWS_API_KEY:
        return {
            "ticker": ticker,
            "error": "NEWS_API_KEY not configured — skipping NewsAPI",
            "articles": [],
            "source": "NewsAPI",
        }

    from_date = (
        datetime.date.today() - datetime.timedelta(days=days_back)
    ).isoformat()

    # Use exact phrase for company name; add ticker as fallback term
    query = f'"{company_name}" OR "{ticker}" earnings OR revenue OR guidance OR analyst'

    data = _news_api_get(
        "everything",
        {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 15,
            "from": from_date,
            "domains": _FINANCIAL_DOMAINS,
        },
    )

    articles = [
        {
            "title": a.get("title", ""),
            "source": a.get("source", {}).get("name", ""),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
            "description": a.get("description", ""),
        }
        for a in data.get("articles", [])
        if a.get("title") and "[Removed]" not in a.get("title", "")
    ]

    return {
        "ticker": ticker,
        "company_name": company_name,
        "query_period_days": days_back,
        "total_results": data.get("totalResults", 0),
        "articles_returned": len(articles),
        "articles": articles,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "NewsAPI (newsapi.org)",
    }


@handle_api_errors("NewsAPI get_topic_news_newsapi")
def get_topic_news_newsapi(
    topic: str,
    days_back: int = 30,
) -> dict:
    """
    Fetch recent news articles about a macro research topic from NewsAPI.

    Searches premium financial and general news sources for the topic.
    Returns up to 15 articles suitable for macro context.

    Args:
        topic:     Macro research topic (e.g., "US interest rate outlook")
        days_back: How many days of articles to retrieve (default 30)

    Returns:
        Dictionary with article list and metadata, or error dict on failure.
    """
    if not _NEWS_API_KEY:
        return {
            "topic": topic,
            "error": "NEWS_API_KEY not configured — skipping NewsAPI",
            "articles": [],
            "source": "NewsAPI",
        }

    from_date = (
        datetime.date.today() - datetime.timedelta(days=days_back)
    ).isoformat()

    # Broader domains for macro topics (include policy/economic outlets)
    macro_domains = (
        _FINANCIAL_DOMAINS
        + ",economist.com,imf.org,worldbank.org,ecb.europa.eu,federalreserve.gov"
    )

    data = _news_api_get(
        "everything",
        {
            "q": topic,
            "language": "en",
            "sortBy": "relevancy",
            "pageSize": 15,
            "from": from_date,
            "domains": macro_domains,
        },
    )

    articles = [
        {
            "title": a.get("title", ""),
            "source": a.get("source", {}).get("name", ""),
            "url": a.get("url", ""),
            "published_at": a.get("publishedAt", ""),
            "description": a.get("description", ""),
        }
        for a in data.get("articles", [])
        if a.get("title") and "[Removed]" not in a.get("title", "")
    ]

    return {
        "topic": topic,
        "query_period_days": days_back,
        "total_results": data.get("totalResults", 0),
        "articles_returned": len(articles),
        "articles": articles,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "NewsAPI (newsapi.org)",
    }
