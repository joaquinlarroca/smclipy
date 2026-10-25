import argparse
import io
import json
from typing import Any
from unittest.mock import Mock

import pytest
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, TALB, TIT2, TPE1
from PIL import Image

import smclipy.db as db
from smclipy import search, tag
from smclipy.metadata import has_cover, read_song_uuid, scan_library
from smclipy.musicbrainz import CoverArtUnavailable, MusicBrainzMatch

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Foo", artist="Artist", album="Album") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["album"] = [album]
    audio.save(path)


def a_match(**overrides) -> MusicBrainzMatch:
    values: dict[str, Any] = {
        "title": "New Title",
        "artists": ["Artist"],
        "album": "New Album",
        "date": "1980",
        "album_artist": "Artist",
        "track_number": "5",
        "recording_id": "rec-1",
        "release_group_id": "rg-1",
    }
    values.update(overrides)
    return MusicBrainzMatch(**values)


def test_list_library_songs_only_mp3s(app_settings, tmp_path):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3", title="Song", artist="Artist")
    (music / "notes.txt").write_text("not music", encoding="utf-8")
    (music / "Other.mp3").write_bytes(b"invalid-mp3")

    songs = tag.list_library_songs()

    names = [song.path.name for song in songs]
    assert "Artist-Song.mp3" in names
    assert "Other.mp3" in names
    assert all(song.path.suffix == ".mp3" for song in songs)


def test_library_song_display_name_falls_back_to_stem(app_settings, tmp_path):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Naked.mp3").write_bytes(b"invalid-mp3")

    song = tag.LibrarySong(music / "Naked.mp3")

    assert song.display_name == "Naked"


def test_enabled_fields_only_valid_and_configured(app_settings):
    app_settings.tag_fields = ["title", "cover", "bogus"]
    assert tag._enabled_fields() == {"cover", "title"}


def test_search_query_falls_back_to_file_stem_for_untagged(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "My Song.mp3"
    file.write_bytes(MINIMAL_MP3)

    song = tag.LibrarySong(file)

    assert song.current["title"] == ""
    assert tag._search_query(song) == ("My Song", [], None)


def test_intend_title_only_when_changed_and_enabled(app_settings):
    app_settings.tag_fields = ["title"]
    match = a_match(title="New Title")
    assert tag._intend_title(match, {"title": "Old"}) == "New Title"
    assert tag._intend_title(match, {"title": "New Title"}) is None


def test_intend_artists_matches_existing(app_settings):
    app_settings.tag_fields = ["artists"]
    match = a_match(artists=["Artist A", "Artist B"])
    assert tag._intend_artists(match, {"artists": "Artist A, Artist B"}) is None
    assert tag._intend_artists(match, {"artists": "Artist A"}) == [
        "Artist A",
        "Artist B",
    ]


def test_intend_artists_keeps_existing_spelling(app_settings):
    app_settings.tag_fields = ["artists"]
    db.add_authors(["Twenty One Pilots"])
    match = a_match(artists=["twenty one pilots"])
    assert tag._intend_artists(match, {"artists": "Twenty One Pilots"}) is None


def test_intend_artists_resolves_known_spelling_when_tag_empty(app_settings):
    app_settings.tag_fields = ["artists"]
    db.add_authors(["Twenty One Pilots"])
    match = a_match(artists=["twenty one pilots"])
    assert tag._intend_artists(match, {"artists": ""}) == ["Twenty One Pilots"]


def test_intend_album_and_date(app_settings):
    app_settings.tag_fields = ["album", "date"]
    match = a_match(album="New", date="1990")
    assert tag._intend_album(match, {"album": "Old"}) == "New"
    assert tag._intend_album(match, {"album": "New"}) is None
    assert tag._intend_date(match, {"date": ""}) == "1990"


def test_intend_genre_only_when_changed_and_enabled(app_settings):
    app_settings.tag_fields = ["genre"]
    match = a_match(genre="progressive rock; rock")
    assert tag._intend_genre(match, {"genre": ""}) == "progressive rock; rock"
    assert tag._intend_genre(match, {"genre": "progressive rock; rock"}) is None
    assert tag._intend_genre(a_match(), {"genre": ""}) is None
    app_settings.tag_fields = ["title"]
    assert tag._intend_genre(match, {"genre": ""}) is None


def test_intend_album_skips_when_equal_to_title(app_settings):
    app_settings.tag_fields = ["album"]
    match = a_match(title="Choker", album="Choker")
    assert tag._intend_album(match, {"album": ""}) is None
    assert tag._intend_album(match, {"album": "Old Album"}) is None
    case_only = a_match(title="Choker", album="choker")
    assert tag._intend_album(case_only, {"album": ""}) is None


def test_intend_album_writes_when_equal_to_title_if_enabled(app_settings):
    app_settings.tag_fields = ["album"]
    app_settings.write_album_if_same_as_title = True
    match = a_match(title="Choker", album="Choker")
    assert tag._intend_album(match, {"album": ""}) == "Choker"
    assert tag._intend_album(match, {"album": "Choker"}) is None


def test_process_song_no_matches_returns_false(monkeypatch, app_settings):
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [])
    song = tag.LibrarySong(app_settings.music_folder / "missing.mp3")
    assert tag.process_song(song) is False


