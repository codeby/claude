"""Single source of truth for reading and persisting credentials.

Lookup order: st.secrets → env → ~/.content_parser/config.json.
Persistence: write to ~/.content_parser/config.json AND .streamlit/secrets.toml
so the same key is available locally and via st.secrets in the same project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".content_parser"
CONFIG_PATH = CONFIG_DIR / "config.json"
SECRETS_PATH = Path(".streamlit") / "secrets.toml"


def _load_local_config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
    except Exception:
        return {}


def _save_local_config(data: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def get_secret(name: str, default: str = "") -> str:
    """Read a single secret by name."""
    try:
        import streamlit as st  # noqa: PLC0415
        try:
            if name in st.secrets:
                return str(st.secrets[name])
        except Exception:
            pass
    except ImportError:
        pass
    env = os.environ.get(name)
    if env:
        return env
    return _load_local_config().get(name, default)


def save_secret(name: str, value: str) -> None:
    data = _load_local_config()
    data[name] = value
    _save_local_config(data)
    _upsert_secrets_toml(name, value)


def delete_secret(name: str) -> None:
    data = _load_local_config()
    if name in data:
        del data[name]
        _save_local_config(data)
    _remove_from_secrets_toml(name)


def _toml_escape(value: str) -> str:
    """Escape backslashes and double quotes for TOML basic strings."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _is_streamlit_cloud() -> bool:
    """Best-effort detection for Streamlit Cloud, where .streamlit/secrets.toml
    is read-only and writing it would either fail or be silently ignored."""
    if os.environ.get("STREAMLIT_RUNTIME") == "cloud":
        return True
    if os.environ.get("STREAMLIT_SHARING") in ("1", "true", "True"):
        return True
    # Streamlit Cloud containers have hostnames like 'streamlit-app-xyz'.
    hostname = os.environ.get("HOSTNAME", "")
    return hostname.startswith("streamlit-")


def _upsert_secrets_toml(key: str, value: str) -> None:
    if _is_streamlit_cloud():
        # secrets.toml is managed via Settings → Secrets in the Cloud UI;
        # filesystem writes are pointless and may raise. Local config.json
        # write in save_secret() above already persisted the value for this
        # session — Cloud users have to mirror it via the dashboard.
        return
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f'{key} = "{_toml_escape(value)}"'
    if SECRETS_PATH.exists():
        existing = SECRETS_PATH.read_text(encoding="utf-8").splitlines()
        replaced = False
        new_lines: list[str] = []
        for ln in existing:
            stripped = ln.lstrip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                new_lines.append(line)
                replaced = True
            else:
                new_lines.append(ln)
        if not replaced:
            new_lines.append(line)
        SECRETS_PATH.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    else:
        SECRETS_PATH.write_text(line + "\n", encoding="utf-8")
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def _remove_from_secrets_toml(key: str) -> None:
    if not SECRETS_PATH.exists():
        return
    existing = SECRETS_PATH.read_text(encoding="utf-8").splitlines()
    new_lines = [
        ln for ln in existing
        if not (ln.lstrip().startswith(f"{key}=") or ln.lstrip().startswith(f"{key} ="))
    ]
    if new_lines and any(ln.strip() for ln in new_lines):
        SECRETS_PATH.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    else:
        SECRETS_PATH.unlink()


def secret_locations(name: str) -> list[str]:
    """Where the secret currently lives — for UI hints."""
    locs: list[str] = []
    try:
        import streamlit as st  # noqa: PLC0415
        try:
            if name in st.secrets:
                locs.append("st.secrets")
        except Exception:
            pass
    except ImportError:
        pass
    if os.environ.get(name):
        locs.append("env")
    if name in _load_local_config():
        locs.append(str(CONFIG_PATH))
    return locs
