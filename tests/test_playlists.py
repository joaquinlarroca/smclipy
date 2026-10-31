import argparse
import json
import os

import pytest

import smclipy.db as db
import smclipy.playlists as pl
from smclipy.helpers import join_authors


def _song_row(path="Artist-Song.mp3", title="Song", artists="Artist"):
    song_uuid = db.insert_song(current_path=path, title=title, artists=artists)
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    return row


def test_serialize_m3u_relative_paths(app_settings, tmp_path):
    row = _song_row("Artist-Song.mp3", "Song", "Artist One")
    text = pl.serialize_m3u([row], tmp_path, app_settings.music_folder)
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#EXTINF:-1,Artist One - Song"
    assert lines[2] == "Music/Artist-Song.mp3"
    assert text.endswith("\n")


def test_serialize_m3u_absolute_flag(app_settings, tmp_path):
    row = _song_row("Artist-Song.mp3", "Song", "Artist")
    text = pl.serialize_m3u([row], tmp_path, app_settings.music_folder, absolute=True)
    assert (app_settings.music_folder / "Artist-Song.mp3").resolve().as_posix() in text
    assert text.splitlines()[2].startswith("/")


def test_serialize_m3u_skips_missing(app_settings, tmp_path):
    song_uuid = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(song_uuid)
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None

    text = pl.serialize_m3u([row], tmp_path, app_settings.music_folder)
    assert "#EXTM3U" in text
    assert "gone.mp3" not in text
    assert "#EXTINF" not in text


def test_serialize_m3u_link_one_url_per_line(app_settings):
    first = db.insert_song(
        current_path="Artist-Song.mp3",
        title="Song",
        artists="Artist",
        source_url="https://example.com/watch?v=one",
    )
    second = db.insert_song(
        current_path="Other.mp3",
        title="Other",
        source_url="https://example.com/watch?v=two",
    )
    third = db.insert_song(current_path="NoSource.mp3", title="No Source")
    rows = [db.get_song_by_uuid(uuid) for uuid in (first, second, third)]
    rows = [row for row in rows if row is not None]

    text = pl.serialize_m3u_link(rows)

    assert text.splitlines() == [
        "#EXTM3U",
        "#EXTINF:-1,Artist - Song",
        "https://example.com/watch?v=one",
        "#EXTINF:-1,Other",
        "https://example.com/watch?v=two",
    ]
    assert text.endswith("\n")


def test_serialize_m3u_link_empty_without_urls(app_settings):
    song_uuid = db.insert_song(current_path="NoSource.mp3", title="No Source")
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None

    assert pl.serialize_m3u_link([row]) == ""


def test_cmd_playlist_export_m3u_link(app_settings, tmp_path, capsys):
    song_uuid = db.insert_song(
        current_path="Artist-Song.mp3",
        title="Song",
        source_url="https://example.com/watch?v=one",
    )
    other_uuid = db.insert_song(current_path="Other.mp3", title="Other")
    db.create_playlist("Chill")
    db.add_song_to_playlist("Chill", song_uuid)
    db.add_song_to_playlist("Chill", other_uuid)

    out = tmp_path / "chill.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="Chill",
            format="m3u-link",
            output=str(out),
            absolute=False,
            no_scan=True,
        )
    )

    assert out.read_text() == (
        "#EXTM3U\n#EXTINF:-1,Song\nhttps://example.com/watch?v=one\n"
    )
    captured = capsys.readouterr().out
    assert "Exported" in captured
    assert "Skipped 1 sourceless song." in captured


def test_cmd_playlist_export_m3u_link_default_extension(
    monkeypatch, app_settings, tmp_path
):
    song_uuid = db.insert_song(
        current_path="Artist-Song.mp3",
        title="Song",
        source_url="https://example.com/watch?v=one",
    )
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)
    monkeypatch.chdir(tmp_path)

    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u-link", output=None, absolute=False, no_scan=True
        )
    )

    assert (tmp_path / "P.m3u").read_text() == (
        "#EXTM3U\n#EXTINF:-1,Song\nhttps://example.com/watch?v=one\n"
    )


def test_serialize_json(app_settings):
    row = _song_row("Artist-Song.mp3", "Song", "Artist")
    data = json.loads(pl.serialize_json("P", [row], app_settings.music_folder))
    assert data["smclipy_playlist"] == 2
    assert data["name"] == "P"
    song = data["songs"][0]
    assert song["uuid"] == row["uuid"]
    assert song["title"] == "Song"
    assert song["artists"] == "Artist"
    assert song["album"] == ""
    assert song["album_artist"] == ""
    assert song["genres"] == []
    assert song["date"] == ""
    assert song["track_number"] == ""
    assert song["source_url"] == ""
    assert song["path"] == "Artist-Song.mp3"


