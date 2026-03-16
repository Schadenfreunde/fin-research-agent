"""
semantic_scholar_api.py — Academic paper search via the Semantic Scholar API.

Semantic Scholar indexes 200+ million academic papers across all fields of
science. The API returns structured paper metadata (title, authors, year,
abstract, DOI, journal, URL) without consuming any Vertex AI quota.

This module is used as the automatic fallback inside search_academic_core()
when CORE API hits rate limits or is otherwise unavailable. It can also be
called directly.

Free tiers:
  Without API key: ~100 requests per 5 minutes
  With API key:    ~1,000 requests per 5 minutes

API key is stored in Secret Manager as 'semantics-scholar-api-key' and
mounted as SEMANTIC_SCHOLAR_API_KEY env var on Cloud Run.
The key is OPTIONAL — the API works without it at reduced rate limits.

Auth: x-api-key header (optional).
Docs: https://api.semanticscholar.org/graph/v1
"""

import datetime
import urllib.parse

from tools.http_client import get_api_key, api_get


_BASE_URL = "https://api.semanticscholar.org/graph/v1"
_FIELDS   = "title,abstract,authors,year,externalIds,url,journal"


def _ss_get(path: str, params: dict) -> dict:
    """Make a GET request to the Semantic Scholar API. Attaches key if available."""
    key = get_api_key("SEMANTIC_SCHOLAR_API_KEY", "semantics-scholar-api-key", required=False)
    query_string = urllib.parse.urlencode(params)
    url = f"{_BASE_URL}{path}?{query_string}"
    headers = {"Accept": "application/json"}
    if key:
        headers["x-api-key"] = key
    return api_get(url, headers=headers, timeout=30, source_label="SemanticScholar")


def search_academic_semantic_scholar(query: str, num_results: int = 5) -> dict:
    """
    Search Semantic Scholar for academic papers.

    Returns structured paper metadata in the same format as search_academic_core(),
    making it a drop-in fallback. Does NOT consume Vertex AI quota.

    Args:
        query: Research topic or keywords (e.g., "German GDP growth determinants")
        num_results: Maximum papers to return (1–10, default 5)

    Returns:
        {
          "query": str,
          "total_found": int,
          "papers": [
            {
              "title": str,
              "authors": [str, ...],     # up to 5 names; "et al." appended if more
              "year": int or None,
              "abstract_snippet": str,   # first 300 chars of abstract
              "doi": str or None,
              "url": str or None,
              "journal": str or None,
            },
            ...
          ],
          "source": "Semantic Scholar (semanticscholar.org)",
          "fetched_date": str,          # ISO date string
        }
    """
    try:
        num_results = max(1, min(num_results, 10))
        data = _ss_get("/paper/search", {
            "query": query,
            "limit": num_results,
            "fields": _FIELDS,
        })

        papers = []
        for result in data.get("data", []):
            # Authors
            raw_authors = result.get("authors") or []
            author_names = [a.get("name", "") for a in raw_authors if isinstance(a, dict)]
            author_names = [n for n in author_names if n]
            if len(author_names) > 5:
                author_names = author_names[:5] + ["et al."]

            # Abstract snippet
            abstract = result.get("abstract") or ""
            abstract_snippet = (abstract[:300] + "...") if len(abstract) > 300 else abstract

            # DOI
            ext_ids = result.get("externalIds") or {}
            doi = ext_ids.get("DOI") or ext_ids.get("doi") or None

            # URL — prefer Semantic Scholar's URL, fall back to DOI link
            url = result.get("url") or (f"https://doi.org/{doi}" if doi else None)

            # Journal name
            journal_obj = result.get("journal") or {}
            journal = journal_obj.get("name") if isinstance(journal_obj, dict) else None

            papers.append({
                "title": result.get("title") or "",
                "authors": author_names,
                "year": result.get("year"),
                "abstract_snippet": abstract_snippet,
                "doi": doi,
                "url": url,
                "journal": journal,
            })

        return {
            "query": query,
            "total_found": data.get("total", len(papers)),
            "papers": papers,
            "source": "Semantic Scholar (semanticscholar.org)",
            "fetched_date": datetime.date.today().isoformat(),
        }

    except Exception as e:
        return {
            "query": query,
            "total_found": 0,
            "papers": [],
            "error": str(e),
            "source": "Semantic Scholar (semanticscholar.org)",
            "fetched_date": datetime.date.today().isoformat(),
        }
