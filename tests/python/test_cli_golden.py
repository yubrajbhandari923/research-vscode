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
    bad = [(w["argv"], k) for w, g in zip(want, got) for k in ("rc", "stdout", "stderr") if w[k] != g[k]]
    assert not bad, bad
