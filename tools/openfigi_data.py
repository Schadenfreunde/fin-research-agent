"""
openfigi_data.py — OpenFIGI API for instrument identification and metadata.

API key loaded from Secret Manager via environment variable OPENFIGI_API_KEY.
Register free at https://www.openfigi.com/api

OpenFIGI assigns globally unique, persistent identifiers (FIGIs) to financial
instruments. Useful for:
  - Confirming the exact instrument being analysed (share class, exchange, ADR vs. ordinary)
  - Retrieving standardised security type and market sector metadata
  - Cross-referencing ISIN / CUSIP / SEDOL to ticker-level identity

Rate limits:
  With API key:    25 requests / 6 seconds (≈ 250/min); 100 jobs per request
  Without API key: 25 requests / minute;                 10 jobs per request
"""

import json
import time
import datetime
import logging
import urllib.request
import urllib.error

from tools.http_client import get_api_key, handle_api_errors

logger = logging.getLogger("finresearch.openfigi")

_OPENFIGI_BASE = "https://api.openfigi.com/v3"

# required=False: pipeline continues gracefully if key is absent
_OPENFIGI_KEY = get_api_key("OPENFIGI_API_KEY", "open-figi-api-key", required=False)


def _openfigi_post(endpoint: str, payload: list, timeout: int = 15) -> list:
    """
    POST a batch of mapping jobs to the OpenFIGI API.

    Args:
        endpoint: API path (e.g., "/mapping")
        payload:  List of job dicts (e.g., [{"idType": "TICKER", "idValue": "AAPL"}])
        timeout:  Request timeout in seconds

    Returns:
        Parsed JSON list (one result dict per input job).

    Raises:
        Exception on HTTP or JSON errors (caller should use @handle_api_errors).
    """
    url = f"{_OPENFIGI_BASE}{endpoint}"
    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if _OPENFIGI_KEY:
        headers["X-OPENFIGI-APIKEY"] = _OPENFIGI_KEY

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        logger.debug(
            "OpenFIGI POST OK: %s (%.0fms)",
            endpoint,
            (time.monotonic() - start) * 1000,
        )
        return data
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(
            f"OpenFIGI HTTP {e.code}: {body_text[:200]}"
        ) from e


@handle_api_errors("OpenFIGI get_figi_mapping")
def get_figi_mapping(ticker: str, exch_code: str = "US") -> dict:
    """
    Map a stock ticker to its FIGI identifier and retrieve instrument metadata.

    Sends two jobs to the /v3/mapping endpoint:
      1. Ticker + exchange code (primary — most precise)
      2. Ticker only without exchange constraint (fallback breadth)

    Returns composite FIGI, security type, market sector, exchange code, and
    instrument name for the best match found.

    Args:
        ticker:    Stock ticker symbol (e.g., "AAPL")
        exch_code: Exchange code (default "US" for US-listed equities;
                   use "LN" for London, "GY" for German, "HK" for Hong Kong, etc.)

    Returns:
        Dictionary with FIGI data and instrument metadata, or error dict on failure.
    """
    jobs = [
        {"idType": "TICKER", "idValue": ticker, "exchCode": exch_code, "securityType": "Common Stock"},
        {"idType": "TICKER", "idValue": ticker, "exchCode": exch_code},
        {"idType": "TICKER", "idValue": ticker},  # broadest fallback
    ]

    results = _openfigi_post("/mapping", jobs)

    # Collect all instrument hits across all jobs (deduplicated by FIGI)
    seen_figis: set = set()
    instruments = []

    for job_result in results:
        if not isinstance(job_result, dict):
            continue
        for item in job_result.get("data", []):
            figi = item.get("figi", "")
            if figi and figi not in seen_figis:
                seen_figis.add(figi)
                instruments.append(
                    {
                        "figi": figi,
                        "composite_figi": item.get("compositeFIGI", ""),
                        "share_class_figi": item.get("shareClassFIGI", ""),
                        "name": item.get("name", ""),
                        "ticker": item.get("ticker", ""),
                        "exchange_code": item.get("exchCode", ""),
                        "security_type": item.get("securityType", ""),
                        "security_type_2": item.get("securityType2", ""),
                        "market_sector": item.get("marketSector", ""),
                    }
                )

    # Best match: prefer Common Stock on target exchange
    best = None
    for inst in instruments:
        if inst["exchange_code"] == exch_code and "Common Stock" in inst.get("security_type", ""):
            best = inst
            break
    if best is None and instruments:
        best = instruments[0]

    return {
        "ticker": ticker,
        "exchange_code_queried": exch_code,
        "best_match": best,
        "all_matches": instruments[:10],  # cap output at 10
        "total_matches": len(instruments),
        "fetched_date": datetime.date.today().isoformat(),
        "source": "OpenFIGI API (openfigi.com)",
        "note": (
            "FIGI is a globally unique, persistent instrument identifier. "
            "Composite FIGI groups all listings of the same share class across exchanges."
        ),
    }
