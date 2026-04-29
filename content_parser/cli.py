"""Unified CLI: python -m content_parser.cli {run,list-sources}."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.registry import all_plugins, get_plugin
from .core.runner import run
from .core.secrets import get_secret


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="content_parser")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list-sources", help="Show registered source plugins")

    # ----- jobs subcommand -----
    jobs_p = sub.add_parser("jobs", help="Manage scheduled jobs")
    jobs_sub = jobs_p.add_subparsers(dest="jobs_command", required=True)
    jobs_sub.add_parser("list", help="List all saved jobs")
    show_p = jobs_sub.add_parser("show", help="Print a job's YAML")
    show_p.add_argument("name")
    run_job_p = jobs_sub.add_parser("run", help="Run a job once")
    run_job_p.add_argument("name")
    jobs_sub.add_parser("install-cron", help="Regenerate the managed crontab block")
    jobs_sub.add_parser("remove-cron", help="Remove the managed crontab block")
    jobs_sub.add_parser("cron-status", help="Show what's currently in the managed block")

    run_p = sub.add_parser("run", help="Resolve inputs and fetch items for one source")
    run_p.add_argument("--source", required=True, help="Plugin name (e.g. youtube, instagram)")
    run_p.add_argument("--output", "-o", default=None, help="Output directory")

    # Generic input flags — repeatable. Plugin decides which kinds it understands.
    run_p.add_argument(
        "--input", "-i", action="append", default=[],
        metavar="KIND=VALUE",
        help='Input as "kind=value" (e.g. --input video=https://youtu.be/x). Repeatable.',
    )
    # Convenience aliases
    run_p.add_argument("--query", "-q", action="append", default=[])
    run_p.add_argument("--channel", "-c", action="append", default=[])
    run_p.add_argument("--playlist", "-p", action="append", default=[])
    run_p.add_argument("--video", "-v", action="append", default=[])
    run_p.add_argument("--hashtag", action="append", default=[])
    run_p.add_argument("--account", action="append", default=[])
    run_p.add_argument("--post", action="append", default=[])

    # Plugin settings as key=value, repeatable
    run_p.add_argument(
        "--set", action="append", default=[],
        metavar="KEY=VALUE",
        help='Override a plugin setting (e.g. --set max_comments=100). Repeatable.',
    )
    return p


def _parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in items:
        if "=" not in s:
            raise SystemExit(f"Expected KEY=VALUE, got {s!r}")
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def cmd_list_sources() -> int:
    for p in all_plugins():
        kinds = ", ".join(s.kind for s in p.input_specs())
        print(f"{p.name:12s}  {p.label:20s}  inputs=[{kinds}]  secrets={p.secret_keys}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plugin = get_plugin(args.source)

    inputs: dict[str, list[str]] = {s.kind: [] for s in plugin.input_specs()}

    # Aliases → inputs
    for alias_attr, kind in [
        ("query", "query"), ("channel", "channel"), ("playlist", "playlist"),
        ("video", "video"), ("hashtag", "hashtag"), ("account", "account"),
        ("post", "post"),
    ]:
        for v in getattr(args, alias_attr, []):
            inputs.setdefault(kind, []).append(v)

    # Generic --input KIND=VALUE
    for raw in args.input:
        if "=" not in raw:
            raise SystemExit(f"--input expects KIND=VALUE, got {raw!r}")
        kind, value = raw.split("=", 1)
        inputs.setdefault(kind.strip(), []).append(value.strip())

    # Drop empty kinds
    inputs = {k: v for k, v in inputs.items() if v}

    if not inputs:
        accepted = ", ".join(s.kind for s in plugin.input_specs())
        raise SystemExit(f"No inputs given. Plugin {args.source!r} accepts: {accepted}")

    # Settings
    settings: dict = {s.key: s.default for s in plugin.settings_specs()}
    for k, v in _parse_kv(args.set).items():
        settings[k] = _coerce(v)

    # Secrets
    secrets: dict[str, str] = {k: get_secret(k) for k in plugin.secret_keys}
    # also pull any well-known optional secrets the plugin might use
    for opt in ("WEBSHARE_USERNAME", "WEBSHARE_PASSWORD", "PROXY_HTTP_URL", "PROXY_HTTPS_URL"):
        v = get_secret(opt)
        if v:
            secrets[opt] = v

    out_dir = Path(args.output) if args.output else None

    def log(msg: str) -> None:
        print(msg)

    def progress(done: int, total: int, message: str) -> None:
        print(f"  [{done}/{total}] {message}")

    result = run(plugin, inputs, settings, secrets, output_dir=out_dir, log=log, progress=progress)
    print(f"\nDone. {len(result.items)} item(s) saved to {result.out_dir.resolve()}")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from .jobs import store as jobs_store  # noqa: PLC0415
    from .jobs.runner import run_job  # noqa: PLC0415
    from .jobs.schema import dump_job_yaml  # noqa: PLC0415

    if args.jobs_command == "list":
        jobs = jobs_store.list_jobs()
        if not jobs:
            print("No jobs found in", jobs_store.JOBS_DIR)
            return 0
        for job in jobs:
            schedule = job.schedule or "(manual)"
            inputs_summary = ", ".join(f"{k}={len(v)}" for k, v in job.inputs.items()) or "—"
            sheet_count = len(job.sheet_inputs)
            print(
                f"{job.name:30s}  source={job.source:10s}  schedule={schedule:20s}  "
                f"inline=[{inputs_summary}]  sheet_refs={sheet_count}"
            )
        invalid = jobs_store.list_invalid()
        if invalid:
            print()
            print("Invalid job files:")
            for name, err in invalid:
                print(f"  {name}: {err}")
        return 0

    if args.jobs_command == "show":
        job = jobs_store.load_job(args.name)
        print(dump_job_yaml(job))
        return 0

    if args.jobs_command == "run":
        result = run_job(args.name, log=print, progress=lambda d, t, m: print(f"  [{d}/{t}] {m}"))
        print(f"\nDone. {len(result.items)} item(s) saved to {result.out_dir.resolve()}")
        return 0

    if args.jobs_command == "install-cron":
        from .jobs.cron import install_cron  # noqa: PLC0415
        entries = install_cron()
        if not entries:
            print("No scheduled jobs found. Managed block cleared.")
            return 0
        print(f"Installed {len(entries)} entrie(s) in crontab:")
        for e in entries:
            print(f"  {e.schedule}  {e.job_name}")
        return 0

    if args.jobs_command == "remove-cron":
        from .jobs.cron import remove_cron  # noqa: PLC0415
        removed = remove_cron()
        print("Removed managed block." if removed else "Managed block not present.")
        return 0

    if args.jobs_command == "cron-status":
        from .jobs.cron import read_block  # noqa: PLC0415
        entries = read_block()
        if not entries:
            print("Managed block is empty or absent.")
            return 0
        for e in entries:
            print(f"{e.schedule}  job:{e.job_name}\n  → {e.command}")
        return 0

    return 2


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list-sources":
        return cmd_list_sources()
    if args.command == "run":
        return cmd_run(args)
    if args.command == "jobs":
        return cmd_jobs(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
