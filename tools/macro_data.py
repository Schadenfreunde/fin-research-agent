"""
macro_data.py — Tools for fetching macroeconomic data from FRED (Federal Reserve).

API key is loaded from Google Secret Manager via environment variable FRED_API_KEY.
Free registration at https://fred.stlouisfed.org/docs/api/api_key.html
"""

import datetime

from tools.http_client import get_api_key, api_get

_FRED_BASE = "https://api.stlouisfed.org/fred"


def get_series(
    series_id: str,
    observation_start: str | None = None,
    observation_end: str | None = None,
) -> dict:
    """
    Fetch a FRED data series by its series ID.

    Args:
        series_id: FRED series identifier (e.g., "DFF", "DGS10", "CPIAUCSL")
        observation_start: Start date in "YYYY-MM-DD" format. Defaults to 5 years ago.
        observation_end: End date in "YYYY-MM-DD" format. Defaults to today.

    Returns:
        Dictionary with series metadata and observations.

    Common series IDs:
        Interest rates:
            DFF        — Federal Funds Effective Rate (daily)
            DGS2       — 2-Year Treasury yield (daily)
            DGS5       — 5-Year Treasury yield (daily)
            DGS10      — 10-Year Treasury yield (daily)
            DGS30      — 30-Year Treasury yield (daily)
            T10Y2Y     — 10Y-2Y Treasury spread (daily)
            T10Y3M     — 10Y-3M Treasury spread (daily)
            TB3MS      — 3-Month Treasury Bill rate (monthly)

        Inflation:
            CPIAUCSL   — CPI All Items (monthly)
            CPILFESL   — Core CPI (monthly)
            PCEPI      — PCE Price Index (monthly)
            PCEPILFE   — Core PCE — Fed's preferred measure (monthly)
            T10YIE     — 10-Year Breakeven Inflation Rate (daily)

        Growth / employment:
            GDP        — Real GDP (quarterly)
            PAYEMS     — Total Nonfarm Payrolls (monthly)
            UNRATE     — Unemployment Rate (monthly)
            INDPRO     — Industrial Production Index (monthly)
            NAPM       — ISM Manufacturing PMI (monthly)
            UMCSENT    — Consumer Sentiment (monthly)

        Financial conditions:
            NFCI       — Chicago Fed National Financial Conditions Index (weekly)
            STLFSI4    — St. Louis Fed Financial Stress Index (weekly)

        Credit spreads:
            BAMLH0A0HYM2  — US High Yield Option-Adjusted Spread (daily)
            BAMLC0A0CM    — US IG Corporate Bond OAS (daily)

        Commodities:
            DCOILWTICO    — WTI Crude Oil Price (daily)
            GOLDAMGBD228NLBM — Gold price AM fix (daily)
    """
    key = get_api_key("FRED_API_KEY", "fred-api-key")

    if observation_start is None:
        five_years_ago = datetime.date.today() - datetime.timedelta(days=5 * 365)
        observation_start = five_years_ago.isoformat()

    if observation_end is None:
        observation_end = datetime.date.today().isoformat()

    # Fetch series metadata
    meta_url = (
        f"{_FRED_BASE}/series?series_id={series_id}&api_key={key}&file_type=json"
    )
    try:
        meta = api_get(meta_url, timeout=15, source_label=f"FRED meta/{series_id}")
    except Exception as e:
        return {"series_id": series_id, "error": f"FRED metadata fetch failed: {e}",
                "fetched_date": datetime.date.today().isoformat(), "stats": {}, "observations": []}

    series_info = meta.get("seriess", [{}])[0]

    # Fetch observations
    obs_url = (
        f"{_FRED_BASE}/series/observations"
        f"?series_id={series_id}"
        f"&observation_start={observation_start}"
        f"&observation_end={observation_end}"
        f"&api_key={key}&file_type=json"
    )
    try:
        obs_data = api_get(obs_url, timeout=15, source_label=f"FRED obs/{series_id}")
    except Exception as e:
        return {"series_id": series_id, "error": f"FRED observations fetch failed: {e}",
                "fetched_date": datetime.date.today().isoformat(), "stats": {}, "observations": []}

    observations = [
        {
            "date": obs["date"],
            "value": float(obs["value"]) if obs["value"] != "." else None,
        }
        for obs in obs_data.get("observations", [])
    ]

    # Compute quick stats on non-null values
    valid_values = [o["value"] for o in observations if o["value"] is not None]
    latest = observations[-1] if observations else None
    prior_year = None
    if len(observations) >= 252:  # Approximately 1 year of daily data
        prior_year = observations[-252]
    elif len(observations) >= 12:  # Monthly data
        prior_year = observations[-12]

    stats = {}
    if valid_values:
        stats = {
            "current_value": valid_values[-1] if valid_values else None,
            "current_date": latest["date"] if latest else None,
            "prior_year_value": prior_year["value"] if prior_year else None,
            "prior_year_date": prior_year["date"] if prior_year else None,
            "5y_min": min(valid_values),
            "5y_max": max(valid_values),
            "5y_mean": round(sum(valid_values) / len(valid_values), 4),
            "change_from_prior_year": (
                round(valid_values[-1] - prior_year["value"], 4)
                if prior_year and prior_year["value"] is not None
                else None
            ),
        }

    return {
        "series_id": series_id,
        "title": series_info.get("title"),
        "units": series_info.get("units"),
        "frequency": series_info.get("frequency"),
        "last_updated": series_info.get("last_updated"),
        "fetched_date": datetime.date.today().isoformat(),
        "period": {"start": observation_start, "end": observation_end},
        "stats": stats,
        "observations": observations,
    }


