import pytest
import yt_dlp.utils

import smclipy.downloader as downloader
from smclipy.downloader import (
    VideoDownloadError,
    download,
    extract_video_ids,
    get_yt_channel_author,
)


class FakeYDL:
    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> dict:
        if self._error is not None:
            raise self._error
        return {"id": "abc123", "title": "Song", "download": download, "url": url}


def _patch_ytdlp(monkeypatch, fake: FakeYDL) -> None:
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda _opts: fake)


def test_extract_video_ids():
    assert extract_video_ids("https://www.youtube.com/watch?v=abcdefghijk") == [
        "abcdefghijk"
    ]


def test_extract_video_ids_youtu_be_bare():
    assert extract_video_ids("https://youtu.be/abcdefghijk") == ["abcdefghijk"]


def test_extract_video_ids_shorts():
    assert extract_video_ids("https://www.youtube.com/shorts/abcdefghijk") == [
        "abcdefghijk"
    ]


def test_extract_video_ids_embed_and_live():
    assert extract_video_ids(
        "https://youtube.com/embed/abcdefghijk https://www.youtube.com/live/ABCDEFGHIJK"
    ) == ["abcdefghijk", "ABCDEFGHIJK"]


def test_extract_video_ids_mobile():
    assert extract_video_ids("https://m.youtube.com/watch?v=abcdefghijk") == [
        "abcdefghijk"
    ]


def test_extract_video_ids_ignores_bare_v_fragment():
    assert extract_video_ids("check out v=abcdefghijk in this text") == []


def test_extract_video_ids_dedupes():
    assert extract_video_ids(
        "https://youtu.be/abcdefghijk https://www.youtube.com/watch?v=abcdefghijk"
    ) == ["abcdefghijk"]


def test_get_yt_channel_author_from_channel():
    assert get_yt_channel_author({"channel": "Femtanyl"}) == ["Femtanyl"]


def test_get_yt_channel_author_strips_whitespace():
    assert get_yt_channel_author({"channel": "  Femtanyl  "}) == ["Femtanyl"]


def test_get_yt_channel_author_missing_or_empty():
    assert get_yt_channel_author({"title": "Song"}) == []
    assert get_yt_channel_author({"channel": ""}) == []
    assert get_yt_channel_author({"channel": "   "}) == []


def test_download_returns_info(monkeypatch, app_settings):
    fake = FakeYDL(result={"id": "abc123"})
    _patch_ytdlp(monkeypatch, fake)
    info = download("abc123")
    assert info["title"] == "Song"


def test_download_raises_video_download_error_on_403(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 403: Forbidden")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(VideoDownloadError) as exc_info:
        download("abc123")
    assert exc_info.value.status_code == 403
    assert exc_info.value.video_id == "abc123"


def test_download_raises_video_download_error_on_404(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 404: Not Found")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(VideoDownloadError) as exc_info:
        download("abc123")
    assert exc_info.value.status_code == 404


def test_download_reraise_non_4xx(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 500: Server Error")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(yt_dlp.utils.DownloadError):
        download("abc123")


def test_download_reraise_extractor_error(monkeypatch, app_settings):
    error = yt_dlp.utils.ExtractorError("Something went wrong, not an HTTP error")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(yt_dlp.utils.ExtractorError):
        download("abc123")
