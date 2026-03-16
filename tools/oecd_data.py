"""
oecd_data.py — OECD Stats API for macroeconomic leading indicators.

No API key required. Uses the OECD SDMX REST API (new Data Explorer endpoint).
Base URL: https://sdmx.oecd.org/public/rest/

Fetches:
  - Composite Leading Indicators (CLI): forward-looking business cycle signals
    for major economies. CLI > 100 = expansion, < 100 = contraction.
  - Economic Outlook projections: OECD's biannual GDP and inflation forecasts.

OECD country codes used here are ISO 3166-1 alpha-3 (USA, GBR, DEU, etc.).

Documentation: https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html
"""

import csv
import io
import time
import datetime
import logging
import urllib.request
import urllib.parse

from tools.http_client import handle_api_errors

logger = logging.getLogger("finresearch.oecd")

_OECD_SDMX_BASE = "https://sdmx.oecd.org/public/rest"

# ── Default country sets ──────────────────────────────────────────────────────
# CLI countries: all OECD members that appear in the DF_CLI dataflow
CLI_DEFAULT_COUNTRIES = [
    "USA", "GBR", "DEU", "FRA", "JPN", "KOR", "CAN", "AUS", "ITA", "ESP",
    "NLD", "SWE", "CHE", "POL", "MEX", "TUR", "BRA", "IND", "CHN", "ZAF",
]

# Smaller set for Economic Outlook (EO covers fewer countries directly)
EO_DEFAULT_COUNTRIES = [
    "USA", "GBR", "DEU", "FRA", "JPN", "KOR", "CAN", "AUS", "ITA", "ESP",
]


