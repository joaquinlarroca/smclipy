import pytest
import yt_dlp.utils

import smclipy.downloader as downloader
from smclipy.downloader import (
    VideoDownloadError,
    download,
    extract_urls,
    get_album,
    get_author,
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


def test_extract_urls_youtube_watch():
    assert extract_urls("https://www.youtube.com/watch?v=abcdefghijk") == [
        "https://www.youtube.com/watch?v=abcdefghijk"
    ]


def test_extract_urls_youtube_bare_youtu_be():
    assert extract_urls("https://youtu.be/abcdefghijk") == [
        "https://www.youtube.com/watch?v=abcdefghijk"
    ]


def test_extract_urls_youtube_shorts():
    assert extract_urls("https://www.youtube.com/shorts/abcdefghijk") == [
        "https://www.youtube.com/watch?v=abcdefghijk"
    ]


def test_extract_urls_youtube_embed_and_live():
    assert extract_urls(
        "https://youtube.com/embed/abcdefghijk https://www.youtube.com/live/ABCDEFGHIJK"
    ) == [
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://www.youtube.com/watch?v=ABCDEFGHIJK",
    ]


def test_extract_urls_youtube_mobile():
    assert extract_urls("https://m.youtube.com/watch?v=abcdefghijk") == [
        "https://www.youtube.com/watch?v=abcdefghijk"
    ]


def test_extract_urls_ignores_bare_v_fragment():
    assert extract_urls("check out v=abcdefghijk in this text") == []


def test_extract_urls_dedupes_youtube():
    assert extract_urls(
        "https://youtu.be/abcdefghijk https://www.youtube.com/watch?v=abcdefghijk"
    ) == ["https://www.youtube.com/watch?v=abcdefghijk"]


def test_extract_urls_soundcloud():
    assert extract_urls("https://soundcloud.com/artist/track") == [
        "https://soundcloud.com/artist/track"
    ]


def test_extract_urls_soundcloud_with_query_and_trailing_slash():
    assert extract_urls("https://soundcloud.com/user-1/my-song?in=playlist/1") == [
        "https://soundcloud.com/user-1/my-song"
    ]


def test_extract_urls_soundcloud_subdomain():
    assert extract_urls("https://www.soundcloud.com/artist/track") == [
        "https://soundcloud.com/artist/track"
    ]


def test_extract_urls_soundcloud_without_scheme():
    assert extract_urls("soundcloud.com/artist/track text") == [
        "https://soundcloud.com/artist/track"
    ]


def test_extract_urls_soundcloud_dotted_username():
    assert extract_urls("https://soundcloud.com/artist.mc/track") == [
        "https://soundcloud.com/artist.mc/track"
    ]


def test_extract_urls_soundcloud_mixes_platforms_and_dedupes():
    assert extract_urls(
        "https://soundcloud.com/artist/track https://youtu.be/abcdefghijk "
        "https://soundcloud.com/artist/track"
    ) == [
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://soundcloud.com/artist/track",
    ]


def test_extract_urls_youtube_ignores_longer_ids():
    assert extract_urls("https://youtu.be/abcdefghijkl") == []
    assert extract_urls("https://www.youtube.com/watch?v=abcdefghijkl") == []


def test_extract_urls_soundcloud_ignores_non_track_paths():
    assert extract_urls("https://soundcloud.com/search?q=foo") == []


def test_extract_urls_soundcloud_ignores_page_segments():
    assert (
        extract_urls(
            "https://soundcloud.com/artist/tracks https://soundcloud.com/artist/sets "
            "https://soundcloud.com/artist/likes https://soundcloud.com/artist/playlists"
        )
        == []
    )


def test_extract_urls_soundcloud_still_extracts_track_named_tracks():
    assert extract_urls("https://soundcloud.com/artist/song") == [
        "https://soundcloud.com/artist/song"
    ]


def test_temp_stem_youtube():
    assert downloader.temp_stem("https://youtu.be/abcdefghijk") == "yt-abcdefghijk"


def test_temp_stem_soundcloud():
    assert (
        downloader.temp_stem("https://soundcloud.com/user-1/my-song")
        == "sc-user-1-my-song"
    )


def test_temp_stem_unknown_url_is_hashed():
    assert downloader.temp_stem("not a url") == downloader.temp_stem("not a url")
    assert downloader.temp_stem("not a url").startswith("tmp-")


def test_download_uses_stem_in_outtmpl(monkeypatch, app_settings):
    captured = {}

    def fake_ydl(opts):
        captured["opts"] = opts
        return FakeYDL()

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", fake_ydl)

    download("https://youtu.be/abcdefghijk", "yt-abcdefghijk")

    assert captured["opts"]["outtmpl"] == str(
        app_settings.temp_folder.joinpath("yt-abcdefghijk.%(ext)s")
    )


def test_get_author_prefers_artist():
    info = {
        "artist": "Real Artist",
        "uploader": "Uploader",
        "channel": "Channel",
    }
    assert get_author(info) == ["Real Artist"]


def test_get_author_falls_back_to_uploader():
    assert get_author({"uploader": "Uploader", "channel": "Channel"}) == ["Uploader"]


def test_get_author_falls_back_to_channel():
    assert get_author({"channel": "Femtanyl"}) == ["Femtanyl"]


def test_get_author_handles_creator_and_track_list():
    assert get_author({"track": ["A", "B"]}) == ["A", "B"]


def test_get_author_ignores_string_track():
    assert get_author({"track": "Some Song"}) == []


def test_get_author_string_track_defers_to_uploader():
    assert get_author({"track": "Some Song", "uploader": "UP"}) == ["UP"]


def test_get_author_strips_whitespace():
    assert get_author({"uploader": "  Uploader  ", "channel": "   "}) == ["Uploader"]


def test_get_author_missing_or_empty():
    assert get_author({"title": "Song"}) == []
    assert get_author({"channel": ""}) == []
    assert get_author({"channel": "   "}) == []


def test_get_album():
    assert get_album({"album": " Great Album "}) == "Great Album"


def test_get_album_missing_or_non_string():
    assert get_album({"title": "Song"}) == ""
    assert get_album({"album": 5}) == ""
    assert get_album({}) == ""


def test_download_returns_info(monkeypatch, app_settings):
    fake = FakeYDL(result={"id": "abc123"})
    _patch_ytdlp(monkeypatch, fake)
    info = download("https://soundcloud.com/artist/track")
    assert info["title"] == "Song"
    assert info["url"] == "https://soundcloud.com/artist/track"


def test_download_raises_video_download_error_on_403(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 403: Forbidden")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(VideoDownloadError) as exc_info:
        download("https://soundcloud.com/artist/track")
    assert exc_info.value.status_code == 403
    assert exc_info.value.video_id == "https://soundcloud.com/artist/track"


def test_download_raises_video_download_error_on_404(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 404: Not Found")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(VideoDownloadError) as exc_info:
        download("https://soundcloud.com/artist/track")
    assert exc_info.value.status_code == 404


def test_download_reraise_non_4xx(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: HTTP Error 500: Server Error")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(yt_dlp.utils.DownloadError):
        download("https://soundcloud.com/artist/track")


def test_download_reraise_extractor_error(monkeypatch, app_settings):
    error = yt_dlp.utils.ExtractorError("Something went wrong, not an HTTP error")
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(yt_dlp.utils.ExtractorError):
        download("https://soundcloud.com/artist/track")


def test_download_reraises_keyboard_interrupt_from_chain(monkeypatch, app_settings):
    error = yt_dlp.utils.DownloadError("ERROR: interrupted")
    error.__cause__ = KeyboardInterrupt()
    _patch_ytdlp(monkeypatch, FakeYDL(error=error))
    with pytest.raises(KeyboardInterrupt):
        download("https://soundcloud.com/artist/track")