def test_serialize_json_includes_all_metadata(app_settings):
    song_uuid = db.insert_song(
        current_path="Artist-Song.mp3",
        title="Song",
        artists="Artist",
        album="Album",
        album_artist="Album Artist",
        date="1980-01-01",
        genre="pop; rock",
        track_number="3",
        source_url="https://youtube.com/watch?v=abc",
    )
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None

    data = json.loads(pl.serialize_json("P", [row], app_settings.music_folder))

    assert data["smclipy_playlist"] == 2
    assert data["songs"][0] == {
        "uuid": song_uuid,
        "title": "Song",
        "artists": "Artist",
        "artists_list": ["Artist"],
        "album": "Album",
        "album_artist": "Album Artist",
        "genres": ["pop", "rock"],
        "date": "1980-01-01",
        "track_number": "3",
        "source_url": "https://youtube.com/watch?v=abc",
        "path": "Artist-Song.mp3",
    }


def test_serialize_json_absolute(app_settings):
    row = _song_row("Artist-Song.mp3")
    data = json.loads(
        pl.serialize_json("P", [row], app_settings.music_folder, absolute=True)
    )
    assert data["songs"][0]["path"] == str(
        (app_settings.music_folder / "Artist-Song.mp3").resolve()
    )


def test_artists_are_displayed_unescaped(app_settings, tmp_path):
    artists = join_authors(["Earth, Wind & Fire", "Maurice White"])
    row = _song_row("Artist-Song.mp3", "September", artists)

    assert pl._song_label(row) == "September - Earth, Wind & Fire, Maurice White"

    text = pl.serialize_m3u([row], tmp_path, app_settings.music_folder)
    assert "#EXTINF:-1,Earth, Wind & Fire, Maurice White - September" in text

    data = json.loads(pl.serialize_json("P", [row], app_settings.music_folder))
    assert data["songs"][0]["artists"] == "Earth, Wind & Fire, Maurice White"


def test_parse_m3u_skips_headers_and_normalizes_separators():
    text = (
        "#EXTM3U\n"
        "#EXTINF:-1,Artist - Song\n"
        "C:\\Music\\a.mp3\n"
        "\n"
        "#EXTINF:-1,x\n"
        "relative.mp3\n"
    )
    assert pl.parse_m3u(text) == ["C:/Music/a.mp3", "relative.mp3"]


def test_parse_json():
    name, entries = pl.parse_json(
        '{"name": "P", "songs": [{"uuid": "u", "path": "a.mp3"}]}'
    )
    assert name == "P"
    assert entries == [{"uuid": "u", "path": "a.mp3"}]

    name, entries = pl.parse_json('{"name": "  ", "songs": []}')
    assert name is None


def test_parse_json_rejects_bad_shape():
    with pytest.raises(ValueError):
        pl.parse_json("[]")
    with pytest.raises(ValueError):
        pl.parse_json('{"name": "P"}')
    with pytest.raises(ValueError):
        pl.parse_json("not json")


def test_infer_export_format():
    def ns(output=None, format=None):
        return argparse.Namespace(output=output, format=format)

    assert pl._infer_export_format(ns("x.m3u")) == "m3u"
    assert pl._infer_export_format(ns("x.m3u8")) == "m3u"
    assert pl._infer_export_format(ns("x.m3u-link")) == "m3u-link"
    assert pl._infer_export_format(ns("x.json")) == "json"
    assert pl._infer_export_format(ns("x.txt")) == "m3u"
    assert pl._infer_export_format(ns(None, "json")) == "json"
    assert pl._infer_export_format(ns(None, None)) == "m3u"
    assert pl._infer_export_format(ns("x.json", "m3u")) == "m3u"


def test_resolve_entry_by_uuid_path_and_basename(app_settings):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    by_uuid, by_abs, by_name = pl._index_songs()
    music = app_settings.music_folder
    base = music.parent

    entry = {"uuid": song_uuid, "path": "does-not-matter.mp3"}
    assert pl._resolve_entry(entry, music, base, by_uuid, by_abs, by_name) == song_uuid
    assert (
        pl._resolve_entry(
            "Music/Artist-Song.mp3", music, base, by_uuid, by_abs, by_name
        )
        == song_uuid
    )
    absolute = str((music / "Artist-Song.mp3").resolve())
    assert (
        pl._resolve_entry(absolute, music, base, by_uuid, by_abs, by_name) == song_uuid
    )
    assert (
        pl._resolve_entry("no-such.mp3", music, base, by_uuid, by_abs, by_name) is None
    )
    assert pl._resolve_entry("", music, base, by_uuid, by_abs, by_name) is None


