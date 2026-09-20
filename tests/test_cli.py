import argparse
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from mutagen.easyid3 import EasyID3
from PIL import Image

import smclipy.db as db
from smclipy.cli import cmd_crop, process_video
from smclipy.metadata import SaveResult, write_song_uuid

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def test_collect_new_queue_resets_state(monkeypatch, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "collect_urls", lambda: ["A", "B"])
    pending_ids = cli._collect_new_queue()
    assert pending_ids == ["A", "B"]
    assert db.get_pending_urls() == ["A", "B"]


def test_process_video_deletes_stale_temp_files(monkeypatch, app_settings):
    import smclipy.cli as cli

    temp = app_settings.temp_folder
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "temp.mp3").write_bytes(b"old-mp3")
    (temp / "temp.png").write_bytes(b"old-png")
    (temp / "yt-abcdefghijk.mp3").write_bytes(b"old-stem-mp3")
    (temp / "yt-abcdefghijk.webp").write_bytes(b"old-stem-thumb")
    (temp / "tag-cover-rec-1.jpg").write_bytes(b"old-tag-cover")

    get_image_from_file_calls = []

    def fake_get_image(file, save_to_path, save_as):
        get_image_from_file_calls.append((file.name, save_as))
        return None

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", fake_get_image)
    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert not (temp / "temp.mp3").exists()
    assert not (temp / "temp.png").exists()
    assert not (temp / "yt-abcdefghijk.mp3").exists()
    assert not (temp / "yt-abcdefghijk.webp").exists()
    assert not (temp / "tag-cover-rec-1.jpg").exists()
    assert get_image_from_file_calls == [("sc-artist-track.mp3", "sc-artist-track")]
    assert db.get_authors() == ["Artist"]


def test_process_video_skips_crop_prompt_when_already_1_to_1(
    monkeypatch, app_settings, capsys
):
    import smclipy.cli as cli

    temp = app_settings.temp_folder
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "temp.png").write_bytes(b"image")

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    cover_path = temp / "temp.png"
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: cover_path)
    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "is_image_1_to_1", lambda *a, **k: True)
    prompt_crop_called = Mock(
        side_effect=AssertionError("prompt_crop should not be called")
    )
    monkeypatch.setattr(cli, "prompt_crop", prompt_crop_called)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])

    prompt_crop_called.assert_not_called()
    assert "Already 1:1" in capsys.readouterr().out


def test_process_video_no_prompt_skips_cover_display_and_crop(
    monkeypatch, app_settings
):
    import smclipy.cli as cli

    temp = app_settings.temp_folder
    temp.mkdir(parents=True, exist_ok=True)
    cover_path = temp / "temp.png"
    cover_path.write_bytes(b"image")

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: cover_path)
    for name in (
        "display_image",
        "is_image_1_to_1",
        "prompt_crop",
        "prompt_title",
        "prompt_album",
        "prompt_authors",
    ):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))
    monkeypatch.setattr(cli, "get_album", lambda info: "")
    monkeypatch.setattr(cli, "get_author", lambda info: ["Artist"])
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [], no_prompt=True)


def test_process_video_no_prompt_uses_defaults(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"title": "Song Title"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_album", lambda info: "Album Name")
    monkeypatch.setattr(cli, "get_author", lambda info: ["Artist"])
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    for name in ("prompt_title", "prompt_album", "prompt_authors", "prompt_crop"):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))

    def fake_save(temp, cover, title, authors, album, **kwargs):
        captured.update(title=title, authors=authors, album=album)
        return SaveResult.SAVED, None

    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [], no_prompt=True)

    assert captured == {
        "title": "Song Title",
        "authors": "Artist",
        "album": "Album Name",
    }


def test_process_video_no_prompt_falls_back_to_stem_title(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_album", lambda info: "")
    monkeypatch.setattr(cli, "get_author", lambda info: ["Artist"])
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    for name in ("prompt_title", "prompt_album", "prompt_authors", "prompt_crop"):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))

    def fake_save(temp, cover, title, authors, album, **kwargs):
        captured["title"] = title
        return SaveResult.SAVED, None

    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [], no_prompt=True)

    assert captured["title"] == "sc-artist-track"


