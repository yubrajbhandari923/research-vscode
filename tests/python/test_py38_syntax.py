"""The package supports Python 3.8: `dict | dict` (3.9) and newer grammar must not creep in."""
import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = [f for f in glob.glob(os.path.join(ROOT, "packages", "**", "*.py"), recursive=True) if "_vendor" not in f]


def _dictish(n, dicts):
    return isinstance(n, (ast.Dict, ast.DictComp)) or (isinstance(n, ast.Name) and n.id in dicts)


def test_no_py39_syntax_or_dict_union():
    bad = []
    for f in FILES:
        src = open(f, encoding="utf-8").read()
        try:
            tree = ast.parse(src, f, feature_version=(3, 8))
        except SyntaxError as e:
            bad.append(f"{f}: {e}")
            continue
        dicts = {t.id for a in ast.walk(tree) if isinstance(a, ast.Assign) and _dictish(a.value, set())
                 for t in a.targets if isinstance(t, ast.Name)}
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) and (_dictish(n.left, dicts) or _dictish(n.right, dicts)):
                bad.append(f"{f}:{n.lineno}: dict | dict needs Python 3.9; use {{**a, **b}}")
    assert not bad, "\n".join(bad)
