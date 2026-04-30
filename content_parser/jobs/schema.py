"""Job schema: YAML on disk ↔ Job dataclass in memory.

A job describes one scheduled run: which plugin to use, what inputs it needs
(inline values, Google Sheets references, or both), and an optional cron
schedule. Jobs without a schedule still work via `cli jobs run <name>`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import PluginError


# Cron expressions can vary, but common forms have 5 whitespace-separated tokens:
# "min hour day-of-month month day-of-week" with @-aliases as a separate case.
_CRON_TOKEN_RE = re.compile(r"^[\d\*/,\-A-Za-z]+$")
_CRON_ALIAS_RE = re.compile(r"^@(yearly|annually|monthly|weekly|daily|hourly|reboot)$")
# Job names map to filenames, so they must be a safe filesystem token.
_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class SheetInput:
    """One Google Sheets reference inside a job."""
    sheet: str               # URL or ID
    target: str              # plugin input kind, e.g. "community" / "channel"
    tab: str | None = None   # None or "" → first sheet
    range_a1: str = "A:A"
    skip_header: bool = False


@dataclass
class Job:
    """In-memory job. Use `Job.from_dict` / `Job.to_dict` for YAML I/O."""
    name: str
    source: str
    inputs: dict[str, list[str]] = field(default_factory=dict)
    sheet_inputs: list[SheetInput] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    schedule: str | None = None
    description: str | None = None
    output_dir: str | None = None
    notify_on_failure: str = "log"     # "log" | "none"

    # ------------------------------------------------------------------
    # Validation

    def validate(self) -> None:
        if not _JOB_NAME_RE.match(self.name):
            raise PluginError(
                f"Invalid job name {self.name!r}. "
                "Allowed: letters, digits, underscore, hyphen; 1-64 chars."
            )
        if not self.source or not isinstance(self.source, str):
            raise PluginError(f"Job {self.name!r} is missing 'source'.")
        if self.schedule is not None and not is_valid_cron(self.schedule):
            raise PluginError(
                f"Job {self.name!r} schedule {self.schedule!r} is not a valid cron expression."
            )
        if self.notify_on_failure not in ("log", "none"):
            raise PluginError(
                f"Job {self.name!r} notify_on_failure must be 'log' or 'none', "
                f"got {self.notify_on_failure!r}."
            )
        if not self.inputs and not self.sheet_inputs:
            raise PluginError(
                f"Job {self.name!r} has no inputs (need 'inputs' or 'sheet_inputs')."
            )
        for kind, values in self.inputs.items():
            if not isinstance(values, list):
                raise PluginError(
                    f"Job {self.name!r} inputs.{kind} must be a list, got {type(values).__name__}."
                )
        for ref in self.sheet_inputs:
            if not ref.sheet:
                raise PluginError(
                    f"Job {self.name!r} sheet_inputs entry missing 'sheet'."
                )
            if not ref.target:
                raise PluginError(
                    f"Job {self.name!r} sheet_inputs entry missing 'target'."
                )
        # output_dir guard: reject path-traversal segments. Absolute paths are
        # allowed (user explicitly opted in), but ".." anywhere in the value
        # is rejected — it's almost always a bug, and on shared/multi-user
        # hosts could surprise the user where files actually land.
        if self.output_dir:
            parts = Path(self.output_dir).parts
            if ".." in parts:
                raise PluginError(
                    f"Job {self.name!r} output_dir {self.output_dir!r} must not contain '..'."
                )

    # ------------------------------------------------------------------
    # Output dir resolution

    def resolved_output_dir(self, *, timestamp: str | None = None) -> Path:
        """Return the path where this run should write its files.

        Default: output/scheduled/<job-name>/<timestamp>/.
        If `output_dir` is set in YAML and absolute, used as-is.
        If relative, resolved against cwd; the timestamp subdir is still appended.
        """
        from datetime import datetime  # noqa: PLC0415

        ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.output_dir:
            base = Path(self.output_dir)
            if not base.is_absolute():
                base = Path.cwd() / base
        else:
            base = Path("output") / "scheduled" / self.name
        return base / ts

    # ------------------------------------------------------------------
    # Serialization

    @classmethod
    def from_dict(cls, data: dict, *, name_hint: str | None = None) -> Job:
        if not isinstance(data, dict):
            raise PluginError(
                f"Job YAML must be a mapping at the top level, got {type(data).__name__}."
            )
        # Allow filename to provide the name when not in the body.
        name = data.get("name") or name_hint or ""
        sheet_refs_raw = data.get("sheet_inputs") or []
        sheet_inputs: list[SheetInput] = []
        for i, ref in enumerate(sheet_refs_raw):
            if not isinstance(ref, dict):
                raise PluginError(
                    f"sheet_inputs[{i}] must be a mapping, got {type(ref).__name__}."
                )
            sheet_inputs.append(SheetInput(
                sheet=str(ref.get("sheet") or ""),
                target=str(ref.get("target") or ""),
                tab=ref.get("tab"),
                range_a1=str(ref.get("range") or ref.get("range_a1") or "A:A"),
                skip_header=bool(ref.get("skip_header", False)),
            ))
        # inputs: dict of kind → list[str]
        inputs_raw = data.get("inputs") or {}
        if not isinstance(inputs_raw, dict):
            raise PluginError("'inputs' must be a mapping kind → list.")
        inputs: dict[str, list[str]] = {}
        for k, v in inputs_raw.items():
            # Catch the common typo 'community: name' (string instead of list of names).
            # Without this check, the loop would iterate the string character by
            # character and produce one-letter "values" — silent corruption.
            if v is not None and not isinstance(v, list):
                raise PluginError(
                    f"inputs.{k} must be a list, got {type(v).__name__}: {v!r}. "
                    "Wrap a single value in [] like `inputs.{k}: [name]`."
                )
            inputs[str(k)] = [str(x) for x in (v or [])]
        settings = data.get("settings") or {}
        if not isinstance(settings, dict):
            raise PluginError("'settings' must be a mapping.")

        job = cls(
            name=str(name),
            source=str(data.get("source") or ""),
            inputs=inputs,
            sheet_inputs=sheet_inputs,
            settings=dict(settings),
            schedule=(str(data["schedule"]) if data.get("schedule") else None),
            description=(str(data["description"]) if data.get("description") else None),
            output_dir=(str(data["output_dir"]) if data.get("output_dir") else None),
            notify_on_failure=str(data.get("notify_on_failure") or "log"),
        )
        job.validate()
        return job

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "name": self.name,
            "source": self.source,
        }
        if self.description:
            out["description"] = self.description
        if self.schedule:
            out["schedule"] = self.schedule
        if self.inputs:
            out["inputs"] = {k: list(v) for k, v in self.inputs.items()}
        if self.sheet_inputs:
            out["sheet_inputs"] = [
                {
                    "sheet": ref.sheet,
                    "target": ref.target,
                    **({"tab": ref.tab} if ref.tab else {}),
                    "range": ref.range_a1,
                    **({"skip_header": True} if ref.skip_header else {}),
                }
                for ref in self.sheet_inputs
            ]
        if self.settings:
            out["settings"] = dict(self.settings)
        if self.output_dir:
            out["output_dir"] = self.output_dir
        if self.notify_on_failure != "log":
            out["notify_on_failure"] = self.notify_on_failure
        return out


# ----------------------------------------------------------------------
# YAML I/O


def load_job_yaml(text: str, *, name_hint: str | None = None) -> Job:
    """Parse a YAML document into a Job. ALWAYS uses safe_load."""
    import yaml  # noqa: PLC0415

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PluginError(f"Invalid YAML: {e}") from e
    if data is None:
        raise PluginError("Empty YAML document.")
    return Job.from_dict(data, name_hint=name_hint)


def dump_job_yaml(job: Job) -> str:
    import yaml  # noqa: PLC0415

    return yaml.safe_dump(
        job.to_dict(), allow_unicode=True, sort_keys=False, default_flow_style=False
    )


# ----------------------------------------------------------------------
# Cron validation


def is_valid_cron(expr: str) -> bool:
    """Loose check: 5 whitespace-separated tokens of cron-y characters,
    or a recognised @-alias (@daily, @weekly, ...). Doesn't fully validate
    field ranges — leaves that to crond at install time."""
    if not isinstance(expr, str):
        return False
    expr = expr.strip()
    if _CRON_ALIAS_RE.match(expr):
        return True
    parts = expr.split()
    if len(parts) != 5:
        return False
    return all(_CRON_TOKEN_RE.match(p) for p in parts)
