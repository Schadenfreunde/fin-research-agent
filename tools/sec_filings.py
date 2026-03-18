"""
sec_filings.py — Tools for fetching SEC filings via the free EDGAR API.

No API key required. Uses the public SEC EDGAR REST API.
Rate limit: 10 requests/second (SEC requirement — we respect this automatically).
"""

import os
import time
import json
import datetime
import threading
import urllib.request
import urllib.parse
from typing import Optional

# SEC EDGAR requires a User-Agent header identifying who is making the request.
# Set SEC_USER_AGENT in your config.yaml (google_cloud.sec_user_agent) — deploy.sh
# passes it to Cloud Run as an env var. Must include a real contact email per SEC policy.
_EDGAR_USER_AGENT = os.environ.get("SEC_USER_AGENT", "FinResearchAgent your-email@example.com")
_EDGAR_BASE = "https://data.sec.gov"
_EDGAR_SUBMISSIONS = f"{_EDGAR_BASE}/submissions"
_EDGAR_COMPANY_FACTS = f"{_EDGAR_BASE}/api/xbrl/companyfacts"

# Thread-safe rate limiter for SEC's 10 req/s limit.
# threading.Lock ensures correctness when calls are parallelised via run_in_executor.
_EDGAR_LOCK = threading.Lock()
_LAST_REQUEST_TIME = 0.0


