import argparse
import io

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from PIL import Image

import smclipy.db as db
from smclipy import modify
from smclipy.metadata import (
    apply_tag_update,
    has_cover,
    read_song_uuid,
    scan_library,
)

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Song", artist="Artist", album="Album") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["album"] = [album]
    audio.save(path)


def make_png(path) -> None:
    path.write_bytes(_png_bytes())


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return buf.getvalue()


def _make_two_songs(music):
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Album"
    )


def test_resolve_text_semantics():
    assert modify._resolve_text("New", "Old") == "New"
    assert modify._resolve_text("Old", "Old") is None
    assert modify._resolve_text("", "Old") is None
    assert modify._resolve_text("/clear", "Old") == ""
    assert modify._resolve_text("  new  ", "Old") == "new"


def test_resolve_artists_semantics():
    assert modify._resolve_artists("A\\B", "C") == ["A", "B"]
    assert modify._resolve_artists("A, B", "C") == ["A", "B"]
    assert modify._resolve_artists("", "C") is None
    assert modify._resolve_artists("/clear", "C") == []
    assert modify._resolve_artists("C", "C") is None


def test_cmd_modify_empty_library_prints_message(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(modify, "scan_library", lambda: None)
    modify.cmd_modify(argparse.Namespace())
    assert "No audio files found" in capsys.readouterr().out


def test_cmd_modify_no_selection_cancels(monkeypatch, app_settings, capsys):
    _make_two_songs(app_settings.music_folder)
    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [])
    modify.cmd_modify(argparse.Namespace())
    out = capsys.readouterr().out
    assert "No songs selected." not in out
    assert "Updated" not in out


def test_cmd_modify_no_fields_cancels(monkeypatch, app_settings, capsys):
    _make_two_songs(app_settings.music_folder)
    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: None)
    monkeypatch.setattr(modify, "scan_library", lambda: None)
    modify.cmd_modify(argparse.Namespace())
    assert "Nothing to modify." in capsys.readouterr().out


def test_cmd_modify_applies_range_bulk_edit(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    _make_two_songs(music)
    scan_library()

    def fake_field_value(label, default=""):
        if label.startswith("Title"):
            return "Renamed"
        if label.startswith("Album"):
            return "New Album"
        return default

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0, 1])
    monkeypatch.setattr(
        modify, "prompt_field_selection", lambda *a, **k: ["title", "album"]
    )
    monkeypatch.setattr(modify, "prompt_field_value", fake_field_value)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    out = capsys.readouterr().out
    assert "[1/2] Updated" in out
    assert "[2/2] Updated" in out
    assert "Updated 2 song(s)" in out

    for name, _title in (
        ("Artist-One.mp3", "Renamed"),
        ("Artist-Two.mp3", "Renamed"),
    ):
        id3 = ID3(music / name)
        assert str(id3["TIT2"]) == "Renamed"
        assert str(id3["TALB"]) == "New Album"
        row = db.get_song_by_path(name)[0]
        assert row["title"] == "Renamed"
        assert row["album"] == "New Album"
        assert any(e["event"] == "modified" for e in db.get_events(row["uuid"]))


