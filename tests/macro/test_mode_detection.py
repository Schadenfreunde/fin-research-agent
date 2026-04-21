"""
Unit tests for macro pipeline mode detection logic.
Tests _parse_mode_detector_output() helper and explicit flag handling.
No API calls — pure logic tests.
"""
import sys
import pathlib
import pytest

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


# ── Tests for _parse_mode_detector_output ─────────────────────────────────────

def _parse(raw: str):
    """Import and call the parse helper from main.py."""
    # Extract just the helper function from main.py
    src = pathlib.Path(__file__).parent.parent.parent / "main.py"
    text = src.read_text()
    # Extract just the helper function
    start = text.index("def _parse_mode_detector_output(")
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
    return ns["_parse_mode_detector_output"](raw)


def test_parse_research_mode():
    mode, rationale = _parse("REPORT_MODE: research\nRATIONALE: Topic is structural exploration.")
    assert mode == "research"
    assert "structural" in rationale


def test_parse_both_mode():
    mode, rationale = _parse("REPORT_MODE: both\nRATIONALE: Topic contains long positioning for EUR/USD.")
    assert mode == "both"
    assert rationale != ""


def test_parse_case_insensitive():
    mode, _ = _parse("report_mode: RESEARCH\nrationale: Thematic.")
    assert mode == "research"


def test_parse_fallback_on_garbage():
    mode, rationale = _parse("I am unable to classify this.")
    assert mode == "research"  # safe fallback
    assert rationale != ""


def test_parse_unknown_mode_falls_back():
    mode, _ = _parse("REPORT_MODE: signal_only\nRATIONALE: Something.")
    assert mode == "research"  # "signal_only" not in allowed set


def test_parse_rationale_present_both():
    _, rationale = _parse("REPORT_MODE: both\nRATIONALE: Explicit EUR/USD long positioning.")
    assert len(rationale) > 5


def test_parse_rationale_present_research():
    _, rationale = _parse("REPORT_MODE: research\nRATIONALE: Demographic analysis.")
    assert len(rationale) > 5


# ── Tests for agent configuration (AST-based to avoid importing google.adk) ─────

def test_mode_detector_agent_importable():
    """Verify that macro_mode_detector agent is defined in agents/team.py."""
    import ast

    # Read the team.py file and parse it
    team_path = pathlib.Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find the macro_mode_detector assignment
    macro_mode_detector_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "macro_mode_detector":
                    macro_mode_detector_found = True
                    # Verify it's a call to Agent()
                    if isinstance(node.value, ast.Call):
                        # Check that name="macro_mode_detector" is in the call
                        for keyword in node.value.keywords:
                            if keyword.arg == "name":
                                if isinstance(keyword.value, ast.Constant):
                                    assert keyword.value.value == "macro_mode_detector"
                    break

    assert macro_mode_detector_found, "macro_mode_detector agent not found in agents/team.py"


def test_mode_detector_has_no_tools():
    """Verify that macro_mode_detector agent has tools=[] configuration."""
    import ast

    # Read the team.py file and parse it
    team_path = pathlib.Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find the macro_mode_detector assignment and check tools parameter
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "macro_mode_detector":
                    if isinstance(node.value, ast.Call):
                        # Check that tools=[] is in the call
                        tools_found = False
                        for keyword in node.value.keywords:
                            if keyword.arg == "tools":
                                tools_found = True
                                # Should be an empty list
                                if isinstance(keyword.value, ast.List):
                                    assert len(keyword.value.elts) == 0, "tools should be empty list"
                        assert tools_found, "tools parameter not found in macro_mode_detector"
                    break