def test_resolve_entry_ambiguous_basename(app_settings):
    first = db.insert_song(current_path="d1/track.mp3")
    second = db.insert_song(current_path="d2/track.mp3")
    by_uuid, by_abs, by_name = pl._index_songs()
    music = app_settings.music_folder

    assert (
        pl._resolve_entry("track.mp3", music, music.parent, by_uuid, by_abs, by_name)
        is None
    )
    assert (
        pl._resolve_entry("d1/track.mp3", music, music.parent, by_uuid, by_abs, by_name)
        == first
    )
    assert (
        pl._resolve_entry("d2/track.mp3", music, music.parent, by_uuid, by_abs, by_name)
        == second
    )


def test_cmd_playlist_create_delete_handlers(app_settings, capsys):
    pl.cmd_playlist_create(argparse.Namespace(name="Chill", description="desc"))
    assert db.get_playlist("Chill") is not None

    with pytest.raises(SystemExit):
        pl.cmd_playlist_create(argparse.Namespace(name="Chill", description=""))
    with pytest.raises(SystemExit):
        pl.cmd_playlist_create(argparse.Namespace(name="  ", description=""))

    pl.cmd_playlist_delete(argparse.Namespace(name="Chill"))
    assert db.get_playlist("Chill") is None
    with pytest.raises(SystemExit):
        pl.cmd_playlist_delete(argparse.Namespace(name="Nope"))
    assert "Playlist 'Chill' created." in capsys.readouterr().out


def test_cmd_playlist_rename_handler(app_settings, capsys):
    db.create_playlist("A")
    db.create_playlist("Taken")
    pl.cmd_playlist_rename(argparse.Namespace(name="A", new_name="B"))
    assert db.get_playlist("A") is None
    assert db.get_playlist("B") is not None

    with pytest.raises(SystemExit):
        pl.cmd_playlist_rename(argparse.Namespace(name="B", new_name="Taken"))
    with pytest.raises(SystemExit):
        pl.cmd_playlist_rename(argparse.Namespace(name="X", new_name="Y"))
    with pytest.raises(SystemExit):
        pl.cmd_playlist_rename(argparse.Namespace(name="", new_name="Y"))
    assert "Renamed 'A' to 'B'." in capsys.readouterr().out


def test_cmd_playlist_list_and_show(app_settings, capsys):
    pl.cmd_playlist_list(argparse.Namespace())
    assert "No playlists yet" in capsys.readouterr().out

    db.create_playlist("Chill")
    song_uuid = db.insert_song(current_path="a.mp3", title="Alpha")
    db.add_song_to_playlist("Chill", song_uuid)

    pl.cmd_playlist_list(argparse.Namespace())
    assert "Chill (1 song)" in capsys.readouterr().out

    pl.cmd_playlist_show(argparse.Namespace(name="Chill"))
    out = capsys.readouterr().out
    assert "(1 song):" in out
    assert "Alpha" in out

    with pytest.raises(SystemExit):
        pl.cmd_playlist_show(argparse.Namespace(name="Nope"))


def test_cmd_playlist_add(monkeypatch, app_settings, capsys):
    first = db.insert_song(current_path="a.mp3", title="Alpha", artists="AA")
    second = db.insert_song(current_path="b.mp3", title="Beta", artists="BB")
    db.create_playlist("P")

    monkeypatch.setattr(pl, "scan_library", lambda: None)
    monkeypatch.setattr(pl, "prompt_song_selection", lambda count: [1, 0])

    pl.cmd_playlist_add(argparse.Namespace(name="P"))
    rows = db.get_playlist_songs("P")
    songs = db.list_songs()
    assert [r["uuid"] for r in rows] == [songs[1]["uuid"], songs[0]["uuid"]]
    assert {r["uuid"] for r in rows} == {first, second}
    assert "Added 2 song(s) to 'P'" in capsys.readouterr().out


def test_cmd_playlist_add_skips_present(monkeypatch, app_settings, capsys):
    song_uuid = db.insert_song(current_path="a.mp3", title="Alpha")
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)

    monkeypatch.setattr(pl, "scan_library", lambda: None)
    monkeypatch.setattr(pl, "prompt_song_selection", lambda count: [0])

    pl.cmd_playlist_add(argparse.Namespace(name="P"))
    assert "Skipped 1 already present." in capsys.readouterr().out


