"""Streamlit UI for the YouTube parser. Run with: streamlit run app.py"""
from __future__ import annotations

import io
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


st.set_page_config(page_title="YouTube Parser", page_icon="🎬", layout="wide")


def _default_api_key() -> str:
    try:
        if "YOUTUBE_API_KEY" in st.secrets:
            return str(st.secrets["YOUTUBE_API_KEY"])
    except Exception:
        pass
    return os.environ.get("YOUTUBE_API_KEY", "")


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


# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    st.session_state.api_key = st.text_input(
        "YouTube Data API key",
        value=st.session_state.api_key,
        type="password",
        help="Get one at console.cloud.google.com → Enable YouTube Data API v3",
    )

    st.subheader("Comments")
    fetch_comments_flag = st.checkbox("Fetch comments", value=True)
    include_replies = st.checkbox("Include replies", value=False)
    max_comments = st.number_input(
        "Max comments per video (0 = all)", min_value=0, value=0, step=50
    )
    comment_order = st.selectbox("Order", ("relevance", "time"), index=0)

    st.subheader("Transcripts")
    fetch_transcripts_flag = st.checkbox("Fetch transcripts", value=True)
    transcript_langs = st.text_input(
        "Preferred languages (comma-separated)", value="ru,en"
    )

    st.subheader("Limits")
    search_max = st.number_input(
        "Max videos per search query", min_value=1, max_value=500, value=10
    )
    per_source_max = st.number_input(
        "Max videos per channel/playlist (0 = all)",
        min_value=0,
        value=20,
    )

# ---------- Main ----------
st.title("🎬 YouTube Parser")
st.caption("Parse comments and transcripts from YouTube videos. Results: JSON + Markdown + CSV.")

tab_query, tab_channel, tab_playlist, tab_video = st.tabs(
    ["🔎 Search", "📺 Channels", "📑 Playlists", "🎞️ Videos"]
)

with tab_query:
    queries_text = st.text_area(
        "Search queries — one per line",
        placeholder="python tutorial\nclaude code demo",
        height=120,
    )
    st.caption("⚠️ Each search query costs 100 quota units (out of 10 000/day).")

with tab_channel:
    channels_text = st.text_area(
        "Channels — one per line (URL, @handle, or channel ID)",
        placeholder="https://youtube.com/@veritasium\n@3blue1brown\nUC...",
        height=120,
    )

with tab_playlist:
    playlists_text = st.text_area(
        "Playlists — one per line (URL or ID)",
        placeholder="https://youtube.com/playlist?list=PL...",
        height=120,
    )

with tab_video:
    videos_text = st.text_area(
        "Videos — one per line (URL or ID)",
        placeholder="https://youtu.be/dQw4w9WgXcQ\nhttps://youtube.com/watch?v=...",
        height=120,
    )

st.divider()
run_clicked = st.button("▶️ Run", type="primary", use_container_width=True)

# ---------- Run ----------
if run_clicked:
    queries = _split_lines(queries_text)
    channels = _split_lines(channels_text)
    playlists = _split_lines(playlists_text)
    videos = _split_lines(videos_text)

    if not (queries or channels or playlists or videos):
        st.error("Add at least one input (search / channel / playlist / video).")
        st.stop()

    if not st.session_state.api_key:
        st.error("API key required. Paste it in the sidebar.")
        st.stop()

    out_dir = Path("output") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    youtube = build(
        "youtube", "v3", developerKey=st.session_state.api_key, cache_discovery=False
    )

    log = st.status("Resolving inputs…", expanded=True)

    try:
        with log:
            st.write("Resolving video IDs…")
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
                st.error("No videos resolved.")
                st.stop()
            st.write(f"Found {len(video_ids)} unique video(s).")

            st.write("Fetching metadata…")
            metadata = fetch_video_metadata(youtube, video_ids)

        languages = [s.strip() for s in transcript_langs.split(",") if s.strip()]
        results: list[dict] = []

        progress = st.progress(0.0, text="Processing…")
        for i, vid in enumerate(video_ids, 1):
            meta = metadata.get(vid)
            if not meta:
                with log:
                    st.write(f"⚠️ {vid}: metadata unavailable, skipped")
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
                        st.write(f"   • comments: {len(comments)}")
                except Exception as e:
                    with log:
                        st.write(f"   • comments error: {e}")

            transcript = None
            if fetch_transcripts_flag:
                transcript = fetch_transcript(vid, languages=languages)
                with log:
                    if transcript:
                        kind = "auto" if transcript["is_generated"] else "manual"
                        st.write(
                            f"   • transcript: {transcript['language']} ({kind}, "
                            f"{len(transcript['segments'])} segments)"
                        )
                    else:
                        st.write("   • transcript: not available")

            record = dict(meta)
            record["comments"] = comments
            record["transcript"] = transcript
            results.append(record)

            write_video_json(record, out_dir)
            write_video_markdown(record, out_dir)

            progress.progress(i / len(video_ids), text=f"{i}/{len(video_ids)}")

        write_summary_csv(results, out_dir)
        write_combined_markdown(results, out_dir)

        log.update(label=f"Done — {len(results)} video(s)", state="complete")
        st.session_state.last_run = {
            "out_dir": str(out_dir),
            "results": results,
        }

    except Exception as e:
        log.update(label=f"Failed: {e}", state="error")
        st.exception(e)
        st.stop()

# ---------- Results ----------
if st.session_state.last_run:
    run = st.session_state.last_run
    out_dir = Path(run["out_dir"])
    results = run["results"]

    st.divider()
    st.subheader(f"📦 Results — {len(results)} video(s)")
    st.caption(f"Saved to `{out_dir.resolve()}`")

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇️ Download all (ZIP)",
            data=_zip_directory(out_dir),
            file_name=f"{out_dir.name}.zip",
            mime="application/zip",
            use_container_width=True,
        )
    with col2:
        summary_path = out_dir / "summary.csv"
        if summary_path.exists():
            st.download_button(
                "⬇️ Download summary.csv",
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
            f"{title} — {comments_n} comment(s), transcript: {'yes' if has_t else 'no'}"
        ):
            st.markdown(
                f"**Channel:** {v.get('channel_title', '—')}  \n"
                f"**URL:** {v.get('url')}  \n"
                f"**Published:** {v.get('published_at', '—')}  \n"
                f"**Views:** {v.get('view_count', '—')} · "
                f"**Likes:** {v.get('like_count', '—')} · "
                f"**Comments (channel):** {v.get('comment_count', '—')}"
            )

            if v.get("transcript") and v["transcript"].get("text"):
                with st.expander("Transcript"):
                    st.text(v["transcript"]["text"])

            comments = v.get("comments") or []
            if comments:
                with st.expander(f"Comments ({len(comments)})"):
                    for c in comments[:200]:
                        prefix = "↳ " if c.get("parent_id") else ""
                        st.markdown(
                            f"{prefix}**{c.get('author', '—')}** "
                            f"_({c.get('published_at', '—')}, ♥ {c.get('like_count', 0)})_"
                        )
                        st.write(c.get("text") or "")
                    if len(comments) > 200:
                        st.caption(
                            f"…showing first 200 of {len(comments)}. "
                            "Full list in JSON/Markdown files."
                        )
