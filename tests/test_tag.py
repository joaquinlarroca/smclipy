import argparse
import io
from typing import Any

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from PIL import Image

from smclipy import tag
from smclipy.musicbrainz import MusicBrainzMatch
from smclipy.storage import read_lines

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
    app_settings.authors_file.parent.mkdir(parents=True, exist_ok=True)
    app_settings.authors_file.write_text("Twenty One Pilots\n", encoding="utf-8")
    match = a_match(artists=["twenty one pilots"])
    assert tag._intend_artists(match, {"artists": "Twenty One Pilots"}) is None


def test_intend_artists_resolves_known_spelling_when_tag_empty(app_settings):
    app_settings.tag_fields = ["artists"]
    app_settings.authors_file.parent.mkdir(parents=True, exist_ok=True)
    app_settings.authors_file.write_text("Twenty One Pilots\n", encoding="utf-8")
    match = a_match(artists=["twenty one pilots"])
    assert tag._intend_artists(match, {"artists": ""}) == ["Twenty One Pilots"]


def test_intend_album_and_date(app_settings):
    app_settings.tag_fields = ["album", "date"]
    match = a_match(album="New", date="1990")
    assert tag._intend_album(match, {"album": "Old"}) == "New"
    assert tag._intend_album(match, {"album": "New"}) is None
    assert tag._intend_date(match, {"date": ""}) == "1990"


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


def test_process_song_writes_authors_and_songs_info(
    monkeypatch, app_settings, tmp_path
):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Old.mp3", title="Old", artist="Artist", album="Old Album"
    )

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

    assert tag.process_song(tag.LibrarySong(music / "Artist-Old.mp3")) is True

    assert read_lines(app_settings.authors_file) == ["Guest"]
    assert read_lines(app_settings.songs_info) == ["New Title - Artist, Guest"]


def test_cmd_tag_empty_library_prints_message(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tag, "scan_library", lambda: None)
    tag.cmd_tag(argparse.Namespace())
    assert "No MP3 files found" in capsys.readouterr().out


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

    monkeypatch.setattr(tag, "scan_library", lambda: None)
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
    assert "2 MP3(s) in the library" in out
    assert "[1/1] === Two - Artist ===" in out
    assert "Updated 1 song(s)" in out

    assert read_lines(app_settings.tagged_files_file) == ["Artist-Two.mp3"]

    id3 = ID3(music / "Artist-Two.mp3")
    assert str(id3["TIT2"]) == "Two New"
    id3_one = ID3(music / "Artist-One.mp3")
    assert str(id3_one["TIT2"]) == "One"


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
        {
            "title": "Song",
            "artists": "Artist",
            "album": "Album",
            "date": "2020",
            "album_artist": "Album Artist Pair",
            "track_number": "9",
        },
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
    assert not app_settings.tagged_files_file.exists() or (
        "Artist-Old.mp3"
        not in app_settings.tagged_files_file.read_text(encoding="utf-8")
    )


def test_apply_marks_file_as_tagged(app_settings, tmp_path):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-Song.mp3", title="Song", artist="Artist", album="Album"
    )

    tag._apply(
        tag.LibrarySong(music / "Artist-Song.mp3"),
        a_match(title="New Title"),
        {
            "title": "New Title",
            "artists": "Artist",
            "album": "New Album",
            "date": "1980",
            "album_artist": "",
            "track_number": "",
        },
        None,
    )

    assert read_lines(app_settings.tagged_files_file) == ["Artist-Song.mp3"]


def test_cmd_tag_skips_already_tagged(monkeypatch, app_settings, capsys):
    app_settings.tag_fields = ["title", "artists", "album", "date", "cover"]
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    app_settings.tagged_files_file.parent.mkdir(parents=True, exist_ok=True)
    app_settings.tagged_files_file.write_text("Artist-One.mp3\n", encoding="utf-8")

    monkeypatch.setattr(tag, "scan_library", lambda: None)
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
        {"title": "Song", "artists": "ARTIST", "album": "Album"},
        None,
    )

    assert read_lines(app_settings.authors_file) == []


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
        {
            "title": "New",
            "artists": "Ripper",
            "album": "New Album",
            "date": "2001",
            "album_artist": "",
            "track_number": "",
        },
        None,
        fields={"album"},
    )

    id3 = ID3(music / "Artist-Song.mp3")
    assert str(id3["TALB"]) == "New Album"
    assert str(id3["TIT2"]) == "Song"
    assert str(id3["TPE1"]) == "Artist"
    assert "TDRC" not in id3


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