def test_process_song_skip_returns_false(monkeypatch, app_settings):
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [a_match()])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: None)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    song = tag.LibrarySong(app_settings.music_folder / "missing.mp3")
    assert tag.process_song(song) is False


def test_process_song_records_not_found_status(monkeypatch, app_settings):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        app_settings.music_folder / "Artist-Song.mp3", title="Song", artist="Artist"
    )
    scan_library()
    song = tag.LibrarySong(app_settings.music_folder / "Artist-Song.mp3")
    assert song.uuid is not None

    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [])
    assert tag.process_song(song) is False

    row = db.get_song_by_uuid(song.uuid)
    assert row is not None
    assert row["musicbrainz_status"] == "not_found"


def test_process_song_records_skipped_status(monkeypatch, app_settings):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        app_settings.music_folder / "Artist-Song.mp3", title="Song", artist="Artist"
    )
    scan_library()
    song = tag.LibrarySong(app_settings.music_folder / "Artist-Song.mp3")
    assert song.uuid is not None

    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [a_match()])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: None)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    assert tag.process_song(song) is False

    row = db.get_song_by_uuid(song.uuid)
    assert row is not None
    assert row["musicbrainz_status"] == "skipped"


def test_process_song_no_enabled_fields_does_not_mark_tagged(monkeypatch, app_settings):
    app_settings.tag_fields = []
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )
    scan_library()
    song = tag.LibrarySong(music / "Artist-Song.mp3")
    assert song.uuid is not None

    def fail_search(*_args, **_kwargs):
        raise AssertionError("should not search with no enabled fields")

    monkeypatch.setattr(tag, "search_recordings", fail_search)

    assert tag.process_song(song) is False
    row = db.get_song_by_uuid(song.uuid)
    assert row["tagged"] == 0
    assert row["musicbrainz_status"] is None


def test_process_song_perfect_match_marks_tagged(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "artists", "album"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )
    scan_library()
    song = tag.LibrarySong(music / "Artist-Song.mp3")
    assert song.uuid is not None
    assert db.get_song_by_uuid(song.uuid)["tagged"] == 0

    match = a_match(title="Song", artists=["Artist"], album="Album")
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)

    assert tag.process_song(song) is False

    row = db.get_song_by_uuid(song.uuid)
    assert row["musicbrainz_status"] == "tagged"
    assert row["tagged"] == 1
    assert row["musicbrainz_recording_id"] == "rec-1"


def test_process_song_applies_changes(monkeypatch, app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Old Album"
    )

    match = a_match(
        title="New Title",
        artists=["Artist"],
        album="New Album",
        date="1984",
    )
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(
        tag,
        "fetch_cover_art",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(
        tag,
        "prompt_tag_changes",
        lambda *a, **k: ["title", "artists", "album", "date", "cover"],
    )

    song = tag.LibrarySong(music / "Artist-Old.mp3")
    assert song.has_cover is False
    assert tag.process_song(song) is True

    id3 = ID3(music / "Artist-Old.mp3")
    assert str(id3["TIT2"]) == "New Title"
    assert str(id3["TALB"]) == "New Album"
    assert str(id3["TDRC"]) == "1984"


def test_process_song_applies_genre_and_reports_it(monkeypatch, app_settings):
    app_settings.tag_fields = ["genre"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Album"
    )
    scan_library()

    called = []
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [a_match()])
    monkeypatch.setattr(
        tag,
        "fetch_genre",
        lambda rg, rec: called.append((rg, rec)) or "progressive rock; rock",
    )
    report: list[dict[str, Any]] = []
    song = tag.LibrarySong(music / "Artist-Old.mp3")

    assert tag.process_song(song, mode="auto", report=report) is True

    assert called == [("rg-1", "rec-1")]
    id3 = ID3(music / "Artist-Old.mp3")
    assert str(id3["TCON"]) == "progressive rock; rock"
    assert song.uuid is not None
    assert db.get_song_by_uuid(song.uuid)["genre"] == "progressive rock; rock"
    assert report[0]["outcome"] == "applied"
    assert report[0]["fields"] == ["genre"]


