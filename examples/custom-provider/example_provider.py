"""Minimal custom provider adapter for a hypothetical `myagent` CLI. Import this module before dispatching
(for example from a script that calls research.agents.dispatch), then use `provider: myagent` in a profile."""
from research import providers


def build(prof, model, effort, prompt, cwd, extra_dirs, headless):
    argv = [prof.get("command") or "myagent", "--workdir", cwd]
    for d in extra_dirs:
        argv += ["--also-read", d]
    if model:
        argv += ["--model", str(model)]
    return argv + (["--batch", prompt] if headless else [prompt])   # a list, never a shell string


providers.register("myagent", build, {
    "interactive": True, "headless": True, "model": True, "effort": False, "structured_output": None,
    "usage": False, "resume": False, "mcp": None, "instructions": "AGENTS.md"})
