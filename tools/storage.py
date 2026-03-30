"""
storage.py — Tools for saving and retrieving research reports from Google Cloud Storage.

The bucket name is loaded from the REPORTS_BUCKET environment variable,
which is set from config.yaml during deployment.
"""

import os
import datetime
import json
import subprocess
import tempfile
import time
from typing import Optional

from google.cloud import storage as gcs


def _get_bucket() -> str:
    """Get Cloud Storage bucket name from environment."""
    bucket = os.environ.get("REPORTS_BUCKET")
    if not bucket:
        raise EnvironmentError(
            "REPORTS_BUCKET not set. This should be configured in config.yaml "
            "and set as an environment variable in Cloud Run."
        )
    return bucket


def save_report(
    content: str,
    report_type: str,
    identifier: str,
    file_format: str = "md",
) -> dict:
    """
    Save a research report to Cloud Storage.

    Args:
        content: Report content as a string (Markdown)
        report_type: "equity" or "macro"
        identifier: Ticker symbol or macro topic slug (e.g., "AAPL", "us-interest-rates")
        file_format: File extension — "md" for Markdown (default)

    Returns:
        Dictionary with the storage path and public URL for the report.
    """
    try:
        client = gcs.Client()
        bucket = client.bucket(_get_bucket())

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        blob_name = f"{report_type}/{identifier}/{timestamp}.{file_format}"

        blob = bucket.blob(blob_name)
        blob.upload_from_string(content, content_type="text/markdown")

        return {
            "saved": True,
            "bucket": _get_bucket(),
            "path": blob_name,
            "gcs_uri": f"gs://{_get_bucket()}/{blob_name}",
            "timestamp": timestamp,
            "size_bytes": len(content.encode("utf-8")),
        }
    except Exception as e:
        print(f"ERROR: save_report failed for {report_type}/{identifier}: {e}")
        return {
            "saved": False,
            "error": str(e),
            "report_type": report_type,
            "identifier": identifier,
        }


def load_report(blob_name: str) -> str:
    """
    Load a previously saved report from Cloud Storage.

    Args:
        blob_name: Full path within the bucket (e.g., "equity/AAPL/20260301_120000.md")

    Returns:
        Report content as a string, or error message on failure.
    """
    try:
        client = gcs.Client()
        bucket = client.bucket(_get_bucket())
        blob = bucket.blob(blob_name)
        return blob.download_as_text()
    except Exception as e:
        print(f"ERROR: load_report failed for {blob_name}: {e}")
        return f"[ERROR: Failed to load report '{blob_name}': {e}]"


def list_reports(
    report_type: Optional[str] = None,
    identifier: Optional[str] = None,
    limit: int = 20,
) -> list:
    """
    List saved reports in Cloud Storage.

    Args:
        report_type: Filter by "equity" or "macro". If None, lists all.
        identifier: Filter by ticker/topic (e.g., "AAPL"). If None, lists all.
        limit: Maximum number of results to return.

    Returns:
        List of report metadata dictionaries, newest first.
    """
    try:
        client = gcs.Client()
        bucket = client.bucket(_get_bucket())

        # Build prefix for filtering
        if report_type and identifier:
            prefix = f"{report_type}/{identifier}/"
        elif report_type:
            prefix = f"{report_type}/"
        else:
            prefix = ""

        blobs = list(bucket.list_blobs(prefix=prefix, max_results=limit))
        blobs.sort(key=lambda b: b.time_created, reverse=True)

        return [
            {
                "path": blob.name,
                "gcs_uri": f"gs://{_get_bucket()}/{blob.name}",
                "created": blob.time_created.isoformat() if blob.time_created else None,
                "size_bytes": blob.size,
            }
            for blob in blobs
        ]
    except Exception as e:
        print(f"ERROR: list_reports failed: {e}")
        return []


