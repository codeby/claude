"""Manage a managed block in the user's crontab.

We never touch lines outside our markers. `install` regenerates the block
from current jobs (anything inside the markers gets replaced); `remove`
deletes the block entirely; `read` returns what's currently inside.

All shell-bound paths and arguments go through `shlex.quote` so a
malicious job name (which the schema regex already rejects) couldn't
inject extra commands even if it slipped through.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .schema import Job
from .store import list_jobs


BEGIN_MARKER = "# >>> content_parser jobs >>>"
END_MARKER = "# <<< content_parser jobs <<<"


@dataclass
class CronEntry:
    """One cron line as managed by us."""
    schedule: str
    job_name: str
    command: str    # full command string, post-quoting


class CronError(Exception):
    pass


# ----------------------------------------------------------------------
# Block edit


def _existing_crontab() -> str:
    """Return the user's current crontab, or '' if none / no crontab program."""
    try:
        proc = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise CronError(
            "`crontab` command not found. cron-installer requires a Unix system "
            "with cron installed (or use `cli jobs run` directly)."
        ) from e
    # crontab -l exits 1 with stderr 'no crontab for ...' when empty — treat as ''.
    if proc.returncode != 0:
        if "no crontab" in (proc.stderr or "").lower():
            return ""
        raise CronError(f"crontab -l failed: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def _write_crontab(text: str) -> None:
    """Replace the user's crontab with `text`."""
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=text,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise CronError("`crontab` command not found.") from e
    if proc.returncode != 0:
        raise CronError(f"crontab - failed: {proc.stderr.strip() or proc.returncode}")


def _strip_block(crontab_text: str) -> str:
    """Return crontab with our managed block removed (idempotent)."""
    lines = crontab_text.splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == END_MARKER:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return "\n".join(out).rstrip() + ("\n" if out else "")


def _build_block(entries: list[CronEntry]) -> list[str]:
    """Produce the lines (including markers) that go into the crontab."""
    if not entries:
        return []
    lines = [BEGIN_MARKER]
    for e in entries:
        lines.append(f"{e.schedule} {e.command}  # job:{e.job_name}")
    lines.append(END_MARKER)
    return lines


# ----------------------------------------------------------------------
# Commands


def build_command_for_job(
    job: Job,
    *,
    project_root: Path | None = None,
    python_executable: str | None = None,
    log_path: Path | None = None,
) -> str:
    """Compose the shell command that cron should execute for this job.

    project_root: where to `cd` before running. Defaults to cwd.
    python_executable: path to the python interpreter. Defaults to sys.executable.
    log_path: file to append stdout+stderr to. Defaults to
              <project_root>/output/scheduled/.cron.log.
    """
    project_root = (project_root or Path.cwd()).resolve()
    python_executable = python_executable or sys.executable
    log_path = log_path or (project_root / "output" / "scheduled" / ".cron.log")

    cd_part = f"cd {shlex.quote(str(project_root))}"
    run_part = " ".join([
        shlex.quote(python_executable),
        "-m", "content_parser.cli",
        "jobs", "run",
        shlex.quote(job.name),
    ])
    redirect = f">> {shlex.quote(str(log_path))} 2>&1"
    return f"{cd_part} && {run_part} {redirect}"


def install_cron(
    *,
    jobs: list[Job] | None = None,
    project_root: Path | None = None,
    python_executable: str | None = None,
    log_path: Path | None = None,
) -> list[CronEntry]:
    """Regenerate our managed block in the user's crontab.

    Anything outside the markers is preserved. Jobs without a schedule are
    skipped. Returns the entries that ended up in the block.
    """
    job_list = jobs if jobs is not None else list_jobs()
    entries: list[CronEntry] = []
    for job in job_list:
        if not job.schedule:
            continue
        cmd = build_command_for_job(
            job,
            project_root=project_root,
            python_executable=python_executable,
            log_path=log_path,
        )
        entries.append(CronEntry(schedule=job.schedule, job_name=job.name, command=cmd))

    existing = _existing_crontab()
    stripped = _strip_block(existing)
    block = _build_block(entries)
    if not block:
        # Nothing to install — still flush the (now empty) block from crontab.
        new_crontab = stripped
    else:
        new_crontab = (stripped + "\n".join(block) + "\n") if stripped else ("\n".join(block) + "\n")
    _write_crontab(new_crontab)
    return entries


def remove_cron() -> bool:
    """Delete our managed block from crontab. Returns True if anything was removed."""
    existing = _existing_crontab()
    if BEGIN_MARKER not in existing:
        return False
    _write_crontab(_strip_block(existing))
    return True


def read_block() -> list[CronEntry]:
    """Parse current managed block back into entries (best-effort)."""
    existing = _existing_crontab()
    out: list[CronEntry] = []
    in_block = False
    for line in existing.splitlines():
        stripped_line = line.strip()
        if stripped_line == BEGIN_MARKER:
            in_block = True
            continue
        if stripped_line == END_MARKER:
            break
        if not in_block or not stripped_line or stripped_line.startswith("#"):
            continue
        # Format: "<5 schedule tokens> <command>  # job:<name>"
        parts = stripped_line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        rest = parts[5]
        # Try to extract job name from the trailing " # job:<name>" comment.
        job_name = ""
        if "# job:" in rest:
            cmd_part, _, comment = rest.partition("# job:")
            cmd_part = cmd_part.rstrip()
            job_name = comment.strip()
        else:
            cmd_part = rest
        out.append(CronEntry(schedule=schedule, job_name=job_name, command=cmd_part))
    return out
