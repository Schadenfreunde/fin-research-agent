# tests/macro/test_section5_rendering.py
"""
Unit tests for Section 5 conditional rendering logic (_get_section5_mode).
No API calls — pure logic tests.
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


def _get_section5_mode(report_mode: str, signal_tier: int) -> str:
    """Import and call the helper from main.py."""
    # Extract just the helper function from main.py
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    # Extract just the helper function
    start = text.index("def _get_section5_mode(")
    # Find the next top-level function definition (def or async def at line start)
    # Skip comments and blank lines
    lines = text[start:].split('\n')
    fn_lines = []
    in_function = False
    indent_level = None
    for i, line in enumerate(lines):
        if i == 0:
            # First line is the def line
            indent_level = len(line) - len(line.lstrip())
            fn_lines.append(line)
            in_function = True
        elif in_function:
            # Check if we've reached the next top-level function
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= indent_level and (stripped.startswith('def ') or stripped.startswith('async def ')):
                    # Found the next function
                    break
            fn_lines.append(line)
    fn_src = '\n'.join(fn_lines)
    ns = {}
    exec(fn_src, ns)
    return ns["_get_section5_mode"](report_mode, signal_tier)


def test_research_mode_renders_market_relevance():
    assert _get_section5_mode("research", 3) == "market_relevance"


def test_research_mode_tier1_still_market_relevance():
    # Signal agent doesn't run in research mode — tier is always 3
    assert _get_section5_mode("research", 3) == "market_relevance"


def test_both_mode_tier1_renders_trade_recommendation():
    assert _get_section5_mode("both", 1) == "trade_recommendation"


def test_both_mode_tier2_renders_investment_implications():
    assert _get_section5_mode("both", 2) == "investment_implications"


def test_both_mode_tier3_renders_market_relevance():
    assert _get_section5_mode("both", 3) == "market_relevance"


def test_unknown_tier_falls_back_to_market_relevance():
    assert _get_section5_mode("both", 99) == "market_relevance"
