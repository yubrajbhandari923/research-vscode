"""docs/PROVIDERS.md promises: the table is generated from the adapters (drift is caught by test_docs_reference), a provider can be added
at runtime, and the shipped example does that."""
import importlib.util
import os

from research import providers

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_runtime_registration_and_the_shipped_example():
    spec = importlib.util.spec_from_file_location("example_provider", os.path.join(ROOT, "examples", "custom-provider", "example_provider.py"))
    try:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        a = providers.ADAPTERS["myagent"]
        assert providers.build_argv({"provider": "myagent", "command": "myagent"}, "x", "m", "P", "/w", ["/l"], True) == \
            ["myagent", "--workdir", "/w", "--also-read", "/l", "--model", "m", "--batch", "P"]
        assert "myagent" not in providers.KNOWN and providers.LABELS["myagent"] == "myagent" and providers.CAPABILITIES["myagent"] is a.caps
    finally:
        for d in (providers.ADAPTERS, providers.LABELS, providers.CAPABILITIES):
            d.pop("myagent", None)


def test_generated_tables_name_every_provider():
    import gen_reference as gen
    for body in (gen.capabilities(), gen.levels()):
        assert all(n in body for n in providers.ADAPTERS)