def save_latex_report(
    md_content: str,
    report_type: str,
    identifier: str,
    timestamp: str,
) -> dict:
    """
    Convert a Markdown report to a LaTeX .tex file using pandoc and save it to GCS.

    The .tex file is saved alongside the .md file with the same timestamp:
        {report_type}/{identifier}/{timestamp}.tex

    Args:
        md_content: Report content as Markdown string (with YAML front matter)
        report_type: "equity" or "macro"
        identifier: Ticker symbol or macro topic slug
        timestamp: Timestamp string from the original save_report() call

    Returns:
        Dictionary with storage path and status.
    """
    try:
        # Write markdown to a temp file, run pandoc, read .tex output.
        # Retry up to 3 times — pandoc YAML parse failures are occasionally
        # transient (HsYAML parser can fail on first attempt then succeed on retry).
        _PANDOC_MAX_RETRIES = 3
        tex_content = None
        last_pandoc_error = None

        for _pandoc_attempt in range(1, _PANDOC_MAX_RETRIES + 1):
            with tempfile.TemporaryDirectory() as tmpdir:
                md_path = os.path.join(tmpdir, "report.md")
                tex_path = os.path.join(tmpdir, "report.tex")

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)

                result = subprocess.run(
                    ["pandoc", md_path, "--standalone", "-o", tex_path],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode == 0:
                    with open(tex_path, "r", encoding="utf-8") as f:
                        tex_content = f.read()
                    if _pandoc_attempt > 1:
                        print(
                            f"[save_latex_report] pandoc succeeded on attempt "
                            f"{_pandoc_attempt}/{_PANDOC_MAX_RETRIES} for "
                            f"{report_type}/{identifier}"
                        )
                    break
                else:
                    last_pandoc_error = result.stderr.strip()
                    # Log diagnostics: show YAML header start AND body start
                    # (content[:300] is just the YAML header which is rarely the problem)
                    _yaml_end = md_content.find('\n---\n', 4)
                    _body_start = repr(md_content[_yaml_end + 5: _yaml_end + 505]) if _yaml_end != -1 else repr(md_content[300:600])
                    print(
                        f"[save_latex_report] pandoc attempt {_pandoc_attempt}/"
                        f"{_PANDOC_MAX_RETRIES} failed for {report_type}/{identifier}.\n"
                        f"  stderr: {last_pandoc_error}\n"
                        f"  total_chars: {len(md_content)}\n"
                        f"  yaml_header[:300]: {repr(md_content[:300])}\n"
                        f"  body_start[0:500]: {_body_start}"
                    )
                    if _pandoc_attempt < _PANDOC_MAX_RETRIES:
                        time.sleep(2)

        if tex_content is None:
            raise RuntimeError(f"pandoc failed after {_PANDOC_MAX_RETRIES} attempts: {last_pandoc_error}")

        # Upload .tex to GCS
        client = gcs.Client()
        bucket = client.bucket(_get_bucket())
        blob_name = f"{report_type}/{identifier}/{timestamp}.tex"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(tex_content, content_type="application/x-tex")

        return {
            "saved": True,
            "bucket": _get_bucket(),
            "path": blob_name,
            "gcs_uri": f"gs://{_get_bucket()}/{blob_name}",
            "size_bytes": len(tex_content.encode("utf-8")),
        }

    except Exception as e:
        print(f"ERROR: save_latex_report failed for {report_type}/{identifier}: {e}")
        return {
            "saved": False,
            "error": str(e),
            "report_type": report_type,
            "identifier": identifier,
        }


def save_run_metadata(metadata: dict, run_id: str) -> dict:
    """
    Save run metadata (agent outputs, timing, review results) as JSON.
    Useful for debugging and audit trails.

    Args:
        metadata: Dictionary of run metadata
        run_id: Unique run identifier (timestamp-based)

    Returns:
        Dictionary with storage path.
    """
    try:
        client = gcs.Client()
        bucket = client.bucket(_get_bucket())
        blob_name = f"metadata/{run_id}.json"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(metadata, indent=2, default=str),
            content_type="application/json",
        )
        return {
            "saved": True,
            "path": blob_name,
            "gcs_uri": f"gs://{_get_bucket()}/{blob_name}",
        }
    except Exception as e:
        print(f"ERROR: save_run_metadata failed for {run_id}: {e}")
        return {"saved": False, "error": str(e)}
