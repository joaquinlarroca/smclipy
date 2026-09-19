import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import ID3 as MutagenID3
from mutagen.id3 import UFID
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

import smclipy.config as config
import smclipy.db as db
from smclipy import cli
from smclipy.downloader import _yt_dlp_opts
from smclipy.formats import SUPPORTED_FORMATS, Track, audio_ext
from smclipy.metadata import (
    SaveResult,
    read_song_uuid,
    save_song_temp_to_main,
    scan_library,
)

_FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg is required")

_CODECS = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "flac": "flac",
    "opus": "libopus",
    "ogg": "libvorbis",
}
SONG_UUID = "12345678-1234-5678-1234-567812345678"


def ffmpeg() -> str:
    assert _FFMPEG is not None
    return _FFMPEG


def make_audio(path: Path, audio_format: str) -> Path:
    subprocess.run(
        [
            ffmpeg(),
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            _CODECS[audio_format],
            str(path),
        ],
        check=True,
    )
    return path


def make_cover(path: Path, image_format: str = "JPEG") -> Path:
    Image.new("RGB", (16, 16), (200, 10, 10)).save(path, image_format)
    return path


def test_settings_default_audio_format_is_mp3(app_settings):
    assert app_settings.audio_format == "mp3"


def test_settings_rejects_unknown_audio_format(tmp_path, capsys):
    s = config.Settings({"path_to_music_folder": str(tmp_path), "audio_format": "wav"})
    assert s.audio_format == "mp3"
    assert "audio_format" in capsys.readouterr().err


@pytest.mark.parametrize("audio_format", SUPPORTED_FORMATS)
def test_settings_accepts_each_audio_format(tmp_path, audio_format):
    s = config.Settings(
        {"path_to_music_folder": str(tmp_path), "audio_format": audio_format}
    )
    assert s.audio_format == audio_format


@pytest.mark.parametrize(
    ("audio_format", "codec"),
    [
        ("mp3", "mp3"),
        ("m4a", "m4a"),
        ("flac", "flac"),
        ("opus", "opus"),
        ("ogg", "vorbis"),
    ],
)
def test_yt_dlp_opts_uses_configured_codec(app_settings, audio_format, codec):
    app_settings.audio_format = audio_format
    extract_audio = _yt_dlp_opts("stem")["postprocessors"][0]
    assert extract_audio["preferredcodec"] == codec
    assert ("preferredquality" in extract_audio) is (audio_format == "mp3")


@requires_ffmpeg
@pytest.mark.parametrize("audio_format", SUPPORTED_FORMATS)
def test_track_round_trip(tmp_path, audio_format):
    audio_file = make_audio(tmp_path / f"song{audio_ext(audio_format)}", audio_format)
    track = Track.open(audio_file)
    assert track is not None
    assert track.audio_format == audio_format
    assert track.read_uuid() is None
    profile, has_cover = track.read_profile()
    assert profile["title"] == "Unknown"
    assert profile["artists"] == ""
    assert has_cover is False

    fields = {
        "title": "My Title",
        "album": "My Album",
        "date": "2020-05-01",
        "genre": "Rock",
        "album_artist": "Album Artist",
        "track_number": "3",
    }
    for key, value in fields.items():
        track.set_field(key, value)
    track.set_artists(["First Artist", "Second Artist"])
    track.write_uuid(SONG_UUID)
    track.set_cover(make_cover(tmp_path / "cover.jpg"))
    track.save()

    reopened = Track.open(audio_file)
    assert reopened is not None
    for key, value in fields.items():
        assert reopened.read_text(key) == value
    assert reopened.read_artists() == ["First Artist", "Second Artist"]
    assert reopened.read_uuid() == SONG_UUID
    assert reopened.has_cover()
    mime, data = reopened.read_cover()
    assert mime == "image/jpeg"
    assert data[:2] == b"\xff\xd8"


@requires_ffmpeg
@pytest.mark.parametrize("audio_format", SUPPORTED_FORMATS)
def test_scan_library_tracks_each_format(app_settings, audio_format):
    extension = audio_ext(audio_format)
    app_settings.audio_format = audio_format
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    audio_file = make_audio(
        app_settings.music_folder / f"Library{extension}", audio_format
    )
    track = Track.open(audio_file)
    assert track is not None
    track.set_field("title", "Library Song")
    track.set_artists(["Library Artist"])
    track.save()

    scan_library()

    rows = db.get_song_by_path(f"Library{extension}")
    assert len(rows) == 1
    assert rows[0]["title"] == "Library Song"
    assert read_song_uuid(audio_file) == rows[0]["uuid"]


