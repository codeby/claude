"""Trim user-provided strings before they reach logs or exception messages.

Used by every plugin's fetch() error path. URLs may carry tokens in their
?query or #fragment (OAuth implicit-flow access tokens, gsheets share
tokens, etc.); long strings can flood logs. Single shared implementation
so a future fix lands everywhere.
"""
from __future__ import annotations


_TRUNCATE_LIMIT = 80
_TRUNCATE_MARKER = "…"


def redact_spec(spec: str) -> str:
    """Drop ?query / #fragment, cap total length to 80 chars.

    >>> redact_spec("post:https://reddit.com/r/x/?token=secret")
    'post:https://reddit.com/r/x/?…'
    >>> redact_spec("channel:https://t.me/durov#access_token=xxx")
    'channel:https://t.me/durov#…'
    """
    for sep in ("?", "#"):
        if sep in spec:
            spec = spec.split(sep, 1)[0] + sep + _TRUNCATE_MARKER
            break
    if len(spec) > _TRUNCATE_LIMIT:
        spec = spec[: _TRUNCATE_LIMIT - len(_TRUNCATE_MARKER)] + _TRUNCATE_MARKER
    return spec
