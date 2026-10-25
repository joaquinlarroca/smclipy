import argparse

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3

import smclipy.restore as restore
from smclipy.metadata import (
    apply_tag_update,
    read_song_uuid,
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
    monkeypatch.setattr(restore, "prompt_position_selection", lambda count, message: 0)
    monkeypatch.setattr(restore, "prompt_confirm", lambda *a, **k: True)

    restore.cmd_restore(argparse.Namespace())

    out = capsys.readouterr().out
    # Only the backup-owning song is listed, so "1" must mean that song.
    assert counts == [1]
    assert "1. Wrong - Beta" in out
    assert "One - Alpha" not in out
    assert str(ID3(music / "Beta-Two.mp3")["TIT2"]) == "Two"
    assert "Restored 'Wrong - Beta'" in out
    assert "Restore complete: 1 restored, 0 failed." in out
