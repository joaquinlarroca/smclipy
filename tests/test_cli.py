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

    monkeypatch.setattr(cli, "collect_video_ids", lambda: ["A", "B"])
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

    monkeypatch.setattr(cli, "download", lambda video_id: {"title": "Song"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "display_image", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "show_yt_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", lambda *a, **k: "Artist")
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: None)

    process_video("A" * 11, [])

    assert not (temp / "temp.mp3").exists()
    assert not (temp / "temp.png").exists()
    assert read_lines(app_settings.authors_file) == ["Artist"]
    assert read_lines(app_settings.songs_info) == ["Song - Artist"]


def test_process_video_prefills_corrected_authors(monkeypatch, app_settings):
    import smclipy.cli as cli

    app_settings.authors_file.parent.mkdir(parents=True, exist_ok=True)
    app_settings.authors_file.write_text("author1\n", encoding="utf-8")

    captured = {}

    def fake_prompt_authors(authors_list, default=""):
        captured["default"] = default
        return default

    monkeypatch.setattr(cli, "download", lambda video_id: {"channel": "Author 1"})
    monkeypatch.setattr(cli, "get_image_from_file", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_crop", lambda *a, **k: False)
    monkeypatch.setattr(cli, "prompt_title", lambda *a, **k: "Song")
    monkeypatch.setattr(cli, "show_yt_video_info", lambda *a, **k: None)
    monkeypatch.setattr(cli, "prompt_authors", fake_prompt_authors)
    monkeypatch.setattr(cli, "save_song_temp_to_main", lambda *a, **k: None)

    process_video("A" * 11, ["author1"])

    assert captured["default"] == "author1"


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
