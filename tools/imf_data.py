"""
imf_data.py — IMF World Economic Outlook (WEO) data via the DataMapper API.

No API key required. Fully open access.
Base URL: https://www.imf.org/external/datamapper/api/v1/

Provides WEO projections for major economies: real GDP growth, inflation,
unemployment, and current account balance. Unlike World Bank data (which lags
~12 months), WEO includes forward-looking projections for the current and
next year, making it valuable for macro forecasting context.

Documentation: https://www.imf.org/external/datamapper/api/v1/
"""

import datetime

from tools.http_client import api_get, handle_api_errors

_IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"

# ── Default indicator set ────────────────────────────────────────────────────
# Covers the four most relevant WEO dimensions for macro research
IMF_WEO_INDICATORS = {
    "NGDP_RPCH":  "Real GDP growth (%)",
    "PCPIPCH":    "Inflation, average consumer prices (%)",
    "LUR":        "Unemployment rate (%)",
    "BCA_NGDPD":  "Current account balance (% of GDP)",
}

# ── Default country list ─────────────────────────────────────────────────────
# IMF uses ISO 3-letter country codes
IMF_DEFAULT_COUNTRIES = {
    "USA": "United States",
    "GBR": "United Kingdom",
    "DEU": "Germany",
    "FRA": "France",
    "JPN": "Japan",
    "KOR": "South Korea",
    "CAN": "Canada",
    "CHN": "China",
    "IND": "India",
    "BRA": "Brazil",
}


@handle_api_errors("IMF get_imf_weo_indicator")
def get_imf_weo_indicator(
    indicator_code: str,
    countries: list[str] | None = None,
) -> dict:
    """
    Fetch a single WEO indicator for multiple countries.

    Args:
        indicator_code: IMF WEO indicator code (e.g., "NGDP_RPCH")
        countries:      List of ISO 3-letter country codes (default: 10 major economies)

    Returns:
        Dictionary with per-country latest-year and next-year projection data.
    """
    if countries is None:
        countries = list(IMF_DEFAULT_COUNTRIES.keys())

    url = f"{_IMF_BASE}/{indicator_code}"
    data = api_get(url, timeout=15, source_label=f"IMF-WEO/{indicator_code}")

    # API returns: {"values": {"INDICATOR": {"COUNTRY": {"2023": 2.5, ...}}}}
    if not isinstance(data, dict) or "values" not in data:
        return {
            "indicator_code": indicator_code,
            "error": "no data returned or malformed response",
            "countries": {},
            "fetched_date": datetime.date.today().isoformat(),
            "source": "IMF World Economic Outlook (DataMapper API, no API key required)",
        }

    indicator_values = data["values"].get(indicator_code, {})
    indicator_label = IMF_WEO_INDICATORS.get(indicator_code, indicator_code)

    current_year = datetime.date.today().year
    country_results = {}

    for country_code in countries:
        country_data = indicator_values.get(country_code, {})
        if not country_data:
            country_results[country_code] = {
                "country": IMF_DEFAULT_COUNTRIES.get(country_code, country_code),
                "error": "no data available",
            }
            continue

        # Sort available years descending to find latest actual + next projection
        available_years = sorted(country_data.keys(), reverse=True)

        # Find the latest year and the next-year projection
        latest_year = None
        next_year = None
        for yr in available_years:
            try:
                yr_int = int(yr)
            except (ValueError, TypeError):
                continue
            val = country_data[yr]
            if val is None:
                continue
            if yr_int <= current_year and latest_year is None:
                latest_year = {"year": yr_int, "value": round(float(val), 3)}
            elif yr_int == current_year + 1 and next_year is None:
                next_year = {"year": yr_int, "value": round(float(val), 3)}

        # If we didn't find a latest year <= current year, take the most recent available
        if latest_year is None:
            for yr in available_years:
                val = country_data[yr]
                if val is not None:
                    try:
                        latest_year = {"year": int(yr), "value": round(float(val), 3)}
                        break
                    except (ValueError, TypeError):
                        continue

        country_results[country_code] = {
            "country": IMF_DEFAULT_COUNTRIES.get(country_code, country_code),
            "latest": latest_year,
            "next_year_projection": next_year,
        }

    return {
        "indicator_code": indicator_code,
        "indicator_name": indicator_label,
        "countries": country_results,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "IMF World Economic Outlook (DataMapper API, no API key required)",
    }


@handle_api_errors("IMF get_imf_weo_snapshot")
def get_imf_weo_snapshot(
    countries: list[str] | None = None,
) -> dict:
    """
    Fetch all four key WEO indicators for multiple countries.

    Convenience wrapper around get_imf_weo_indicator that fetches Real GDP
    growth, inflation, unemployment, and current account balance in one call.

    Args:
        countries: List of ISO 3-letter country codes (default: 10 major economies)

    Returns:
        Dictionary with per-indicator, per-country data including projections.
    """
    if countries is None:
        countries = list(IMF_DEFAULT_COUNTRIES.keys())

    indicator_results = {}
    for indicator_code, indicator_label in IMF_WEO_INDICATORS.items():
        try:
            result = get_imf_weo_indicator(indicator_code, countries)
            # If the inner call returned an error dict from the decorator, pass it through
            if "error" in result and "countries" not in result:
                indicator_results[indicator_code] = {
                    "label": indicator_label,
                    "error": result["error"],
                    "countries": {},
                }
            else:
                indicator_results[indicator_code] = {
                    "label": indicator_label,
                    "countries": result.get("countries", {}),
                }
        except Exception as exc:
            indicator_results[indicator_code] = {
                "label": indicator_label,
                "error": str(exc),
                "countries": {},
            }

    return {
        "countries_queried": {c: IMF_DEFAULT_COUNTRIES.get(c, c) for c in countries},
        "indicators": indicator_results,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "IMF World Economic Outlook (DataMapper API, no API key required)",
        "note": (
            "WEO data includes IMF staff projections for the current and next year. "
            "Values are updated biannually (April and October WEO publications)."
        ),
    }