def test_cmd_modify_blank_keeps_unchanged(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    _make_two_songs(music)

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(
        modify, "prompt_field_selection", lambda *a, **k: ["title", "album"]
    )
    monkeypatch.setattr(modify, "prompt_field_value", lambda label, default="": default)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    out = capsys.readouterr().out
    assert "needs no changes" in out
    assert "Updated 0 song(s)" in out

    id3 = ID3(music / "Artist-One.mp3")
    assert str(id3["TIT2"]) == "One"
    assert str(id3["TALB"]) == "Album"


def test_cmd_modify_clear_token_empties_field(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    _make_two_songs(music)
    scan_library()

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(
        modify, "prompt_field_selection", lambda *a, **k: ["title", "album"]
    )

    def fake_field_value(label, default=""):
        return "/clear" if label.startswith("Title") else default

    monkeypatch.setattr(modify, "prompt_field_value", fake_field_value)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "[1/1] Updated" in capsys.readouterr().out
    id3 = ID3(music / "Artist-One.mp3")
    assert "TIT2" not in id3 or not str(id3["TIT2"])
    assert str(id3["TALB"]) == "Album"
    row = db.get_song_by_path("Artist-One.mp3")[0]
    assert row["title"] == ""


def test_cmd_modify_artists_apply_and_dedupe(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")
    scan_library()
    db.add_authors(["Artist"])

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["artists"])
    monkeypatch.setattr(
        modify, "prompt_authors", lambda ops, default="": "Artist\\Guest"
    )
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "[1/1] Updated" in capsys.readouterr().out
    id3 = ID3(music / "Artist-Song.mp3")
    assert list(id3["TPE1"].text) == ["Artist", "Guest"]
    assert db.get_authors() == ["Artist", "Guest"]


def test_cmd_modify_embed_cover(monkeypatch, app_settings, capsys, tmp_path):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")
    scan_library()
    cover = tmp_path / "cover.png"
    make_png(cover)

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["cover"])
    monkeypatch.setattr(modify, "prompt_cover", lambda: cover)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "[1/1] Updated" in capsys.readouterr().out
    assert has_cover(music / "Artist-Song.mp3")
    row = db.get_song_by_path("Artist-Song.mp3")[0]
    assert row["has_cover"] == 1


def test_cmd_modify_remove_cover(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Artist-Song.mp3"
    write_tagged_mp3(path)
    scan_library()

    cover_tmp = music / "cover.png"
    make_png(cover_tmp)
    assert apply_tag_update(path, image=cover_tmp) is True
    assert has_cover(path)

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["cover"])
    monkeypatch.setattr(modify, "prompt_cover", lambda: "remove")
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "[1/1] Updated" in capsys.readouterr().out
    assert not has_cover(path)
    row = db.get_song_by_path("Artist-Song.mp3")[0]
    assert row["has_cover"] == 0


def test_cmd_modify_remove_cover_when_absent_is_noop(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["cover"])
    monkeypatch.setattr(modify, "prompt_cover", lambda: "remove")
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "needs no changes" in capsys.readouterr().out


def test_cmd_modify_invalid_cover_keeps_current(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Artist-Song.mp3"
    write_tagged_mp3(path)
    scan_library()
    cover = music / "cover.png"
    make_png(cover)
    assert apply_tag_update(path, image=cover) is True

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["cover"])
    monkeypatch.setattr(
        modify,
        "prompt_field_value",
        lambda label, default="": str(music / "missing.png"),
    )
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    captured = capsys.readouterr()
    assert "needs no changes" in captured.out
    assert has_cover(path)


def test_cmd_modify_reports_failed_tag_write(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["title"])
    monkeypatch.setattr(
        modify, "prompt_field_value", lambda label, default="": "Edited"
    )
    monkeypatch.setattr(modify, "apply_tag_update", lambda *a, **k: False)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    captured = capsys.readouterr()
    assert "Could not modify" in captured.out
    assert "needs no changes" not in captured.out
    id3 = ID3(music / "Artist-Song.mp3")
    assert str(id3["TIT2"]) == "Song"


def test_cmd_modify_reports_failed_cover_removal(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Artist-Song.mp3"
    write_tagged_mp3(path)
    scan_library()
    cover = music / "cover.png"
    make_png(cover)
    assert apply_tag_update(path, image=cover) is True

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["cover"])
    monkeypatch.setattr(modify, "prompt_cover", lambda: "remove")
    monkeypatch.setattr(modify, "remove_cover", lambda file: False)
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    captured = capsys.readouterr()
    assert "Could not modify" in captured.out
    assert "needs no changes" not in captured.out
    assert has_cover(path)


def test_cmd_modify_reset_mb_resets_match(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")
    scan_library()
    song_uuid = read_song_uuid(music / "Artist-Song.mp3")
    assert song_uuid is not None
    db.record_mb_status(
        song_uuid,
        db.MB_STATUS_TAGGED,
        recording_id="rec-1",
        release_group_id="rg-1",
        tagged=True,
    )

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["title"])
    monkeypatch.setattr(
        modify, "prompt_field_value", lambda label, default="": "Edited"
    )
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace(reset_mb=True))

    assert "[1/1] Updated" in capsys.readouterr().out
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["title"] == "Edited"
    assert row["musicbrainz_status"] is None
    assert row["musicbrainz_recording_id"] is None
    assert row["tagged"] == 0
    assert any(
        e["event"] == "musicbrainz" and e["detail"] == '{"status": "reset"}'
        for e in db.get_events(song_uuid)
    )


def test_cmd_modify_leaves_mb_state_when_flag_off(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")
    scan_library()
    song_uuid = read_song_uuid(music / "Artist-Song.mp3")
    assert song_uuid is not None
    db.record_mb_status(
        song_uuid,
        db.MB_STATUS_TAGGED,
        recording_id="rec-1",
        release_group_id="rg-1",
        tagged=True,
    )

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["title"])
    monkeypatch.setattr(
        modify, "prompt_field_value", lambda label, default="": "Edited"
    )
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace(reset_mb=False))

    capsys.readouterr()
    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["musicbrainz_status"] == "tagged"
    assert row["tagged"] == 1


def test_cmd_modify_untracked_song_still_edits(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-Song.mp3")

    monkeypatch.setattr(modify, "prompt_song_selection", lambda count: [0])
    monkeypatch.setattr(modify, "prompt_field_selection", lambda *a, **k: ["title"])
    monkeypatch.setattr(
        modify, "prompt_field_value", lambda label, default="": "Edited"
    )
    monkeypatch.setattr(modify, "scan_library", lambda: None)

    modify.cmd_modify(argparse.Namespace())

    assert "[1/1] Updated" in capsys.readouterr().out
    id3 = ID3(music / "Artist-Song.mp3")
    assert str(id3["TIT2"]) == "Edited"
    assert db.get_song_by_path("Artist-Song.mp3") == []