def test_cmd_playlist_add_requires_playlist(app_settings, capsys):
    with pytest.raises(SystemExit):
        pl.cmd_playlist_add(argparse.Namespace(name="Nope"))


def test_cmd_playlist_add_skips_missing_songs(monkeypatch, app_settings, capsys):
    present = db.insert_song(current_path="a.mp3", title="Alpha")
    missing = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(missing)
    db.create_playlist("P")

    counts: list[int] = []

    def pick(count: int) -> list[int]:
        counts.append(count)
        return [0]

    monkeypatch.setattr(pl, "scan_library", lambda: None)
    monkeypatch.setattr(pl, "prompt_song_selection", pick)

    pl.cmd_playlist_add(argparse.Namespace(name="P"))

    assert counts == [1]
    assert [row["uuid"] for row in db.get_playlist_songs("P")] == [present]
    assert "missing" not in capsys.readouterr().out


def test_cmd_playlist_remove(monkeypatch, app_settings, capsys):
    first = db.insert_song(current_path="a.mp3")
    second = db.insert_song(current_path="b.mp3")
    db.create_playlist("P")
    db.add_song_to_playlist("P", first)
    db.add_song_to_playlist("P", second)

    monkeypatch.setattr(pl, "prompt_song_selection", lambda count: [0])
    pl.cmd_playlist_remove(argparse.Namespace(name="P"))
    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [second]
    assert "Removed 1 song(s) from 'P'." in capsys.readouterr().out


def test_cmd_playlist_move(monkeypatch, app_settings, capsys):
    first = db.insert_song(current_path="a.mp3", title="Alpha")
    second = db.insert_song(current_path="b.mp3", title="Beta")
    db.create_playlist("P")
    db.add_song_to_playlist("P", first)
    db.add_song_to_playlist("P", second)

    positions = []

    def fake_prompt(count, message):
        positions.append((count, message))
        return 1 if "to a position" in message else 0

    monkeypatch.setattr(pl, "prompt_position_selection", fake_prompt)
    pl.cmd_playlist_move(argparse.Namespace(name="P"))
    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [second, first]
    assert len(positions) == 2
    assert "Moved" in capsys.readouterr().out


def test_cmd_playlist_sort(app_settings, capsys):
    db.create_playlist("P")
    db.add_song_to_playlist("P", db.insert_song(current_path="z.mp3", title="Zeta"))
    db.add_song_to_playlist("P", db.insert_song(current_path="a.mp3", title="Alpha"))

    pl.cmd_playlist_sort(argparse.Namespace(name="P", key="title", reverse=False))
    rows = db.get_playlist_songs("P")
    assert [r["title"] for r in rows] == ["Alpha", "Zeta"]
    assert "by title (ascending)" in capsys.readouterr().out


def test_cmd_playlist_export_m3u(app_settings, tmp_path, capsys):
    song_uuid = db.insert_song(
        current_path="Artist-Song.mp3", title="Song", artists="Artist"
    )
    db.create_playlist("Chill")
    db.add_song_to_playlist("Chill", song_uuid)

    out = tmp_path / "chill.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="Chill", format="m3u", output=str(out), absolute=False, no_scan=True
        )
    )
    text = out.read_text()
    assert text.startswith("#EXTM3U\n")
    assert "#EXTINF:-1,Artist - Song" in text
    assert "Music/Artist-Song.mp3" in text
    assert "Exported" in capsys.readouterr().out


def test_cmd_playlist_export_scans_by_default(
    monkeypatch, app_settings, tmp_path, capsys
):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)
    calls: list[bool] = []
    monkeypatch.setattr(pl, "scan_library", lambda: calls.append(True))

    out = tmp_path / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u", output=str(out), absolute=False, no_scan=False
        )
    )

    assert calls == [True]
    assert "Scanning music files..." in capsys.readouterr().out
    assert out.exists()


def test_cmd_playlist_export_no_scan_skips_scan(monkeypatch, app_settings, tmp_path):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)

    def fail() -> None:
        raise AssertionError("scan_library should not be called")

    monkeypatch.setattr(pl, "scan_library", fail)

    out = tmp_path / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u", output=str(out), absolute=False, no_scan=True
        )
    )
    assert out.exists()


