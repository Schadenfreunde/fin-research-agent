"""
Tests for macro_signal_agent integration.
"""

import sys
import pathlib
import pytest


def test_signal_agent_importable():
    """Verify that macro_signal_agent is defined in agents/team.py."""
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


def test_signal_agent_has_no_tools():
    """Verify that macro_signal_agent agent has tools=[] configuration."""
    import ast

    # Read the team.py file and parse it
    team_path = pathlib.Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find the macro_signal_agent assignment and check tools parameter
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "macro_signal_agent":
                    if isinstance(node.value, ast.Call):
                        # Check that tools=[] is in the call
                        tools_found = False
                        for keyword in node.value.keywords:
                            if keyword.arg == "tools":
                                tools_found = True
                                # Should be an empty list
                                if isinstance(keyword.value, ast.List):
                                    assert len(keyword.value.elts) == 0, "tools should be empty list"
                        assert tools_found, "tools parameter not found in macro_signal_agent"
                    break


def test_signal_agent_in_macro_agents_dict():
    """Verify that macro_signal_agent is in the MACRO_AGENTS dictionary."""
    import ast

    # Read the team.py file and parse it
    team_path = pathlib.Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find the MACRO_AGENTS dictionary
    macro_agents_dict = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MACRO_AGENTS":
                    if isinstance(node.value, ast.Dict):
                        macro_agents_dict = node.value
                    break

    assert macro_agents_dict is not None, "MACRO_AGENTS dictionary not found"

    # Check if "macro_signal_agent" is a key in the dictionary
    keys = []
    for key in macro_agents_dict.keys:
        if isinstance(key, ast.Constant):
            keys.append(key.value)

    assert "macro_signal_agent" in keys, "macro_signal_agent not in MACRO_AGENTS dictionary"