def test_process_song_skips_when_genre_fetch_fails(monkeypatch, app_settings):
    app_settings.tag_fields = ["genre"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Album"
    )
    scan_library()

    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [a_match()])
    monkeypatch.setattr(tag, "fetch_genre", lambda rg, rec: None)
    report: list[dict[str, Any]] = []
    song = tag.LibrarySong(music / "Artist-Old.mp3")

    assert tag.process_song(song, mode="auto", report=report) is False

    assert report[0]["outcome"] == "unavailable"
    assert ID3(music / "Artist-Old.mp3").get("TCON") is None
    assert song.uuid is not None
    row = db.get_song_by_uuid(song.uuid)
    # The match itself is recorded (the recording ids are useful), but the
    # song is left untagged so the next run retries the missing genre.
    assert row["musicbrainz_status"] == db.MB_STATUS_TAGGED
    assert not row["tagged"]


def test_process_song_persists_authors_and_mb_status(
    monkeypatch, app_settings, tmp_path
):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Old Album"
    )
    scan_library()

    match = a_match(title="New Title", artists=["Artist", "Guest"], date="1984")
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(
        tag,
        "prompt_tag_changes",
        lambda *a, **k: ["title", "artists", "album", "date", "cover"],
    )

    song = tag.LibrarySong(music / "Artist-Old.mp3")
    assert song.uuid is not None
    assert tag.process_song(song) is True

    assert db.get_authors() == ["Artist", "Guest"]

    row = db.get_song_by_uuid(song.uuid)
    assert row is not None
    assert row["musicbrainz_status"] == "tagged"
    assert row["musicbrainz_recording_id"] == "rec-1"
    assert row["musicbrainz_release_group_id"] == "rg-1"
    assert row["tagged"] == 1
    assert any(e["event"] == "tagged" for e in db.get_events(song.uuid))


def test_cmd_tag_empty_library_prints_message(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tag, "scan_library", lambda: None)
    tag.cmd_tag(argparse.Namespace())
    assert "No audio files found" in capsys.readouterr().out


def test_cmd_tag_selection_and_updates(monkeypatch, app_settings, tmp_path, capsys):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Album"
    )

    monkeypatch.setattr(tag, "prompt_song_selection", lambda count: [1])
    monkeypatch.setattr(
        tag,
        "search_recordings",
        lambda *a, **k: [a_match(title="Two New", date="1995")],
    )
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(
        tag,
        "prompt_tag_changes",
        lambda *a, **k: ["title", "artists", "album", "date", "cover"],
    )

    tag.cmd_tag(argparse.Namespace())

    out = capsys.readouterr().out
    assert "2 audio file(s) in the library" in out
    assert "[1/1] === Two - Artist ===" in out
    assert "Updated 1 song(s)" in out

    row = db.get_song_by_path("Artist-Two.mp3")[0]
    assert row["title"] == "Two New"
    assert row["musicbrainz_status"] == "tagged"
    assert row["tagged"] == 1

    id3 = ID3(music / "Artist-Two.mp3")
    assert str(id3["TIT2"]) == "Two New"
    id3_one = ID3(music / "Artist-One.mp3")
    assert str(id3_one["TIT2"]) == "One"