def test_cmd_playlist_export_skips_missing(app_settings, tmp_path, capsys):
    song_uuid = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(song_uuid)
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)

    out = tmp_path / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u", output=str(out), absolute=False, no_scan=True
        )
    )
    text = out.read_text()
    assert "EXTINF" not in text
    assert "Skipped 1 missing song." in capsys.readouterr().out


def test_cmd_playlist_export_empty(app_settings, tmp_path, capsys):
    db.create_playlist("P")
    out = tmp_path / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u", output=str(out), absolute=False, no_scan=True
        )
    )
    assert not out.exists()
    assert "empty" in capsys.readouterr().out


def test_cmd_playlist_import_m3u_name_optional(
    monkeypatch, app_settings, tmp_path, capsys
):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    monkeypatch.setattr(pl, "scan_library", lambda: None)

    file_ = tmp_path / "mix.m3u"
    file_.write_text("#EXTM3U\n#EXTINF:-1,A - S\nMusic/Artist-Song.mp3\n")
    pl.cmd_playlist_import(
        argparse.Namespace(file=str(file_), name=None, replace=False)
    )
    assert db.get_playlist("mix") is not None
    rows = db.get_playlist_songs("mix")
    assert [r["uuid"] for r in rows] == [song_uuid]
    assert "Imported 1 song(s) into playlist 'mix'" in capsys.readouterr().out


def test_cmd_playlist_import_m3u_utf8_bom(monkeypatch, app_settings, tmp_path, capsys):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    monkeypatch.setattr(pl, "scan_library", lambda: None)

    file_ = tmp_path / "bom.m3u"
    file_.write_bytes(b"\xef\xbb\xbf#EXTM3U\n#EXTINF:-1,A - S\nMusic/Artist-Song.mp3\n")
    pl.cmd_playlist_import(
        argparse.Namespace(file=str(file_), name="bom", replace=False)
    )
    assert [r["uuid"] for r in db.get_playlist_songs("bom")] == [song_uuid]
    assert "Imported 1 song(s)" in capsys.readouterr().out


def test_cmd_playlist_import_json_and_replace(monkeypatch, app_settings, tmp_path):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    monkeypatch.setattr(pl, "scan_library", lambda: None)

    file_ = tmp_path / "roadtrip.json"
    file_.write_text(
        json.dumps(
            {
                "smclipy_playlist": 1,
                "name": "Roadtrip",
                "songs": [{"uuid": song_uuid, "path": "Artist-Song.mp3"}],
            }
        )
    )

    def ns(replace=False):
        return argparse.Namespace(file=str(file_), name=None, replace=replace)

    pl.cmd_playlist_import(ns())
    assert db.get_playlist("Roadtrip") is not None
    assert [r["uuid"] for r in db.get_playlist_songs("Roadtrip")] == [song_uuid]

    with pytest.raises(SystemExit):
        pl.cmd_playlist_import(ns())
    pl.cmd_playlist_import(ns(replace=True))
    assert [r["uuid"] for r in db.get_playlist_songs("Roadtrip")] == [song_uuid]


def test_cmd_playlist_import_json_v2(monkeypatch, app_settings, tmp_path):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    monkeypatch.setattr(pl, "scan_library", lambda: None)

    file_ = tmp_path / "roadtrip.json"
    file_.write_text(
        json.dumps(
            {
                "smclipy_playlist": 2,
                "name": "Roadtrip",
                "songs": [
                    {
                        "uuid": song_uuid,
                        "title": "Exported Title",
                        "artists": "Exported Artist",
                        "album": "Exported Album",
                        "genres": ["pop", "rock"],
                        "date": "1980-01-01",
                        "track_number": "3",
                        "source_url": "https://youtube.com/watch?v=abc",
                        "path": "Artist-Song.mp3",
                    }
                ],
            }
        )
    )

    pl.cmd_playlist_import(
        argparse.Namespace(file=str(file_), name=None, replace=False)
    )
    assert [r["uuid"] for r in db.get_playlist_songs("Roadtrip")] == [song_uuid]


def test_cmd_playlist_import_reports_unresolved(
    monkeypatch, app_settings, tmp_path, capsys
):
    monkeypatch.setattr(pl, "scan_library", lambda: None)
    file_ = tmp_path / "ghost.m3u"
    file_.write_text("no-such-song.mp3\nno-other-song.mp3\n")
    with pytest.raises(SystemExit) as excinfo:
        pl.cmd_playlist_import(
            argparse.Namespace(file=str(file_), name=None, replace=False)
        )
    assert excinfo.value.code == 1
    assert db.get_playlist("ghost") is None
    err = capsys.readouterr().err
    assert "2 song(s) not found in your library" in err
    assert "refusing to create" in err


