---
name: promote-validated-work
description: "Safely move verified experiment code from its worktree into main through Research OS promotion: select the commits, run tests, get review and approval, then integrate without losing history or pulling in experiment clutter. Use when an experiment's code is validated and should become part of the project, or when asked to merge experiment work."
---

# Promote validated work

Experiment code reaches `main` only through the promotion gates. Never `git merge`/`push` experiment branches into main by hand.

1. **Is it ready?** The experiment's tasks are verified (`research task show T-…`), findings that depend on the code are reviewed, no failing hooks (`research hook results`), worktree clean (`git status`), history readable.
2. **See the candidate**: `research promote changes WS-…` lists the commits and files ahead of main. Exclude what does not belong: generated outputs, large artifacts, caches, notebooks with outputs, secrets, one-off scripts. Keep code, tests, configs and small documentation.
3. **Prepare**: `research promote prepare WS-…` creates a candidate from the selected commits (`research promote show PROMO-…`, `diff PROMO-…`).
4. **Verify**: `research promote verify PROMO-…` runs the configured tests and hooks against the candidate (`promotion.test_command`). A failure blocks approval: fix on the experiment branch and prepare again.
5. **Review**: someone other than the author records a verdict (`research promote review PROMO-… --verdict approve|changes|comment --note …`); use `independent-review`.
6. **Approve and integrate** (human): `research promote approve PROMO-…` then `research promote integrate PROMO-… --confirm`. Integration is recorded; main keeps a clear commit with the experiment and finding ids in the message.
7. **After**: update the experiment/finding records (`research mark …`), note the promotion in the synthesis, and remove the worktree only when nothing references it (`research workspace archive`).

## Never
Skip verification or review · promote with failing tests or a dirty tree · include data/results blobs · rewrite history of promoted commits · promote on behalf of the human approval step.
