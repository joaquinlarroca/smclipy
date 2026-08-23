import re
from typing import Any

import yt_dlp
import yt_dlp.utils

from smclipy.config import settings


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails with a client-side HTTP error (e.g. 403)."""

    def __init__(self, video_id: str, status_code: int) -> None:
        super().__init__(f"Video '{video_id}' failed with HTTP {status_code}")
        self.video_id = video_id
        self.status_code = status_code


def extract_video_ids(text: str) -> list[str]:
    video_ids = re.findall(
        r"(?:"
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/"
        r"(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
        r"|(?:https?://)?youtu\.be/"
        r")([a-zA-Z0-9_-]{11})",
        text,
    )
    return list(dict.fromkeys(video_ids))


def get_yt_channel_author(info: dict[str, Any]) -> list[str]:
    channel = info.get("channel")
    if isinstance(channel, str) and channel.strip():
        return [channel.strip()]
    return []


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


def download(video_id: str) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_yt_dlp_opts()) as ydl:  # type: ignore[attr-defined]
            return ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=True
            )
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        status_code = _extract_http_status(exc)
        if status_code is not None and 400 <= status_code < 500:
            raise VideoDownloadError(video_id, status_code) from exc
        raise
