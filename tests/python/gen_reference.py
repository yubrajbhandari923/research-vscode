"""Regenerate the reference tables in the docs from the code (CLI parser, op registry, MCP tools, settings registry).

    python tests/python/gen_reference.py            # rewrite the tables in place
    python tests/python/gen_reference.py --check    # exit 1 when a table is stale (test_docs_reference.py does this)

A table lives between `<!-- generated:NAME -->` and `<!-- /generated:NAME -->` in the file named in TARGETS. Edit the code, not the table.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for sub in ("packages/core", "packages/cli"):
    sys.path.insert(0, os.path.join(ROOT, sub))

COMMAND = "python tests/python/gen_reference.py"
TARGETS = {"cli": "docs/spec/09-interfaces.md", "ops": "docs/spec/09-interfaces.md", "mcp": "docs/spec/09-interfaces.md",
           "config": "docs/spec/10-configuration.md"}


def _cell(s) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def _table(head, rows) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows])


def _subactions(parser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _commands(sa):
    """(visible: [(name, aliases, help, parser)], hidden names). A parser registered under several names is one command."""
    names = {}
    for k, p in sa.choices.items():
        names.setdefault(id(p), []).append(k)
    visible, shown = [], set()
    for act in sa._choices_actions:
        p = sa.choices[act.dest]
        shown.add(id(p))
        visible.append((act.dest, [k for k in names[id(p)] if k != act.dest], act.help or "", p))
    hidden = sorted(k for i, ks in names.items() if i not in shown for k in ks)
    return visible, hidden


def cli() -> str:
    from research_cli.main import build_parser
    visible, hidden = _commands(_subactions(build_parser()))
    out = [_table(["Command", "Aliases", "Summary"], [(f"`research {n}`", ", ".join(f"`{a}`" for a in al), h) for n, al, h, _ in visible]), ""]
    for n, _, _, p in visible:
        sa = _subactions(p)
        if sa and sa._choices_actions:
            v, hid = _commands(sa)
            out.append(f"**`research {n}`** actions: " + "; ".join(f"`{a}`" + (f" ({h})" if h else "") for a, _, h, _ in v)
                       + (f". Hidden aliases: {', '.join(f'`{x}`' for x in hid)}." if hid else ""))
            out.append("")
    out.append("Hidden top-level aliases (kept so older scripts and agent sessions keep working; not listed by `--help`): "
               + ", ".join(f"`{h}`" for h in hidden) + ".")
    return "\n".join(out)


def ops() -> str:
    from research import api
    rows = []
    for name in sorted(api.OPS):
        o = api.OPS[name]
        rows.append((f"`{name}`", ", ".join(o.params) or "", "yes" if o.mutates else "", f"`{o.mcp}`" if o.mcp else "", o.chat or ""))
    return f"{len(rows)} operations.\n\n" + _table(["Op (RPC method)", "Parameters", "Mutates", "MCP tool", "Chat"], rows)


def mcp() -> str:
    from research import mcp as m
    tools = m._tools()
    rows = [(f"`{t.name}`", re.split(r"(?<=[.])\s", t.description)[0], "" if not t.mutating else "yes", ", ".join(f"`{k}`" for k in t.schema["required"])) for t in tools]
    return f"{len(rows)} tools.\n\n" + _table(["Tool", "Purpose", "Mutates", "Required arguments"], rows)


def config() -> str:
    from research import registry
    cats = dict(registry.CATEGORIES)
    out = [f"{len(registry.OPTIONS)} registered settings. Keys not listed here still work in YAML but have no form; run `research config options` for the full help text."]
    for cat, label in registry.CATEGORIES:
        opts = [o for o in registry.OPTIONS if o.category == cat]
        if not opts:
            continue
        rows = [(f"`{o.key}`", o.type, f"`{registry.default(o.key)!r}`" if registry.default(o.key) is not None else "`null`", "human only" if o.risk != "normal" else "", o.help) for o in opts]
        out += ["", f"#### {cats[cat]}", "", _table(["Key", "Type", "Default", "Who", "Meaning"], rows)]
    return "\n".join(out)


def render(name: str, text: str, body: str) -> str:
    pat = re.compile(rf"(<!-- generated:{name} -->).*?(<!-- /generated:{name} -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"missing markers for generated:{name}")
    return pat.sub(lambda m: m.group(1) + "\n" + body.strip("\n") + "\n" + m.group(2), text, count=1)


def stale() -> list:
    """Names whose table in the docs differs from what the code generates."""
    return [n for n, f in TARGETS.items() if render(n, open(os.path.join(ROOT, f), encoding="utf8").read(), globals()[n]()) != open(os.path.join(ROOT, f), encoding="utf8").read()]


def main() -> int:
    check = "--check" in sys.argv[1:]
    bad = []
    for f in sorted(set(TARGETS.values())):
        path = os.path.join(ROOT, f)
        old = text = open(path, encoding="utf8").read()
        for n in (n for n, t in TARGETS.items() if t == f):
            text = render(n, text, globals()[n]())
        if text != old:
            bad.append(f)
            if not check:
                open(path, "w", encoding="utf8").write(text)
    if check and bad:
        print(f"stale reference tables in {', '.join(bad)}; run: {COMMAND}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
