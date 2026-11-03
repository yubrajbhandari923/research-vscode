"""Entry point of the persistent supervisor process: `python -m research.supervisord run --root DIR`.

Kept separate from supervisor.py so there is exactly one `research.supervisor` module object in the process (running that
module with -m would create a second copy whose completion grants the plan guard cannot see)."""
import sys

from research import supervisor

if __name__ == "__main__":
    sys.exit(supervisor.main(sys.argv[1:]))
