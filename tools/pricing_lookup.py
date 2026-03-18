"""
pricing_lookup.py — Live Vertex AI model pricing via Google Cloud Billing Catalog API.

Fetches current per-token prices for Vertex AI generative models at process startup
and caches the result for the lifetime of the Cloud Run instance. Falls back to
config.yaml prices if the API is unavailable or the service account lacks permission.

Required IAM permission on the Cloud Run service account (one-time setup):
    gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \\
        --member="serviceAccount:YOUR_SA@YOUR_PROJECT.iam.gserviceaccount.com" \\
        --role="roles/billing.viewer"

How the Billing Catalog API works:
  - Lists SKUs (billing line items) for the Vertex AI service.
  - Each Gemini/Claude model has separate Input and Output token SKUs.
  - SKU description examples:
      "Vertex AI Gemini 2.5 Flash: Input Tokens"
      "Vertex AI Gemini 2.5 Pro: Output Tokens"
  - Prices are in Google's Money format: {units: "0", nanos: 150000000}
    meaning $0 + 150000000 / 1e9 = $0.15 per usageUnit (usually "1M tokens").

Pricing returned is USD per 1M tokens, matching the format used by debug_report.py.

API reference: https://cloud.google.com/billing/docs/reference/rest/v1/services.skus/list
"""

import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("finresearch.pricing")

# ── Constants ─────────────────────────────────────────────────────────────────

# Vertex AI service ID in the Billing Catalog
_VERTEX_AI_SERVICE_ID = "services/aiplatform.googleapis.com"

# Billing Catalog SKU list endpoint (v1)
_BILLING_SKU_URL = (
    f"https://cloudbilling.googleapis.com/v1/{_VERTEX_AI_SERVICE_ID}/skus"
    f"?currencyCode=USD&pageSize=5000"
)

# Metadata server endpoint for the access token (Cloud Run service account)
_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts"
    "/default/token"
)

# Cache TTL: re-fetch prices once per Cloud Run instance startup only.
# Prices rarely change; a fresh deploy will always get the latest prices.
_CACHE: dict = {}          # populated on first call to get_vertex_ai_pricing()
_CACHE_FETCHED_AT: Optional[float] = None
_PRICING_SOURCE: str = "unknown"   # "Billing Catalog API" | "config.yaml" | "none"


# ── Token / auth helpers ──────────────────────────────────────────────────────

