import json
import os
from pathlib import Path

import pytest

from smclipy.config import DEFAULT_CONFIG, Settings, _load_raw_config


def test_load_raw_config_missing_creates_default(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 1
    assert cfg.is_file()
    assert json.loads(cfg.read_text(encoding="utf-8")) == DEFAULT_CONFIG


def test_load_raw_config_missing_no_create_returns_defaults(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    assert _load_raw_config(create_if_missing=False) == DEFAULT_CONFIG
    assert not cfg.exists()


def test_load_raw_config_invalid_json_exits_cleanly(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 1
    assert "could not read config" in capsys.readouterr().err


def test_load_raw_config_valid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    assert _load_raw_config() == DEFAULT_CONFIG


def test_load_raw_config_backfills_missing_fields(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"name": "smclipy", "path_to_music_folder": "./Music"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    raw = _load_raw_config()

    assert raw == DEFAULT_CONFIG
    assert json.loads(cfg.read_text(encoding="utf-8")) == DEFAULT_CONFIG


def test_load_raw_config_backfill_keeps_custom_values(tmp_path, monkeypatch):
    custom = {
        "name": "custom",
        "path_to_music_folder": "./Custom",
        "description_max_lines": 2,
        "tag_fields": ["title"],
    }
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(custom), encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    expected = {**DEFAULT_CONFIG, **custom}
    raw = _load_raw_config()

    assert raw == expected
    assert json.loads(cfg.read_text(encoding="utf-8")) == expected


def test_load_raw_config_rejects_non_object_json(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[]", encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 1
    assert "must contain a JSON object" in capsys.readouterr().err


def test_settings_falls_back_on_bad_description_max_lines(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "description_max_lines": "abc",
        }
    )
    assert s.description_max_lines == 5


def test_settings_uses_valid_description_max_lines(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "description_max_lines": 3,
        }
    )
    assert s.description_max_lines == 3


def test_settings_rejects_non_integral_description_max_lines(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "description_max_lines": 2.5,
        }
    )
    assert s.description_max_lines == 5


def test_settings_clamps_negative_description_max_lines(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "description_max_lines": -3,
        }
    )
    assert s.description_max_lines == 0


def test_settings_rejects_bool_description_max_lines(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "description_max_lines": True,
        }
    )
    assert s.description_max_lines == 5


def test_settings_default_tag_fields(tmp_path):
    s = Settings({"name": "smclipy", "path_to_music_folder": str(tmp_path)})
    assert s.tag_fields == DEFAULT_CONFIG["tag_fields"]


def test_settings_filters_invalid_tag_fields(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "tag_fields": ["title", "bogus", "cover", 5],
        }
    )
    assert s.tag_fields == ["title", "cover"]


def test_settings_rejects_non_list_tag_fields(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "tag_fields": "title",
        }
    )
    assert s.tag_fields == []


def test_settings_default_write_album_if_same_as_title(tmp_path):
    s = Settings({"name": "smclipy", "path_to_music_folder": str(tmp_path)})
    assert s.write_album_if_same_as_title is False


def test_settings_honors_write_album_if_same_as_title(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "write_album_if_same_as_title": True,
        }
    )
    assert s.write_album_if_same_as_title is True


def test_settings_rejects_non_bool_write_album_if_same_as_title(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "write_album_if_same_as_title": "true",
        }
    )
    assert s.write_album_if_same_as_title is False


def test_settings_default_clean_unwanted_tags(tmp_path):
    s = Settings({"name": "smclipy", "path_to_music_folder": str(tmp_path)})
    assert s.clean_unwanted_tags is False


def test_settings_honors_clean_unwanted_tags(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "clean_unwanted_tags": True,
        }
    )
    assert s.clean_unwanted_tags is True


def test_settings_rejects_non_bool_clean_unwanted_tags(tmp_path):
    s = Settings(
        {
            "name": "smclipy",
            "path_to_music_folder": str(tmp_path),
            "clean_unwanted_tags": "yes",
        }
    )
    assert s.clean_unwanted_tags is False


def test_settings_coerces_non_string_music_folder(capsys):
    s = Settings({"name": "smclipy", "path_to_music_folder": None})
    assert s.music_folder == Path(".")
    assert "path_to_music_folder" in capsys.readouterr().err