def test_cmd_playlist_import_suggests_download_for_missing_json_songs(
    monkeypatch, app_settings, tmp_path, capsys
):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    monkeypatch.setattr(pl, "scan_library", lambda: None)
    file_ = tmp_path / "roadtrip.json"
    file_.write_text(
        json.dumps(
            {
                "smclipy_playlist": 2,
                "name": "Roadtrip",
                "songs": [
                    {"uuid": song_uuid, "path": "Artist-Song.mp3"},
                    {
                        "uuid": "missing-uuid",
                        "title": "Gone",
                        "artists": "Artist",
                        "source_url": "https://youtube.com/watch?v=abc",
                    },
                ],
            }
        )
    )

    pl.cmd_playlist_import(
        argparse.Namespace(file=str(file_), name=None, replace=False)
    )

    out = capsys.readouterr().out
    assert "1 song(s) not found in your library." in out
    assert f"smclipy download --file {file_}" in out


def test_cmd_playlist_import_hint_when_json_has_no_source_urls(
    monkeypatch, app_settings, tmp_path, capsys
):
    monkeypatch.setattr(pl, "scan_library", lambda: None)
    file_ = tmp_path / "ghost.json"
    file_.write_text(
        json.dumps(
            {
                "smclipy_playlist": 2,
                "name": "Ghost",
                "songs": [{"uuid": "missing-uuid", "title": "Gone"}],
            }
        )
    )

    with pytest.raises(SystemExit):
        pl.cmd_playlist_import(
            argparse.Namespace(file=str(file_), name=None, replace=False)
        )

    err = capsys.readouterr().err
    assert "no source URL" in err
    assert "smclipy download --file" not in err


def test_cmd_playlist_import_counts_missing_tracked_files(
    monkeypatch, app_settings, tmp_path, capsys
):
    song_uuid = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(song_uuid)
    monkeypatch.setattr(pl, "scan_library", lambda: None)
    file_ = tmp_path / "roadtrip.json"
    file_.write_text(
        json.dumps(
            {
                "smclipy_playlist": 2,
                "name": "Roadtrip",
                "songs": [
                    {
                        "uuid": song_uuid,
                        "source_url": "https://youtube.com/watch?v=abc",
                    }
                ],
            }
        )
    )

    pl.cmd_playlist_import(
        argparse.Namespace(file=str(file_), name=None, replace=False)
    )

    out = capsys.readouterr().out
    assert "1 song(s) not found in your library." in out
    assert "smclipy download --file" in out


def test_parse_json_v2_requires_current_version():
    with pytest.raises(ValueError):
        pl.parse_json_v2('{"songs": []}')
    with pytest.raises(ValueError):
        pl.parse_json_v2('{"smclipy_playlist": 1, "songs": []}')

    name, entries = pl.parse_json_v2(
        '{"smclipy_playlist": 2, "name": "P", "songs": []}'
    )
    assert name == "P"
    assert entries == []


def test_song_meta_from_entry_prefers_artists_list():
    meta = pl.song_meta_from_entry(
        {
            "uuid": "u",
            "title": " Song ",
            "artists": "Earth, Wind & Fire",
            "artists_list": ["Earth, Wind & Fire", "Maurice White"],
            "genres": ["pop", "rock"],
            "date": "1980",
            "source_url": "https://example.test/song",
        }
    )
    assert meta.uuid == "u"
    assert meta.title == "Song"
    assert meta.artists == ["Earth, Wind & Fire", "Maurice White"]
    assert meta.genre == "pop; rock"
    assert meta.date == "1980"
    assert meta.source_url == "https://example.test/song"

    fallback = pl.song_meta_from_entry({"artists": "Artist A, Artist B"})
    assert fallback.artists == ["Artist A", "Artist B"]


def test_cmd_playlist_import_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        pl.cmd_playlist_import(
            argparse.Namespace(
                file=str(tmp_path / "nope.m3u"), name=None, replace=False
            )
        )


def test_cmd_playlist_import_unsupported_ext(app_settings, tmp_path):
    file_ = tmp_path / "mix.txt"
    file_.write_text("x")
    with pytest.raises(SystemExit):
        pl.cmd_playlist_import(
            argparse.Namespace(file=str(file_), name=None, replace=False)
        )


