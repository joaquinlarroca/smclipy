import argparse
from datetime import datetime
from io import BytesIO
from unittest.mock import Mock

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3
from PIL import Image

import smclipy.metadata as metadata
import smclipy.restore as restore
from smclipy.metadata import (
    apply_tag_update,
    change_cover,
    has_cover,
    read_song_profile,
    read_song_uuid,
    remove_cover,
    scan_library,
    snapshot_song,
)

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Song", artist="Artist") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio.save(path)


def make_png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return buf.getvalue()


def freeze_snapshot_times(monkeypatch, *seconds: int) -> None:
    monkeypatch.setattr(
        metadata,
        "datetime",
        Mock(
            now=Mock(
                side_effect=[datetime(2026, 1, 1, 0, 0, second) for second in seconds]
            )
        ),
    )


def _prepare_restore(monkeypatch, app_settings, picks, *, confirm=True):
    monkeypatch.setattr(restore, "init", lambda: app_settings)
    monkeypatch.setattr(restore, "scan_library", lambda: None)
    monkeypatch.setattr(restore, "prompt_song_selection", lambda count: picks)
    monkeypatch.setattr(restore, "prompt_confirm", lambda *a, **k: confirm)


def test_cmd_restore_numbers_only_songs_with_backups(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Alpha-One.mp3", title="One", artist="Alpha")
    write_tagged_mp3(music / "Beta-Two.mp3", title="Two", artist="Beta")
    scan_library()
    song_uuid = read_song_uuid(music / "Beta-Two.mp3")
    assert song_uuid is not None
    assert snapshot_song(music / "Beta-Two.mp3", song_uuid, "tag") is not None
    assert apply_tag_update(music / "Beta-Two.mp3", title="Wrong") is True

    counts: list[int] = []
    monkeypatch.setattr(restore, "init", lambda: app_settings)
    monkeypatch.setattr(restore, "scan_library", lambda: None)
    monkeypatch.setattr(
        restore,
        "prompt_song_selection",
        lambda count: counts.append(count) or [0],
    )
    monkeypatch.setattr(restore, "prompt_confirm", lambda *a, **k: True)

    restore.cmd_restore(argparse.Namespace(all=False, limit=25))

    out = capsys.readouterr().out
    # Only the backup-owning song is listed, so "1" must mean that song.
    assert counts == [1]
    assert "Restore points (newest first):" in out
    assert "Wrong - Beta" in out
    assert "Title: Wrong -> Two" in out
    assert "One - Alpha" not in out
    assert str(ID3(music / "Beta-Two.mp3")["TIT2"]) == "Two"
    assert "Restored 'Wrong - Beta'" in out
    assert "Restore complete: 1 restored, 0 failed." in out


def test_restore_lists_newest_restore_point_first(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Alpha-One.mp3"
    write_tagged_mp3(path, title="Song", artist="Alpha")
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None
    freeze_snapshot_times(monkeypatch, 1, 2, 3)

    assert snapshot_song(path, song_uuid, "tag") is not None
    assert apply_tag_update(path, title="Middle") is True
    assert snapshot_song(path, song_uuid, "modify") is not None
    assert apply_tag_update(path, title="Wrong") is True
    assert snapshot_song(path, song_uuid, "crop") is not None
    assert apply_tag_update(path, title="Current") is True

    _prepare_restore(monkeypatch, app_settings, [])

    restore.cmd_restore(argparse.Namespace(all=False, limit=25))

    out = capsys.readouterr().out
    assert (
        out.index("Before crop") < out.index("Before modify") < out.index("Before tag")
    )
    assert "Current -> Wrong" in out
    assert "Current -> Middle" in out
    assert "Current -> Song" in out
    assert "Nothing to restore." in out


def test_restore_hides_points_that_change_nothing(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Alpha-One.mp3"
    write_tagged_mp3(path, title="Song", artist="Alpha")
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None
    assert snapshot_song(path, song_uuid, "tag") is not None

    calls: list[int] = []
    monkeypatch.setattr(restore, "init", lambda: app_settings)
    monkeypatch.setattr(restore, "scan_library", lambda: None)
    monkeypatch.setattr(
        restore, "prompt_song_selection", lambda count: calls.append(count) or []
    )

    restore.cmd_restore(argparse.Namespace(all=False, limit=25))

    out = capsys.readouterr().out
    assert "No backups with changes found." in out
    assert calls == []


def test_restore_previews_cover_change(monkeypatch, app_settings, tmp_path, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Alpha-One.mp3"
    write_tagged_mp3(path, title="Song", artist="Alpha")
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())
    assert change_cover(image, path) is True
    assert snapshot_song(path, song_uuid, "crop") is not None
    assert remove_cover(path) is True

    _prepare_restore(monkeypatch, app_settings, [0])

    restore.cmd_restore(argparse.Namespace(all=False, limit=25))

    out = capsys.readouterr().out
    assert "Cover: added from backup" in out
    assert has_cover(path) is True


def test_restore_ignores_older_selection_for_same_song(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "Alpha-One.mp3"
    write_tagged_mp3(path, title="Song", artist="Alpha")
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None
    freeze_snapshot_times(monkeypatch, 1, 2, 3)

    assert snapshot_song(path, song_uuid, "tag") is not None
    assert apply_tag_update(path, title="Middle") is True
    assert snapshot_song(path, song_uuid, "modify") is not None
    assert apply_tag_update(path, title="Wrong") is True

    _prepare_restore(monkeypatch, app_settings, [1, 0])

    restore.cmd_restore(argparse.Namespace(all=False, limit=25))

    out = capsys.readouterr().out
    assert "Ignoring 1 older selection" in out
    profile, _ = read_song_profile(path)
    assert profile["title"] == "Middle"
    assert "Restore complete: 1 restored, 0 failed." in out


def test_restore_limit_and_all(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    for name, title in (("Alpha-One.mp3", "One"), ("Beta-Two.mp3", "Two")):
        write_tagged_mp3(music / name, title=title, artist=name.split("-")[0])
    scan_library()

    for name in ("Alpha-One.mp3", "Beta-Two.mp3"):
        path = music / name
        song_uuid = read_song_uuid(path)
        assert song_uuid is not None
        assert snapshot_song(path, song_uuid, "tag") is not None
        assert apply_tag_update(path, title=f"Wrong {name}") is True

    counts: list[int] = []
    monkeypatch.setattr(restore, "init", lambda: app_settings)
    monkeypatch.setattr(restore, "scan_library", lambda: None)
    monkeypatch.setattr(
        restore, "prompt_song_selection", lambda count: counts.append(count) or []
    )

    restore.cmd_restore(argparse.Namespace(all=False, limit=1))
    out = capsys.readouterr().out
    assert counts == [1]
    assert "Showing the newest 1; use --all to see the rest." in out

    restore.cmd_restore(argparse.Namespace(all=True, limit=1))
    out = capsys.readouterr().out
    assert counts == [1, 2]
    assert "Showing the newest" not in out