def test_process_song_auto_applies_top_match_without_prompts(
    monkeypatch, app_settings, tmp_path
):
    app_settings.tag_fields = ["title", "artists", "album", "date"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Old Album"
    )

    match = a_match(
        title="New Title",
        artists=["Artist"],
        album="New Album",
        date="1984",
    )
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(
        tag,
        "prompt_match_selection",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    monkeypatch.setattr(
        tag, "prompt_tag_changes", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(
        tag, "display_image", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    song = tag.LibrarySong(music / "Artist-Old.mp3")
    assert tag.process_song(song, mode="auto") is True

    id3 = ID3(music / "Artist-Old.mp3")
    assert str(id3["TIT2"]) == "New Title"
    assert str(id3["TALB"]) == "New Album"
    assert str(id3["TDRC"]) == "1984"
    assert str(id3["TPE1"]) == "Artist"


def test_process_song_semi_picks_top_match_and_confirms(
    monkeypatch, app_settings, tmp_path
):
    app_settings.tag_fields = ["title", "artists", "album", "date"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Old Album"
    )

    match = a_match(title="New Title", date="1984")
    confirmed: list[dict] = []

    def fake_prompt_tag_changes(changes, **kwargs):
        confirmed.append(changes)
        return ["title", "date"]

    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(
        tag,
        "prompt_match_selection",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    monkeypatch.setattr(tag, "prompt_tag_changes", fake_prompt_tag_changes)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    song = tag.LibrarySong(music / "Artist-Old.mp3")
    assert tag.process_song(song, mode="semi") is True

    assert confirmed, "semi mode must still confirm changes"
    fields = {field for field, _, _ in confirmed[0]}
    assert "title" in fields
    assert "album" in fields

    id3 = ID3(music / "Artist-Old.mp3")
    assert str(id3["TIT2"]) == "New Title"
    assert str(id3["TDRC"]) == "1984"
    assert str(id3["TALB"]) == "Old Album"


def test_cmd_tag_auto_applies_selected_range_without_prompts(
    monkeypatch, app_settings, capsys
):
    app_settings.tag_fields = ["title", "artists", "album", "date"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Album"
    )

    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(tag, "prompt_song_selection", lambda count: [1])
    monkeypatch.setattr(
        tag,
        "search_recordings",
        lambda *a, **k: [a_match(title="Two New", date="1995")],
    )
    monkeypatch.setattr(
        tag,
        "prompt_match_selection",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    monkeypatch.setattr(
        tag, "prompt_tag_changes", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(
        tag, "display_image", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    tag.cmd_tag(argparse.Namespace(auto=True))

    out = capsys.readouterr().out
    assert "Auto mode: the top MusicBrainz match will be applied" in out
    assert "[1/1] Auto-tagging 'Two - Artist'..." in out
    assert "Updated 1 song(s)" in out

    id3 = ID3(music / "Artist-Two.mp3")
    assert str(id3["TIT2"]) == "Two New"
    assert str(id3["TALB"]) == "New Album"


def test_apply_tag_update_writes_extra_frames(app_settings, tmp_path):
    app_settings.tag_fields = [
        "title",
        "artists",
        "album",
        "date",
        "album_artist",
        "track_number",
    ]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )

    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(album_artist="Album Artist Pair", track_number="9", date="2020"),
        None,
    )

    id3 = ID3(music / "Artist-Song.mp3")
    assert str(id3["TDRC"]) == "2020"
    assert str(id3["TPE2"]) == "Album Artist Pair"
    assert str(id3["TRCK"]) == "9"


def _make_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return buf.getvalue()


def test_process_song_cleans_fetched_cover(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Album"
    )

    cover = app_settings.temp_folder / "tag-cover-rec-1.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_make_png_bytes())

    match = a_match(title="New Title")
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: cover)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(tag, "prompt_tag_changes", lambda *a, **k: ["title", "cover"])

    assert tag.process_song(tag.LibrarySong(music / "Artist-Old.mp3")) is True
    assert not cover.exists()


def test_process_song_cleans_cover_when_skipped(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Album"
    )

    cover = app_settings.temp_folder / "tag-cover-rec-1.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_make_png_bytes())

    match = a_match(title="New Title")
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: cover)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(tag, "prompt_tag_changes", lambda *a, **k: None)

    assert tag.process_song(tag.LibrarySong(music / "Artist-Old.mp3")) is False
    assert not cover.exists()


def test_process_song_empty_selection_is_skipped(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Album"
    )

    cover = app_settings.temp_folder / "tag-cover-rec-1.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(_make_png_bytes())

    match = a_match(title="New Title")
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: cover)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(tag, "prompt_tag_changes", lambda *a, **k: [])

    assert tag.process_song(tag.LibrarySong(music / "Artist-Old.mp3")) is False
    assert not cover.exists()

    id3 = ID3(music / "Artist-Old.mp3")
    assert str(id3["TIT2"]) == "Old"
    assert str(id3["TALB"]) == "Album"
    assert db.get_song_by_path("Artist-Old.mp3") == []


def test_apply_marks_file_as_tagged(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )
    scan_library()

    song_uuid = read_song_uuid(music / "Artist-Song.mp3")
    assert song_uuid is not None
    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(title="New Title"),
        None,
    )

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["tagged"] == 1
    assert row["musicbrainz_status"] == "tagged"
    assert any(e["event"] == "tagged" for e in db.get_events(song_uuid))


def test_cmd_tag_skips_already_tagged(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    scan_library()
    song_uuid = read_song_uuid(music / "Artist-One.mp3")
    assert song_uuid is not None
    db.record_mb_status(song_uuid, db.MB_STATUS_TAGGED, tagged=True)

    monkeypatch.setattr(tag, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(
        tag, "process_song", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )

    tag.cmd_tag(argparse.Namespace())

    out = capsys.readouterr().out
    assert "already tagged before" in out
    assert "Skipped 1 already-tagged song(s)" in out
    assert "Updated 0 song(s)" in out


def test_apply_dedupes_case_insensitive_authors(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )

    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(title="Song", artists=["ARTIST"]),
        None,
    )

    assert db.get_authors() == []


def test_apply_partial_fields_only_updates_selected(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )

    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(title="New", artists=["Ripper"], album="New Album", date="2001"),
        None,
        fields={"album"},
    )

    id3 = ID3(music / "Artist-Song.mp3")
    assert str(id3["TALB"]) == "New Album"
    assert str(id3["TIT2"]) == "Song"
    assert str(id3["TPE1"]) == "Artist"
    assert "TDRC" not in id3


def test_apply_partial_fields_keeps_db_in_sync(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )
    scan_library()
    song_uuid = read_song_uuid(music / "Artist-Song.mp3")
    assert song_uuid is not None

    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(title="New", artists=["Ripper"], album="New Album", date="2001"),
        None,
        fields={"album"},
    )

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["album"] == "New Album"
    assert row["title"] == "Song"
    assert row["artists"] == "Artist"
    assert row["date"] in (None, "")


