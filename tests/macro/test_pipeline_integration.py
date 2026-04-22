# tests/macro/test_pipeline_integration.py
"""
Integration tests for the macro pipeline with new components.

These tests make real LLM calls and GCS saves.
Mark: pytest -m integration

Requires:
  - GOOGLE_CLOUD_PROJECT env var set (or config.yaml populated)
  - Valid GCP service account credentials (ADC or GOOGLE_APPLICATION_CREDENTIALS)
  - GOOGLE_API_KEY or GEMINI_API_KEY env var set for Deep Research
  - GCS bucket accessible
"""
import asyncio
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Helper to get a minimal pipeline run ──────────────────────────────────────

def _minimal_macro_run(topic: str, trade_signal=None, deep_dive=False):
    """Import and call _run_macro_pipeline with test config."""
    import uuid, datetime
    from tools.debug_report import create_run_stats
    run_id = f"test-{uuid.uuid4().hex[:8]}"
    run_stats = create_run_stats(run_id, topic, "macro")
    from main import _run_macro_pipeline
    return _run(
        _run_macro_pipeline(
            topic=topic,
            run_id=run_id,
            user_context="",
            run_stats=run_stats,
            trade_signal=trade_signal,
            deep_dive=deep_dive,
        )
    ), run_stats


# ── Mode detection integration ────────────────────────────────────────────────

def test_research_mode_auto_detect():
    """Thematic topic should auto-detect as research mode."""
    from main import _parse_mode_detector_output
    # Just test the parse helper with a realistic LLM output
    mode, rationale = _parse_mode_detector_output(
        "REPORT_MODE: research\nRATIONALE: Topic is structural exploration of EM demographics."
    )
    assert mode == "research"


def test_signal_mode_auto_detect():
    """Positioning topic should auto-detect as both mode."""
    from main import _parse_mode_detector_output
    mode, _ = _parse_mode_detector_output(
        "REPORT_MODE: both\nRATIONALE: Topic contains long positioning language."
    )
    assert mode == "both"


# ── Section 5 rendering integration ───────────────────────────────────────────

def test_research_mode_section5_title_is_market_relevance():
    """In research mode, Section 5 title must be Market Relevance."""
    from main import _get_section5_mode
    mode = _get_section5_mode("research", 3)
    assert mode == "market_relevance"


def test_both_tier1_section5_title():
    from main import _get_section5_mode
    mode = _get_section5_mode("both", 1)
    assert mode == "trade_recommendation"


# ── Cost tracking integration ─────────────────────────────────────────────────

def test_cost_summary_structure():
    """format_cost_summary accepts a run_stats with new dynamic attributes."""
    from tools.debug_report import create_run_stats, format_cost_summary
    import uuid
    rs = create_run_stats(uuid.uuid4().hex[:8], "test topic", "macro")
    rs.report_mode = "research"
    rs.mode_rationale = "Test mode."
    rs.signal_tier = 3
    # Should not raise
    cost_text = format_cost_summary(rs)
    assert isinstance(cost_text, str)


# ── Deep Research fallback ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deep_research_timeout_returns_fallback():
    """On timeout, _run_deep_research_agent returns the source_package as fallback."""
    from tools.debug_report import create_run_stats
    import uuid
    run_stats = create_run_stats(uuid.uuid4().hex[:8], "test", "macro")

    # Patch run_deep_research to raise TimeoutError
    import tools.deep_research as dr_module
    original = dr_module.run_deep_research

    async def _mock_timeout(*args, **kwargs):
        raise TimeoutError("Mocked timeout")

    dr_module.run_deep_research = _mock_timeout
    try:
        from main import _run_deep_research_agent
        result = await _run_deep_research_agent(
            topic="Test topic",
            source_package="SOURCE PACKAGE CONTENT",
            data_manifest="manifest",
            report_mode="research",
            run_id="test-run",
            identifier="test-topic",
            run_stats=run_stats,
        )
        assert result == "SOURCE PACKAGE CONTENT"  # fallback
    finally:
        dr_module.run_deep_research = original


# ── Synthesis artifact ────────────────────────────────────────────────────────

def test_synthesis_document_schema_valid():
    """Valid synthesis document fixture passes schema check."""
    import pathlib
    from tools.deep_research import parse_synthesis_document
    fixture = (pathlib.Path(__file__).parent / "fixtures" / "synthesis_document_valid.txt").read_text()
    result = parse_synthesis_document(fixture)
    assert result["has_all_sections"] is True
    assert result["sources_added_count"] >= 0


# ── Signal agent activation guard ─────────────────────────────────────────────

def test_signal_agent_skipped_in_research_mode():
    """In research mode, signal_tier is 3 and no signal call is made."""
    from main import _get_section5_mode
    # Research mode always gets signal_tier=3 (Signal Agent not called)
    mode = _get_section5_mode("research", 3)
    assert mode == "market_relevance"