def test_settings_coerces_blank_music_folder(tmp_path, capsys):
    s = Settings({"name": "smclipy", "path_to_music_folder": "   "})
    assert s.music_folder == Path(".")
    assert "path_to_music_folder" in capsys.readouterr().err


def test_settings_falls_back_on_invalid_name(tmp_path):
    s = Settings({"name": "///", "path_to_music_folder": str(tmp_path)})
    assert s.script_folder == tmp_path / "smclipy"


def test_settings_falls_back_on_non_string_name(tmp_path):
    s = Settings({"name": 5, "path_to_music_folder": str(tmp_path)})
    assert s.script_folder == tmp_path / "smclipy"


def test_settings_default_max_download_attempts(tmp_path):
    s = Settings({"path_to_music_folder": str(tmp_path)})
    assert s.max_download_attempts == 3


def test_settings_parses_max_download_attempts(tmp_path):
    s = Settings({"path_to_music_folder": str(tmp_path), "max_download_attempts": "5"})
    assert s.max_download_attempts == 5


def test_settings_rejects_bogus_max_download_attempts(tmp_path, capsys):
    s = Settings(
        {"path_to_music_folder": str(tmp_path), "max_download_attempts": "often"}
    )
    assert s.max_download_attempts == 3
    assert "max_download_attempts" in capsys.readouterr().err


def test_settings_default_cookies_empty(tmp_path):
    s = Settings({"path_to_music_folder": str(tmp_path)})
    assert s.cookies == ""
    assert s.cookies_from_browser == ""


def test_settings_parses_cookies(tmp_path):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": f" {cookies_file} ",
        }
    )
    assert s.cookies == str(cookies_file)


def test_settings_parses_cookies_from_browser(tmp_path):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies_from_browser": " firefox ",
        }
    )
    assert s.cookies_from_browser == "firefox"


def test_settings_expands_tilde_in_cookie_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": "~/cookies.txt",
        }
    )
    assert s.cookies == str(cookies_file)


def test_settings_rejects_non_string_cookies(tmp_path, capsys):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": 5,
            "cookies_from_browser": ["chrome"],
        }
    )
    assert s.cookies == ""
    assert s.cookies_from_browser == ""
    err = capsys.readouterr().err
    assert "cookies" in err
    assert "cookies_from_browser" in err


def test_settings_rejects_blank_cookies(tmp_path, capsys):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": "   ",
            "cookies_from_browser": "  ",
        }
    )
    assert s.cookies == ""
    assert s.cookies_from_browser == ""
    err = capsys.readouterr().err
    assert "'cookies' is blank" in err
    assert "'cookies_from_browser' is blank" in err


def test_settings_rejects_missing_cookie_file(tmp_path, capsys):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": str(tmp_path / "does-not-exist.txt"),
        }
    )
    assert s.cookies == ""
    assert "does not exist or is not readable" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_settings_rejects_unreadable_cookie_file(tmp_path, capsys):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    cookies_file.chmod(0)
    try:
        s = Settings(
            {"path_to_music_folder": str(tmp_path), "cookies": str(cookies_file)}
        )
        assert s.cookies == ""
        assert "does not exist or is not readable" in capsys.readouterr().err
    finally:
        cookies_file.chmod(0o600)


def test_settings_rejects_unsupported_browser(tmp_path, capsys):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies_from_browser": "firfox",
        }
    )
    assert s.cookies_from_browser == ""
    assert "not supported by yt-dlp" in capsys.readouterr().err


def test_settings_accepts_browser_with_profile_suffix(tmp_path):
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies_from_browser": "firefox:default",
        }
    )
    assert s.cookies_from_browser == "firefox:default"


def test_settings_cookie_file_wins_over_browser(tmp_path, capsys):
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("", encoding="utf-8")
    s = Settings(
        {
            "path_to_music_folder": str(tmp_path),
            "cookies": str(cookies_file),
            "cookies_from_browser": "chrome",
        }
    )
    assert s.cookies == str(cookies_file)
    assert s.cookies_from_browser == ""
    assert "cookies_from_browser" in capsys.readouterr().err
