import re
from typing import Any, cast

import yt_dlp
import yt_dlp.utils

from smclipy.config import settings


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails with a client-side HTTP error (e.g. 403)."""

    def __init__(self, video_id: str, status_code: int) -> None:
        super().__init__(f"Video '{video_id}' failed with HTTP {status_code}")
        self.video_id = video_id
        self.status_code = status_code


def _extract_youtube_urls(text: str) -> list[str]:
    video_ids = re.findall(
        r"(?:"
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/"
        r"(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
        r"|(?:https?://)?youtu\.be/"
        r")([a-zA-Z0-9_-]{11})",
        text,
    )
    return [f"https://www.youtube.com/watch?v={video_id}" for video_id in video_ids]


def _extract_soundcloud_urls(text: str) -> list[tuple[str, str]]:
    return cast(
        list[tuple[str, str]],
        re.findall(
            r"https?://(?:\w+\.)?soundcloud\.com/"
            r"(?!search|discover|you|upload|settings|messages|notifications|people)"
            r"([\w-]+)/([\w-]+)(?:[/?#].*)?",
            text,
        ),
    )


def extract_urls(text: str) -> list[str]:
    youtube_urls = _extract_youtube_urls(text)
    soundcloud_urls = [
        f"https://soundcloud.com/{user}/{track}"
        for user, track in _extract_soundcloud_urls(text)
    ]
    return list(dict.fromkeys(youtube_urls + soundcloud_urls))


def get_author(info: dict[str, Any]) -> list[str]:
    artists: list[str] = []

    creators = info.get("artist") or info.get("creator") or info.get("track")
    if creators:
        artists.extend(creators if isinstance(creators, list) else [creators])
    else:
        for key in ("uploader", "channel"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                artists.append(value.strip())
                break

    return list(
        dict.fromkeys(a for a in artists if isinstance(a, str) and a.strip())
    )


def get_album(info: dict[str, Any]) -> str:
    album = info.get("album")
    return album.strip() if isinstance(album, str) else ""


def _extract_http_status(exc: BaseException) -> int | None:
    match = re.search(r"HTTP Error (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _yt_dlp_opts() -> dict[str, Any]:
    return {
        "format": "bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(settings().temp_folder.joinpath("temp.%(ext)s")),
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


def download(url: str) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_yt_dlp_opts()) as ydl:  # type: ignore[attr-defined]
            return ydl.extract_info(url, download=True)
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        status_code = _extract_http_status(exc)
        if status_code is not None and 400 <= status_code < 500:
            raise VideoDownloadError(url, status_code) from exc
        raise