def test_cmd_playlist_dispatcher(monkeypatch, capsys):
    actions = [
        "create",
        "rename",
        "delete",
        "list",
        "show",
        "add",
        "remove",
        "move",
        "sort",
        "export",
        "import",
    ]
    called = []
    spy = {action: (lambda args, a=action: called.append(a)) for action in actions}
    monkeypatch.setattr(pl, "_ACTIONS", spy)
    for action in actions:
        pl.cmd_playlist(argparse.Namespace(playlist_action=action))
    assert called == actions


def test_serialize_json_skips_missing(app_settings):
    song_uuid = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(song_uuid)
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None

    data = json.loads(pl.serialize_json("P", [row], app_settings.music_folder))
    assert data["name"] == "P"
    assert data["songs"] == []


def test_cmd_playlist_export_json_skips_missing(app_settings, tmp_path, capsys):
    song_uuid = db.insert_song(current_path="gone.mp3", title="Gone")
    db.mark_song_missing(song_uuid)
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)

    out = tmp_path / "p.json"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="json", output=str(out), absolute=False, no_scan=True
        )
    )
    assert json.loads(out.read_text())["songs"] == []
    assert "Skipped 1 missing song." in capsys.readouterr().out


def test_cmd_playlist_export_explicit_format_wins_over_output_suffix(
    app_settings, tmp_path
):
    row = _song_row("Artist-Song.mp3")
    db.create_playlist("P")
    db.add_song_to_playlist("P", row["uuid"])

    out = tmp_path / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="json", output=str(out), absolute=False, no_scan=True
        )
    )
    assert json.loads(out.read_text())["songs"][0]["uuid"] == row["uuid"]


def test_cmd_playlist_export_creates_parent_dirs(app_settings, tmp_path):
    row = _song_row("Artist-Song.mp3")
    db.create_playlist("P")
    db.add_song_to_playlist("P", row["uuid"])

    out = tmp_path / "deep" / "nested" / "p.m3u"
    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="m3u", output=str(out), absolute=False, no_scan=True
        )
    )
    assert out.exists()


def test_cmd_playlist_export_default_name_falls_back_when_empty(
    monkeypatch, app_settings, tmp_path
):
    row = _song_row("Artist-Song.mp3")
    db.create_playlist("///")
    db.add_song_to_playlist("///", row["uuid"])
    monkeypatch.chdir(tmp_path)

    pl.cmd_playlist_export(
        argparse.Namespace(
            name="///", format="m3u", output=None, absolute=False, no_scan=True
        )
    )

    assert (tmp_path / "playlist.m3u").exists()


def test_cmd_playlist_write_error_reports_cleanly(app_settings, tmp_path, capsys):
    row = _song_row("Artist-Song.mp3")
    db.create_playlist("P")
    db.add_song_to_playlist("P", row["uuid"])

    out = tmp_path / "x.m3u"
    out.mkdir()
    with pytest.raises(SystemExit):
        pl.cmd_playlist_export(
            argparse.Namespace(
                name="P", format="m3u", output=str(out), absolute=False, no_scan=True
            )
        )
    assert "could not write" in capsys.readouterr().err


def test_cmd_playlist_remove_sort_require_existing_playlist(app_settings, capsys):
    for action, kwargs in (
        (pl.cmd_playlist_remove, {}),
        (pl.cmd_playlist_sort, {"reverse": False, "key": "title"}),
        (pl.cmd_playlist_move, {}),
    ):
        with pytest.raises(SystemExit):
            action(argparse.Namespace(name="Nope", **kwargs))
        assert "not found" in capsys.readouterr().err


def test_cmd_playlist_handlers_strip_names(app_settings):
    db.create_playlist("P")
    pl.cmd_playlist_delete(argparse.Namespace(name="  P  "))
    assert db.get_playlist("P") is None


def test_cmd_playlist_unknown_action(monkeypatch):
    monkeypatch.setattr(pl, "_ACTIONS", {})
    with pytest.raises(SystemExit):
        pl.cmd_playlist(argparse.Namespace(playlist_action="nope"))


