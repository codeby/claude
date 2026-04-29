"""Plugin discovery — explicit list, no entry-point magic."""
from __future__ import annotations

from .plugin import SourcePlugin


def all_plugins() -> list[SourcePlugin]:
    """Instantiate every registered plugin. Import lazily so optional deps don't break startup."""
    plugins: list[SourcePlugin] = []

    try:
        from ..plugins.youtube.plugin import YouTubePlugin
        plugins.append(YouTubePlugin())
    except Exception:
        pass

    try:
        from ..plugins.instagram.plugin import InstagramPlugin
        plugins.append(InstagramPlugin())
    except Exception:
        pass

    return plugins


def get_plugin(name: str) -> SourcePlugin:
    for p in all_plugins():
        if p.name == name:
            return p
    raise KeyError(f"No plugin named {name!r}. Available: {[p.name for p in all_plugins()]}")