@requires_ffmpeg
def test_save_song_temp_to_main_uses_configured_extension(app_settings, tmp_path):
    app_settings.audio_format = "flac"
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    app_settings.temp_folder.mkdir(parents=True, exist_ok=True)
    temp_track = make_audio(app_settings.temp_folder / "temp.flac", "flac")

    result, target = save_song_temp_to_main(
        temp_track, make_cover(tmp_path / "cover.jpg"), "Title", "Artist", "Album"
    )

    assert result is SaveResult.SAVED
    assert target is not None
    assert target.name == "Artist-Title.flac"
    assert target.is_file()
    assert read_song_uuid(target) is not None
    saved = Track.open(target)
    assert saved is not None
    assert saved.read_text("title") == "Title"
    assert saved.read_text("album") == "Album"
    assert saved.has_cover()


@requires_ffmpeg
def test_crop_lookups_match_non_mp3(app_settings):
    app_settings.audio_format = "flac"
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    audio_file = make_audio(app_settings.music_folder / "Cropped.flac", "flac")
    track = Track.open(audio_file)
    assert track is not None
    track.set_field("title", "Cropped")
    track.set_artists(["Artist"])
    track.save()
    scan_library()
    song_uuid = read_song_uuid(audio_file)
    assert song_uuid is not None

    assert cli._crop_target_tracks(app_settings, "Cropped") == [audio_file]

    cli._record_cover_status("Cropped", db.COVER_STATUS_CROPPED)
    assert db.get_song_by_uuid(song_uuid)["cover_status"] == db.COVER_STATUS_CROPPED


@requires_ffmpeg
def test_vorbis_corrupt_uuid_reads_as_none(tmp_path):
    audio_file = make_audio(tmp_path / "song-with-bad-uuid.ogg", "ogg")
    track = Track.open(audio_file)
    assert track is not None
    track.tags["SMCLIPY_UUID"] = ["not-a-valid-uuid"]
    track.save()

    assert Track.open(audio_file).read_uuid() is None


@requires_ffmpeg
def test_mp3_write_uuid_preserves_foreign_ufid_frames(tmp_path):
    audio_file = make_audio(tmp_path / "song.mp3", "mp3")
    track = Track.open(audio_file)
    assert track is not None
    track.write_uuid(SONG_UUID)
    track.save()

    audio = MP3(audio_file, ID3=MutagenID3)
    audio.tags.add(UFID(owner="some-other-app", data=b"\x00" * 16))
    audio.save()
    track = Track.open(audio_file)
    assert track is not None
    track.write_uuid("87654321-4321-8765-4321-876543210987")
    track.save()

    reopened = Track.open(audio_file)
    assert reopened is not None
    assert reopened.read_uuid() == "87654321-4321-8765-4321-876543210987"
    assert any(
        getattr(frame, "owner", None) == "some-other-app"
        for frame in reopened.tags.getall("UFID")
    )


@requires_ffmpeg
def test_m4a_cover_with_wrong_declared_type_is_sniffed(tmp_path):
    audio_file = make_audio(tmp_path / "song.m4a", "m4a")
    track = Track.open(audio_file)
    assert track is not None
    cover = Path(tmp_path / "cover.png")
    make_cover(cover, "PNG")
    png_bytes = cover.read_bytes()
    # PNG bytes falsely flagged as JPEG, as some encoders in the wild do.
    audio = MP4(audio_file)
    audio.tags["covr"] = [MP4Cover(png_bytes, imageformat=13)]
    audio.save()

    reopened = Track.open(audio_file)
    assert reopened is not None
    mime, data = reopened.read_cover()
    assert mime == "image/png"
    assert data == png_bytes


@requires_ffmpeg
def test_m4a_set_cover_transcodes_webp_to_png(tmp_path):
    audio_file = make_audio(tmp_path / "song.m4a", "m4a")
    track = Track.open(audio_file)
    assert track is not None
    cover = tmp_path / "cover.webp"
    Image.new("RGB", (16, 16), (200, 10, 10)).save(cover, "WEBP")
    track.set_cover(cover)
    track.save()

    reopened = Track.open(audio_file)
    assert reopened is not None
    mime, data = reopened.read_cover()
    assert mime == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
