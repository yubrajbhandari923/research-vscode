---
name: reproducibility-audit
description: "Audit whether a result can be reproduced: code revision and cleanliness, environment and dependency versions, random seeds, data identity and hashes, parameters, hardware and artifact provenance. Use before reporting a result, before promoting experiment code, when a result cannot be recreated, or when asked \"is this reproducible?\"."
---

# Reproducibility audit

Audit one run or experiment at a time. For each item answer *recorded / not recorded / wrong*, with the evidence.

1. **Code**: `research show RUN-…` lists commit and dirty state. A run from a dirty tree is not reproducible: say which files differed. The commit must exist in the repo (`git cat-file -t <sha>`).
2. **Command and parameters**: the exact command and every parameter, including defaults the code fills in. Hidden defaults and environment variables count.
3. **Environment**: interpreter and package versions (`pip freeze`/lockfile, `uv.lock`), OS, hardware/accelerator, library determinism flags. `research env check` verifies the project interpreter.
4. **Randomness**: every seed (Python, NumPy, framework, data shuffling, dataloader workers, CUDA). Nondeterministic kernels named. Seeds are recorded as run parameters, not in someone's head.
5. **Data**: inputs identified by hash, version or DOI, not by path. Splits are files or seeds that regenerate them. Preprocessing is code in the commit.
6. **Outputs**: metrics logged with `research metric add`/`metrics.jsonl`; artifacts registered with `research artifact add` and hashed (`research evidence artifact`). Verify: `research evidence verify F-…`.
7. **Re-run**: the strongest test is to re-run from a clean checkout of the recorded commit with the recorded environment and compare within a tolerance justified by the nondeterminism you found.

## Report
List gaps by severity: *blocks reproduction* (dirty code, missing data identity, unrecorded seed) · *weakens* (loose versions) · *cosmetic*. Fix what you can through records (register the artifact, add the missing parameter note) and never rewrite history. Do not call a result reproducible unless step 7 was done, and say what it covered.