def test_process_video_no_prompt_passes_overwrite_false(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"title": "Song Title"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_album", lambda info: "Album Name")
    monkeypatch.setattr(cli, "get_author", lambda info: ["Artist"])
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    for name in ("prompt_title", "prompt_album", "prompt_authors", "prompt_crop"):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))

    def fake_save(temp, cover, title, authors, album, **kwargs):
        captured["overwrite"] = kwargs.get("overwrite")
        return SaveResult.SAVED, None

    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [], no_prompt=True)
    assert captured["overwrite"] is False

    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")

    process_video("https://soundcloud.com/artist/track", [])
    assert captured["overwrite"] is None


def test_process_video_reuses_existing_song_uuid(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist-Song.mp3"
    target.write_bytes(MINIMAL_MP3)
    existing_uuid = write_song_uuid(target)
    assert existing_uuid is not None

    captured = {}

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_album", lambda info: "Album Name")
    monkeypatch.setattr(cli, "get_author", lambda info: ["Artist"])
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)

    def fake_save(temp, cover, title, authors, album, **kwargs):
        captured["song_uuid"] = kwargs.get("song_uuid")
        return SaveResult.SAVED, target

    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [])
    assert captured["song_uuid"] == existing_uuid


def test_process_video_prefills_corrected_authors(monkeypatch, app_settings):
    import smclipy.cli as cli

    db.add_authors(["author1"])

    captured = {}

    def fake_prompt_authors(authors_list, default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"channel": "Author 1"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", ["author1"])

    assert captured["default"] == "author1"


def test_process_video_prefills_multiple_artists_with_backslash(
    monkeypatch, app_settings
):
    import smclipy.cli as cli

    captured = {}

    def fake_prompt_authors(authors_list, default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(
        cli,
        "download",
        lambda url, stem="temp": {"track": ["Artist One", "Artist Two"]},
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "Artist One\\Artist Two"


def test_process_video_splits_comma_separated_autofill(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_prompt_authors(authors_list, default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(
        cli,
        "download",
        lambda url, stem="temp": {"uploader": "author1, author2"},
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "author1, author2"
    assert db.get_authors() == ["author1", "author2"]


def test_process_video_prefills_album(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_prompt_album(default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"album": "Great Album"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", fake_prompt_album)
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "Great Album"


def test_process_video_passes_album_to_save(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_save(temp_mp3, temp_png, title, authors, album, **kwargs):
        captured["album"] = album
        return (SaveResult.SAVED, None)

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"album": "Great Album"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "My Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["album"] == "My Album"


def test_process_video_skips_records_when_save_cancelled(monkeypatch, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli, "save_song_temp_to_main", lambda *a, **k: (SaveResult.FAILED, None)
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert db.get_authors() == []


def make_pillarbox_png(path) -> None:
    img = Image.new("RGB", (200, 100), "black")
    for x in range(60, 140):
        for y in range(100):
            img.putpixel((x, y), (255, 0, 0))
    img.save(path)


def _patch_crop_flow(monkeypatch, change_cover: Mock) -> None:
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: True)
    monkeypatch.setattr(cli, "crop_image_1_to_1", lambda *a, **k: None)
    monkeypatch.setattr(cli, "change_cover", change_cover)


def test_cmd_crop_missing_mp3_does_not_reembed(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_not_called()


def test_cmd_crop_reembeds_when_mp3_exists(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_called_once()


def test_cmd_crop_reembeds_dotted_title_cover(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.One.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.One.mp3").write_bytes(b"audio")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_called_once()


def test_cmd_crop_reembeds_into_renamed_mp3(monkeypatch, app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    original = music / "Artist-Song.mp3"
    original.write_bytes(MINIMAL_MP3)
    song_uuid = write_song_uuid(original, "11111111-1111-1111-1111-111111111111")
    assert song_uuid is not None
    db.insert_song(
        song_uuid=song_uuid,
        current_path="Artist-Song.mp3",
        title="Song",
        artists="Artist",
    )
    db.set_song_path(song_uuid, "Renamed.mp3")
    original.rename(music / "Renamed.mp3")

    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_called_once()
    assert change_cover.call_args.args[1] == music / "Renamed.mp3"


def test_cmd_crop_records_status_for_renamed_song(app_settings):
    import smclipy.cli as cli

    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    original = music / "Artist-Song.mp3"
    original.write_bytes(MINIMAL_MP3)
    song_uuid = write_song_uuid(original, "11111111-1111-1111-1111-111111111111")
    assert song_uuid is not None
    db.insert_song(
        song_uuid=song_uuid,
        current_path="Artist-Song.mp3",
        title="Song",
        artists="Artist",
    )
    db.set_song_path(song_uuid, "Renamed.mp3")
    original.rename(music / "Renamed.mp3")

    cli._record_cover_status("Artist-Song", db.COVER_STATUS_CROPPED)

    row = db.get_song_by_uuid(song_uuid)
    assert row["cover_status"] == db.COVER_STATUS_CROPPED


def test_cmd_directories_prints_all_paths(monkeypatch, tmp_path, capsys):
    import json

    import smclipy.cli as cli
    import smclipy.config as config

    config_file = tmp_path / "config" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    music = tmp_path / "Music"
    config_file.write_text(
        json.dumps({"name": "smclipy", "path_to_music_folder": str(music)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", config_file)
    monkeypatch.setattr(cli, "CONFIG_PATH", config_file)

    cli.main(["-d"])

    out = capsys.readouterr().out
    assert f"Config dir:  {config_file.parent.resolve()}" in out
    assert f"Music:       {music.resolve()}" in out
    assert f"Library:     {(music / 'smclipy').resolve()}" in out
    assert f"Temp:        {(music / 'smclipy' / '.temp').resolve()}" in out
    assert f"Covers:      {(music / 'smclipy' / 'covers').resolve()}" in out
    assert f"Database:    {(music / 'smclipy' / 'smclipy.db').resolve()}" in out


def test_cmd_directories_works_without_config(monkeypatch, tmp_path, capsys):
    import smclipy.cli as cli
    import smclipy.config as config

    config_file = tmp_path / "config" / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", config_file)
    monkeypatch.setattr(cli, "CONFIG_PATH", config_file)

    cli.main(["-d"])

    out = capsys.readouterr().out
    assert "no config file" in out
    assert not config_file.exists()
    assert "Music:" in out


def test_main_exits_when_stdin_not_a_tty_for_download(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["download"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_exits_when_stdin_not_a_tty_for_tag(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["tag"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_dispatch_dispatches_tag(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_tag(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_tag", fake_cmd_tag)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["tag"])
    assert len(calls) == 1


def test_main_dispatch_dispatches_tag_auto(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_tag(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_tag", fake_cmd_tag)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["tag", "--auto"])
    assert len(calls) == 1
    assert calls[0].auto is True
    assert calls[0].semi is False


def test_main_dispatch_dispatches_tag_semi(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_tag(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_tag", fake_cmd_tag)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["tag", "-s"])
    assert len(calls) == 1
    assert calls[0].auto is False
    assert calls[0].semi is True


def test_main_rejects_tag_auto_and_semi_together(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["tag", "--auto", "--semi"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "not allowed with argument" in captured.err


def test_main_rejects_tag_json_without_auto_all(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["tag", "--json"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--json requires --auto and --all" in captured.err


def test_main_rejects_tag_json_semi(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["tag", "--json", "--semi", "--all"])
    assert exc.value.code == 2
    assert "requires --auto and --all" in capsys.readouterr().err


def test_main_accepts_tag_json_auto_all(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_tag(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_tag", fake_cmd_tag)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["tag", "--json", "--auto", "--all"])
    assert len(calls) == 1
    assert calls[0].json is True
    assert calls[0].auto is True
    assert calls[0].all is True


def test_main_exits_when_stdin_not_a_tty_for_modify(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["modify"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_dispatch_dispatches_modify(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_modify(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_modify", fake_cmd_modify)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["modify"])
    assert len(calls) == 1
    assert calls[0].reset_mb is False


def test_main_dispatch_dispatches_modify_reset_mb(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_modify(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_modify", fake_cmd_modify)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    cli.main(["modify", "--reset-mb"])
    assert len(calls) == 1
    assert calls[0].reset_mb is True


def test_main_dispatch_dispatches_update(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []

    def fake_cmd_update(_args):
        calls.append(_args)

    monkeypatch.setattr(cli, "cmd_update", fake_cmd_update)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    cli.main(["update"])
    assert len(calls) == 1


def test_main_update_works_without_tty(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "cmd_update", lambda _args: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    cli.main(["update"])
    assert "interactive terminal" not in capsys.readouterr().out


def test_cmd_update_reports_scan_summary(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Song"]
    audio["artist"] = ["Artist"]
    audio.save(music / "Artist-Song.mp3")

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    cli.cmd_update(argparse.Namespace())

    out = capsys.readouterr()
    assert "Scanning music files..." in out.err
    assert "Updated: 1 added" in out.out

    cli.cmd_update(argparse.Namespace())
    assert "Already up to date." in capsys.readouterr().out


def test_main_help_works_without_tty(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "download" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-v", "--version"])
def test_main_version_prints_version(monkeypatch, capsys, flag):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main([flag])
    assert exc.value.code == 0
    assert f"smclipy {cli.__version__}" in capsys.readouterr().out


def test_collect_urls_exits_cleanly_on_eof(monkeypatch, capsys, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(EOFError()),
    )
    with pytest.raises(SystemExit) as exc:
        cli.collect_urls()
    assert exc.value.code == 130
    assert "Interrupted" in capsys.readouterr().out


def test_collect_urls_warns_on_unrecognized_input(monkeypatch, capsys, app_settings):
    import smclipy.cli as cli

    inputs = iter(["not a url at all", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    urls = cli.collect_urls()
    assert urls == []
    assert "no recognizable video URLs" in capsys.readouterr().out


def test_collect_urls_still_extracts_valid_urls(monkeypatch, capsys, app_settings):
    import smclipy.cli as cli

    inputs = iter(
        ["garbage https://youtu.be/abcdefghijk", "https://soundcloud.com/a/b", ""]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    urls = cli.collect_urls()
    assert urls == [
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://soundcloud.com/a/b",
    ]


def test_collect_urls_persists_partial_queue_on_interrupt(
    monkeypatch, capsys, app_settings
):
    import smclipy.cli as cli

    inputs = ["https://youtu.be/abcdefghijk"]
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: inputs.pop(0) if inputs else (_ for _ in ()).throw(EOFError()),
    )
    with pytest.raises(SystemExit) as exc:
        cli.collect_urls()
    assert exc.value.code == 130
    assert db.get_pending_urls() == ["https://www.youtube.com/watch?v=abcdefghijk"]
    assert "partial queue" in capsys.readouterr().out


def test_collect_urls_dedupes_repeated_urls(monkeypatch, app_settings):
    import smclipy.cli as cli

    inputs = iter(
        [
            "https://youtu.be/abcdefghijk",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://soundcloud.com/a/b",
            "https://youtu.be/abcdefghijk",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    assert cli.collect_urls() == [
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://soundcloud.com/a/b",
    ]


def test_process_video_reprompts_on_empty_title(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls: list[str] = []

    def fake_prompt_title(default: str = "") -> str:
        calls.append(default)
        return "" if len(calls) == 1 else "Song"

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", fake_prompt_title)
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (
            SaveResult.SAVED,
            app_settings.music_folder / "Artist-Song.mp3",
        ),
    )

    process_video("https://soundcloud.com/artist/track", [])
    assert len(calls) == 2
    assert calls == ["Song", "Song"]


def test_process_video_reprompts_on_empty_authors(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}
    calls: list[str] = []
    defaults: list[str] = []

    def fake_prompt_authors(authors_list: list[str], default: str = "") -> str:
        calls.append("call")
        defaults.append(default)
        return "" if len(calls) == 1 else "Artist"

    def fake_save(
        temp_mp3: Path,
        temp_png: Path | None,
        title: str,
        authors: str,
        album: str,
        **kwargs: object,
    ) -> tuple[SaveResult, Path | None]:
        captured["authors"] = authors
        return (SaveResult.SAVED, None)

    monkeypatch.setattr(
        cli, "download", lambda url, stem="temp": {"title": "Song", "channel": "Artist"}
    )
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(cli, "save_song_temp_to_main", fake_save)

    process_video("https://soundcloud.com/artist/track", [])

    assert len(calls) == 2
    assert defaults == ["Artist", "Artist"]
    assert captured["authors"] == "Artist"


def test_cmd_download_skips_unexpected_errors(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: ["vid-a", "vid-b"])
    monkeypatch.setattr(
        cli,
        "process_video",
        lambda vid, authors, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    cli.cmd_download(argparse.Namespace())

    out = capsys.readouterr().out
    assert "unexpected error" in out
    assert "vid-a" in out
    assert "vid-b" in out
    assert db.get_pending_urls() == ["vid-a", "vid-b"]


def test_cmd_download_cleans_temp_files_on_finish(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.temp_folder.mkdir(parents=True, exist_ok=True)
    (app_settings.temp_folder / "temp.mp3").write_bytes(b"stale")
    (app_settings.temp_folder / "temp.png").write_bytes(b"stale")
    (app_settings.temp_folder / "temp.webm").write_bytes(b"stale")

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: [])

    cli.cmd_download(argparse.Namespace())

    assert not (app_settings.temp_folder / "temp.mp3").exists()
    assert not (app_settings.temp_folder / "temp.png").exists()
    assert not (app_settings.temp_folder / "temp.webm").exists()


def test_cmd_download_survives_processed_recording_error(
    monkeypatch, app_settings, capsys
):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: ["vid-a"])
    monkeypatch.setattr(
        cli, "process_video", lambda vid, authors, **kwargs: SaveResult.SAVED
    )

    def raisy(url, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cli.db, "mark_processed", raisy)

    cli.cmd_download(argparse.Namespace())

    out = capsys.readouterr().out
    assert "could not record" in out


def test_cmd_download_repends_unsaved_videos(monkeypatch, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: ["vid-a"])
    monkeypatch.setattr(
        cli, "process_video", lambda vid, authors, **kwargs: SaveResult.FAILED
    )

    cli.cmd_download(argparse.Namespace())

    assert db.get_pending_urls() == ["vid-a"]


def test_cmd_download_notices_permanently_failed_videos(
    monkeypatch, app_settings, capsys
):
    import smclipy.cli as cli

    app_settings.max_download_attempts = 1
    db.reset_queue(["vid-a"])
    db.requeue(["vid-a"])
    db.requeue(["vid-a"])
    assert db.get_failed_urls() == ["vid-a"]

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: [])

    cli.cmd_download(argparse.Namespace())

    out = capsys.readouterr().out
    assert "permanently dropped" in out
    assert "vid-a" in out
    assert db.get_failed_urls() == ["vid-a"]


def _raises_keyboard_interrupt(vid, authors, **kwargs):
    raise KeyboardInterrupt


def test_cmd_download_interrupt_exits_130(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: ["vid-a", "vid-b"])
    monkeypatch.setattr(cli, "process_video", _raises_keyboard_interrupt)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_download(argparse.Namespace())
    assert exc.value.code == 130
    assert "Interrupted" in capsys.readouterr().out


def test_cmd_download_interrupt_persists_skipped(monkeypatch, app_settings):
    import smclipy.cli as cli
    from smclipy.downloader import VideoDownloadError

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda: ["vid-a", "vid-b"])

    calls: list[str] = []

    def fake_process(vid, authors, **kwargs):
        calls.append(vid)
        if vid == "vid-a":
            raise VideoDownloadError("vid-a", 500)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "process_video", fake_process)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_download(argparse.Namespace())
    assert exc.value.code == 130
    assert sorted(db.get_pending_urls()) == ["vid-a", "vid-b"]


def test_cmd_crop_reembeds_jpg_cover(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_jpg(covers / "Artist-Song.jpg")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_called_once()


def test_cmd_crop_reembeds_webp_cover(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_webp(covers / "Artist-Song.webp")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_called_once()


def test_cmd_crop_skips_corrupt_cover(monkeypatch, app_settings, capsys):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    (covers / "Corrupt-Art.png").write_bytes(b"not an image")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    change_cover.assert_not_called()
    assert "could not analyze" in capsys.readouterr().out


def make_pillarbox_jpg(path) -> None:
    img = Image.new("RGB", (200, 100), "black")
    for x in range(60, 140):
        for y in range(100):
            img.putpixel((x, y), (255, 0, 0))
    img.save(path, "JPEG")


def make_pillarbox_webp(path) -> None:
    img = Image.new("RGB", (200, 100), "black")
    for x in range(60, 140):
        for y in range(100):
            img.putpixel((x, y), (255, 0, 0))
    img.save(path, "WEBP")


def test_process_video_records_song_source(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.temp_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist-Song.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(MINIMAL_MP3)
    write_song_uuid(target)

    monkeypatch.setattr(cli, "download", lambda url, stem="temp": {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(
        cli,
        "save_song_temp_to_main",
        lambda *a, **k: (SaveResult.SAVED, target),
    )

    process_video("https://soundcloud.com/artist/track", [])

    songs = db.list_songs()
    assert len(songs) == 1
    row = songs[0]
    assert row["source_url"] == "https://soundcloud.com/artist/track"
    assert row["current_path"] == "Artist-Song.mp3"
    assert row["title"] == "Song"
    assert row["artists"] == "Artist"
    assert any(e["event"] == "downloaded" for e in db.get_events(row["uuid"]))
    assert db.get_authors() == ["Artist"]


def test_cmd_crop_records_cropped_status(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")
    db.insert_song(current_path="Artist-Song.mp3", title="Song", artists="Artist")

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    assert db.get_song_by_path("Artist-Song.mp3")[0]["cover_status"] == "cropped"
    change_cover.assert_called_once()


def test_cmd_crop_scans_library_before_recording_status(monkeypatch, app_settings):
    from mutagen.easyid3 import EasyID3

    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    mp3 = music / "Artist-Song.mp3"
    mp3.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Song"]
    audio["artist"] = ["Artist"]
    audio.save(mp3)

    change_cover = Mock()
    _patch_crop_flow(monkeypatch, change_cover)

    cmd_crop(argparse.Namespace())

    assert db.get_song_by_path("Artist-Song.mp3")[0]["cover_status"] == "cropped"
    change_cover.assert_called_once()


def test_cmd_crop_records_false_positive(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    make_pillarbox_png(covers / "Artist-Song.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")
    db.insert_song(current_path="Artist-Song.mp3", title="Song", artists="Artist")

    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "crop_image_1_to_1", lambda *a, **k: None)

    change_cover = Mock()
    monkeypatch.setattr(cli, "change_cover", change_cover)

    cmd_crop(argparse.Namespace())

    assert db.get_false_positives() == {"Artist-Song"}
    assert db.get_song_by_path("Artist-Song.mp3")[0]["cover_status"] == "false_positive"
    change_cover.assert_not_called()

    cmd_crop(argparse.Namespace())
    change_cover.assert_not_called()
    assert "No images to crop" in capsys.readouterr().out


def test_cmd_crop_records_not_pillarbox(monkeypatch, app_settings):
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "red").save(covers / "Artist-Song.png")
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(b"audio")
    db.insert_song(current_path="Artist-Song.mp3", title="Song", artists="Artist")

    cmd_crop(argparse.Namespace())

    assert db.get_song_by_path("Artist-Song.mp3")[0]["cover_status"] == "not_pillarbox"


def test_main_dispatches_playlist(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_playlist", lambda args: calls.append(args))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    cli.main(["playlist", "list"])
    assert len(calls) == 1
    assert calls[0].playlist_action == "list"


def test_main_exits_without_tty_for_playlist_add(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["playlist", "add", "Chill"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_exits_without_tty_for_playlist_remove(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["playlist", "remove", "Chill"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_playlist_list_runs_without_tty(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "cmd_playlist", lambda _args: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    cli.main(["playlist", "list"])
    assert "interactive terminal" not in capsys.readouterr().out


def test_main_playlist_import_runs_without_tty(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_playlist", lambda args: calls.append(args))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    cli.main(["playlist", "import", "mix.m3u"])
    assert len(calls) == 1
    assert calls[0].playlist_action == "import"


def test_main_directories_flag_does_not_shadow_command(monkeypatch, app_settings):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_update", lambda args: calls.append(args))
    cli.main(["-d", "update"])
    assert len(calls) == 1


def test_main_tag_all_auto_runs_without_tty(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_tag", lambda args: calls.append(args))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    cli.main(["tag", "--auto", "--all"])
    assert len(calls) == 1
    assert calls[0].all is True
    assert "interactive terminal" not in capsys.readouterr().out


def test_main_tag_all_without_auto_requires_tty(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["tag", "--all"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_download_batch_runs_without_tty(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_download", lambda args: calls.append(args))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    cli.main(["download", "--batch", "urls.txt", "--no-prompt"])
    assert len(calls) == 1
    assert calls[0].batch == "urls.txt"
    assert calls[0].no_prompt is True
    assert "interactive terminal" not in capsys.readouterr().out


def test_main_download_batch_without_no_prompt_requires_tty(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["download", "--batch", "urls.txt"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_main_restore_dispatches(monkeypatch, capsys):
    import smclipy.cli as cli

    calls = []
    monkeypatch.setattr(cli, "cmd_restore", lambda args: calls.append(args))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    cli.main(["restore"])
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert "interactive terminal" not in captured.out + captured.err


def test_main_restore_requires_tty(monkeypatch, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["restore"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "interactive terminal" in captured.out + captured.err


def test_cmd_update_json_reports_changes(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    (music / "Artist-Song.mp3").write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Song"]
    audio["artist"] = ["Artist"]
    audio.save(music / "Artist-Song.mp3")

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    cli.cmd_update(argparse.Namespace(json=True))

    captured = capsys.readouterr()
    assert "Scanning music files..." in captured.err
    payload = json.loads(captured.out)
    assert payload["added"] == 1
    assert payload["renamed"] == 0
    assert payload["missing"] == 0
    assert len(payload["songs"]) == 1
    song = payload["songs"][0]
    assert song["status"] == "added"
    assert song["path"] == "Artist-Song.mp3"
    assert song["old_path"] is None
    assert song["uuid"]


def test_cmd_update_missing_music_folder_exits(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_update(argparse.Namespace(json=False))
    assert exc.value.code == 1
    assert "Music folder not found" in capsys.readouterr().out


def test_cmd_update_json_missing_music_folder_exits(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_update(argparse.Namespace(json=True))
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "music_folder_not_found"


def test_collect_urls_interrupt_preserves_prior_pending(
    monkeypatch, app_settings, capsys
):
    import smclipy.cli as cli

    db.reset_queue(["old-a", "old-b"])
    replies = iter(["https://youtu.be/abcdefghijk"])

    def fake_input(_prompt=""):
        try:
            return next(replies)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr("builtins.input", fake_input)

    with pytest.raises(SystemExit) as exc:
        cli.collect_urls()
    assert exc.value.code == 130
    pending = db.get_pending_urls()
    assert "old-a" in pending and "old-b" in pending
    assert "https://www.youtube.com/watch?v=abcdefghijk" in pending


def test_collect_batch_reads_and_dedupes_urls(app_settings, tmp_path, capsys):
    import smclipy.cli as cli

    batch = tmp_path / "urls.txt"
    batch.write_text(
        "\n# a comment\nhttps://youtu.be/abcdefghijk\n"
        "https://www.youtube.com/watch?v=abcdefghijk\n",
        encoding="utf-8",
    )

    result = cli._collect_batch(batch)

    assert result == ["https://www.youtube.com/watch?v=abcdefghijk"]
    assert db.get_pending_urls() == ["https://www.youtube.com/watch?v=abcdefghijk"]


def test_collect_batch_missing_file_exits(monkeypatch, app_settings, tmp_path, capsys):
    import smclipy.cli as cli

    with pytest.raises(SystemExit) as exc:
        cli._collect_batch(tmp_path / "nope.txt")
    assert exc.value.code == 1
    assert "could not read batch file" in capsys.readouterr().err
