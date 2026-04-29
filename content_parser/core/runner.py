"""Source-agnostic orchestrator: resolve → fetch → write."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .output import (
    write_index_markdown,
    write_item_json,
    write_item_markdown,
    write_summary_csv,
)
from .plugin import ProgressCb, SourcePlugin
from .schema import Item


LogCb = Callable[[str], None]


@dataclass
class RunResult:
    out_dir: Path
    items: list[Item] = field(default_factory=list)


def run(
    plugin: SourcePlugin,
    inputs: dict[str, list[str]],
    settings: dict[str, Any],
    secrets: dict[str, str],
    output_dir: Path | None = None,
    log: LogCb | None = None,
    progress: ProgressCb | None = None,
) -> RunResult:
    log = log or (lambda _msg: None)

    missing = plugin.validate_secrets(secrets)
    if missing:
        raise ValueError(f"Missing required secrets for {plugin.name}: {missing}")

    out_dir = output_dir or (
        Path("output") / plugin.name / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Output: {out_dir.resolve()}")

    log("Resolving inputs…")
    item_ids = plugin.resolve(inputs, settings, secrets)
    if not item_ids:
        log("No items resolved.")
        return RunResult(out_dir=out_dir, items=[])
    log(f"Found {len(item_ids)} item(s).")

    items: list[Item] = []
    for item in plugin.fetch(item_ids, settings, secrets, progress=progress):
        items.append(item)
        write_item_json(item, out_dir)
        write_item_markdown(item, out_dir)

    write_summary_csv(items, out_dir)
    write_index_markdown(items, out_dir)
    log(f"Done — {len(items)} item(s).")
    return RunResult(out_dir=out_dir, items=items)
