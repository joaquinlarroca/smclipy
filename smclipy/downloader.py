import hashlib
import re
from typing import Any, cast

import yt_dlp as yt_dlp
import yt_dlp.utils

from smclipy.config import settings
from smclipy.helpers import sanitize_filename


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails with a client-side HTTP error (e.g. 403)."""

    def __init__(self, video_id: str, status_code: int) -> None:
        super().__init__(f"Video '{video_id}' failed with HTTP {status_code}")
        self.video_id = video_id
        self.status_code = status_code


_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:"
    r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/"
    r"(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
    r"|(?:https?://)?youtu\.be/"
    r")([a-zA-Z0-9_-]{11})(?![\w-])"
)


def _extract_youtube_urls(text: str) -> list[str]:
    video_ids = _YOUTUBE_VIDEO_ID_RE.findall(text)
    return [f"https://www.youtube.com/watch?v={video_id}" for video_id in video_ids]


_FORBIDDEN_SOUNDCLOUD_SEGMENTS = (
    "search|discover|you|upload|settings|messages|notifications|people"
    "|sets|tracks|likes|albums|playlists|followers|following|comments|stream"
)

_SOUNDCLOUD_URL_RE = re.compile(
    rf"(?:https?://)?(?:\w+\.)?soundcloud\.com/"
    rf"(?!{_FORBIDDEN_SOUNDCLOUD_SEGMENTS})([\w.-]+)/"
    rf"(?!{_FORBIDDEN_SOUNDCLOUD_SEGMENTS})([\w-]+)(?:[/?#].*)?"
)


def _extract_soundcloud_urls(text: str) -> list[tuple[str, str]]:
    return cast(list[tuple[str, str]], _SOUNDCLOUD_URL_RE.findall(text))


def extract_urls(text: str) -> list[str]:
    youtube_urls = _extract_youtube_urls(text)
    soundcloud_urls = [
        f"https://soundcloud.com/{user}/{track}"
        for user, track in _extract_soundcloud_urls(text)
    ]
    return list(dict.fromkeys(youtube_urls + soundcloud_urls))


def temp_stem(url: str) -> str:
    video_ids = _YOUTUBE_VIDEO_ID_RE.findall(url)
    if video_ids:
        return f"yt-{video_ids[0]}"
    soundcloud_urls = _extract_soundcloud_urls(url)
    if soundcloud_urls:
        user, track = soundcloud_urls[0]
        return sanitize_filename(f"sc-{user}-{track}")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"tmp-{digest}"


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
