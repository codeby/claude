"""Plugin contract — every source (YouTube, Instagram, Reddit, …) implements this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

from .schema import Item


WidgetType = Literal["text", "textarea", "password", "number", "checkbox", "select"]


@dataclass
class FieldSpec:
    key: str
    label: str
    widget: WidgetType = "text"
    default: Any = None
    options: list[str] = field(default_factory=list)
    help: str = ""
    placeholder: str = ""
    min_value: float | int | None = None
    max_value: float | int | None = None


@dataclass
class InputSpec:
    """Describes one kind of input the plugin can accept (e.g. 'channel', 'hashtag')."""
    kind: str
    label: str
    placeholder: str = ""
    help: str = ""


ProgressCb = Callable[[int, int, str], None]  # (done, total, message)


class SourcePlugin(ABC):
    name: str = ""              # internal id, e.g. "youtube"
    label: str = ""             # human label, e.g. "YouTube"
    secret_keys: list[str] = []  # required st.secrets / env vars

    @abstractmethod
    def input_specs(self) -> list[InputSpec]: ...

    @abstractmethod
    def settings_specs(self) -> list[FieldSpec]: ...

    @abstractmethod
    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        """Resolve raw inputs into a deduplicated list of item identifiers."""

    @abstractmethod
    def fetch(
        self,
        item_ids: list[str],
        settings: dict[str, Any],
        secrets: dict[str, str],
        progress: ProgressCb | None = None,
    ) -> Iterator[Item]:
        """Yield fully-populated Item for each id (or a partial Item with an error)."""

    def validate_secrets(self, secrets: dict[str, str]) -> list[str]:
        return [k for k in self.secret_keys if not secrets.get(k)]
