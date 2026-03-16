"""
http_client.py — Shared HTTP client and utilities for all financial API modules.

Provides:
  - api_get()           — GET with consistent timeout, error format, and latency logging
  - api_get_with_auth() — GET with an auth query parameter (token=, apikey=, etc.)
  - get_api_key()       — Unified environment variable loader (replaces 7 identical helpers)
  - @handle_api_errors  — Decorator that wraps functions in try/except returning error dicts

All functions are synchronous (called via loop.run_in_executor from async code).
"""

import os
import json
import time
import logging
import urllib.request
import urllib.parse
import urllib.error
from functools import wraps
from typing import Optional

logger = logging.getLogger("finresearch.http")


# ── API key loading ───────────────────────────────────────────────────────────

def get_api_key(env_var: str, secret_name: str = "", required: bool = True) -> str:
    """
    Load an API key from an environment variable.

    Args:
        env_var: Environment variable name (e.g., "FINNHUB_API_KEY")
        secret_name: Secret Manager name for the error message (e.g., "finnhub-api-key")
        required: If True, raises EnvironmentError when missing. If False, returns "".

    Returns:
        The API key string, or "" if not required and not set.
    """
    key = os.environ.get(env_var, "")
    if not key and required:
        raise EnvironmentError(
            f"{env_var} not set. Store your key in Secret Manager as '{secret_name}' "
            f"and mount it as an environment variable in Cloud Run (see MAINTENANCE.md)."
        )
    return key


# ── HTTP GET helpers ──────────────────────────────────────────────────────────

def api_get(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 15,
    source_label: str = "unknown",
) -> dict | list:
    """
    Make a GET request, parse JSON, return data.

    On failure, raises the exception (let @handle_api_errors or caller deal with it).
    Logs latency for every call.

    Args:
        url: Full URL including query string.
        headers: Optional HTTP headers dict.
        timeout: Socket timeout in seconds (default 15).
        source_label: Label for logging (e.g., "Finnhub", "FMP").

    Returns:
        Parsed JSON (dict or list).
    """
    req = urllib.request.Request(url, headers=headers or {})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        latency_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "API OK: %s (%.0fms)", source_label, latency_ms,
        )
        return data
    except Exception:
        latency_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "API FAIL: %s (%.0fms)", source_label, latency_ms,
        )
        raise


def api_get_with_auth(
    base_url: str,
    path: str,
    auth_param: tuple[str, str],
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 15,
    source_label: str = "unknown",
) -> dict | list:
    """
    GET with an auth query parameter appended (e.g., token=KEY, apikey=KEY).

    Args:
        base_url: API base URL (e.g., "https://finnhub.io/api/v1").
        path: API endpoint path (e.g., "/quote").
        auth_param: Tuple of (param_name, param_value) — e.g., ("token", key).
        params: Additional query parameters.
        headers: Optional HTTP headers.
        timeout: Socket timeout in seconds.
        source_label: Label for logging.

    Returns:
        Parsed JSON (dict or list).
    """
    query = {auth_param[0]: auth_param[1]}
    if params:
        query.update(params)
    url = f"{base_url}{path}?{urllib.parse.urlencode(query)}"
    return api_get(url, headers=headers, timeout=timeout, source_label=source_label)


# ── Error handling decorator ──────────────────────────────────────────────────

def handle_api_errors(source_name: str):
    """
    Decorator that wraps a function in try/except and returns an error dict on failure.

    The decorated function's body becomes the happy path only — no try/except needed.
    On exception, returns {"error": str(e), "source": source_name} merged with any
    keyword args that were passed (e.g., ticker, series_id) for context.

    Usage:
        @handle_api_errors("Finnhub get_quote")
        def get_quote_finnhub(ticker: str) -> dict:
            ...  # happy path only, no try/except
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # Build a minimal error dict with context
                error_dict = {
                    "error": str(e),
                    "source": source_name,
                }
                # Include the first positional arg if it looks like a ticker/id
                if args:
                    first_arg = args[0]
                    if isinstance(first_arg, str) and len(first_arg) < 30:
                        error_dict["ticker"] = first_arg
                return error_dict
        return wrapper
    return decorator
