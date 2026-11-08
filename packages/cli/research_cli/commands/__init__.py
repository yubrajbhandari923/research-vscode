"""Command modules. Each defines its `cmd_*` functions and an `attach(sub, add, group)` that registers its parsers."""
MUTATING: set = set()


def mut(fn):
    """Mark a command as mutating: `main()` regenerates current.md after it succeeds."""
    MUTATING.add(fn.__name__)
    return fn