def _edgar_get(url: str) -> dict:
    """Make a rate-limited GET request to EDGAR. Enforces 10 req/sec limit.

    Returns the parsed JSON dict on success.
    On HTTP errors (404 = concept not found, 429 = rate-limited, 503 = EDGAR down)
    or network errors, returns {"_edgar_error": "<reason>", "_url": "<url>"} rather
    than raising. All callers use .get() with defaults, so missing keys are handled
    gracefully — the agent sees empty data and notes it rather than crashing.
    """
    global _LAST_REQUEST_TIME
    with _EDGAR_LOCK:
        elapsed = time.time() - _LAST_REQUEST_TIME
        if elapsed < 0.11:  # 10 requests/second = 0.1s gap; add a small buffer
            time.sleep(0.11 - elapsed)
        _LAST_REQUEST_TIME = time.time()

    req = urllib.request.Request(url, headers={"User-Agent": _EDGAR_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
        return data
    except urllib.error.HTTPError as e:
        return {"_edgar_error": f"HTTP {e.code}: {e.reason}", "_url": url}
    except urllib.error.URLError as e:
        return {"_edgar_error": f"URLError: {e.reason}", "_url": url}
    except Exception as e:
        return {"_edgar_error": str(e), "_url": url}


def _get_cik(ticker: str) -> str:
    """
    Look up the SEC CIK (Central Index Key) for a given ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        Zero-padded 10-digit CIK string.

    Raises:
        ValueError: If the ticker is not found or EDGAR is unavailable.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    data = _edgar_get(url)

    # _edgar_get returns {"_edgar_error": ...} on HTTP/network failure
    if "_edgar_error" in data:
        raise ValueError(f"EDGAR company list unavailable: {data['_edgar_error']}")

    ticker_upper = ticker.upper()
    for entry in data.values():
        # Guard against non-dict values in case the response is malformed
        if isinstance(entry, dict) and entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            return cik

    raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR company list.")


def get_recent_filings(ticker: str, form_types: Optional[list] = None) -> dict:
    """
    Fetch a list of recent SEC filings for a company.

    Args:
        ticker: Stock ticker symbol
        form_types: List of form types to filter (e.g., ["10-K", "10-Q", "DEF 14A"])
                    If None, returns all recent filings.

    Returns:
        Dictionary with filing metadata including accession numbers and dates.

    Note:
        Results are capped per form type to prevent context bloat. Large-cap companies
        like AAPL can have 300+ Form 4 insider filings in EDGAR's recent submissions
        window. Without a cap, this section alone can exceed 200K input tokens.
    """
    if form_types is None:
        form_types = ["10-K", "10-Q", "DEF 14A", "8-K", "4"]

    # Max filings to return per form type — keeps the section token-efficient.
    # Agents care about recency, not exhaustive history; adjust in config if needed.
    _PER_FORM_LIMIT: dict[str, int] = {
        "10-K":    3,   # annual reports: last 3 years
        "10-Q":    4,   # quarterly reports: last 4 quarters
        "DEF 14A": 2,   # proxy statements: last 2
        "8-K":    10,   # material events: last 10
        "4":      15,   # insider transactions: last 15
    }
    _DEFAULT_LIMIT = 5  # fallback for any form type not listed above

    cik = _get_cik(ticker)
    url = f"{_EDGAR_SUBMISSIONS}/CIK{cik}.json"
    data = _edgar_get(url)

    filings = data.get("filings", {}).get("recent", {})
    form_list = filings.get("form", [])
    date_list = filings.get("filingDate", [])
    accession_list = filings.get("accessionNumber", [])
    description_list = filings.get("primaryDocument", [])

    results = []
    form_counts: dict[str, int] = {}
    for form, date, accession, doc in zip(form_list, date_list, accession_list, description_list):
        if form not in form_types:
            continue
        limit = _PER_FORM_LIMIT.get(form, _DEFAULT_LIMIT)
        if form_counts.get(form, 0) >= limit:
            continue
        form_counts[form] = form_counts.get(form, 0) + 1
        accession_clean = accession.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession_clean}/{doc}"
        )
        results.append({
            "form": form,
            "filing_date": date,
            "accession_number": accession,
            "primary_document_url": filing_url,
            "viewer_url": (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type={urllib.parse.quote(form)}&dateb=&owner=include&count=10"
            ),
        })

    return {
        "ticker": ticker,
        "cik": cik,
        "fetched_date": datetime.date.today().isoformat(),
        "filings_requested": form_types,
        "filings_found": results,
        "count": len(results),
    }


def get_company_facts(ticker: str) -> dict:
    """
    Fetch all structured XBRL financial facts for a company from EDGAR.
    This is the most comprehensive financial dataset available from SEC.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with all reported financial facts across all periods.
        Note: Large response — typically 1–5 MB of data.
    """
    cik = _get_cik(ticker)
    url = f"{_EDGAR_COMPANY_FACTS}/CIK{cik}.json"
    data = _edgar_get(url)

    return {
        "ticker": ticker,
        "cik": cik,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL Company Facts",
        "entity_name": data.get("entityName"),
        "facts": data.get("facts", {}),
    }


def get_specific_fact(ticker: str, concept: str, taxonomy: str = "us-gaap") -> dict:
    """
    Fetch a specific financial concept from EDGAR XBRL data.
    Useful for pulling a single line item (e.g., SBC, revenue, shares outstanding).

    Args:
        ticker: Stock ticker symbol
        concept: XBRL concept name (e.g., "ShareBasedCompensation", "Revenues",
                 "CommonStockSharesOutstanding", "NetIncomeLoss")
        taxonomy: Taxonomy namespace — "us-gaap" (default) or "dei"

    Returns:
        Dictionary with all historical values for this concept.

    Common concepts:
        - ShareBasedCompensation — stock-based compensation expense
        - Revenues — total revenues
        - NetIncomeLoss — GAAP net income
        - OperatingIncomeLoss — GAAP operating income
        - CashAndCashEquivalentsAtCarryingValue — cash on hand
        - LongTermDebt — total long-term debt
        - CommonStockSharesOutstanding — diluted shares outstanding
        - GoodwillAndIntangibleAssetsNet — goodwill + intangibles
        - ResearchAndDevelopmentExpense — R&D expense
    """
    cik = _get_cik(ticker)
    url = f"{_EDGAR_BASE}/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
    data = _edgar_get(url)

    # Extract the annual (10-K) values for easy consumption
    units = data.get("units", {})
    usd_values = units.get("USD", units.get("shares", []))

    annual_values = [
        {
            "end_date": item.get("end"),
            "value": item.get("val"),
            "filed": item.get("filed"),
            "form": item.get("form"),
            "accession": item.get("accn"),
        }
        for item in usd_values
        if item.get("form") in ("10-K", "10-Q")
    ]

    # Sort by end date descending (most recent first)
    annual_values.sort(key=lambda x: x.get("end_date", ""), reverse=True)

    return {
        "ticker": ticker,
        "concept": concept,
        "taxonomy": taxonomy,
        "fetched_date": datetime.date.today().isoformat(),
        "source": f"SEC EDGAR XBRL {taxonomy}/{concept}",
        "label": data.get("label"),
        "description": data.get("description"),
        "values": annual_values[:16],  # Last 16 periods (8 annual + 8 quarterly)
    }


def get_insider_transactions(ticker: str) -> dict:
    """
    Fetch recent Form 4 insider transaction filings.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with recent insider transaction filings (links and dates).
        Note: Full parsing of Form 4 XML requires additional processing.
        Returns filing metadata for the Earnings Quality agent to reference.
    """
    cik = _get_cik(ticker)
    url = f"{_EDGAR_SUBMISSIONS}/CIK{cik}.json"
    data = _edgar_get(url)

    filings = data.get("filings", {}).get("recent", {})
    form_list = filings.get("form", [])
    date_list = filings.get("filingDate", [])
    accession_list = filings.get("accessionNumber", [])

    form4_filings = []
    for form, date, accession in zip(form_list, date_list, accession_list):
        if form == "4" and len(form4_filings) < 20:  # Last 20 Form 4s
            accession_clean = accession.replace("-", "")
            form4_filings.append({
                "form": "4",
                "filing_date": date,
                "accession_number": accession,
                "viewer_url": (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/"
                ),
            })

    return {
        "ticker": ticker,
        "cik": cik,
        "fetched_date": datetime.date.today().isoformat(),
        "form4_filings": form4_filings,
        "count": len(form4_filings),
        "note": (
            "Form 4 viewer links provided. Full transaction details (buy/sell amounts, "
            "prices) available at each filing URL. Use web_search tool to retrieve "
            "parsed insider transaction summaries from financial data providers."
        ),
    }
