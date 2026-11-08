#!/usr/bin/env python3
"""Stand-in provider binary for the usage-limit tests (tests/python/test_usage_limit.py). Used as a profile's `args`, so it can be any provider kind.
--case PROVIDER:ID   while the --flag file exists, behave exactly as the fixture row (tests/python/fixtures/limits/PROVIDER.jsonl): its stdout, stderr and
                     exit code. `empty` as ID: exit 1 with nothing printed (Claude's weekly limit). Without the flag file (the limit is over) it does the work.
--transcript TEXT --projects DIR   also leave Claude's session transcript with TEXT as an API error message (where the empty-stdout weekly limit lives)
--log FILE           append this invocation's argv (tests count launches and look for --resume)
The work is one file, done.txt, in the working directory. Nothing here talks to a model."""
import json
import os
import re
import sys

a = sys.argv
if "--version" in a:         # opencode refuses headless dispatch when it cannot read a version
    print("1.19.4")
    sys.exit(0)


def opt(name):
    return a[a.index(name) + 1] if name in a else None


if opt("--log"):
    with open(opt("--log"), "a") as f:
        f.write(json.dumps(a) + "\n")
case, flag = opt("--case"), opt("--flag")
if case and flag and os.path.exists(flag):
    prov, cid = case.split(":")
    if opt("--transcript"):
        d = os.path.join(opt("--projects"), re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd())))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s.jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "isApiErrorMessage": True, "message": {"content": [{"type": "text", "text": opt("--transcript")}]}}) + "\n")
    if cid == "empty":
        sys.exit(1)
    fix = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "fixtures", "limits", prov + ".jsonl")
    row = next(r for r in map(json.loads, open(fix, encoding="utf-8")) if r["id"] == cid)
    sys.stdout.write(row["stdout"] + "\n")
    sys.stderr.write(row["stderr"] + "\n")
    sys.exit(row["exit"])
with open("done.txt", "w") as f:
    f.write("ok\n")
