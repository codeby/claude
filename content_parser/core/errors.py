"""Common errors raised by plugins; UI/CLI map them to user-friendly messages."""


class PluginError(Exception):
    """Generic plugin error."""


class AuthError(PluginError):
    """Missing or invalid credentials."""


class RateLimitError(PluginError):
    """Source rejected the request due to rate limiting / quota."""


class ItemUnavailable(PluginError):
    """The requested item is private, removed, or geo-blocked."""
