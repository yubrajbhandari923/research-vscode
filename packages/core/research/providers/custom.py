"""`custom`: any other CLI, described entirely by the profile (args, model_flag, prompt_flag, headless_args)."""
from __future__ import annotations

from typing import List

from ..util import ResearchError, as_list
from .base import Adapter


def build(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    argv = [prof.get("command"), *[str(a) for a in as_list(prof.get("args"))]]
    if headless:
        if not prof.get("headless_args"):
            raise ResearchError(f"Profile {prof.get('command')!r} has no headless_args, so headless execution is unsupported",
                                hint="Set agents.profiles.<name>.headless_args (and usage_parser) or use interactive mode.")
        argv += [str(a) for a in as_list(prof["headless_args"])]
    if model and prof.get("model_flag"):
        argv += [prof["model_flag"], str(model)]
    if prof.get("prompt_flag"):
        argv.append(prof["prompt_flag"])
    return argv + [prompt]


# No model is known for an arbitrary CLI: the level pack is empty on purpose, so a profile for `custom` is always the user's own.
ADAPTER = Adapter(
    name="custom", label="Custom CLI", build=build, levels={lv: {"model": None, "effort": None} for lv in ("fast", "balanced", "deep")},
    caps={"interactive": True, "headless": "if headless_args", "model": "if model_flag", "effort": False,
          "structured_output": None, "usage": "if usage_parser", "resume": False, "mcp": None, "instructions": None})
