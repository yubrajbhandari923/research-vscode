# AGENTS.md — working in this repo (development branch only)

This file, and everything under "Dev-only" below, lives on `development`. **It must never reach `main`.**

## Branches
- `development`: working branch. Messy is fine here: agent instructions, internal reviews, audits, specs and proposals, design notes,
  scratch scripts, agent skills (`.claude/`), `AGENTS.md`/`CLAUDE.md`, local fixtures.
- `main`: release branch, production-ready only. Product source, tests that CI runs, user-facing docs
  (`README.md`, `CHANGELOG.md`, `docs/*.md` guides), `examples/`, release scripts (`scripts/package.sh`, `scripts/clean_install_test.sh`),
  packaging files. Every `main` commit is a merge from `development` with the dev-only paths removed. Precedent: commit a997e44.

## Dev-only (never on main)
`AGENTS.md`, `CLAUDE.md`, `.claude/`, `*REVIEW*`, `*AUDIT*`, `docs/PHASE_*`, `docs/*_SUMMARY.md`, `docs/UPGRADING.md`-style internal notes,
proposals, plans (including anything about a specific user's project such as PRAVAH), backups (`*.bak`, `*.orig`), build output (`dist/*`, `*.vsix`, `*.whl`).
Release artifacts go on the GitHub release, not in git.

## Releasing (when the user asks)
1. On `development`: bump version in `pyproject.toml`, `package.json`, `packages/vscode-extension/package.json`, `packages/core/research/__init__.py`; add a `CHANGELOG.md` entry; update README and docs for user-visible changes.
2. Run tests (`python -m pytest -q -m "not daemon"`, then `python -m pytest -q -n 0 -m daemon`), `npm run typecheck`, `scripts/package.sh`; install the VSIX locally.
3. Commit and push `development`.
4. Merge to main without committing, then strip dev-only paths:
   ```bash
   git checkout main && git merge --no-ff --no-commit development
   git rm -rqf --ignore-unmatch AGENTS.md CLAUDE.md .claude   # plus any other dev-only path
   git commit -m "Merge development: <version>" && git push origin main
   git tag -a vX.Y.Z -m "Research OS X.Y.Z" && git push origin vX.Y.Z
   # build wheel/sdist/VSIX from a CLEAN worktree of the main commit (scripts/package.sh), write SHA256SUMS, then
   gh release create vX.Y.Z --draft --title "Research OS X.Y.Z" --notes-file notes.md <wheel> <sdist> <vsix> SHA256SUMS
   gh release edit vX.Y.Z --draft=false      # publishing triggers .github/workflows/publish.yml -> PyPI (research-panel)
   ```
   PyPI versions cannot be re-uploaded: only publish a release you have already installed and smoke-tested. After the workflow succeeds,
   check `uv tool install "research-panel==X.Y.Z"` in a clean environment.
5. Check `git ls-tree -r --name-only main` shows no dev-only path. Push and release only when the user says so.

## Rules that apply to every change
Opt-in and backward-compatible; never relax governance or expose PINs; never edit a user's scientific plan; bump the version for every feature; keep docs current in the same change.
