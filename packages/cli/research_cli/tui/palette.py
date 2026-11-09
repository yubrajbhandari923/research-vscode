"""ctrl+p: jump to any section, any setting, or run a common action, by typing a few letters."""
from __future__ import annotations

from textual.command import DiscoveryHit, Hit, Hits, Provider


class Finder(Provider):
    def _entries(self):
        from .app import NAV_ITEMS
        app = self.app
        for ident, key, label, _ in NAV_ITEMS:
            yield f"Go to {label}", f"Show the {label} section ({key})", (lambda i=ident: app.action_nav(i))
        yield "Refresh now", "Reload everything from the project", app.action_refresh
        yield "Open the guide", "What the statuses and actions mean", app.action_help
        yield "Switch theme", "Dark or light", app.action_theme
        for o in ((app._settings_cache or {}).get("options") or []):
            yield f"Setting: {o['label']}", o["key"], (lambda k=o["key"]: app.open_setting(k))
        for it in app.inbox[:30]:
            yield f"Inbox: {it['title'][:70]}", it["why"][:90], (lambda k=it["key"]: app.open_inbox(k))

    async def discover(self) -> Hits:
        from .app import NAV_ITEMS
        app = self.app
        for ident, key, label, _ in NAV_ITEMS:
            yield DiscoveryHit(f"Go to {label}", (lambda i=ident: app.action_nav(i)), help=f"Press {key}")

    async def search(self, query: str) -> Hits:
        m = self.matcher(query)
        for title, help_, fn in self._entries():
            score = m.match(title)
            if score > 0:
                yield Hit(score, m.highlight(title), fn, help=help_)
