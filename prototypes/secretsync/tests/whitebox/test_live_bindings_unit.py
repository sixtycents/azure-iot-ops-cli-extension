"""Static live binding completeness without importing fixtures or contacting a live environment."""

import ast
from pathlib import Path
import re

from secretsync_bdd.inventory import load_inventory


def test_every_selected_live_step_has_one_semantic_binding():
    root = Path(__file__).resolve().parents[4]
    source = root / "prototypes" / "secretsync" / "tests" / "e2e" / "test_features.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    patterns = [
        re.compile(ast.literal_eval(decorator.args[0]))
        for node in ast.walk(module) if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name) and decorator.func.id in ("g", "w", "t")
    ]
    cases = load_inventory(root / "features" / "secretsync", e2e=True)
    assert len(cases) == 10
    for case in cases:
        for step in case.steps:
            assert sum(bool(pattern.fullmatch(step)) for pattern in patterns) == 1, (case.scenario_id, step)