def _get_access_token() -> str:
    """
    Fetch a short-lived OAuth2 access token from the GCE metadata server.
    Only works on Cloud Run / GCE. Raises RuntimeError if unavailable.
    """
    req = urllib.request.Request(
        _METADATA_TOKEN_URL,
        headers={"Metadata-Flavor": "Google"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError("Empty access_token in metadata response")
        return token
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Metadata server unreachable (not running on GCP?): {exc}"
        ) from exc


# ── Billing Catalog fetch ─────────────────────────────────────────────────────

def _money_to_float(money: dict) -> float:
    """Convert a Google Money object {units, nanos} to a float."""
    return float(money.get("units", 0)) + float(money.get("nanos", 0)) / 1e9


def _extract_price_per_million(sku: dict) -> Optional[float]:
    """
    Return the price per 1M tokens for a SKU, or None if unparseable.

    Handles two usageUnit formats:
      "1M"        → price is already per 1M (modern Gemini models)
      "1000 char" → approximate; skipped (old legacy format, not used for 2.x)
    """
    pricing_info = sku.get("pricingInfo", [])
    if not pricing_info:
        return None
    expr = pricing_info[0].get("pricingExpression", {})
    unit_desc = (expr.get("usageUnitDescription") or "").lower()
    rates = expr.get("tieredRates", [])
    if not rates:
        return None
    # Use the first (base) rate
    unit_price = rates[0].get("unitPrice", {})
    price_per_unit = _money_to_float(unit_price)
    # Normalise to per-1M-tokens
    if "1m" in unit_desc or "million" in unit_desc:
        return price_per_unit
    if "1k" in unit_desc or "1,000" in unit_desc or "thousand" in unit_desc:
        return price_per_unit * 1000
    # Unknown unit — skip
    return None


# Map from keywords found in SKU descriptions to canonical model-name substrings
# used in debug_report.py for substring matching.
_SKU_MODEL_MAP = [
    # Gemini 3.x — must be checked BEFORE 3.0 / 2.x to avoid "3" matching "3.1"
    ("gemini 3.1 pro",         "gemini-3.1-pro-preview"),
    ("gemini 3.1 flash lite",  "gemini-3.1-flash-lite"),
    ("gemini 3.1 flash",       "gemini-3.1-flash-preview"),
    ("gemini 3 flash",         "gemini-3-flash-preview"),
    ("gemini 3 pro",           "gemini-3-pro-preview"),
    # Gemini 2.5
    ("gemini 2.5 flash",       "gemini-2.5-flash"),
    ("gemini 2.5 pro",         "gemini-2.5-pro"),
    # Gemini 2.0
    ("gemini 2.0 flash",       "gemini-2.0-flash"),
    # Gemini 1.5
    ("gemini 1.5 flash",       "gemini-1.5-flash"),
    ("gemini 1.5 pro",         "gemini-1.5-pro"),
    # Claude on Vertex — descriptions typically include the full model name
    ("claude-opus-4-6",     "claude-opus-4-6"),
    ("claude-sonnet-4-6",   "claude-sonnet-4-6"),
    ("claude-haiku-4-5",    "claude-haiku-4-5"),
    ("claude-opus-4-5",     "claude-opus-4-5"),
    ("claude-sonnet-4-5",   "claude-sonnet-4-5"),
    ("claude-3-5-sonnet",   "claude-3-5-sonnet"),
    ("claude-3-5-haiku",    "claude-3-5-haiku"),
    ("claude-3-opus",       "claude-3-opus"),
]


def _parse_skus(skus: list[dict]) -> dict[str, dict[str, float]]:
    """
    Parse a list of Billing Catalog SKU dicts into the pricing table format:
        { "gemini-2.5-flash": {"input": 0.15, "output": 0.60}, ... }
    """
    pricing: dict[str, dict[str, float]] = {}
    for sku in skus:
        desc = (sku.get("description") or "").lower()
        # Identify the model
        model_key = None
        for keyword, key in _SKU_MODEL_MAP:
            if keyword in desc:
                model_key = key
                break
        if model_key is None:
            continue
        # Identify input vs output
        if "input" in desc:
            direction = "input"
        elif "output" in desc:
            direction = "output"
        else:
            continue
        price = _extract_price_per_million(sku)
        if price is None:
            continue
        pricing.setdefault(model_key, {})[direction] = price

    # Only keep entries that have both input and output prices
    return {
        k: v for k, v in pricing.items()
        if "input" in v and "output" in v
    }


def _fetch_from_billing_api() -> dict[str, dict[str, float]]:
    """
    Fetch all Vertex AI SKUs from the Billing Catalog and return a pricing dict.
    Raises RuntimeError on auth failure or unexpected HTTP errors.
    """
    token = _get_access_token()
    all_skus = []
    url = _BILLING_SKU_URL

    while url:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise RuntimeError(
                    "Billing Catalog API returned 403 Forbidden. "
                    "Add roles/billing.viewer to the Cloud Run service account:\n"
                    "  gcloud projects add-iam-policy-binding PROJECT_ID \\\n"
                    "    --member=serviceAccount:SA_EMAIL \\\n"
                    "    --role=roles/billing.viewer"
                ) from exc
            raise RuntimeError(f"Billing Catalog API HTTP {exc.code}: {exc.reason}") from exc

        all_skus.extend(data.get("skus", []))
        # Handle pagination
        next_token = data.get("nextPageToken")
        if next_token:
            sep = "&" if "?" in _BILLING_SKU_URL else "?"
            url = f"{_BILLING_SKU_URL}{sep}pageToken={next_token}"
        else:
            url = None

    return _parse_skus(all_skus)


# ── Config.yaml fallback ──────────────────────────────────────────────────────

def _load_from_config() -> dict[str, dict[str, float]]:
    """Read pricing from config.yaml → pricing.models."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        models = (cfg.get("pricing") or {}).get("models") or {}
        return {
            str(k).lower(): {
                "input":  float(v["input"]),
                "output": float(v["output"]),
            }
            for k, v in models.items()
            if isinstance(v, dict) and "input" in v and "output" in v
        }
    except Exception as exc:
        logger.warning("Could not load pricing from config.yaml: %s", exc)
        return {}


def _load_search_cost_from_config() -> float:
    """Read search_cost_per_call from config.yaml → pricing section."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return float((cfg.get("pricing") or {}).get("search_cost_per_call", 0.035))
    except Exception:
        return 0.035


# ── Public interface ──────────────────────────────────────────────────────────

def get_vertex_ai_pricing() -> tuple[dict[str, dict[str, float]], float]:
    """
    Return (model_pricing_dict, search_cost_per_call) for cost estimation.

    Pricing dict format: { "gemini-2.5-flash": {"input": 0.15, "output": 0.60}, ... }
    Prices are USD per 1M tokens.

    Lookup order:
      1. Process-level cache (populated on first call, reused for all reports in the session)
      2. Google Cloud Billing Catalog API (live prices, requires roles/billing.viewer)
      3. config.yaml → pricing.models (user-maintained fallback)

    Logs a warning when falling back so you know which source was used.
    """
    global _CACHE, _CACHE_FETCHED_AT, _PRICING_SOURCE

    # Return cached result if already fetched this session
    if _CACHE:
        return _CACHE["models"], _CACHE["search_cost"]

    search_cost = _load_search_cost_from_config()

    # Try the live Billing Catalog API first
    try:
        t0 = time.monotonic()
        live_prices = _fetch_from_billing_api()
        elapsed = time.monotonic() - t0
        if live_prices:
            logger.info(
                "Pricing loaded from Billing Catalog API in %.1fs (%d models found)",
                elapsed, len(live_prices),
            )
            _PRICING_SOURCE = "Google Cloud Billing Catalog API (live)"
            _CACHE = {"models": live_prices, "search_cost": search_cost}
            _CACHE_FETCHED_AT = time.monotonic()
            return live_prices, search_cost
        else:
            logger.warning(
                "Billing Catalog API returned 0 parseable model SKUs "
                "— falling back to config.yaml prices."
            )
    except RuntimeError as exc:
        logger.warning(
            "Billing Catalog API unavailable (%s) — falling back to config.yaml prices.",
            exc,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected error fetching live pricing (%s) — falling back to config.yaml prices.",
            exc,
        )

    # Fall back to config.yaml
    config_prices = _load_from_config()
    if config_prices:
        logger.info("Pricing loaded from config.yaml (%d models).", len(config_prices))
        _PRICING_SOURCE = "config.yaml (manual)"
    else:
        logger.warning(
            "No pricing found in config.yaml either — cost estimates will show $0.00."
        )
        _PRICING_SOURCE = "none (costs unavailable)"

    _CACHE = {"models": config_prices, "search_cost": search_cost}
    _CACHE_FETCHED_AT = time.monotonic()
    return config_prices, search_cost


def get_pricing_source() -> str:
    """
    Return a human-readable string describing where pricing data was loaded from.
    Call get_vertex_ai_pricing() first; returns 'unknown' if called before that.

    Examples:
        "Google Cloud Billing Catalog API (live)"
        "config.yaml (manual)"
        "none (costs unavailable)"
    """
    return _PRICING_SOURCE
