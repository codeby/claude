"""Plugin discovery — explicit list, no entry-point magic.

Plugins that fail to import for *missing optional dependencies* are skipped
quietly so the rest of the registry stays usable. Any other failure (typo,
runtime error in plugin module) is reported to stderr instead of disappearing.
"""
from __future__ import annotations

import logging
import sys

from .plugin import SourcePlugin

logger = logging.getLogger(__name__)


def _try_load(loader, label: str) -> SourcePlugin | None:
    try:
        return loader()
    except ImportError as e:
        logger.debug("Skipping %s plugin (optional dep missing): %s", label, e)
        return None
    except Exception as e:  # pragma: no cover - defensive
        print(
            f"[content_parser.registry] WARNING: {label} plugin failed to load: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None


def all_plugins() -> list[SourcePlugin]:
    """Instantiate every registered plugin."""
    plugins: list[SourcePlugin] = []

    def _load_youtube():
        from ..plugins.youtube.plugin import YouTubePlugin
        return YouTubePlugin()

    def _load_instagram():
        from ..plugins.instagram.plugin import InstagramPlugin
        return InstagramPlugin()

    def _load_reddit():
        from ..plugins.reddit.plugin import RedditPlugin
        return RedditPlugin()

    def _load_vk():
        from ..plugins.vk.plugin import VKPlugin
        return VKPlugin()

    def _load_telegram():
        from ..plugins.telegram.plugin import TelegramPlugin
        return TelegramPlugin()

    def _load_instagram_graph():
        from ..plugins.instagram_graph.plugin import InstagramGraphPlugin
        return InstagramGraphPlugin()

    for loader, label in [
        (_load_youtube, "youtube"),
        (_load_instagram, "instagram"),
        (_load_reddit, "reddit"),
        (_load_vk, "vk"),
        (_load_telegram, "telegram"),
        (_load_instagram_graph, "instagram_graph"),
    ]:
        p = _try_load(loader, label)
        if p is not None:
            plugins.append(p)

    return plugins


def get_plugin(name: str) -> SourcePlugin:
    for p in all_plugins():
        if p.name == name:
            return p
    available = [p.name for p in all_plugins()]
    raise KeyError(f"No plugin named {name!r}. Available: {available}")
