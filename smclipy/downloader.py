import hashlib
import re
from typing import Any, cast

import yt_dlp as yt_dlp
import yt_dlp.utils

from smclipy.config import settings
from smclipy.helpers import sanitize_filename

YT_STEM_PREFIX = "yt-"
SC_STEM_PREFIX = "sc-"
TMP_STEM_PREFIX = "tmp-"
TEMP_STEM_PREFIXES = (YT_STEM_PREFIX, SC_STEM_PREFIX, TMP_STEM_PREFIX)


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails with a client-side HTTP error (e.g. 403)."""

    def __init__(self, video_id: str, status_code: int) -> None:
        super().__init__(f"Video '{video_id}' failed with HTTP {status_code}")
        self.video_id = video_id
        self.status_code = status_code


def _is_host_at_boundary(text: str, start: int) -> bool:
    """Whether `text[start:]` begins at a real host position.

    A host must start at the beginning of the text, after whitespace, or right
    after a ``://`` scheme. This rejects lookalike hosts nested inside another
    URL, e.g. ``https://evil.com/soundcloud.com/artist/track``.
    """
    if start == 0:
        return True
    prefix = text[:start]
    return prefix[-1].isspace() or prefix.endswith("://")


_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)*youtube\.com/"
    r"(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
    r"([a-zA-Z0-9_-]{11})(?![\w-])"
)
_YOUTUBE_SHORT_URL_RE = re.compile(
    r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})(?![\w-])"
)


def _iter_youtube_ids(text: str) -> list[str]:
    video_ids: list[str] = []
    for url_re in (_YOUTUBE_VIDEO_ID_RE, _YOUTUBE_SHORT_URL_RE):
        for match in url_re.finditer(text):
            if _is_host_at_boundary(text, match.start()):
                video_ids.append(match.group(1))
    return video_ids


def _extract_youtube_urls(text: str) -> list[str]:
    return [
        f"https://www.youtube.com/watch?v={video_id}"
        for video_id in _iter_youtube_ids(text)
    ]


_RESERVED_USER_SEGMENTS = (
    "search|discover|you|upload|settings|messages|notifications|people"
    "|sets|tracks|likes|albums|playlists|followers|following|comments|stream"
)
_RESERVED_TRACK_SEGMENTS = (
    "search|discover|upload|settings|messages|notifications"
    "|sets|tracks|likes|albums|playlists|followers|following|comments|stream"
)

_SOUNDCLOUD_URL_RE = re.compile(
    rf"(?:https?://)?(?:(?:www|m|mobile)\.)?soundcloud\.com/"
    rf"(?!{_RESERVED_USER_SEGMENTS})([\w.-]+)/"
    rf"(?!{_RESERVED_TRACK_SEGMENTS})([\w-]+)(?:[/?#].*)?"
)


def _extract_soundcloud_urls(text: str) -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(2))
        for match in _SOUNDCLOUD_URL_RE.finditer(text)
        if _is_host_at_boundary(text, match.start())
    ]


def extract_urls(text: str) -> list[str]:
    youtube_urls = _extract_youtube_urls(text)
    soundcloud_urls = [
        f"https://soundcloud.com/{user}/{track}"
        for user, track in _extract_soundcloud_urls(text)
    ]
    return list(dict.fromkeys(youtube_urls + soundcloud_urls))


def temp_stem(url: str) -> str:
    video_ids = _iter_youtube_ids(url)
    if video_ids:
        return f"{YT_STEM_PREFIX}{video_ids[0]}"
    soundcloud_urls = _extract_soundcloud_urls(url)
    if soundcloud_urls:
        user, track = soundcloud_urls[0]
        return sanitize_filename(f"{SC_STEM_PREFIX}{user}-{track}")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{TMP_STEM_PREFIX}{digest}"


def get_author(info: dict[str, Any]) -> list[str]:
    artists: list[str] = []

    creators = info.get("artist") or info.get("creator")
    if creators:
        artists.extend(creators if isinstance(creators, list) else [creators])
    elif isinstance(info.get("track"), list):
        artists.extend(info["track"])
    else:
        for key in ("uploader", "channel"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                artists.append(value.strip())
                break

    return list(dict.fromkeys(a for a in artists if isinstance(a, str) and a.strip()))


def get_album(info: dict[str, Any]) -> str:
    album = info.get("album")
    return album.strip() if isinstance(album, str) else ""


def _extract_http_status(exc: BaseException) -> int | None:
    match = re.search(r"HTTP Error (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _contains_keyboard_interrupt(exc: BaseException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, KeyboardInterrupt):
            return True
        cause = cause.__cause__
    return False


def _yt_dlp_opts(stem: str) -> dict[str, Any]:
    return {
        "format": "bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(settings().temp_folder.joinpath(f"{stem}.%(ext)s")),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
    }


def download(url: str, stem: str = "temp") -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_yt_dlp_opts(stem)) as ydl:
            return cast(dict[str, Any], ydl.extract_info(url, download=True))
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        if _contains_keyboard_interrupt(exc):
            raise KeyboardInterrupt from exc
        status_code = _extract_http_status(exc)
        if status_code is not None and 400 <= status_code < 500:
            raise VideoDownloadError(url, status_code) from exc
        raise
