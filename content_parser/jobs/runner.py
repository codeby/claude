"""Run a saved job: merge inline + Sheet inputs, call core.runner.run."""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..core.errors import PluginError
from ..core.registry import get_plugin
from ..core.runner import RunResult, run as core_run
from ..core.secrets import get_secret
from .schema import Job
from .store import load_job


# Optional secrets that any plugin may need but may not have been declared.
_OPTIONAL_SECRET_KEYS = (
    "WEBSHARE_USERNAME",
    "WEBSHARE_PASSWORD",
    "PROXY_HTTP_URL",
    "PROXY_HTTPS_URL",
    "GOOGLE_SHEETS_CREDENTIALS",
)


def _collect_secrets(plugin_secret_keys: list[str], *, need_sheets: bool) -> dict[str, str]:
    """Gather every secret the job may need."""
    keys = list(plugin_secret_keys)
    if need_sheets and "GOOGLE_SHEETS_CREDENTIALS" not in keys:
        keys.append("GOOGLE_SHEETS_CREDENTIALS")
    secrets: dict[str, str] = {k: get_secret(k) for k in keys}
    for opt in _OPTIONAL_SECRET_KEYS:
        v = get_secret(opt)
        if v:
            secrets[opt] = v
    return secrets


def _resolve_inputs(job: Job, secrets: dict[str, str]) -> dict[str, list[str]]:
    """Combine inline inputs with values pulled from any Google Sheets refs."""
    merged: dict[str, list[str]] = {k: list(v) for k, v in job.inputs.items()}

    if job.sheet_inputs:
        from ..loaders.gsheets import GoogleSheetsLoader  # noqa: PLC0415

        loader = GoogleSheetsLoader.from_secrets(secrets)
        for ref in job.sheet_inputs:
            loaded = loader.load(
                ref.sheet,
                tab=ref.tab,
                range_a1=ref.range_a1,
                skip_header=ref.skip_header,
            )
            merged.setdefault(ref.target, []).extend(loaded.values)

    # Per-kind dedupe preserving insertion order.
    for kind, values in list(merged.items()):
        merged[kind] = list(dict.fromkeys(values))
    # Drop empty kinds so plugins don't see them.
    return {k: v for k, v in merged.items() if v}


def run_job(
    name: str,
    *,
    log: Callable[[str], None] | None = None,
    progress=None,
) -> RunResult:
    """Resolve inputs and run a saved job. Writes last_error.txt on failure."""
    job = load_job(name)
    return run_job_obj(job, log=log, progress=progress)


def run_job_obj(
    job: Job,
    *,
    log: Callable[[str], None] | None = None,
    progress=None,
) -> RunResult:
    log = log or (lambda _msg: None)

    log(f"Job: {job.name} (source={job.source})")
    secrets = _collect_secrets(
        get_plugin(job.source).secret_keys,
        need_sheets=bool(job.sheet_inputs),
    )

    try:
        inputs = _resolve_inputs(job, secrets)
    except Exception as e:
        _record_failure(job, e)
        raise

    if not inputs:
        msg = f"Job {job.name!r} has no resolved inputs (inline empty, Sheets returned nothing)."
        _record_failure(job, PluginError(msg))
        raise PluginError(msg)

    plugin = get_plugin(job.source)
    out_dir = job.resolved_output_dir()
    log(f"Output: {out_dir}")

    try:
        result = core_run(
            plugin,
            inputs,
            job.settings,
            secrets,
            output_dir=out_dir,
            log=log,
            progress=progress,
        )
    except Exception as e:
        _record_failure(job, e, out_dir=out_dir)
        raise

    _record_success(job, out_dir, result)
    return result


# ----------------------------------------------------------------------
# Last-run / last-error markers


def _record_success(job: Job, out_dir: Path, result: RunResult) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / ".last_run.txt"
    marker.write_text(
        f"job: {job.name}\n"
        f"finished_at: {datetime.now().isoformat()}\n"
        f"items: {len(result.items)}\n",
        encoding="utf-8",
    )


def _record_failure(job: Job, exc: Exception, *, out_dir: Path | None = None) -> None:
    if job.notify_on_failure == "none":
        return
    target_dir = out_dir or job.resolved_output_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "last_error.txt").write_text(
            f"job: {job.name}\n"
            f"failed_at: {datetime.now().isoformat()}\n"
            f"error: {type(exc).__name__}: {exc}\n\n"
            f"{traceback.format_exc()}",
            encoding="utf-8",
        )
    except OSError:
        pass
