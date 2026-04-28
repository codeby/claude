"""Streamlit-интерфейс для YouTube-парсера. Запуск: streamlit run app.py"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from googleapiclient.discovery import build

from youtube_parser.comments import fetch_comments
from youtube_parser.output import (
    write_combined_markdown,
    write_summary_csv,
    write_video_json,
    write_video_markdown,
)
from youtube_parser.sources import collect_video_ids, fetch_video_metadata
from youtube_parser.transcripts import fetch_transcript


st.set_page_config(page_title="YouTube Парсер", page_icon="🎬", layout="wide")


CONFIG_PATH = Path.home() / ".youtube_parser_config.json"


def _load_saved_key() -> str:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return str(data.get("api_key", ""))
        except Exception:
            return ""
    return ""


def _save_key(api_key: str) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"api_key": api_key}, ensure_ascii=False), encoding="utf-8"
    )
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _delete_saved_key() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def _default_api_key() -> str:
    try:
        if "YOUTUBE_API_KEY" in st.secrets:
            return str(st.secrets["YOUTUBE_API_KEY"])
    except Exception:
        pass
    env = os.environ.get("YOUTUBE_API_KEY")
    if env:
        return env
    return _load_saved_key()


if "api_key" not in st.session_state:
    st.session_state.api_key = _default_api_key()
if "last_run" not in st.session_state:
    st.session_state.last_run = None


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _zip_directory(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in directory.rglob("*"):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(directory))
    return buf.getvalue()


# ---------- Боковая панель ----------
with st.sidebar:
    st.header("⚙️ Настройки")

    st.session_state.api_key = st.text_input(
        "API-ключ YouTube",
        value=st.session_state.api_key,
        type="password",
        help="Получить можно на console.cloud.google.com → включить YouTube Data API v3",
    )

    col_save, col_clear = st.columns(2)
    with col_save:
        if st.button("💾 Сохранить", use_container_width=True):
            if st.session_state.api_key:
                _save_key(st.session_state.api_key)
                st.success("Ключ сохранён")
            else:
                st.warning("Сначала вставьте ключ")
    with col_clear:
        if st.button("🗑️ Удалить", use_container_width=True):
            _delete_saved_key()
            st.session_state.api_key = ""
            st.success("Ключ удалён")
            st.rerun()

    if CONFIG_PATH.exists():
        st.caption(f"💾 Ключ сохранён в `{CONFIG_PATH}`")
    else:
        st.caption("Ключ не сохранён — нужно вводить каждый раз")

    st.divider()

    st.subheader("Комментарии")
    fetch_comments_flag = st.checkbox("Парсить комментарии", value=True)
    include_replies = st.checkbox("Включая ответы на комментарии", value=False)
    max_comments = st.number_input(
        "Макс. комментариев на видео (0 = все)", min_value=0, value=0, step=50
    )
    comment_order = st.selectbox(
        "Сортировка",
        ("relevance", "time"),
        index=0,
        format_func=lambda x: {"relevance": "По релевантности", "time": "По времени"}[x],
    )

    st.subheader("Транскрипты")
    fetch_transcripts_flag = st.checkbox("Парсить транскрипты (субтитры)", value=True)
    transcript_langs = st.text_input(
        "Предпочитаемые языки (через запятую)", value="ru,en"
    )

    st.subheader("Лимиты")
    search_max = st.number_input(
        "Макс. видео на один поисковый запрос", min_value=1, max_value=500, value=10
    )
    per_source_max = st.number_input(
        "Макс. видео с канала/плейлиста (0 = все)",
        min_value=0,
        value=20,
    )

# ---------- Основное окно ----------
st.title("🎬 YouTube Парсер")
st.caption(
    "Парсит комментарии и транскрипты с YouTube. "
    "Сохраняет результаты в JSON, Markdown и CSV."
)

tab_query, tab_channel, tab_playlist, tab_video = st.tabs(
    ["🔎 Поиск", "📺 Каналы", "📑 Плейлисты", "🎞️ Видео"]
)

with tab_query:
    queries_text = st.text_area(
        "Поисковые запросы — по одному на строку",
        placeholder="python tutorial\nclaude code demo",
        height=120,
    )
    st.caption(
        "⚠️ Каждый поисковый запрос стоит 100 единиц квоты "
        "(из 10 000 в день)."
    )

with tab_channel:
    channels_text = st.text_area(
        "Каналы — по одному на строку (URL, @handle или ID канала)",
        placeholder="https://youtube.com/@veritasium\n@3blue1brown\nUC...",
        height=120,
    )

with tab_playlist:
    playlists_text = st.text_area(
        "Плейлисты — по одному на строку (URL или ID)",
        placeholder="https://youtube.com/playlist?list=PL...",
        height=120,
    )

with tab_video:
    videos_text = st.text_area(
        "Видео — по одному на строку (URL или ID)",
        placeholder="https://youtu.be/dQw4w9WgXcQ\nhttps://youtube.com/watch?v=...",
        height=120,
    )

st.divider()
run_clicked = st.button("▶️ Запустить", type="primary", use_container_width=True)

# ---------- Запуск ----------
if run_clicked:
    queries = _split_lines(queries_text)
    channels = _split_lines(channels_text)
    playlists = _split_lines(playlists_text)
    videos = _split_lines(videos_text)

    if not (queries or channels or playlists or videos):
        st.error("Заполните хотя бы одну вкладку (Поиск / Каналы / Плейлисты / Видео).")
        st.stop()

    if not st.session_state.api_key:
        st.error("Нужен API-ключ. Вставьте его в боковой панели.")
        st.stop()

    out_dir = Path("output") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    youtube = build(
        "youtube", "v3", developerKey=st.session_state.api_key, cache_discovery=False
    )

    log = st.status("Подготовка…", expanded=True)

    try:
        with log:
            st.write("Получаю список видео…")
            video_ids = collect_video_ids(
                youtube,
                queries=queries,
                channels=channels,
                playlists=playlists,
                videos=videos,
                search_max=int(search_max),
                per_source_max=int(per_source_max) or None,
            )
            if not video_ids:
                st.error("Не удалось получить ни одного видео.")
                st.stop()
            st.write(f"Найдено уникальных видео: {len(video_ids)}")

            st.write("Подгружаю метаданные…")
            metadata = fetch_video_metadata(youtube, video_ids)

        languages = [s.strip() for s in transcript_langs.split(",") if s.strip()]
        results: list[dict] = []

        progress = st.progress(0.0, text="Обработка…")
        for i, vid in enumerate(video_ids, 1):
            meta = metadata.get(vid)
            if not meta:
                with log:
                    st.write(f"⚠️ {vid}: метаданные недоступны, пропускаю")
                continue

            title_short = (meta.get("title") or "")[:80]
            with log:
                st.write(f"**[{i}/{len(video_ids)}]** {title_short}")

            comments: list[dict] = []
            if fetch_comments_flag:
                try:
                    comments = fetch_comments(
                        youtube,
                        vid,
                        include_replies=include_replies,
                        max_comments=int(max_comments) or None,
                        order=comment_order,
                    )
                    with log:
                        st.write(f"   • комментариев: {len(comments)}")
                except Exception as e:
                    with log:
                        st.write(f"   • ошибка комментариев: {e}")

            transcript = None
            if fetch_transcripts_flag:
                transcript = fetch_transcript(vid, languages=languages)
                with log:
                    if transcript:
                        kind = "авто" if transcript["is_generated"] else "ручной"
                        st.write(
                            f"   • транскрипт: {transcript['language']} ({kind}, "
                            f"{len(transcript['segments'])} сегментов)"
                        )
                    else:
                        st.write("   • транскрипт недоступен")

            record = dict(meta)
            record["comments"] = comments
            record["transcript"] = transcript
            results.append(record)

            write_video_json(record, out_dir)
            write_video_markdown(record, out_dir)

            progress.progress(i / len(video_ids), text=f"{i}/{len(video_ids)}")

        write_summary_csv(results, out_dir)
        write_combined_markdown(results, out_dir)

        log.update(label=f"Готово — {len(results)} видео", state="complete")
        st.session_state.last_run = {
            "out_dir": str(out_dir),
            "results": results,
        }

    except Exception as e:
        log.update(label=f"Ошибка: {e}", state="error")
        st.exception(e)
        st.stop()

# ---------- Результаты ----------
if st.session_state.last_run:
    run = st.session_state.last_run
    out_dir = Path(run["out_dir"])
    results = run["results"]

    st.divider()
    st.subheader(f"📦 Результаты — {len(results)} видео")
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
        summary_path = out_dir / "summary.csv"
        if summary_path.exists():
            st.download_button(
                "⬇️ Скачать summary.csv",
                data=summary_path.read_bytes(),
                file_name="summary.csv",
                mime="text/csv",
                use_container_width=True,
            )

    for v in results:
        title = v.get("title") or v["video_id"]
        comments_n = len(v.get("comments") or [])
        has_t = bool((v.get("transcript") or {}).get("segments"))
        with st.expander(
            f"{title} — комментариев: {comments_n}, "
            f"транскрипт: {'есть' if has_t else 'нет'}"
        ):
            st.markdown(
                f"**Канал:** {v.get('channel_title', '—')}  \n"
                f"**Ссылка:** {v.get('url')}  \n"
                f"**Опубликовано:** {v.get('published_at', '—')}  \n"
                f"**Просмотры:** {v.get('view_count', '—')} · "
                f"**Лайки:** {v.get('like_count', '—')} · "
                f"**Комментарии (всего на видео):** {v.get('comment_count', '—')}"
            )

            if v.get("transcript") and v["transcript"].get("text"):
                with st.expander("Транскрипт"):
                    st.text(v["transcript"]["text"])

            comments = v.get("comments") or []
            if comments:
                with st.expander(f"Комментарии ({len(comments)})"):
                    for c in comments[:200]:
                        prefix = "↳ " if c.get("parent_id") else ""
                        st.markdown(
                            f"{prefix}**{c.get('author', '—')}** "
                            f"_({c.get('published_at', '—')}, ♥ {c.get('like_count', 0)})_"
                        )
                        st.write(c.get("text") or "")
                    if len(comments) > 200:
                        st.caption(
                            f"…показаны первые 200 из {len(comments)}. "
                            "Полный список — в JSON/Markdown файлах."
                        )
