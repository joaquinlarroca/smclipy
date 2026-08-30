import argparse
from unittest.mock import Mock

from PIL import Image

from smclipy.cli import cmd_crop, filter_processed_ids, process_video
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

    monkeypatch.setattr(cli, "collect_urls", lambda: ["A", "B"])
    pending_ids, processed_ids = cli._collect_new_queue(app_settings)
    assert pending_ids == ["A", "B"]
    assert processed_ids == []
    assert read_lines(app_settings.pending_ids_file) == ["A", "B"]
    assert read_lines(app_settings.processed_ids_file) == []


def test_process_video_deletes_stale_temp_files(monkeypatch, app_settings):
    import smclipy.cli as cli

    temp = app_settings.temp_folder
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "temp.mp3").write_bytes(b"old-mp3")
    (temp / "temp.png").write_bytes(b"old-png")

    monkeypatch.setattr(cli, "download", lambda url: {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: True)

    process_video("https://soundcloud.com/artist/track", [])

    assert not (temp / "temp.mp3").exists()
    assert not (temp / "temp.png").exists()
    assert read_lines(app_settings.authors_file) == ["Artist"]
    assert read_lines(app_settings.songs_info) == ["Song - Artist"]


def test_process_video_skips_crop_prompt_when_already_1_to_1(
    monkeypatch, app_settings, capsys
):
    import smclipy.cli as cli

    temp = app_settings.temp_folder
    temp.mkdir(parents=True, exist_ok=True)
    (temp / "temp.png").write_bytes(b"image")

    monkeypatch.setattr(cli, "download", lambda url: {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: True)
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
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: True)

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

    monkeypatch.setattr(cli, "download", lambda url: {"channel": "Author 1"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: True)

    process_video("https://soundcloud.com/artist/track", ["author1"])

    assert captured["default"] == "author1"


def test_process_video_prefills_album(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_prompt_album(default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(cli, "download", lambda url: {"album": "Great Album"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", fake_prompt_album)
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: True)

    process_video("https://soundcloud.com/artist/track", [])

    assert captured["default"] == "Great Album"


def test_process_video_passes_album_to_save(monkeypatch, app_settings):
    import smclipy.cli as cli

    captured = {}

    def fake_save(temp_mp3, temp_png, title, authors, album):
        captured["album"] = album
        return True

    monkeypatch.setattr(cli, "download", lambda url: {"album": "Great Album"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
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

    monkeypatch.setattr(cli, "download", lambda url: {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "prompt_album", lambda *a, **k: "Album")
    monkeypatch.setattr(cli, "show_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: False)

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

    cli.main(["directories"])

    out = capsys.readouterr().out
    assert f"Config dir:  {config_file.parent.resolve()}" in out
    assert f"Music:       {music.resolve()}" in out
    assert f"Library:     {(music / 'smclipy').resolve()}" in out
    assert f"Temp:        {(music / 'smclipy' / '.temp').resolve()}" in out
    assert f"Covers:      {(music / 'smclipy' / 'covers').resolve()}" in out
