"""The JSON-RPC wire protocol is what the VS Code extension depends on: a recorded session must replay identically."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_rpc_golden as G  # noqa: E402


def test_rpc_session_replays_identically():
    with open(G.GOLDEN, encoding="utf-8") as f:
        want = [json.loads(l) for l in f if l.strip()]
    got = G.run_all()
    assert len(got) == len(want)
    for w, g in zip(want, got):
        assert (g["method"], g["params"]) == (w["method"], w["params"])
        assert g["resp"] == w["resp"], f"{w['method']} {w['params']} changed:\nwant {w['resp'][:600]}\ngot  {g['resp'][:600]}"