def test_apply_unselected_artists_preserve_file_and_db(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Song.mp3"
    path.write_bytes(MINIMAL_MP3)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Song"))
    tags.add(TPE1(encoding=3, text=["Ace\\Hyde"]))
    tags.add(TALB(encoding=3, text="Album"))
    tags.save(path)
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None
    # The database stores the joined (escaped) form so split_authors can
    # recover the raw artist name later.
    assert db.get_song_by_uuid(song_uuid)["artists"] == "Ace\\\\Hyde"

    tag._apply(
        tag.LibrarySong(path),
        a_match(title="New", artists=["Ripper"], date="2001"),
        None,
        fields={"title"},
    )

    id3 = ID3(path)
    assert id3["TPE1"].text == ["Ace\\Hyde"]
    row = db.get_song_by_uuid(song_uuid)
    assert row["title"] == "New"
    assert row["artists"] == "Ace\\\\Hyde"


def test_process_song_keeps_existing_artist_spelling(
    monkeypatch, app_settings, tmp_path
):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "T-Song.mp3", title="Song", artist="Twenty One Pilots", album=""
    )

    match = a_match(
        title="Choker",
        artists=["twenty one pilots"],
        album="Scaled and Icy",
        date="2021",
    )
    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [match])
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(
        tag,
        "prompt_tag_changes",
        lambda *a, **k: ["title", "artists", "album", "date"],
    )

    assert tag.process_song(tag.LibrarySong(music / "T-Song.mp3")) is True

    id3 = ID3(music / "T-Song.mp3")
    assert str(id3["TPE1"]) == "Twenty One Pilots"
    assert str(id3["TALB"]) == "Scaled and Icy"


def test_cmd_tag_cleans_leaked_cover_files(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )

    leaked = app_settings.temp_folder / "tag-cover-rec-2.jpg"
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_bytes(b"stale")

    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(tag, "prompt_song_selection", lambda count: [])

    tag.cmd_tag(argparse.Namespace())

    assert not leaked.exists()


def test_cmd_tag_warns_on_empty_tag_fields(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = []
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )

    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(tag, "prompt_song_selection", lambda count: [])

    tag.cmd_tag(argparse.Namespace())

    assert "no tag fields are enabled" in capsys.readouterr().out


