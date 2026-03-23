"""
web_search.py — Web search tool using Vertex AI Google Search grounding.

Uses the google-genai SDK (not the deprecated vertexai.generative_models SDK)
with the google_search tool type. No additional API key needed — search is
included in the Vertex AI project billing.
"""

import os
import time
import random
import datetime
import threading
from typing import Optional

from google import genai
from google.genai import types as genai_types

# ── Inter-call throttle ────────────────────────────────────────────────────────
# search_web() calls client.models.generate_content() which consumes Vertex AI
# quota (QPM). On the free tier this quota is very low (~5 QPM for Gemini Flash).
# Multiple agents run web searches independently, so without a global throttle
# they burst many calls at once and hit 429 RESOURCE_EXHAUSTED errors.
#
# This lock + timestamp enforces a minimum gap between successive search_web()
# calls across the entire process, regardless of which agent triggered them.
# threading.Lock is used (not asyncio) because search_web is synchronous and
# runs in the default executor thread pool when called from async agents.
_SEARCH_LOCK = threading.Lock()
_LAST_SEARCH_TIME: float = 0.0
# Minimum seconds between successive search_web() calls across all threads.
# Configurable via SEARCH_MIN_INTERVAL env var (set from config.yaml → search.min_interval_seconds).
# Default: 2.0s for paid Vertex AI accounts. Set to 10.0 on free-tier accounts.
_MIN_SEARCH_INTERVAL: float = float(os.environ.get("SEARCH_MIN_INTERVAL", "2.0"))


