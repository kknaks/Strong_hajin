"""Remove project docstrings from the disposable compiler copy, never from the checkout."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


class _DocstringStripper(ast.NodeTransformer):
    def _strip(self, node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                node.body.pop(0)
                if not node.body and not isinstance(node, ast.Module):
                    node.body.append(ast.Pass())
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


def strip_tree(root: Path) -> None:
    for source in sorted(root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source), type_comments=True)
        transformed = ast.fix_missing_locations(_DocstringStripper().visit(tree))
        source.write_text(ast.unparse(transformed) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: strip_docstrings.py SOURCE_ROOT")
    strip_tree(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
