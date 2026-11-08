#!/usr/bin/env python3
"""Stand-in for the claude / codex / agy binaries (symlinked under those names): answers the probes Research OS makes, runs nothing."""
import os
import sys

name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if "--version" in args:
    print(f"{name} 0.0-fake")
elif args[:2] == ["mcp", "list"]:
    print("research")
elif args[:2] == ["mcp", "add"]:
    pass
elif args[:1] == ["models"]:
    print("fake-model\tFake model")
elif "--help" in args:
    print("--resume --conversation")
sys.exit(0)