def _get_project_id() -> str:
    """Get Google Cloud project ID from environment."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if not project_id:
        raise EnvironmentError(
            "GOOGLE_CLOUD_PROJECT not set. This is automatically set in Cloud Run."
        )
    return project_id


def _get_client() -> genai.Client:
    """Create a google-genai client configured for Vertex AI.

    Always uses us-central1 for the search model (gemini-2.5-flash), which is
    a regional model and is not available on the global endpoint used for Gemini 3.x.
    GOOGLE_CLOUD_LOCATION may be set to "global" for Gemini 3 ADK agents but must
    NOT be used here.
    """
    project_id = _get_project_id()
    return genai.Client(vertexai=True, project=project_id, location="us-central1")


# Max retries for 429 RESOURCE_EXHAUSTED errors in search_web.
# Each retry uses time.sleep() since this is a synchronous tool function.
# Backoff: 15s → 30s → 60s → 120s → 180s (capped). Budget fits within the
# 600s agent timeout when retries are infrequent.
_MAX_SEARCH_RETRIES = 4


def search_web(query: str, num_results: int = 10) -> dict:
    """
    Search the web using Vertex AI Google Search grounding.
    Returns grounded results with citations.

    Throttled: enforces a minimum 10-second gap between calls process-wide to
    avoid burst 429 RESOURCE_EXHAUSTED errors on the free-tier QPM quota.

    Automatically retries up to _MAX_SEARCH_RETRIES times on 429 quota errors
    with exponential backoff (max 180s per retry). Non-quota errors return an
    error dict immediately.

    Args:
        query: Search query string
        num_results: Approximate number of results to retrieve (1–20)

    Returns:
        Dictionary with search results including text and source URLs.

    Example queries:
        - "{company} earnings call transcript Q4 2024"
        - "{ticker} SEC 10-K annual report 2024 site:sec.gov"
        - "{company} competitor analysis 2024"
        - "FRED CPIAUCSL inflation data {date}"
    """
    global _LAST_SEARCH_TIME

    # ── Throttle: enforce minimum gap between successive calls ─────────────────
    # Acquire the lock so only one caller checks/updates the timestamp at a time.
    # All other callers block here until the minimum interval has elapsed.
    with _SEARCH_LOCK:
        now = time.time()
        elapsed = now - _LAST_SEARCH_TIME
        if elapsed < _MIN_SEARCH_INTERVAL:
            wait_gap = _MIN_SEARCH_INTERVAL - elapsed
            print(f"[search_web] Throttle: waiting {wait_gap:.1f}s before next call...")
            time.sleep(wait_gap)
        _LAST_SEARCH_TIME = time.time()

    client = _get_client()
    last_error = None
    query_preview = query[:70] + "..." if len(query) > 70 else query

    for attempt in range(_MAX_SEARCH_RETRIES):
        # Exponential backoff before retrying (skip wait on first attempt)
        if attempt > 0:
            wait = min(180, 15 * attempt + random.uniform(0, 10))
            print(
                f"[search_web] 429 retry {attempt}/{_MAX_SEARCH_RETRIES - 1} "
                f"for query '{query_preview}', waiting {wait:.0f}s..."
            )
            time.sleep(wait)

        try:
            # Use a 60-second timeout for the search request to prevent infinite hangs
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=(
                    f"Search for and summarize the following: {query}\n\n"
                    f"Provide up to {num_results} relevant results. "
                    f"For each result, include: title, URL, date (if available), and a brief summary. "
                    f"Focus on authoritative sources."
                ),
                config=genai_types.GenerateContentConfig(
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                    temperature=0,
                    http_options={'timeout': 60000}  # 60 seconds in milliseconds
                ),
            )

        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str

            if is_rate_limit and attempt < _MAX_SEARCH_RETRIES - 1:
                # Rate limit — will retry after backoff
                last_error = e
                continue

            # Non-429 error, or final retry exhausted — return error dict
            print(f"[search_web] Error for query '{query_preview}': {err_str}")
            return {
                "query": query,
                "fetched_date": datetime.date.today().isoformat(),
                "summary": f"Search failed: {err_str}",
                "sources": [],
                "source_count": 0,
            }

        # ── Successful response — extract results ──────────────────────────────

        # Extract grounding metadata (source citations)
        sources = []
        if response.candidates:
            candidate = response.candidates[0]
            grounding_meta = getattr(candidate, "grounding_metadata", None)
            if grounding_meta and getattr(grounding_meta, "grounding_chunks", None):
                for chunk in grounding_meta.grounding_chunks:
                    web = getattr(chunk, "web", None)
                    if web:
                        sources.append({
                            "title": getattr(web, "title", ""),
                            "url": getattr(web, "uri", ""),
                        })

        # Extract text parts explicitly — response may contain function_call parts
        # alongside text when google_search grounding is active. Accessing .text
        # directly on such a response emits a warning; iterate parts instead.
        text_parts = []
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts or []
            text_parts = [p.text for p in parts if getattr(p, "text", None)]
        summary = "\n".join(text_parts)

        return {
            "query": query,
            "fetched_date": datetime.date.today().isoformat(),
            "summary": summary,
            "sources": sources,
            "source_count": len(sources),
        }

    # All retries exhausted on persistent rate-limit errors
    print(
        f"[search_web] All {_MAX_SEARCH_RETRIES} retries exhausted for "
        f"query '{query_preview}' (persistent 429)"
    )
    return {
        "query": query,
        "fetched_date": datetime.date.today().isoformat(),
        "summary": (
            f"Search quota exhausted after {_MAX_SEARCH_RETRIES} retries. "
            f"Last error: {last_error}. Use pre-gathered structured data for this section."
        ),
        "sources": [],
        "source_count": 0,
    }


def search_news(ticker_or_topic: str, days_back: int = 90,
                company_name: Optional[str] = None) -> dict:
    """
    Search for recent news about a stock or macro topic.

    Args:
        ticker_or_topic: Ticker symbol (e.g., "AAPL") or topic ("US interest rates")
        days_back: How many days back to search (default: 90 days)
        company_name: Optional full company name (e.g., "Apple Inc."). If provided,
                      uses this in the search query instead of the ticker symbol for
                      significantly better results — tickers like "AAPL" produce fewer
                      and less relevant hits than "Apple Inc." in news searches.

    Returns:
        Dictionary with news summaries and source URLs.
    """
    # Prefer company_name for better search quality; fall back to ticker_or_topic
    search_subject = company_name if company_name else ticker_or_topic
    cutoff_date = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    query = (
        f"Latest news and analysis about {search_subject} after {cutoff_date}. "
        f"Focus on: earnings, analyst reports, regulatory developments, competitive dynamics, "
        f"and material business events. Sources: WSJ, FT, Bloomberg, Reuters, Barron's."
    )
    return search_web(query, num_results=15)


def search_earnings_transcript(ticker: str, quarter: Optional[str] = None,
                                company_name: Optional[str] = None) -> dict:
    """
    Search for earnings call transcript for a company.

    Args:
        ticker: Stock ticker symbol
        quarter: Quarter and year (e.g., "Q4 2024"). If None, searches for most recent.
        company_name: Optional full company name (e.g., "Apple Inc."). If provided,
                      uses this in the search query for better results.

    Returns:
        Dictionary with transcript summary and source.
    """
    # Prefer company_name: "Apple Inc. earnings call" finds transcripts more reliably
    # than "AAPL earnings call" which can return ticker-page results instead.
    search_subject = company_name if company_name else ticker
    if quarter:
        query = f"{search_subject} earnings call transcript {quarter} management commentary guidance"
    else:
        query = f"{search_subject} most recent earnings call transcript management commentary guidance"

    return search_web(query, num_results=5)


def search_analyst_reports(ticker: str, company_name: Optional[str] = None) -> dict:
    """
    Search for analyst research reports and price target changes.

    Args:
        ticker: Stock ticker symbol
        company_name: Optional full company name (e.g., "Apple Inc."). If provided,
                      uses this in the search query for better results.

    Returns:
        Dictionary with analyst report summaries and ratings.
    """
    # Prefer company_name for more relevant analyst report results
    search_subject = company_name if company_name else ticker
    query = (
        f"{search_subject} analyst report price target upgrade downgrade 2024 2025 "
        f"buy sell hold consensus Wall Street"
    )
    return search_web(query, num_results=10)


def search_academic_papers(topic: str) -> dict:
    """
    Search for academic papers and expert analyses on a topic.
    Useful for the Data Harvester's academic/expert source requirement.

    Args:
        topic: Research topic (e.g., "agricultural commodity price cycles crop insurance")

    Returns:
        Dictionary with academic source summaries.
    """
    query = (
        f"academic research paper expert analysis {topic} "
        f"site:nber.org OR site:imf.org OR site:bis.org OR site:federalreserve.gov "
        f"OR site:ssrn.com OR site:jstor.org"
    )
    return search_web(query, num_results=8)


def search_competitor_filings(company_name: str, competitors: list[str]) -> dict:
    """
    Search for competitor annual reports, earnings calls, and investor presentations.

    Args:
        company_name: Name of the company being analyzed
        competitors: List of competitor names or tickers

    Returns:
        Dictionary with competitor source summaries.
    """
    competitor_str = " OR ".join(competitors)
    query = (
        f"({competitor_str}) annual report earnings call investor presentation "
        f"2024 2025 competitive positioning market share"
    )
    return search_web(query, num_results=10)
