import json

import pytest

from smclipy.config import DEFAULT_CONFIG, Settings, _load_raw_config


def test_load_raw_config_missing_creates_default(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 0
    assert cfg.is_file()
    assert json.loads(cfg.read_text(encoding="utf-8")) == DEFAULT_CONFIG


def test_load_raw_config_invalid_json_exits_cleanly(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 1
    assert "could not read config" in capsys.readouterr().out


def test_load_raw_config_valid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    assert _load_raw_config() == DEFAULT_CONFIG


def test_load_raw_config_rejects_non_object_json(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[]", encoding="utf-8")
    monkeypatch.setattr("smclipy.config.CONFIG_PATH", cfg)

    with pytest.raises(SystemExit) as exc:
        _load_raw_config()

    assert exc.value.code == 1
    assert "must contain a JSON object" in capsys.readouterr().out


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
