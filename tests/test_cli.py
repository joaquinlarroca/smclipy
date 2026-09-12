import argparse
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from smclipy.cli import cmd_crop, filter_processed_ids, process_video
from smclipy.metadata import SaveResult
from smclipy.storage import read_lines


def test_filter_processed_ids_keeps_unprocessed():
    assert filter_processed_ids(["A", "B", "C"], ["A"]) == ["B", "C"]


def test_filter_processed_ids_empty_processed():
    assert filter_processed_ids(["A", "B"], []) == ["A", "B"]


def test_filter_processed_ids_all_processed():
    assert filter_processed_ids(["A", "B"], ["B", "A"]) == []


def test_filter_processed_ids_preserves_order():
    assert filter_processed_ids(["C", "A", "B"], ["A"]) == ["C", "B"]


def test_collect_new_queue_resets_state(monkeypatch, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "collect_urls", lambda s: ["A", "B"])
    pending_ids = cli._collect_new_queue(app_settings)
    assert pending_ids == ["A", "B"]
    assert read_lines(app_settings.pending_ids_file) == ["A", "B"]
    assert read_lines(app_settings.processed_ids_file) == []


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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

    process_video("https://soundcloud.com/artist/track", [])

    assert not (temp / "temp.mp3").exists()
    assert not (temp / "temp.png").exists()
    assert not (temp / "yt-abcdefghijk.mp3").exists()
    assert not (temp / "yt-abcdefghijk.webp").exists()
    assert not (temp / "tag-cover-rec-1.jpg").exists()
    assert get_image_from_file_calls == [("sc-artist-track.mp3", "sc-artist-track")]
    assert read_lines(app_settings.authors_file) == ["Artist"]
    assert read_lines(app_settings.songs_info) == ["Song - Artist"]


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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

    process_video("https://soundcloud.com/artist/track", [])

    prompt_crop_called.assert_not_called()
    assert "Already 1:1" in capsys.readouterr().out


def test_process_video_prefills_corrected_authors(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.authors_file.parent.mkdir(parents=True, exist_ok=True)
    app_settings.authors_file.write_text("author1\n", encoding="utf-8")

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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "author1, author2"
    assert read_lines(app_settings.authors_file) == ["author1", "author2"]


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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "Great Album"


def test_process_video_passes_album_to_save(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_save(temp_mp3, temp_png, title, authors, album):
        captured["album"] = album
        return SaveResult.SAVED

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
        cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.FAILED
    )

    process_video("https://soundcloud.com/artist/track", [])

    assert read_lines(app_settings.authors_file) == []
    assert read_lines(app_settings.songs_info) == []


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
    assert f"Authors:     {(music / 'smclipy' / 'authors.txt').resolve()}" in out
    assert f"Songs info:  {(music / 'smclipy' / 'songs_info.txt').resolve()}" in out


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
        cli.collect_urls(app_settings)
    assert exc.value.code == 130
    assert "Interrupted" in capsys.readouterr().out


def test_collect_urls_warns_on_unrecognized_input(monkeypatch, capsys, app_settings):
    import smclipy.cli as cli

    inputs = iter(["not a url at all", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    urls = cli.collect_urls(app_settings)
    assert urls == []
    assert "no recognizable video URLs" in capsys.readouterr().out


def test_collect_urls_still_extracts_valid_urls(monkeypatch, capsys, app_settings):
    import smclipy.cli as cli

    inputs = iter(
        ["garbage https://youtu.be/abcdefghijk", "https://soundcloud.com/a/b", ""]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    urls = cli.collect_urls(app_settings)
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
        cli.collect_urls(app_settings)
    assert exc.value.code == 130
    assert read_lines(app_settings.pending_ids_file) == [
        "https://www.youtube.com/watch?v=abcdefghijk"
    ]
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
    assert cli.collect_urls(app_settings) == [
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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: SaveResult.SAVED)

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
    ) -> SaveResult:
        captured["authors"] = authors
        return SaveResult.SAVED

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
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: ["vid-a", "vid-b"])
    monkeypatch.setattr(
        cli,
        "process_video",
        lambda vid, authors: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    cli.cmd_download(argparse.Namespace())

    out = capsys.readouterr().out
    assert "unexpected error" in out
    assert "vid-a" in out
    assert "vid-b" in out
    assert read_lines(app_settings.processed_ids_file) == []


def test_cmd_download_cleans_temp_files_on_finish(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.temp_folder.mkdir(parents=True, exist_ok=True)
    (app_settings.temp_folder / "temp.mp3").write_bytes(b"stale")
    (app_settings.temp_folder / "temp.png").write_bytes(b"stale")
    (app_settings.temp_folder / "temp.webm").write_bytes(b"stale")

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: [])

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
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: ["vid-a"])
    monkeypatch.setattr(cli, "process_video", lambda vid, authors: SaveResult.SAVED)

    def raisy(path, lines):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "append_unique_lines", raisy)

    cli.cmd_download(argparse.Namespace())

    out = capsys.readouterr().out
    assert "could not record" in out
    assert "could not persist" in out


def test_cmd_download_repends_unsaved_videos(monkeypatch, app_settings):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: ["vid-a"])
    monkeypatch.setattr(cli, "process_video", lambda vid, authors: SaveResult.FAILED)

    cli.cmd_download(argparse.Namespace())

    assert read_lines(app_settings.processed_ids_file) == []
    assert read_lines(app_settings.pending_ids_file) == ["vid-a"]


def _raises_keyboard_interrupt(vid, authors):
    raise KeyboardInterrupt


def test_cmd_download_interrupt_exits_130(monkeypatch, app_settings, capsys):
    import smclipy.cli as cli

    monkeypatch.setattr(cli, "init", lambda: app_settings)
    monkeypatch.setattr(cli, "scan_library", lambda: None)
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: ["vid-a", "vid-b"])
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
    monkeypatch.setattr(cli, "get_all_names", lambda: [])
    monkeypatch.setattr(cli, "prompt_resume", lambda: False)
    monkeypatch.setattr(cli, "collect_urls", lambda s: ["vid-a", "vid-b"])

    calls: list[str] = []

    def fake_process(vid, authors):
        calls.append(vid)
        if vid == "vid-a":
            raise VideoDownloadError("vid-a", 500)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "process_video", fake_process)

    with pytest.raises(SystemExit) as exc:
        cli.cmd_download(argparse.Namespace())
    assert exc.value.code == 130
    pending = read_lines(app_settings.pending_ids_file)
    assert "vid-a" in pending
    assert "Interrupted" not in pending
    assert read_lines(app_settings.processed_ids_file) == []


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
