"""Terminal UI for a ResearchOS project (`research tui`). Needs the optional `textual` package (pip install 'research-os[tui]').

`from research_cli.tui import run` is the entry point. widgets.py: shared pieces and dialogs; views.py: what each tab and the status bar show;
app.py: the Textual app, its layout and the actions that change things (always through the op layer)."""
from .app import ResearchTUI, run
from .widgets import BANNER

__all__ = ["ResearchTUI", "run", "BANNER"]
