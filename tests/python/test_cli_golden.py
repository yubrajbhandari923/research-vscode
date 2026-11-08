"""The CLI behaves exactly as when the baseline was recorded (WP8): same stdout, stderr and exit code for ~90 invocations."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import cli_golden


def test_cli_matches_golden():
    want = [json.loads(l) for l in open(cli_golden.GOLDEN, encoding="utf-8")]
    got = cli_golden.run_all()
    assert len(got) == len(want)
    bad = [(w["argv"][:4], k, _first_diff(w[k], g[k])) for w, g in zip(want, got) for k in ("rc", "stdout", "stderr") if w[k] != g[k]]
    assert not bad, bad


def _first_diff(want, got, width=70):
    a, b = str(want), str(got)
    i = next((j for j, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"want …{a[max(0, i - 20):i + width]!r} got …{b[max(0, i - 20):i + width]!r}"
