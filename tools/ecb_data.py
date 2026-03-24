"""
ecb_data.py -- ECB Statistical Data Warehouse via SDMX REST API.

No API key required. Uses the ECB SDMX REST API with CSV output.
Base URL: https://data-api.ecb.europa.eu/service/data/

Fetches:
  - ECB policy rates: deposit facility rate (DFR) and main refinancing rate (MRR).
  - Eurozone HICP inflation: headline and core (excl. food & energy).
  - M3 money supply: annual growth rate of broad money aggregate.

ECB CSV columns (format=csvdata): KEY, FREQ, REF_AREA, TIME_PERIOD, OBS_VALUE, etc.

Documentation: https://data.ecb.europa.eu/help/api/overview
"""

import csv
import io
import time
import datetime
import logging
import urllib.request
import urllib.parse

from tools.http_client import handle_api_errors

logger = logging.getLogger("finresearch.ecb")

_ECB_SDMX_BASE = "https://data-api.ecb.europa.eu/service/data"


# -- Internal helpers ---------------------------------------------------------

def _ecb_csv_get(url: str, timeout: int = 25) -> list[dict]:
    """
    Fetch a CSV-format ECB SDMX response and return it as a list of row dicts.

    Args:
        url:     Full ECB SDMX URL with ?format=csvdata
        timeout: Request timeout in seconds

    Returns:
        List of CSV row dicts (keys = column headers).

    Raises:
        RuntimeError on HTTP error; caller should use @handle_api_errors.
    """
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/csv, application/csv",
            "User-Agent": "FinResearchAgent/1.0 (academic use)",
        },
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        logger.debug("ECB CSV OK (%.0fms)", (time.monotonic() - start) * 1000)
    except urllib.request.HTTPError as exc:
        raise RuntimeError(f"ECB HTTP {exc.code}: {exc.reason}") from exc

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def _start_period(months_back: int) -> str:
    """Return an ISO start period string (YYYY-MM) for the given lookback."""
    dt = datetime.date.today() - datetime.timedelta(days=months_back * 31)
    return dt.strftime("%Y-%m")


def _parse_single_series(rows: list[dict], label: str) -> dict:
    """
    Parse ECB CSV rows for a single series into a summary dict.

    Returns dict with latest value, 12-month-ago value, and recent 6 observations.
    """
    observations = []
    for row in rows:
        period = (row.get("TIME_PERIOD") or row.get("Period") or "").strip()
        raw_val = (row.get("OBS_VALUE") or row.get("Value") or "").strip()
        if not period:
            continue
        try:
            value = float(raw_val)
        except (ValueError, TypeError):
            continue
        observations.append({"period": period, "value": value})

    if not observations:
        return {"label": label, "error": "no data returned"}

    sorted_obs = sorted(observations, key=lambda x: x["period"])
    latest = sorted_obs[-1]
    twelve_months_ago = sorted_obs[-13] if len(sorted_obs) >= 13 else None

    return {
        "label": label,
        "latest_period": latest["period"],
        "latest_value": latest["value"],
        "value_12m_ago": twelve_months_ago["value"] if twelve_months_ago else None,
        "period_12m_ago": twelve_months_ago["period"] if twelve_months_ago else None,
        "recent_6_observations": sorted_obs[-6:],
    }


# -- Public API ---------------------------------------------------------------

@handle_api_errors("ECB get_ecb_policy_rates")
def get_ecb_policy_rates(months_back: int = 24) -> dict:
    """
    Fetch ECB policy rates: deposit facility rate and main refinancing rate.

    The deposit facility rate (DFR) is the rate banks earn on overnight deposits
    with the ECB. The main refinancing rate (MRR) is the rate for regular
    liquidity-providing operations. Both are key monetary policy signals.

    Args:
        months_back: Months of history to retrieve (default: 24).

    Returns:
        Dictionary with latest values, 12-month-ago values, and recent observations
        for both the deposit rate and the main refinancing rate.
    """
    start = _start_period(months_back)

    # Deposit facility rate
    dfr_url = (
        f"{_ECB_SDMX_BASE}/FM/M.U2.EUR.4F.KR.DFR.LEV"
        f"?format=csvdata&startPeriod={start}"
    )
    dfr_rows = _ecb_csv_get(dfr_url)
    dfr = _parse_single_series(dfr_rows, "ECB Deposit Facility Rate (%)")

    # Main refinancing rate
    mrr_url = (
        f"{_ECB_SDMX_BASE}/FM/M.U2.EUR.4F.KR.MRR_RT.LEV"
        f"?format=csvdata&startPeriod={start}"
    )
    mrr_rows = _ecb_csv_get(mrr_url)
    mrr = _parse_single_series(mrr_rows, "ECB Main Refinancing Rate (%)")

    return {
        "indicator": "ECB Policy Rates",
        "deposit_facility_rate": dfr,
        "main_refinancing_rate": mrr,
        "interpretation": (
            "The deposit facility rate sets the floor for overnight money market rates. "
            "The main refinancing rate is the ECB's primary policy rate for weekly "
            "liquidity operations. Rising rates signal tighter monetary policy."
        ),
        "fetched_date": datetime.date.today().isoformat(),
        "source": "ECB Statistical Data Warehouse -- SDMX REST API (no API key required)",
    }