def test_process_song_unreachable_records_no_mb_outcome(monkeypatch, app_settings):
    app_settings.tag_fields = ["title"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3", title="Song", artist="Artist")
    scan_library()
    song_uuid = read_song_uuid(music / "Artist-Song.mp3")
    assert song_uuid is not None

    monkeypatch.setattr(
        tag,
        "search_recordings",
        Mock(side_effect=tag.MusicBrainzUnavailable("down")),
    )

    report: list[dict[str, Any]] = []
    result = tag.process_song(tag.LibrarySong(music / "Artist-Song.mp3"), report=report)

    assert result is False
    row = db.get_song_by_uuid(song_uuid)
    assert row["musicbrainz_status"] is None
    assert report == [
        {
            "outcome": "unavailable",
            "display_name": "Song - Artist",
            "title": "Song",
            "artists": "Artist",
            "recording_id": None,
            "release_group_id": None,
        }
    ]


def test_cmd_tag_json_report(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title", "artists", "album"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )

    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(
        tag,
        "search_recordings",
        lambda *a, **k: [a_match(title="Renamed", artists=["Artist"], album="Album")],
    )
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True))

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["updated"] == 1
    assert payload["skipped"] == 0
    assert len(payload["songs"]) == 1
    assert payload["songs"][0]["outcome"] == "applied"
    assert payload["songs"][0]["title"] == "One"
    assert "title" in payload["songs"][0]["fields"]
    assert payload["songs"][0]["recording_id"] == "rec-1"
    assert payload["songs"][0]["release_group_id"] == "rg-1"
    assert "Auto-tagging" in captured.err

    id3 = ID3(music / "Artist-One.mp3")
    assert str(id3["TIT2"]) == "Renamed"


def test_cmd_tag_json_empty_library_report(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tag, "scan_library", lambda: None)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True))

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"updated": 0, "skipped": 0, "songs": []}
    assert "No audio files found" in captured.err


def test_process_song_semi_report_excludes_unchecked_cover(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "artists", "album", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    scan_library()

    cover_path = app_settings.temp_folder / "tag-cover-rec-1.jpg"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(b"image-bytes")

    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="New Title")]
    )
    monkeypatch.setattr(
        tag, "fetch_cover_art", lambda release_group_id, dest: cover_path
    )
    monkeypatch.setattr(tag, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(tag, "prompt_tag_changes", lambda *a, **k: ["title"])

    report: list[dict[str, Any]] = []
    result = tag.process_song(
        tag.LibrarySong(music / "Artist-One.mp3"), mode="semi", report=report
    )

    assert result is True
    assert len(report) == 1
    entry = report[0]
    assert entry["outcome"] == "applied"
    assert entry["fields"] == ["title"]
    assert "cover" not in entry["fields"]
    assert entry["recording_id"] == "rec-1"
    assert entry["release_group_id"] == "rg-1"
    assert not has_cover(music / "Artist-One.mp3")


def test_cmd_tag_json_reports_crashed_song(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        app_settings.music_folder / "Artist-One.mp3", title="One", artist="Artist"
    )
    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(tag, "process_song", Mock(side_effect=RuntimeError("boom")))

    # The JSON report is still emitted, but a crashed song is a failed run.
    with pytest.raises(SystemExit) as excinfo:
        tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True))

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["updated"] == 0
    assert payload["skipped"] == 0
    assert len(payload["songs"]) == 1
    assert payload["songs"][0]["outcome"] == "error"
    assert payload["songs"][0]["error"] == "RuntimeError('boom')"
    assert payload["songs"][0]["recording_id"] is None
    assert payload["songs"][0]["release_group_id"] is None


def test_parse_max_age_units():
    assert tag.parse_max_age("30s") == 30
    assert tag.parse_max_age("30m") == 1800
    assert tag.parse_max_age("24h") == 86400
    assert tag.parse_max_age("7d") == 604800
    assert tag.parse_max_age("1w") == 604800
    assert tag.parse_max_age("1.5h") == 5400


def test_parse_max_age_rejects_bad_values():
    for bad in ("", "24", "abc", "0h", "-5d", "10x"):
        with pytest.raises(ValueError):
            tag.parse_max_age(bad)


