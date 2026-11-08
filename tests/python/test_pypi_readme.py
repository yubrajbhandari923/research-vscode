"""PyPI renders the README from the package metadata, where relative links and images 404: keep them absolute, and keep the listing's metadata complete."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_readme_has_no_relative_links_or_images():
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    bad = [t for t in re.findall(r"\]\(([^)\s]+)", text) if not re.match(r"(https?://|#|mailto:)", t)]
    assert not bad, f"relative targets break on PyPI, use absolute URLs: {bad[:5]}"


def test_package_metadata_is_complete_for_the_pypi_listing():
    if sys.version_info < (3, 11):
        import pytest
        pytest.skip("tomllib needs Python 3.11+; the metadata is the same on every version")
    import tomllib
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        p = tomllib.load(f)["project"]
    assert p["name"] == "research-panel" and p["readme"] == "README.md"
    assert {"Homepage", "Repository", "Issues", "Changelog", "Documentation"} <= set(p["urls"])
    assert any(c.startswith("License ::") for c in p["classifiers"]) and p["keywords"] and p["description"]
    assert not any("@" in a.get("email", "") for a in p["authors"]), "no personal email in public package metadata"