@handle_api_errors("ECB get_ecb_inflation")
def get_ecb_inflation(months_back: int = 24) -> dict:
    """
    Fetch Eurozone HICP inflation: headline and core (excl. food & energy).

    HICP (Harmonised Index of Consumer Prices) is the ECB's primary inflation
    measure. The headline rate includes all items; the core rate excludes food
    and energy to show underlying price trends. The ECB targets 2% over the
    medium term.

    Args:
        months_back: Months of history to retrieve (default: 24).

    Returns:
        Dictionary with latest values, 12-month-ago values, and recent observations
        for both headline and core HICP annual rates of change.
    """
    start = _start_period(months_back)

    # Headline HICP -- all items, annual rate of change
    headline_url = (
        f"{_ECB_SDMX_BASE}/ICP/M.U2.N.000000.4.ANR"
        f"?format=csvdata&startPeriod={start}"
    )
    headline_rows = _ecb_csv_get(headline_url)
    headline = _parse_single_series(headline_rows, "Eurozone HICP Headline Inflation (% YoY)")

    # Core HICP -- excl. food and energy, annual rate of change
    core_url = (
        f"{_ECB_SDMX_BASE}/ICP/M.U2.N.XEF000.4.ANR"
        f"?format=csvdata&startPeriod={start}"
    )
    core_rows = _ecb_csv_get(core_url)
    core = _parse_single_series(core_rows, "Eurozone Core HICP Inflation (% YoY, excl. food & energy)")

    return {
        "indicator": "Eurozone HICP Inflation (Annual Rate of Change)",
        "headline_hicp": headline,
        "core_hicp": core,
        "interpretation": (
            "HICP is the ECB's target inflation measure. The ECB aims for 2% "
            "over the medium term. Core inflation strips out volatile food and "
            "energy prices to reveal underlying trends."
        ),
        "fetched_date": datetime.date.today().isoformat(),
        "source": "ECB Statistical Data Warehouse -- SDMX REST API (no API key required)",
    }


@handle_api_errors("ECB get_ecb_m3_money_supply")
def get_ecb_m3_money_supply(months_back: int = 24) -> dict:
    """
    Fetch Eurozone M3 money supply annual growth rate.

    M3 is the broadest monetary aggregate tracked by the ECB. It includes
    currency in circulation, overnight deposits, deposits with agreed maturity
    up to 2 years, repos, money market fund shares, and debt securities up to
    2 years. The ECB's reference value for M3 growth is 4.5% per year.

    Args:
        months_back: Months of history to retrieve (default: 24).

    Returns:
        Dictionary with latest value, 12-month-ago value, and recent observations
        for the M3 annual growth rate.
    """
    start = _start_period(months_back)

    url = (
        f"{_ECB_SDMX_BASE}/BSI/M.U2.Y.V.M30.X.1.U2.2300.Z01.A"
        f"?format=csvdata&startPeriod={start}"
    )
    rows = _ecb_csv_get(url)
    m3 = _parse_single_series(rows, "Eurozone M3 Money Supply (% YoY)")

    return {
        "indicator": "Eurozone M3 Broad Money Supply (Annual Growth Rate)",
        "m3_growth": m3,
        "interpretation": (
            "M3 growth tracks the expansion of the broadest money aggregate. "
            "The ECB's reference value is 4.5% annual growth. Persistently high "
            "M3 growth may signal future inflationary pressure; weak growth can "
            "indicate tight financial conditions."
        ),
        "fetched_date": datetime.date.today().isoformat(),
        "source": "ECB Statistical Data Warehouse -- SDMX REST API (no API key required)",
    }


@handle_api_errors("ECB get_ecb_macro_snapshot")
def get_ecb_macro_snapshot() -> dict:
    """
    Convenience function: fetch all ECB macro data in one call.

    Returns a single dict containing ECB policy rates, Eurozone HICP inflation
    (headline and core), and M3 money supply growth.

    Returns:
        Combined dictionary with all ECB macro indicators.
    """
    policy_rates = get_ecb_policy_rates(months_back=24)
    inflation = get_ecb_inflation(months_back=24)
    m3 = get_ecb_m3_money_supply(months_back=24)

    return {
        "ecb_macro_snapshot": {
            "policy_rates": policy_rates,
            "inflation": inflation,
            "m3_money_supply": m3,
        },
        "fetched_date": datetime.date.today().isoformat(),
        "source": "ECB Statistical Data Warehouse -- SDMX REST API (no API key required)",
    }