def test_cmd_tag_skip_not_found(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title", "artists", "album"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Missing.mp3", title="Missing", artist="Artist", album="Album"
    )
    write_tagged_mp3(
        music / "Artist-Found.mp3", title="Found", artist="Artist", album="Album"
    )
    scan_library()
    missing_uuid = read_song_uuid(music / "Artist-Missing.mp3")
    assert missing_uuid is not None
    db.record_mb_status(missing_uuid, db.MB_STATUS_NOT_FOUND)

    monkeypatch.setattr(
        tag,
        "search_recordings",
        lambda *a, **k: [a_match(title="Renamed", artists=["Artist"], album="Album")],
    )
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True, skip_not_found=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == 1
    assert payload["skipped"] == 1
    by_title = {entry["title"]: entry for entry in payload["songs"]}
    assert by_title["Missing"]["outcome"] == "skipped_not_found"
    assert by_title["Found"]["outcome"] == "applied"


def test_cmd_tag_without_skip_not_found_retries(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title", "artists", "album"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Missing.mp3", title="Missing", artist="Artist", album="Album"
    )
    scan_library()
    missing_uuid = read_song_uuid(music / "Artist-Missing.mp3")
    assert missing_uuid is not None
    db.record_mb_status(missing_uuid, db.MB_STATUS_NOT_FOUND)

    monkeypatch.setattr(tag, "search_recordings", lambda *a, **k: [])

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == 0
    assert payload["skipped"] == 0
    assert payload["songs"][0]["outcome"] == "not_found"


def test_cmd_tag_max_age_only_processes_recent(monkeypatch, app_settings, capsys):
    import datetime

    app_settings.tag_fields = ["title", "artists", "album"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    old_path = music / "Artist-Old.mp3"
    new_path = music / "Artist-New.mp3"
    write_tagged_mp3(old_path, title="Old", artist="Artist", album="Album")
    write_tagged_mp3(new_path, title="New", artist="Artist", album="Album")
    scan_library()
    old_seen = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=3)
    ).isoformat(timespec="seconds")
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE songs SET first_seen_at = ? WHERE current_path = ?",
            (old_seen, "Artist-Old.mp3"),
        )
        conn.commit()
    finally:
        conn.close()

    processed: list[str] = []

    def fake_process_song(song, **kwargs):
        processed.append(song.path.name)
        return True

    monkeypatch.setattr(tag, "process_song", fake_process_song)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, max_age="24h"))

    assert processed == ["Artist-New.mp3"]
    assert "Filtered to 1 of 2" in capsys.readouterr().out


def test_cmd_tag_max_age_invalid_exits(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        app_settings.music_folder / "Artist-One.mp3", title="One", artist="Artist"
    )
    monkeypatch.setattr(tag, "scan_library", lambda: None)

    with pytest.raises(SystemExit):
        tag.cmd_tag(argparse.Namespace(auto=True, all=True, max_age="bogus"))


def test_cmd_tag_search_filters_songs(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="A", album="First")
    write_tagged_mp3(music / "Artist-Two.mp3", title="Two", artist="B", album="Second")
    seen: dict[str, int] = {}

    def fake_selection(count):
        seen["count"] = count
        return [0]

    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(tag, "prompt_song_selection", fake_selection)
    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="Two New")]
    )
    monkeypatch.setattr(tag, "prompt_match_selection", lambda *a, **k: 0)
    monkeypatch.setattr(tag, "prompt_tag_changes", lambda *a, **k: ["title"])
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    tag.cmd_tag(argparse.Namespace(search="second"))

    out = capsys.readouterr().out
    assert "Showing 1 of 2 song(s) matching 'second'." in out
    assert "Matching songs:" in out
    assert seen["count"] == 1
    assert str(ID3(music / "Artist-Two.mp3")["TIT2"]) == "Two New"
    assert str(ID3(music / "Artist-One.mp3")["TIT2"]) == "One"


def test_cmd_tag_specific_autocompletes_and_filters(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="A", album="First")
    write_tagged_mp3(music / "Artist-Two.mp3", title="Two", artist="B", album="Second")
    prompts: list[tuple[str, list[str]]] = []

    def fake_prompt(label, values, default=""):
        prompts.append((label, values))
        return "second"

    monkeypatch.setattr(search, "prompt_search_value", fake_prompt)
    monkeypatch.setattr(tag, "scan_library", lambda: None)
    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="Two New")]
    )
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, specific="album"))

    out = capsys.readouterr().out
    assert prompts == [("Album", ["First", "Second"])]
    assert "Showing 1 of 2 song(s) with album 'second'." in out
    assert "Updated 1 song(s)" in out
    assert str(ID3(music / "Artist-Two.mp3")["TIT2"]) == "Two New"
    assert str(ID3(music / "Artist-One.mp3")["TIT2"]) == "One"


