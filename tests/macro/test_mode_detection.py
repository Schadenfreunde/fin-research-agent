"""
Test mode detection agent - imports and basic functionality.
"""

def test_mode_detector_agent_defined():
    """Verify that macro_mode_detector agent is defined in agents/team.py."""
    import ast
    import sys
    from pathlib import Path

    # Read the team.py file and parse it
    team_path = Path(__file__).parent.parent.parent / "agents" / "team.py"
    with open(team_path) as f:
        content = f.read()

    # Parse the AST
    tree = ast.parse(content)

    # Find all assignments
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]

    # Check if macro_mode_detector is defined
    macro_mode_detector_found = False
    for assignment in assignments:
        for target in assignment.targets:
            if isinstance(target, ast.Name) and target.id == "macro_mode_detector":
                macro_mode_detector_found = True
                break

    assert macro_mode_detector_found, "macro_mode_detector agent not found in agents/team.py"

    # Check if it's in MACRO_AGENTS dict
    macro_agents_found = False
    for assignment in assignments:
        for target in assignment.targets:
            if isinstance(target, ast.Name) and target.id == "MACRO_AGENTS":
                # This is the MACRO_AGENTS dict
                macro_agents_found = True
                # Check if macro_mode_detector is in the dict keys
                if isinstance(assignment.value, ast.Dict):
                    keys = [k.value for k in assignment.value.keys if isinstance(k, ast.Constant)]
                    assert "macro_mode_detector" in keys, "macro_mode_detector not in MACRO_AGENTS dict"
                break

    assert macro_agents_found, "MACRO_AGENTS dict not found"