def _oecd_csv_get(url: str, timeout: int = 25) -> list[dict]:
    """
    Fetch a CSV-format OECD SDMX response and return it as a list of row dicts.

    Args:
        url:     Full OECD SDMX URL with ?format=csvfilewithlabels
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
        logger.debug("OECD CSV OK (%.0fms)", (time.monotonic() - start) * 1000)
    except urllib.request.HTTPError as exc:
        raise RuntimeError(f"OECD HTTP {exc.code}: {exc.reason}") from exc

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def _parse_cli_rows(rows: list[dict]) -> dict:
    """
    Parse CLI CSV rows into a per-country summary dict.

    Expected CSV columns (csvfilewithlabels):
        Reference area, TIME_PERIOD, OBS_VALUE  (and optional STATUS, etc.)
    """
    country_obs: dict[str, list] = {}

    for row in rows:
        # Column names vary slightly between OECD API versions — check common aliases
        country = (
            row.get("Reference area")
            or row.get("REF_AREA")
            or row.get("Country")
            or ""
        ).strip()
        period = (row.get("TIME_PERIOD") or row.get("Period") or "").strip()
        raw_val = (row.get("OBS_VALUE") or row.get("Value") or "").strip()

        if not country or not period:
            continue
        try:
            value = float(raw_val)
        except (ValueError, TypeError):
            continue

        country_obs.setdefault(country, []).append({"period": period, "cli": value})

    summary = {}
    for country, observations in country_obs.items():
        sorted_obs = sorted(observations, key=lambda x: x["period"])
        latest = sorted_obs[-1] if sorted_obs else None
        prior_6m = sorted_obs[-7] if len(sorted_obs) >= 7 else None

        trend = "unknown"
        if latest and prior_6m:
            trend = (
                "expanding"
                if latest["cli"] > prior_6m["cli"]
                else "contracting"
            )

        summary[country] = {
            "latest_period": latest["period"] if latest else None,
            "latest_cli": latest["cli"] if latest else None,
            "cli_6m_ago": prior_6m["cli"] if prior_6m else None,
            "trend": trend,
            "above_100": (latest["cli"] >= 100.0) if latest else None,
            "recent_6_observations": sorted_obs[-6:],
        }

    return summary


@handle_api_errors("OECD get_oecd_leading_indicators")
def get_oecd_leading_indicators(
    countries: list = None,
    months_back: int = 24,
) -> dict:
    """
    Fetch OECD Composite Leading Indicators (CLI) for major economies.

    CLI is a forward-looking business cycle indicator:
      - CLI > 100 and rising  → expansion phase
      - CLI > 100 and falling → slowdown phase
      - CLI < 100 and falling → contraction phase
      - CLI < 100 and rising  → recovery phase

    Data is monthly, amplitude-adjusted, with a typical lead time of 6–9 months.

    Args:
        countries:   List of OECD ISO-3 country codes (default: 20 major economies)
        months_back: Months of history to retrieve (default: 24)

    Returns:
        Dictionary with per-country CLI summary and recent observations.
    """
    if countries is None:
        countries = CLI_DEFAULT_COUNTRIES

    countries_str = "+".join(countries)
    start_period = (
        datetime.date.today() - datetime.timedelta(days=months_back * 31)
    ).strftime("%Y-%m")

    # CLI dataflow key: {COUNTRIES}.M.LI...AA...H
    # M = monthly, LI = leading indicator, AA = amplitude adjusted, H = current value
    url = (
        f"{_OECD_SDMX_BASE}/data/"
        f"OECD.SDD.STES,DSD_STES@DF_CLI,1.0/"
        f"{countries_str}.M.LI...AA...H"
        f"?format=csvfilewithlabels&startPeriod={start_period}"
    )

    rows = _oecd_csv_get(url, timeout=25)
    country_summary = _parse_cli_rows(rows)

    if not country_summary:
        return {
            "error": "No CLI data parsed — OECD endpoint may have changed format",
            "url_attempted": url,
            "fetched_date": datetime.date.today().isoformat(),
            "source": "OECD SDMX REST API",
        }

    # Count expansion vs contraction signals
    expanding = [c for c, v in country_summary.items() if v.get("trend") == "expanding"]
    contracting = [c for c, v in country_summary.items() if v.get("trend") == "contracting"]

    return {
        "indicator": "OECD Composite Leading Indicator (CLI), Amplitude-Adjusted",
        "interpretation": (
            "CLI > 100 → above long-run trend (expansion); "
            "CLI < 100 → below long-run trend (contraction). "
            "Rising CLI signals improving outlook; falling CLI signals deterioration."
        ),
        "signal_summary": {
            "expanding_countries": expanding,
            "contracting_countries": contracting,
            "expansion_count": len(expanding),
            "contraction_count": len(contracting),
        },
        "country_data": country_summary,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "OECD Data Explorer — SDMX REST API (no API key required)",
        "data_lag_note": "CLI is monthly; typical publication lag is ~5 weeks after month-end.",
    }


@handle_api_errors("OECD get_oecd_economic_outlook")
def get_oecd_economic_outlook(
    countries: list = None,
    years_back: int = 2,
) -> dict:
    """
    Fetch OECD Economic Outlook GDP growth projections.

    The Economic Outlook (EO) is published twice per year (June and December)
    and provides near-term GDP growth and inflation projections for OECD members.

    Uses the OECD.SDD.NAD,DSD_NAAG@DF_NAAG_I dataflow (National Accounts,
    Annual, GDP growth rate).

    Args:
        countries:  List of OECD ISO-3 country codes (default: 10 major economies)
        years_back: Annual periods to retrieve (default: 2, i.e. current + 1 projection)

    Returns:
        Dictionary with GDP growth actuals and near-term projections per country.
    """
    if countries is None:
        countries = EO_DEFAULT_COUNTRIES

    countries_str = "+".join(countries)
    start_year = datetime.date.today().year - years_back

    # National Accounts — Annual GDP growth rate (real, %)
    url = (
        f"{_OECD_SDMX_BASE}/data/"
        f"OECD.SDD.NAD,DSD_NAAG@DF_NAAG_I,1.0/"
        f"A.{countries_str}.B1GQ_R_GR."
        f"?format=csvfilewithlabels&startPeriod={start_year}"
    )

    rows = _oecd_csv_get(url, timeout=25)

    if not rows:
        return {
            "error": "No Economic Outlook data returned — OECD endpoint may have changed",
            "url_attempted": url,
            "fetched_date": datetime.date.today().isoformat(),
            "source": "OECD SDMX REST API",
        }

    # Parse annual GDP growth by country
    country_gdp: dict[str, list] = {}
    for row in rows:
        country = (
            row.get("Reference area") or row.get("REF_AREA") or ""
        ).strip()
        period = (row.get("TIME_PERIOD") or row.get("Period") or "").strip()
        raw_val = (row.get("OBS_VALUE") or row.get("Value") or "").strip()

        if not country or not period:
            continue
        try:
            value = float(raw_val)
        except (ValueError, TypeError):
            continue

        country_gdp.setdefault(country, []).append({"year": period, "gdp_growth_pct": value})

    # Sort by year descending for each country
    gdp_summary = {
        country: sorted(obs, key=lambda x: x["year"], reverse=True)
        for country, obs in country_gdp.items()
    }

    return {
        "indicator": "OECD Annual Real GDP Growth Rate (%)",
        "note": (
            "Includes actuals and OECD Economic Outlook projections. "
            "Projection years are flagged in the raw data; latest year may be a forecast."
        ),
        "country_gdp_growth": gdp_summary,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "OECD Data Explorer — SDMX REST API (no API key required)",
    }
