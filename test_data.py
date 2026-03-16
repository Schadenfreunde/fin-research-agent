#!/usr/bin/env python3
"""
test_data.py — Local diagnostic for structured data gathering.

Runs the exact same API calls as _gather_structured_data() in main.py,
saves the output locally, and prints a clear pass/fail summary per source.

API keys are fetched automatically from Google Cloud Secret Manager using
your local gcloud credentials (run `gcloud auth application-default login`
once if you haven't already).

Usage:
  python3 test_data.py GOOGL
  python3 test_data.py AAPL MSFT NVDA    # multiple tickers

Output:
  test_output/{TICKER}/raw_data.json         — all API results as raw JSON
  test_output/{TICKER}/structured_data.txt   — formatted text exactly as LLM agents receive it
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Load config ────────────────────────────────────────────────────────────────
try:
    import yaml
except ImportError:
    print("❌  PyYAML not installed. Run: pip3 install pyyaml")
    sys.exit(1)

config_path = Path(__file__).parent / "config.yaml"
if not config_path.exists():
    print(f"❌  config.yaml not found at {config_path}")
    sys.exit(1)

CONFIG = yaml.safe_load(config_path.read_text())
PROJECT_ID = CONFIG.get("google_cloud", {}).get("project_id", "")
SECRETS_CFG = CONFIG.get("secrets", {})

# ── Fetch API keys from Secret Manager (if not already in env) ────────────────
# Maps env var name → Secret Manager secret name (from config.yaml)
SECRET_ENV_MAP = {
    "FINNHUB_API_KEY":   SECRETS_CFG.get("finnhub_api_key",   "finnhub-api-key"),
    "FMP_API_KEY":       SECRETS_CFG.get("fmp_api_key",       "fmp-api-key"),
    "ALPHA_VANTAGE_KEY": SECRETS_CFG.get("alpha_vantage_key", "alpha-vantage-api-key"),
    "FRED_API_KEY":      SECRETS_CFG.get("fred_api_key",      "fred-api-key"),
    "CORE_API_KEY":      SECRETS_CFG.get("core_api_key",      "core-api-key"),
}

_needs_fetch = [env_var for env_var in SECRET_ENV_MAP if not os.environ.get(env_var)]
if _needs_fetch:
    print(f"🔑  Fetching {len(_needs_fetch)} API key(s) from Secret Manager...")
    try:
        from google.cloud import secretmanager as _sm
        _sm_client = _sm.SecretManagerServiceClient()
    except ImportError:
        print("❌  google-cloud-secret-manager not installed. Run: pip3 install google-cloud-secret-manager")
        sys.exit(1)
    except Exception as e:
        print(f"❌  Could not initialise Secret Manager client: {e}")
        print("    Make sure you are authenticated: gcloud auth application-default login")
        sys.exit(1)

    _failed = []
    for env_var in _needs_fetch:
        secret_name = SECRET_ENV_MAP[env_var]
        resource = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
        try:
            response = _sm_client.access_secret_version(request={"name": resource})
            os.environ[env_var] = response.payload.data.decode("utf-8").strip()
            print(f"   ✅ {env_var}  ←  {secret_name}")
        except Exception as e:
            print(f"   ❌ {env_var}  ←  {secret_name}  ({e})")
            _failed.append(env_var)

    if _failed:
        print(f"\n❌  Could not fetch: {_failed}")
        print("    Check that the secrets exist and your account has 'Secret Manager Secret Accessor' role.")
        print("    Re-authenticate if needed: gcloud auth application-default login")
        sys.exit(1)
    print()

# ── Optional API keys — won't fail if not found (used as fallbacks) ───────────
# SEMANTIC_SCHOLAR_API_KEY is optional: the API works without a key.
# Fetching it here improves rate limits for the Semantic Scholar fallback.
_OPTIONAL_SECRET_MAP = {
    "SEMANTICs_SCHOLAR_API_KEY": SECRETS_CFG.get(
        "semantics_scholar_api_key", "semantics-scholar-api-key"
    ),
}
_optional_needs_fetch = [
    env_var for env_var in _OPTIONAL_SECRET_MAP
    if not os.environ.get(env_var)
]
if _optional_needs_fetch:
    try:
        # Re-use the Secret Manager client if already initialised above
        if "_sm_client" not in dir():
            from google.cloud import secretmanager as _sm
            _sm_client = _sm.SecretManagerServiceClient()
        for env_var in _optional_needs_fetch:
            secret_name = _OPTIONAL_SECRET_MAP[env_var]
            resource = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
            try:
                response = _sm_client.access_secret_version(request={"name": resource})
                os.environ[env_var] = response.payload.data.decode("utf-8").strip()
                print(f"   ✅ {env_var}  ←  {secret_name}  (optional)")
            except Exception as e:
                print(f"   ⚠️  {env_var}  ←  {secret_name}  (optional — skipping: {e})")
    except Exception:
        pass  # Skip optional secrets entirely if Secret Manager client unavailable

# ── Imports ────────────────────────────────────────────────────────────────────
from tools.finnhub_data import (
    get_quote_finnhub,
    get_historical_prices_finnhub,
    get_financials_finnhub,
    get_key_metrics_finnhub,
    get_earnings_finnhub,
    get_analyst_ratings_finnhub,
)
from tools.fmp_data import (
    get_income_statement_fmp,
    get_balance_sheet_fmp,
    get_cash_flow_fmp,
    get_key_metrics_fmp,
    get_analyst_estimates_fmp,
)
from tools.stock_data import (
    get_current_price_alpha,
    get_company_overview_alpha,
    get_income_statement_alpha,
    get_earnings_per_share_alpha,
)
from tools.sec_filings import get_recent_filings, get_specific_fact, get_insider_transactions
from tools.macro_data import get_yield_curve_snapshot, get_recession_indicators
from tools.core_api import search_academic_core


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_error(result) -> bool:
    if isinstance(result, Exception):
        return True
    if isinstance(result, dict):
        return "error" in result and len(result) <= 3
    return False


def _error_msg(result) -> str:
    if isinstance(result, Exception):
        return str(result)
    if isinstance(result, dict):
        return result.get("error") or "unknown error"
    return "unknown"


def _fmt(label, result) -> str:
    if isinstance(result, Exception):
        return f"## {label}\n[ERROR: {result}]\n"
    if isinstance(result, dict) and "error" in result and len(result) <= 3:
        return f"## {label}\n[ERROR: {result['error']}]\n"
    return f"## {label}\n```json\n{json.dumps(result, indent=2, default=str)}\n```\n"


# ── Main gather function ───────────────────────────────────────────────────────

async def gather(ticker: str):
    ticker = ticker.upper()
    outdir = Path("test_output") / ticker
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*62}")
    print(f"  Gathering data for {ticker}")
    print(f"  Output → {outdir}/")
    print(f"{'='*62}")

    loop = asyncio.get_running_loop()
    results = {}   # label → raw result
    statuses = {}  # label → "OK" | "ERROR" | "EXCEPTION" | "TIMEOUT"

    def _record(label, raw):
        if isinstance(raw, Exception):
            results[label] = {"error": str(raw)}
            statuses[label] = "EXCEPTION"
            print(f"   ❌ {label}: EXCEPTION — {raw}")
        elif _is_error(raw):
            results[label] = raw
            statuses[label] = "ERROR"
            print(f"   ❌ {label}: ERROR — {_error_msg(raw)}")
        else:
            results[label] = raw
            statuses[label] = "OK"
            if isinstance(raw, dict):
                keys = list(raw.keys())[:4]
                print(f"   ✅ {label}: OK  ({len(raw)} fields: {keys}{'...' if len(raw) > 4 else ''})")
            elif isinstance(raw, list):
                print(f"   ✅ {label}: OK  ({len(raw)} items)")
            else:
                print(f"   ✅ {label}: OK")

    # ── Group 1a: Parallel — Finnhub ──────────────────────────────────────────
    print(f"\n[1/5] Fetching Finnhub (parallel)...")
    finnhub_labels = [
        "price_finnhub",
        "historical_prices_2y_weekly_finnhub",
        "financials_finnhub",
        "key_metrics_finnhub",
        "earnings_finnhub",
        "analyst_ratings_finnhub",
    ]
    finnhub_raw = await asyncio.gather(
        loop.run_in_executor(None, get_quote_finnhub,             ticker),
        loop.run_in_executor(None, get_historical_prices_finnhub, ticker),
        loop.run_in_executor(None, get_financials_finnhub,        ticker),
        loop.run_in_executor(None, get_key_metrics_finnhub,       ticker),
        loop.run_in_executor(None, get_earnings_finnhub,          ticker),
        loop.run_in_executor(None, get_analyst_ratings_finnhub,   ticker),
        return_exceptions=True,
    )
    for label, raw in zip(finnhub_labels, finnhub_raw):
        _record(label, raw)

    # ── Group 1b: Parallel — FMP ───────────────────────────────────────────────
    print(f"\n[2/5] Fetching FMP (parallel)...")
    fmp_labels = [
        "income_statement_fmp",
        "balance_sheet_fmp",
        "cash_flow_fmp",
        "key_metrics_fmp",
        "analyst_estimates_fmp",
    ]
    fmp_raw = await asyncio.gather(
        loop.run_in_executor(None, get_income_statement_fmp,  ticker),
        loop.run_in_executor(None, get_balance_sheet_fmp,    ticker),
        loop.run_in_executor(None, get_cash_flow_fmp,        ticker),
        loop.run_in_executor(None, get_key_metrics_fmp,      ticker),
        loop.run_in_executor(None, get_analyst_estimates_fmp, ticker),
        return_exceptions=True,
    )
    for label, raw in zip(fmp_labels, fmp_raw):
        _record(label, raw)

    # ── Group 1c: Parallel — Alpha Vantage ────────────────────────────────────
    print(f"\n[3/5] Fetching Alpha Vantage (parallel)...")
    av_labels = ["current_price_av", "company_overview_av", "income_statement_av", "eps_av"]
    av_raw = await asyncio.gather(
        loop.run_in_executor(None, get_current_price_alpha,    ticker),
        loop.run_in_executor(None, get_company_overview_alpha, ticker),
        loop.run_in_executor(None, get_income_statement_alpha, ticker),
        loop.run_in_executor(None, get_earnings_per_share_alpha, ticker),
        return_exceptions=True,
    )
    for label, raw in zip(av_labels, av_raw):
        _record(label, raw)

    # ── Group 2: Sequential — SEC EDGAR ───────────────────────────────────────
    print(f"\n[4/5] Fetching SEC EDGAR + FRED (sequential + parallel)...")
    sec_calls = [
        ("sec_recent_filings",       lambda: get_recent_filings(ticker)),
        ("sec_sbc",                  lambda: get_specific_fact(ticker, "ShareBasedCompensation")),
        ("sec_revenue",              lambda: get_specific_fact(ticker, "Revenues")),
        ("sec_net_income",           lambda: get_specific_fact(ticker, "NetIncomeLoss")),
        ("sec_long_term_debt",       lambda: get_specific_fact(ticker, "LongTermDebt")),
        ("sec_insider_transactions", lambda: get_insider_transactions(ticker)),
    ]
    for label, fn in sec_calls:
        try:
            raw = await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=20)
            _record(label, raw)
        except asyncio.TimeoutError:
            results[label] = {"error": "timeout after 20s"}
            statuses[label] = "TIMEOUT"
            print(f"   ❌ {label}: TIMEOUT")
        except Exception as e:
            results[label] = {"error": str(e)}
            statuses[label] = "EXCEPTION"
            print(f"   ❌ {label}: EXCEPTION — {e}")

    # ── FRED macro (run alongside SEC but print with same group) ──────────────
    macro_labels = ["yield_curve_snapshot", "recession_indicators"]
    macro_raw = await asyncio.gather(
        loop.run_in_executor(None, get_yield_curve_snapshot),
        loop.run_in_executor(None, get_recession_indicators),
        return_exceptions=True,
    )
    for label, raw in zip(macro_labels, macro_raw):
        _record(label, raw)

    # ── CORE Academic API ──────────────────────────────────────────────────────
    print(f"\n[5/5] Testing CORE Academic API...")
    try:
        core_raw = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: search_academic_core(f"{ticker} competitive advantage", num_results=3),
            ),
            timeout=45,
        )
        _record("core_academic_search", core_raw)
    except asyncio.TimeoutError:
        results["core_academic_search"] = {"error": "timeout after 45s"}
        statuses["core_academic_search"] = "TIMEOUT"
        print(f"   ❌ core_academic_search: TIMEOUT")
    except Exception as e:
        results["core_academic_search"] = {"error": str(e)}
        statuses["core_academic_search"] = "EXCEPTION"
        print(f"   ❌ core_academic_search: EXCEPTION — {e}")

    # ── Save raw JSON ──────────────────────────────────────────────────────────
    raw_path = outdir / "raw_data.json"
    raw_path.write_text(json.dumps(results, indent=2, default=str))

    # ── Build formatted text matching production pipeline ─────────────────────
    all_labels_in_order = (
        list(zip(finnhub_labels, finnhub_raw)) +
        list(zip(fmp_labels, fmp_raw)) +
        list(zip(av_labels, av_raw)) +
        [(label, results[label]) for label, _ in sec_calls] +
        list(zip(macro_labels, macro_raw)) +
        [("core_academic_search", results.get("core_academic_search", {"error": "not run"}))]
    )
    header_lines = [
        f"# PRE-GATHERED STRUCTURED DATA FOR: {ticker}",
        f"# Fetched locally via test_data.py on {datetime.now().isoformat()}",
        f"# Mirrors _gather_structured_data() in main.py\n",
    ]
    body_lines = [_fmt(label, result) for label, result in all_labels_in_order]
    formatted_text = "\n".join(header_lines + body_lines)

    txt_path = outdir / "structured_data.txt"
    txt_path.write_text(formatted_text)

    # ── Print final summary ────────────────────────────────────────────────────
    ok_count  = sum(1 for s in statuses.values() if s == "OK")
    err_count = len(statuses) - ok_count

    print(f"\n{'─'*62}")
    print(f"  SUMMARY for {ticker}")
    print(f"{'─'*62}")
    for label, status in statuses.items():
        icon = "✅" if status == "OK" else "❌"
        print(f"  {icon} {label:<48} {status}")
    print(f"{'─'*62}")
    print(f"  {ok_count} OK  |  {err_count} failed  |  {len(statuses)} total")
    print(f"\n  Files saved:")
    print(f"    📄 {raw_path}   ({raw_path.stat().st_size:,} bytes)")
    print(f"    📄 {txt_path}   ({len(formatted_text):,} bytes)")

    if len(formatted_text) < 5_000:
        print(f"\n  ⚠️  structured_data.txt is small ({len(formatted_text):,} bytes).")
        print(f"     LLM agents likely received little usable data — check ❌ sources above.")
    elif len(formatted_text) > 50_000:
        print(f"\n  ✅ structured_data.txt looks substantial ({len(formatted_text):,} bytes).")
    else:
        print(f"\n  ✅ structured_data.txt size looks reasonable ({len(formatted_text):,} bytes).")

    return formatted_text


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    tickers = sys.argv[1:]
    if not tickers:
        print("Usage: python3 test_data.py TICKER [TICKER ...]")
        print("Example: python3 test_data.py GOOGL")
        sys.exit(1)

    for ticker in tickers:
        await gather(ticker)

    print(f"\n{'='*62}")
    print(f"  Done. Check test_output/ for results.")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    asyncio.run(main())
