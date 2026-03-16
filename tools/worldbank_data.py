"""
worldbank_data.py — World Bank Indicators API for macroeconomic context.

No API key required. Fully open access.
Base URL: https://api.worldbank.org/v2/

Provides cross-country macroeconomic indicators (GDP growth, inflation,
unemployment, current account balance, government debt, trade openness) for
major economies. Particularly valuable for non-US macro topics where FRED
data is insufficient.

Documentation: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
"""

import datetime
import urllib.parse

from tools.http_client import api_get, handle_api_errors

_WB_BASE = "https://api.worldbank.org/v2"

# ── Default indicator set ────────────────────────────────────────────────────
# Covers the six most relevant dimensions for macro research
WORLDBANK_INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)",
    "FP.CPI.TOTL.ZG":    "Inflation, CPI (annual %)",
    "SL.UEM.TOTL.ZS":    "Unemployment, total (% of labour force, ILO modelled)",
    "BN.CAB.XOKA.GD.ZS": "Current account balance (% of GDP)",
    "GC.DOD.TOTL.GD.ZS": "Central government debt, total (% of GDP)",
    "NE.TRD.GNFS.ZS":    "Trade (% of GDP)",
}

# ── Default country list ─────────────────────────────────────────────────────
# World Bank 2-letter ISO codes; "XC" = Euro Area aggregate
WORLDBANK_COUNTRIES = {
    "US": "United States",
    "XC": "Euro Area",
    "GB": "United Kingdom",
    "DE": "Germany",
    "JP": "Japan",
    "CN": "China",
    "IN": "India",
    "BR": "Brazil",
    "KR": "South Korea",
    "CA": "Canada",
}


@handle_api_errors("WorldBank get_worldbank_indicator")
def get_worldbank_indicator(
    country_code: str,
    indicator_code: str,
    most_recent_values: int = 10,
) -> dict:
    """
    Fetch annual values for a single World Bank indicator for one country.

    Args:
        country_code:        ISO 2-letter country code (e.g., "US", "DE", "CN")
        indicator_code:      World Bank indicator code (e.g., "NY.GDP.MKTP.KD.ZG")
        most_recent_values:  Number of most recent annual data points to return

    Returns:
        Dictionary with indicator metadata and annual observations.
    """
    url = (
        f"{_WB_BASE}/country/{country_code}/indicator/{indicator_code}"
        f"?format=json&mrv={most_recent_values}&per_page={most_recent_values}"
    )
    data = api_get(url, timeout=15, source_label=f"WorldBank/{country_code}/{indicator_code}")

    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return {
            "country_code": country_code,
            "indicator_code": indicator_code,
            "error": "no data returned",
            "records": [],
            "source": "World Bank Indicators API",
        }

    # data[0] = metadata page, data[1] = observations list
    observations = data[1]
    indicator_name = (
        observations[0].get("indicator", {}).get("value", indicator_code)
        if observations else indicator_code
    )

    records = [
        {
            "year": obs["date"],
            "value": obs["value"],
            "country": obs.get("country", {}).get("value", country_code),
        }
        for obs in observations
        if obs.get("value") is not None
    ]

    latest = records[0] if records else None
    prior_year = records[1] if len(records) >= 2 else None

    return {
        "country_code": country_code,
        "indicator_code": indicator_code,
        "indicator_name": indicator_name,
        "latest": latest,
        "prior_year": prior_year,
        "change_yoy": (
            round(latest["value"] - prior_year["value"], 4)
            if latest and prior_year and latest["value"] is not None and prior_year["value"] is not None
            else None
        ),
        "records": records,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "World Bank Indicators API (no API key required)",
    }


@handle_api_errors("WorldBank get_worldbank_macro_snapshot")
def get_worldbank_macro_snapshot(
    countries: dict = None,
    indicators: dict = None,
    most_recent_values: int = 5,
) -> dict:
    """
    Fetch a cross-country macroeconomic snapshot from the World Bank.

    Batches all indicators and countries into as few HTTP calls as possible
    using the World Bank's semicolon-separated multi-country syntax.

    Args:
        countries:           Dict of {iso2_code: label} (default: 10 major economies)
        indicators:          Dict of {indicator_code: label} (default: 6 key macro indicators)
        most_recent_values:  Annual data points per indicator (default: 5 years)

    Returns:
        Dictionary with per-indicator, per-country time series.
    """
    if countries is None:
        countries = WORLDBANK_COUNTRIES
    if indicators is None:
        indicators = WORLDBANK_INDICATORS

    countries_param = ";".join(countries.keys())
    per_page = most_recent_values * len(countries)  # rows = years × countries

    indicator_results = {}
    for indicator_code, indicator_label in indicators.items():
        url = (
            f"{_WB_BASE}/country/{countries_param}/indicator/{indicator_code}"
            f"?format=json&mrv={most_recent_values}&per_page={per_page}"
        )
        try:
            data = api_get(
                url,
                timeout=20,
                source_label=f"WorldBank/multi/{indicator_code}",
            )
            rows = []
            if isinstance(data, list) and len(data) >= 2 and data[1]:
                for obs in data[1]:
                    if obs.get("value") is not None:
                        rows.append(
                            {
                                "country": obs.get("country", {}).get("value", ""),
                                "country_code": obs.get("countryiso3code", ""),
                                "year": obs["date"],
                                "value": obs["value"],
                            }
                        )
            indicator_results[indicator_code] = {
                "label": indicator_label,
                "data": rows,
            }
        except Exception as exc:
            indicator_results[indicator_code] = {
                "label": indicator_label,
                "error": str(exc),
                "data": [],
            }

    return {
        "countries_queried": countries,
        "indicators": indicator_results,
        "years_per_indicator": most_recent_values,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "World Bank Indicators API (no API key required)",
        "note": (
            "XC = Euro Area aggregate. World Bank data is annual and lags ~12 months; "
            "use for structural / long-run context, not real-time signals."
        ),
    }
