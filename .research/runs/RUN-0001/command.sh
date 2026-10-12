#!/bin/bash
# RUN-0001 — re-run this command (paths relative to the project root)
cd "$(dirname "$0")/../../.." && cd .
.venv/bin/python -m pytest -q