def test_cmd_playlist_list_json(app_settings, capsys):
    db.create_playlist("Chill", description="relax")
    db.insert_song(current_path="a.mp3", title="Alpha")

    pl.cmd_playlist_list(argparse.Namespace(json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "Chill"
    assert payload[0]["description"] == "relax"
    assert payload[0]["song_count"] == 0


def test_cmd_playlist_show_json(app_settings, capsys):
    db.create_playlist("Chill")
    song_uuid = db.insert_song(current_path="a.mp3", title="Alpha", artists="AA")
    db.add_song_to_playlist("Chill", song_uuid)

    pl.cmd_playlist_show(argparse.Namespace(name="Chill", json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "Chill"
    assert payload["songs"] == [
        {
            "position": 1,
            "uuid": song_uuid,
            "title": "Alpha",
            "artists": "AA",
            "album": "",
            "path": "a.mp3",
            "missing": False,
        }
    ]


def test_cmd_playlist_show_json_displays_artists(app_settings, capsys):
    db.create_playlist("Chill")
    song_uuid = db.insert_song(
        current_path="a.mp3",
        title="September",
        artists=join_authors(["Earth, Wind & Fire"]),
    )
    db.add_song_to_playlist("Chill", song_uuid)

    pl.cmd_playlist_show(argparse.Namespace(name="Chill", json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["songs"][0]["artists"] == "Earth, Wind & Fire"


def test_cmd_playlist_show_json_marks_missing(app_settings, capsys):
    db.create_playlist("Chill")
    song_uuid = db.insert_song(current_path="a.mp3", title="Alpha")
    db.add_song_to_playlist("Chill", song_uuid)
    db.mark_song_missing(song_uuid)

    pl.cmd_playlist_show(argparse.Namespace(name="Chill", json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["songs"][0]["missing"] is True
    assert payload["songs"][0]["path"] is None


def test_cmd_playlist_show_json_positions_follow_current_order(app_settings, capsys):
    import sqlite3

    db.create_playlist("Chill")
    ids = [db.insert_song(current_path=f"{c}.mp3", title=c) for c in "abc"]
    for song_uuid in ids:
        db.add_song_to_playlist("Chill", song_uuid)

    with sqlite3.connect(app_settings.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM songs WHERE uuid = ?", (ids[1],))
        conn.commit()

    pl.cmd_playlist_show(argparse.Namespace(name="Chill", json=True))

    payload = json.loads(capsys.readouterr().out)
    assert [song["position"] for song in payload["songs"]] == [1, 2]
    assert [song["uuid"] for song in payload["songs"]] == [ids[0], ids[2]]


def test_serialize_m3u_flattens_multiline_labels(app_settings, tmp_path):
    row = _song_row("Artist-Song.mp3", "Song\n#EXTINF:-1,Evil", "Artist")

    text = pl.serialize_m3u([row], tmp_path, app_settings.music_folder)

    lines = text.splitlines()
    assert len(lines) == 3
    assert lines[1] == "#EXTINF:-1,Artist - Song #EXTINF:-1,Evil"


def test_serialize_json_uses_export_dir_relative_paths(app_settings, tmp_path):
    row = _song_row("Artist-Song.mp3")
    export_dir = tmp_path / "lists"

    data = json.loads(
        pl.serialize_json("P", [row], app_settings.music_folder, export_dir=export_dir)
    )

    expected = os.path.relpath(
        app_settings.music_folder / "Artist-Song.mp3", export_dir
    )
    assert data["songs"][0]["path"] == expected


def test_cmd_playlist_export_json_is_relative_to_exported_file(app_settings, tmp_path):
    song_uuid = db.insert_song(current_path="Artist-Song.mp3", title="Song")
    db.create_playlist("P")
    db.add_song_to_playlist("P", song_uuid)
    out = tmp_path / "exports" / "p.json"

    pl.cmd_playlist_export(
        argparse.Namespace(
            name="P", format="json", output=str(out), absolute=False, no_scan=True
        )
    )

    expected = os.path.relpath(
        app_settings.music_folder / "Artist-Song.mp3", out.parent
    )
    assert json.loads(out.read_text())["songs"][0]["path"] == expected


def test_cmd_playlist_import_replace_refuses_to_wipe_existing(
    monkeypatch, app_settings, tmp_path, capsys
):
    keep_uuid = db.insert_song(current_path="Keep.mp3", title="Keep")
    db.create_playlist("Mix")
    db.add_song_to_playlist("Mix", keep_uuid)
    monkeypatch.setattr(pl, "scan_library", lambda: None)
    file_ = tmp_path / "ghost.m3u"
    file_.write_text("missing-one.mp3\nmissing-two.mp3\n")

    with pytest.raises(SystemExit) as excinfo:
        pl.cmd_playlist_import(
            argparse.Namespace(file=str(file_), name="Mix", replace=True)
        )

    assert excinfo.value.code == 1
    assert [row["uuid"] for row in db.get_playlist_songs("Mix")] == [keep_uuid]
    assert "refusing to replace" in capsys.readouterr().err
