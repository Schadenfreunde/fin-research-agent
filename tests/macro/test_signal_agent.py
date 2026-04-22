# tests/macro/test_signal_agent.py
"""
Unit tests for Signal Agent output parsing and activation logic.
No API calls — tests _parse_signal_agent_output and guard conditions.
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


def _parse(raw: str) -> dict:
    """Import and call the parse helper from main.py."""
    # Extract just the helper function from main.py
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    # Extract just the helper function
    start = text.index("def _parse_signal_agent_output(")
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
    return ns["_parse_signal_agent_output"](raw)


_TIER1_OUTPUT = """## Signal Assessment
Conviction tier: 1
Tier rationale: All four conditions met — EUR/USD driver, June 12 ECB catalyst, 1.08 threshold, 2014 analog.

### Recommendation
Instrument: EUR/USD
Direction: Short
Entry rationale: ECB rate path divergence vs Fed on-hold makes EUR/USD structurally weak into June 12.
Stop condition: Close above 1.1000 on a weekly basis.
Time horizon: 4-6 weeks
"""

_TIER2_OUTPUT = """## Signal Assessment
Conviction tier: 2
Tier rationale: Directional but catalyst timing is uncertain — BoE MPC date unclear.

### Recommendation
Bias short gilts (10Y) on a 3-6 month horizon pending BoE MPC guidance. No specific entry or stop.
"""

_TIER3_OUTPUT = """## Signal Assessment
Conviction tier: 3
Tier rationale: Purely thematic — no instrument mapping for demographic shifts in EM.

### Recommendation
This theme is relevant to EM equity (productivity headwind), EM local currency bonds (savings rate decline),
and EM FX (current account dynamics). No actionable stance at this time.
"""


def test_signal_agent_importable():
    """Verify that macro_signal_agent is defined in agents/team.py using AST."""
    import ast

    # Read the team.py file and parse it
    team_path = pathlib.Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find the macro_signal_agent assignment
    macro_signal_agent_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "macro_signal_agent":
                    macro_signal_agent_found = True
                    # Verify it's a call to Agent()
                    if isinstance(node.value, ast.Call):
                        # Check that name="macro_signal_agent" is in the call
                        for keyword in node.value.keywords:
                            if keyword.arg == "name":
                                if isinstance(keyword.value, ast.Constant):
                                    assert keyword.value.value == "macro_signal_agent"
                    break

    assert macro_signal_agent_found, "macro_signal_agent agent not found in agents/team.py"


def test_tier1_parsed_correctly():
    result = _parse(_TIER1_OUTPUT)
    assert result["tier"] == 1
    assert "June 12" in result["tier_rationale"]


def test_tier2_parsed_correctly():
    result = _parse(_TIER2_OUTPUT)
    assert result["tier"] == 2


def test_tier3_parsed_correctly():
    result = _parse(_TIER3_OUTPUT)
    assert result["tier"] == 3


def test_tier1_recommendation_non_empty():
    result = _parse(_TIER1_OUTPUT)
    assert len(result["recommendation"]) > 20


def test_tier_rationale_always_present():
    for raw in (_TIER1_OUTPUT, _TIER2_OUTPUT, _TIER3_OUTPUT):
        result = _parse(raw)
        assert len(result["tier_rationale"]) > 5, f"Empty rationale for tier {result['tier']}"


def test_parse_fallback_on_garbage():
    result = _parse("This is not a valid signal agent output.")
    assert result["tier"] == 3  # safe fallback


def test_full_text_preserved():
    result = _parse(_TIER1_OUTPUT)
    assert result["full_text"] == _TIER1_OUTPUT
