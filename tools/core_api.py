"""
core_api.py — Academic paper search using CORE (core.ac.uk) with automatic
fallback to Semantic Scholar when CORE hits rate limits or is unavailable.

CORE aggregates millions of open-access research papers from repositories
worldwide. The REST API returns structured paper metadata without consuming
Vertex AI quota.

If CORE is unavailable (rate limit, key error, network issue, or 0 results),
this module automatically falls back to Semantic Scholar — no pipeline
interruption and no changes needed in agent prompts.

Free tiers:
  CORE:             ~10,000 requests/month, ~1 request/second
  Semantic Scholar: ~100 req/5 min (no key), ~1,000 req/5 min (with key)

Secrets (Secret Manager):
  CORE_API_KEY:              'core-api-key'
  SEMANTIC_SCHOLAR_API_KEY:  'semantics-scholar-api-key' (optional — SS works
                             without a key, so fallback always available)

Auth: CORE uses Bearer token; Semantic Scholar uses x-api-key (optional).
Docs: https://api.core.ac.uk/docs/v3
      https://api.semanticscholar.org/graph/v1
"""

import datetime
import urllib.parse

from tools.http_client import get_api_key, api_get


_CORE_BASE_URL = "https://api.core.ac.uk/v3"


def _core_get(path: str, params: dict) -> dict:
    """
    Make an authenticated GET request to the CORE API.
    Uses a 30-second timeout (academic APIs are slower than financial APIs).
    Auth: 'Authorization: Bearer {key}' header.
    """
    key = get_api_key("CORE_API_KEY", "core-api-key")
    query_string = urllib.parse.urlencode(params)
    url = f"{_CORE_BASE_URL}{path}?{query_string}"
    return api_get(
        url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=30,
        source_label="CORE",
    )


def _semantic_scholar_fallback(query: str, num_results: int, core_error: str) -> dict:
    """
    Try Semantic Scholar when CORE is unavailable.
    Semantic Scholar's API key is optional, so this fallback is always available.
    Returns a combined error dict only if Semantic Scholar also fails.
    """
    from tools.semantic_scholar_api import search_academic_semantic_scholar
    ss_result = search_academic_semantic_scholar(query, num_results)
    if ss_result.get("papers"):
        ss_result["note"] = (
            f"CORE unavailable ({core_error}); "
            f"automatically fell back to Semantic Scholar."
        )
        return ss_result
    # Both failed — return a combined error dict so the pipeline can continue
    return {
        "query": query,
        "total_found": 0,
        "papers": [],
        "error": (
            f"CORE: {core_error} | "
            f"Semantic Scholar: {ss_result.get('error', 'no results returned')}"
        ),
        "source": "CORE (core.ac.uk) + Semantic Scholar fallback",
        "fetched_date": datetime.date.today().isoformat(),
    }


def search_academic_core(query: str, num_results: int = 5) -> dict:
    """
    Search for open-access academic research papers.

    Tries CORE (core.ac.uk) first. If CORE is unavailable (rate limit, key
    error, network issue) or returns 0 results, automatically falls back to
    Semantic Scholar — no pipeline interruption, no changes needed in prompts.

    Does NOT consume Vertex AI quota — all calls are direct REST API calls.

    Use for:
      - Economic/macro topics: "German GDP growth determinants structural factors"
      - Company/sector topics: "platform business model network effects competitive moat"
      - Technology topics: "large language models enterprise adoption productivity"
      - Policy topics: "monetary policy transmission mechanism GDP"

    Args:
        query: Research topic or keywords
        num_results: Maximum papers to return (1–10, default 5 to conserve CORE quota)

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
              "url": str or None,        # direct download or landing page URL
              "journal": str or None,
            },
            ...
          ],
          "source": "CORE (core.ac.uk)",    # or "Semantic Scholar ..." if fallback used
          "fetched_date": str,              # ISO date string
          "note": str,                      # present only when fallback was triggered
        }
    """
    try:
        num_results = max(1, min(num_results, 10))  # clamp to 1–10
        data = _core_get("/search/works", {"q": query, "limit": num_results})

        papers = []
        for result in data.get("results", []):
            # Extract authors (list of dicts with 'name' key, or list of strings)
            raw_authors = result.get("authors") or []
            author_names = []
            for a in raw_authors:
                if isinstance(a, dict):
                    name = a.get("name") or a.get("fullName") or ""
                elif isinstance(a, str):
                    name = a
                else:
                    name = ""
                if name:
                    author_names.append(name)

            # Cap at 5 authors + "et al." for readability in citations
            if len(author_names) > 5:
                author_names = author_names[:5] + ["et al."]

            # Extract abstract snippet
            abstract = result.get("abstract") or ""
            abstract_snippet = (abstract[:300] + "...") if len(abstract) > 300 else abstract

            # Extract journal name from the first entry in the journals list
            journals = result.get("journals") or []
            journal = None
            if journals and isinstance(journals[0], dict):
                journal = journals[0].get("title") or journals[0].get("name")

            # URL: prefer downloadUrl, fall back to landing page
            url = result.get("downloadUrl") or result.get("fullTextIdentifier")
            if not url:
                paper_id = result.get("id")
                if paper_id:
                    url = f"https://core.ac.uk/works/{paper_id}"

            papers.append({
                "title": result.get("title") or "",
                "authors": author_names,
                "year": result.get("year") or result.get("publishedDate", "")[:4] or None,
                "abstract_snippet": abstract_snippet,
                "doi": result.get("doi"),
                "url": url,
                "journal": journal,
            })

        # If CORE returned no papers, try Semantic Scholar as fallback
        if not papers:
            return _semantic_scholar_fallback(query, num_results, "CORE returned 0 results")

        return {
            "query": query,
            "total_found": data.get("totalHits", len(papers)),
            "papers": papers,
            "source": "CORE (core.ac.uk)",
            "fetched_date": datetime.date.today().isoformat(),
        }

    except EnvironmentError:
        # CORE key not configured — try Semantic Scholar (its key is optional)
        return _semantic_scholar_fallback(query, num_results, "CORE_API_KEY not configured")

    except Exception as e:
        return _semantic_scholar_fallback(query, num_results, str(e))
