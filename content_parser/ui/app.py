"""Streamlit-интерфейс — динамически рисует формы на основе плагинов."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from ..core.registry import all_plugins, get_plugin
from ..core.runner import run
from ..core.schema import Item
from ..core.secrets import (
    delete_secret,
    get_secret,
    save_secret,
    secret_locations,
)


st.set_page_config(page_title="Парсер контента", page_icon="🎬", layout="wide")


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _zip_directory(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in directory.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(directory))
    return buf.getvalue()


def _render_field(spec, key_prefix: str):
    key = f"{key_prefix}_{spec.key}"
    if spec.widget == "text":
        return st.text_input(spec.label, value=str(spec.default or ""), help=spec.help, key=key, placeholder=spec.placeholder)
    if spec.widget == "textarea":
        return st.text_area(spec.label, value=str(spec.default or ""), help=spec.help, key=key, placeholder=spec.placeholder)
    if spec.widget == "password":
        return st.text_input(spec.label, value=str(spec.default or ""), help=spec.help, key=key, type="password")
    if spec.widget == "number":
        kwargs: dict = {"label": spec.label, "value": int(spec.default or 0), "help": spec.help, "key": key}
        if spec.min_value is not None:
            kwargs["min_value"] = int(spec.min_value)
        if spec.max_value is not None:
            kwargs["max_value"] = int(spec.max_value)
        return st.number_input(**kwargs)
    if spec.widget == "checkbox":
        return st.checkbox(spec.label, value=bool(spec.default), help=spec.help, key=key)
    if spec.widget == "select":
        opts = list(spec.options) if spec.options else ([spec.default] if spec.default is not None else [])
        if not opts:
            # No options and no default — render a free-text fallback so the form still works.
            return st.text_input(spec.label, value="", help=spec.help, key=key)
        idx = opts.index(spec.default) if spec.default in opts else 0
        return st.selectbox(spec.label, opts, index=idx, help=spec.help, key=key)
    return st.text_input(spec.label, value=str(spec.default or ""), key=key)


def _sidebar(plugin) -> tuple[dict[str, str], dict]:
    with st.sidebar:
        st.header("⚙️ Настройки")

        # Source selector
        plugins = all_plugins()
        names = [p.name for p in plugins]
        labels = {p.name: p.label for p in plugins}
        current_name = st.selectbox(
            "Источник",
            names,
            index=names.index(plugin.name),
            format_func=lambda n: labels[n],
            key="source_selector",
        )
        if current_name != plugin.name:
            st.session_state["selected_source"] = current_name
            st.rerun()

        st.divider()

        # Secrets per plugin
        st.subheader("🔑 Ключи")
        secrets: dict[str, str] = {}
        for k in plugin.secret_keys:
            session_key = f"secret_{k}"
            if session_key not in st.session_state:
                st.session_state[session_key] = get_secret(k)
            value = st.text_input(k, value=st.session_state[session_key], type="password", key=session_key)
            secrets[k] = value

        # Optional shared secrets that some plugins use
        for opt in ("WEBSHARE_USERNAME", "WEBSHARE_PASSWORD", "PROXY_HTTP_URL", "PROXY_HTTPS_URL"):
            v = get_secret(opt)
            if v:
                secrets[opt] = v

        col_save, col_clear = st.columns(2)
        with col_save:
            if st.button("💾 Сохранить", use_container_width=True, key="btn_save_secrets"):
                saved = []
                for k in plugin.secret_keys:
                    if secrets.get(k):
                        save_secret(k, secrets[k])
                        saved.append(k)
                if saved:
                    st.success(f"Сохранено: {', '.join(saved)}")
                else:
                    st.warning("Нечего сохранять")
        with col_clear:
            if st.button("🗑️ Удалить", use_container_width=True, key="btn_clear_secrets"):
                for k in plugin.secret_keys:
                    delete_secret(k)
                    st.session_state[f"secret_{k}"] = ""
                st.success("Удалено")
                st.rerun()

        for k in plugin.secret_keys:
            locs = secret_locations(k)
            if locs:
                st.caption(f"`{k}` сохранён в: {', '.join(locs)}")

        st.divider()

        # Per-plugin settings
        st.subheader("Параметры")
        settings: dict = {}
        proxy_secrets: dict[str, str] = {}
        for spec in plugin.settings_specs():
            settings[spec.key] = _render_field(spec, key_prefix=f"setting_{plugin.name}")

        # If plugin has a proxy_provider setting, expose Webshare/HTTP fields here
        if "proxy_provider" in settings and settings["proxy_provider"] != "Без прокси":
            with st.expander("Параметры прокси", expanded=True):
                if settings["proxy_provider"] == "Webshare":
                    proxy_secrets["WEBSHARE_USERNAME"] = st.text_input(
                        "Webshare username",
                        value=get_secret("WEBSHARE_USERNAME"),
                        key="ws_user",
                    )
                    proxy_secrets["WEBSHARE_PASSWORD"] = st.text_input(
                        "Webshare password",
                        value=get_secret("WEBSHARE_PASSWORD"),
                        type="password",
                        key="ws_pass",
                    )
                elif settings["proxy_provider"] == "HTTP-прокси":
                    proxy_secrets["PROXY_HTTP_URL"] = st.text_input(
                        "HTTP URL",
                        value=get_secret("PROXY_HTTP_URL"),
                        placeholder="http://user:pass@host:port",
                        key="http_url",
                    )
                    proxy_secrets["PROXY_HTTPS_URL"] = st.text_input(
                        "HTTPS URL (опц.)",
                        value=get_secret("PROXY_HTTPS_URL"),
                        key="https_url",
                    )

        secrets.update({k: v for k, v in proxy_secrets.items() if v})
        return secrets, settings


def _main_area(plugin) -> dict[str, list[str]]:
    st.title(f"🎬 Парсер контента — {plugin.label}")
    st.caption("Парсит метаданные, комментарии и (где возможно) транскрипты. Сохраняет JSON, Markdown и CSV.")

    specs = plugin.input_specs()
    tabs = st.tabs([f"📥 {s.label}" for s in specs])
    inputs: dict[str, list[str]] = {}
    for spec, tab in zip(specs, tabs):
        with tab:
            text = st.text_area(
                f"{spec.label} — по одному на строку",
                placeholder=spec.placeholder,
                help=spec.help,
                height=120,
                key=f"input_{plugin.name}_{spec.kind}",
            )
            inputs[spec.kind] = _split_lines(text)
    return inputs


def main() -> None:
    plugins = all_plugins()
    if not plugins:
        st.error("Нет доступных плагинов. Проверь установку зависимостей.")
        return

    if "selected_source" not in st.session_state:
        st.session_state.selected_source = plugins[0].name
    if "last_run" not in st.session_state:
        st.session_state.last_run = None

    plugin = get_plugin(st.session_state.selected_source)

    secrets, settings = _sidebar(plugin)
    inputs = _main_area(plugin)

    st.divider()
    if st.button("▶️ Запустить", type="primary", use_container_width=True):
        non_empty = {k: v for k, v in inputs.items() if v}
        if not non_empty:
            st.error("Заполни хотя бы одну вкладку.")
            st.stop()

        missing = plugin.validate_secrets(secrets)
        if missing:
            st.error(f"Заполни ключи: {', '.join(missing)}")
            st.stop()

        out_dir = Path("output") / plugin.name / datetime.now().strftime("%Y%m%d_%H%M%S")
        log = st.status("Запуск…", expanded=True)
        progress_bar = st.progress(0.0, text="Подготовка…")

        try:
            with log:
                st.write(f"Источник: **{plugin.label}**")
                st.write(f"Каталог: `{out_dir.resolve()}`")

            def st_log(msg: str) -> None:
                with log:
                    st.write(msg)

            def st_progress(done: int, total: int, message: str) -> None:
                progress_bar.progress(done / max(total, 1), text=f"{done}/{total} — {message[:60]}")

            result = run(plugin, non_empty, settings, secrets, output_dir=out_dir, log=st_log, progress=st_progress)
            log.update(label=f"Готово — {len(result.items)} item(s)", state="complete")
            st.session_state.last_run = {"out_dir": str(result.out_dir), "items": result.items}
        except Exception as e:
            log.update(label=f"Ошибка: {e}", state="error")
            st.exception(e)

    _render_results()


def _render_results() -> None:
    run_data = st.session_state.get("last_run")
    if not run_data:
        return
    items: list[Item] = run_data["items"]
    out_dir = Path(run_data["out_dir"])

    st.divider()
    st.subheader(f"📦 Результаты — {len(items)}")
    st.caption(f"Сохранено в `{out_dir.resolve()}`")

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇️ Скачать всё (ZIP)",
            data=_zip_directory(out_dir),
            file_name=f"{out_dir.name}.zip",
            mime="application/zip",
            use_container_width=True,
        )
    with col2:
        summary = out_dir / "summary.csv"
        if summary.exists():
            st.download_button(
                "⬇️ summary.csv",
                data=summary.read_bytes(),
                file_name="summary.csv",
                mime="text/csv",
                use_container_width=True,
            )

    for it in items:
        title = it.title or it.item_id
        n_comments = len(it.comments)
        has_t = bool(it.transcript and it.transcript.segments)
        with st.expander(f"[{it.source}] {title} — комм.: {n_comments}, транскрипт: {'да' if has_t else 'нет'}"):
            metric_pairs = " · ".join(
                f"**{k}:** {v}" for k, v in it.media.items() if v is not None
            )
            st.markdown(
                f"**Автор:** {it.author or '—'}  \n"
                f"**Ссылка:** {it.url}  \n"
                f"**Опубликовано:** {it.published_at or '—'}  \n"
                + (f"{metric_pairs}\n" if metric_pairs else "")
            )
            if it.transcript and it.transcript.text:
                with st.expander("Транскрипт"):
                    st.text(it.transcript.text)
            if it.comments:
                with st.expander(f"Комментарии ({len(it.comments)})"):
                    for c in it.comments[:200]:
                        prefix = "↳ " if c.parent_id else ""
                        st.markdown(f"{prefix}**{c.author or '—'}** _({c.published_at or '—'}, ♥ {c.like_count})_")
                        st.write(c.text or "")
                    if len(it.comments) > 200:
                        st.caption(f"…первые 200 из {len(it.comments)}")