def test_cmd_tag_search_no_matches_emits_empty_json(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="A", album="First")
    monkeypatch.setattr(tag, "scan_library", lambda: None)

    tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True, search="zzz"))

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"updated": 0, "skipped": 0, "songs": []}
    assert "No songs match 'zzz'." in captured.err


def test_cmd_tag_blank_search_reports_error(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="A", album="First")
    monkeypatch.setattr(tag, "scan_library", lambda: None)

    # A blank filter is a usage error, so the command exits non-zero instead
    # of reporting an empty (successful-looking) run.
    with pytest.raises(SystemExit) as excinfo:
        tag.cmd_tag(argparse.Namespace(auto=True, all=True, json=True, search="   "))

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "--search needs non-empty text" in captured.err


def test_process_song_keeps_song_queued_when_cover_fetch_fails(
    monkeypatch, app_settings
):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-New-Title.mp3", title="New Title", artist="Artist")
    scan_library()

    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="New Title")]
    )
    monkeypatch.setattr(
        tag,
        "fetch_cover_art",
        Mock(side_effect=CoverArtUnavailable("network down")),
    )
    report: list[dict[str, Any]] = []
    song = tag.LibrarySong(music / "Artist-New-Title.mp3")

    assert tag.process_song(song, mode="auto", report=report) is False

    # The rest of the match still applies, but the song must stay queued so
    # the missing cover is retried instead of being lost forever.
    row = db.get_song_by_uuid(song.uuid)
    assert row["tagged"] == 0
    assert report[0]["outcome"] == "unavailable"
    assert "cover" not in report[0]["fields"]


def test_process_song_applies_fields_but_retries_cover_on_next_run(
    monkeypatch, app_settings
):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Old.mp3", title="Old", artist="Artist")
    scan_library()

    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="New Title")]
    )
    monkeypatch.setattr(
        tag, "fetch_cover_art", Mock(side_effect=CoverArtUnavailable("rate limited"))
    )
    report: list[dict[str, Any]] = []
    song = tag.LibrarySong(music / "Artist-Old.mp3")

    assert tag.process_song(song, mode="auto", report=report) is True

    assert str(ID3(music / "Artist-Old.mp3")["TIT2"]) == "New Title"
    assert not has_cover(music / "Artist-Old.mp3")
    row = db.get_song_by_uuid(song.uuid)
    assert row["title"] == "New Title"
    assert row["tagged"] == 0
    assert report[0]["outcome"] == "applied"
    assert report[0]["fields"] == ["title"]


def test_process_song_marks_tagged_when_release_has_no_cover(monkeypatch, app_settings):
    app_settings.tag_fields = ["title", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Old.mp3", title="Old", artist="Artist")
    scan_library()

    monkeypatch.setattr(
        tag, "search_recordings", lambda *a, **k: [a_match(title="New Title")]
    )
    # None means "this release genuinely has no cover" (a 404), which is
    # final: the song must not be retried forever.
    monkeypatch.setattr(tag, "fetch_cover_art", lambda *a, **k: None)
    song = tag.LibrarySong(music / "Artist-Old.mp3")

    assert tag.process_song(song, mode="auto") is True

    row = db.get_song_by_uuid(song.uuid)
    assert row["tagged"] == 1
    assert row["musicbrainz_status"] == "tagged"


def test_apply_round_trips_artist_names_with_commas(app_settings):
    app_settings.tag_fields = ["artists"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3", title="Song", artist="Artist")
    scan_library()

    song = tag.LibrarySong(music / "Artist-Song.mp3")
    assert tag._apply(song, a_match(artists=["Earth, Wind & Fire"]), None)

    # One artist whose name contains a comma must survive the tag write and the
    # database round-trip instead of turning into two artists.
    assert ID3(music / "Artist-Song.mp3")["TPE1"].text == ["Earth, Wind & Fire"]
    row = db.get_song_by_path("Artist-Song.mp3")
    assert row is not None
    assert row[0]["artists"] == "Earth\\, Wind & Fire"
