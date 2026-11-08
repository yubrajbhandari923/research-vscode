"""The generated reference tables in the docs match the code, and every relative link in the docs resolves."""
import glob
import os
import re

import pytest

import gen_reference as gen

ROOT = gen.ROOT
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
DOCS = [os.path.join(ROOT, "README.md")] + sorted(glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True))


def _slug(h: str) -> str:
    h = re.sub(r"[`*]", "", h.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", h))


def _anchors(path):
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", FENCE.sub("", open(path, encoding="utf8").read()), re.M)}


@pytest.mark.parametrize("name", sorted(gen.TARGETS))
def test_generated_table_is_current(name):
    assert name not in gen.stale(), f"docs table '{name}' is stale; regenerate with: {gen.COMMAND}"


@pytest.mark.parametrize("doc", [d for d in DOCS if not re.search(r"PHASE_|REVIEW|AUDIT", d)], ids=lambda d: os.path.relpath(d, ROOT))
def test_relative_links_resolve(doc):
    bad = []
    for target in LINK.findall(FENCE.sub("", open(doc, encoding="utf8").read())):
        if re.match(r"[a-z]+:", target) or target.startswith("#") and target[1:] in _anchors(doc):
            continue
        path, _, frag = target.partition("#")
        dest = os.path.normpath(os.path.join(os.path.dirname(doc), path)) if path else doc
        if not os.path.exists(dest):
            bad.append(target)
        elif frag and dest.endswith(".md") and frag not in _anchors(dest):
            bad.append(target)
    assert not bad, f"{os.path.relpath(doc, ROOT)}: broken links {bad}"
