"""Filesystem-backed job store at ~/.content_parser/jobs/<name>.yaml.

All paths are resolved through `Path.resolve()` and checked to live under
JOBS_DIR before any read/write — the validated job-name regex prevents
filenames like `../../etc/passwd.yaml`, but the resolve check is a belt
on top of the suspenders.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core.errors import PluginError
from .schema import Job, _JOB_NAME_RE, dump_job_yaml, load_job_yaml


JOBS_DIR = Path.home() / ".content_parser" / "jobs"


def _job_path(name: str) -> Path:
    if not _JOB_NAME_RE.match(name):
        raise PluginError(
            f"Invalid job name {name!r}. "
            "Allowed: letters, digits, underscore, hyphen; 1-64 chars."
        )
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    candidate = (JOBS_DIR / f"{name}.yaml").resolve()
    # Defense-in-depth: refuse if the resolved path escapes JOBS_DIR.
    base = JOBS_DIR.resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise PluginError(f"Job path escapes the jobs directory: {candidate}")
    return candidate


def list_jobs() -> list[Job]:
    """Return every well-formed job file in the directory, sorted by name.

    Files that fail to parse are skipped; their names are surfaced separately
    via `list_invalid()` if a caller wants to show errors.
    """
    if not JOBS_DIR.exists():
        return []
    out: list[Job] = []
    for path in sorted(JOBS_DIR.glob("*.yaml")):
        try:
            out.append(load_job(path.stem))
        except PluginError:
            continue
    return out


def list_invalid() -> list[tuple[str, str]]:
    """Return (name, error) pairs for files that don't parse."""
    if not JOBS_DIR.exists():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(JOBS_DIR.glob("*.yaml")):
        try:
            load_job(path.stem)
        except PluginError as e:
            out.append((path.stem, str(e)))
    return out


def load_job(name: str) -> Job:
    path = _job_path(name)
    if not path.exists():
        raise PluginError(f"Job {name!r} not found at {path}")
    return load_job_yaml(path.read_text(encoding="utf-8"), name_hint=name)


def save_job(job: Job) -> Path:
    job.validate()
    path = _job_path(job.name)
    path.write_text(dump_job_yaml(job), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def delete_job(name: str) -> bool:
    """Remove a job file. Returns True if removed, False if it didn't exist."""
    path = _job_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


def job_exists(name: str) -> bool:
    return _job_path(name).exists()
