"""Held-out oracle (not in the agent's workspace): the lab's edge convention is reflection."""
import os, sys
sys.path.insert(0, os.getcwd())
from smooth3 import moving_average
cases = [([1, 2, 3, 4, 5], 3, [1.6666666666666667, 2.0, 3.0, 4.0, 4.333333333333333]),
         ([0, 0, 9, 0, 0], 5, [3.6, 1.8, 1.8, 1.8, 3.6])]
for x, w, want in cases:
    got = moving_average(x, w)
    ok = len(got) == len(want) and all(abs(a - b) < 1e-9 for a, b in zip(got, want))
    if not ok:
        print(f"moving_average({x}, {w}) = {got} but the lab's edge convention requires {want}")
        sys.exit(1)
print("edge convention ok")
