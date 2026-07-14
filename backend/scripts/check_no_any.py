"""Reject explicit typing.Any usage in backend Python code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKED_PATHS = [ROOT / "src", ROOT / "tests"]


def main() -> int:
    violations = [
        violation
        for checked_path in CHECKED_PATHS
        for python_path in sorted(checked_path.rglob("*.py"))
        for violation in _any_violations(python_path)
    ]
    if not violations:
        return 0

    print("typing.Any is not allowed in backend Python code:")
    for violation in violations:
        print(violation)
    return 1


def _any_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.relative_to(ROOT)}:{node.lineno}:{node.col_offset + 1}: {reason}"
        for node, reason in _walk_any_nodes(tree)
    ]


def _walk_any_nodes(tree: ast.AST) -> list[tuple[ast.stmt | ast.expr, str]]:
    violations: list[tuple[ast.stmt | ast.expr, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "Any":
                    violations.append((node, "do not import Any from typing"))
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            if isinstance(node.value, ast.Name) and node.value.id == "typing":
                violations.append((node, "do not reference typing.Any"))
        elif isinstance(node, ast.Name) and node.id == "Any":
            violations.append((node, "do not reference Any"))
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
