"""YouTube plugin — implements SourcePlugin contract."""
from __future__ import annotations

from typing import Any, Iterator

from googleapiclient.discovery import build

from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from .adapter import (
    comment_dict_to_comment,
    metadata_to_item,
    transcript_dict_to_transcript,
)
from .comments import fetch_comments
from .sources import collect_video_ids, fetch_video_metadata
from .transcripts import fetch_transcript_verbose


class YouTubePlugin(SourcePlugin):
    name = "youtube"
    label = "YouTube"
    secret_keys = ["YOUTUBE_API_KEY"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="query",
                label="Поисковые запросы",
                placeholder="python tutorial\nclaude code demo",
                help="Каждый запрос стоит 100 единиц квоты (из 10 000 в день).",
            ),
            InputSpec(
                kind="channel",
                label="Каналы",
                placeholder="https://youtube.com/@veritasium\n@3blue1brown\nUC...",
                help="URL, @handle или channel ID.",
            ),
            InputSpec(
                kind="playlist",
                label="Плейлисты",
                placeholder="https://youtube.com/playlist?list=PL...",
            ),
            InputSpec(
                kind="video",
                label="Видео",
                placeholder="https://youtu.be/dQw4w9WgXcQ",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("fetch_comments", "Парсить комментарии", "checkbox", True),
            FieldSpec("include_replies", "Включая ответы", "checkbox", False),
            FieldSpec("max_comments", "Макс. комментариев на видео (0 = все)",
                      "number", 0, min_value=0),
            FieldSpec("comment_order", "Сортировка комментариев", "select",
                      "relevance", options=["relevance", "time"]),
            FieldSpec("fetch_transcripts", "Парсить транскрипты", "checkbox", True),
            FieldSpec("transcript_langs", "Языки транскриптов (через запятую)",
                      "text", "ru,en"),
            FieldSpec("search_max", "Макс. видео на запрос", "number", 10,
                      min_value=1, max_value=500),
            FieldSpec("per_source_max",
                      "Макс. видео на канал/плейлист (0 = все)",
                      "number", 20, min_value=0),
            FieldSpec("proxy_provider", "Прокси для транскриптов", "select",
                      "Без прокси", options=["Без прокси", "Webshare", "HTTP-прокси"],
                      help="На Streamlit Cloud YouTube блокирует запросы за субтитрами."),
            FieldSpec("transcribe_videos", "🎤 Whisper fallback (если субтитров нет)", "checkbox", False,
                      help="Когда youtube-transcript-api не вернул субтитры, скачивает аудио "
                           "и транскрибирует через OpenAI Whisper. Нужен OPENAI_API_KEY и ffmpeg."),
            FieldSpec("max_audio_seconds_per_video", "Макс. секунд аудио на видео",
                      "number", 600, min_value=10, max_value=3600),
        ]

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        youtube = self._client(secrets)
        return collect_video_ids(
            youtube,
            queries=inputs.get("query", []),
            channels=inputs.get("channel", []),
            playlists=inputs.get("playlist", []),
            videos=inputs.get("video", []),
            search_max=int(settings.get("search_max", 10)),
            per_source_max=int(settings.get("per_source_max", 0)) or None,
        )

    def fetch(
        self,
        item_ids: list[str],
        settings: dict[str, Any],
        secrets: dict[str, str],
        progress: ProgressCb | None = None,
    ) -> Iterator[Item]:
        youtube = self._client(secrets)
        metadata = fetch_video_metadata(youtube, item_ids)
        languages = [s.strip() for s in str(settings.get("transcript_langs", "ru,en")).split(",") if s.strip()]
        proxy_config = self._build_proxy(settings, secrets)
        max_comments = int(settings.get("max_comments", 0)) or None

        for i, vid in enumerate(item_ids, 1):
            meta = metadata.get(vid)
            if progress:
                progress(i, len(item_ids), vid)
            if not meta:
                continue

            item = metadata_to_item(meta)

            if settings.get("fetch_comments", True):
                try:
                    raw = fetch_comments(
                        youtube, vid,
                        include_replies=bool(settings.get("include_replies")),
                        max_comments=max_comments,
                        order=str(settings.get("comment_order", "relevance")),
                    )
                    item.comments = [comment_dict_to_comment(c) for c in raw]
                except Exception as e:
                    item.extra["comments_error"] = str(e)

            if settings.get("fetch_transcripts", True):
                t = fetch_transcript_verbose(vid, languages=languages, proxy_config=proxy_config)
                item.transcript = transcript_dict_to_transcript(t)

            # Whisper fallback: only if youtube-transcript-api couldn't produce
            # segments (subtitles disabled, blocked, etc.) AND user opted in.
            from ...transcription.runner import maybe_transcribe  # noqa: PLC0415
            maybe_transcribe(item, settings, secrets, only_if_missing=True)

            yield item

    def _client(self, secrets: dict[str, str]):
        key = secrets.get("YOUTUBE_API_KEY")
        if not key:
            raise ValueError("YOUTUBE_API_KEY is required")
        return build("youtube", "v3", developerKey=key, cache_discovery=False)

    def _build_proxy(self, settings: dict[str, Any], secrets: dict[str, str]):
        provider = settings.get("proxy_provider", "Без прокси")
        if provider == "Webshare":
            user = secrets.get("WEBSHARE_USERNAME", "")
            pwd = secrets.get("WEBSHARE_PASSWORD", "")
            if user and pwd:
                from youtube_transcript_api.proxies import WebshareProxyConfig
                return WebshareProxyConfig(proxy_username=user, proxy_password=pwd)
        elif provider == "HTTP-прокси":
            http = secrets.get("PROXY_HTTP_URL", "")
            https = secrets.get("PROXY_HTTPS_URL", "") or http
            if http or https:
                from youtube_transcript_api.proxies import GenericProxyConfig
                return GenericProxyConfig(http_url=http or None, https_url=https or None)
        return None