def get_multiple_series(series_ids: list) -> dict:
    """
    Fetch multiple FRED series at once. Returns a dictionary keyed by series ID.

    Args:
        series_ids: List of FRED series IDs

    Returns:
        Dictionary mapping series_id → series data.

    Example:
        get_multiple_series(["DFF", "DGS10", "T10Y2Y"])
    """
    results = {}
    for series_id in series_ids:
        try:
            results[series_id] = get_series(series_id)
        except Exception as e:
            results[series_id] = {"error": str(e), "series_id": series_id}
    return results


def get_yield_curve_snapshot() -> dict:
    """
    Fetch a complete yield curve snapshot with the most recent data for key maturities.
    Also computes the 2s10s and 3m10s spreads.

    Returns:
        Dictionary with current yield curve values and spread analysis.
    """
    series_ids = ["TB3MS", "DGS2", "DGS5", "DGS10", "DGS30", "T10Y2Y", "T10Y3M"]
    today = datetime.date.today()
    start_date = (today - datetime.timedelta(days=30)).isoformat()

    yields = {}

    for sid in series_ids:
        try:
            data = get_series(sid, observation_start=start_date)
            obs = [o for o in data["observations"] if o["value"] is not None]
            if obs:
                yields[sid] = {
                    "value": obs[-1]["value"],
                    "date": obs[-1]["date"],
                    "title": data.get("title"),
                }
        except Exception as e:
            yields[sid] = {"error": str(e)}

    return {
        "fetched_date": today.isoformat(),
        "note": "All yields in % per annum. Spreads = long rate minus short rate (negative = inverted).",
        "yields": yields,
    }


def get_recession_indicators() -> dict:
    """
    Fetch key recession-monitoring indicators from FRED.
    Based on NBER business cycle indicators.

    Returns:
        Dictionary with current values of key recession indicators.
    """
    indicators = {
        "T10Y3M": "10Y-3M Spread (Estrella-Mishkin recession predictor)",
        "SAHM": "Sahm Rule Recession Indicator",
        "UNRATE": "Unemployment Rate",
        "PAYEMS": "Total Nonfarm Payrolls",
        "INDPRO": "Industrial Production Index",
        "NFCI": "National Financial Conditions Index",
        "STLFSI4": "St. Louis Financial Stress Index",
    }

    results = {}
    for series_id, description in indicators.items():
        try:
            data = get_series(series_id)
            results[series_id] = {
                "description": description,
                "current_value": data["stats"].get("current_value"),
                "current_date": data["stats"].get("current_date"),
                "prior_year_value": data["stats"].get("prior_year_value"),
                "change_yoy": data["stats"].get("change_from_prior_year"),
            }
        except Exception as e:
            results[series_id] = {"error": str(e), "description": description}

    return {
        "fetched_date": datetime.date.today().isoformat(),
        "indicators": results,
    }
