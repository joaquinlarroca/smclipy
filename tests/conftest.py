import pytest

import smclipy.config as config


@pytest.fixture
def app_settings(tmp_path, monkeypatch):
    s = config.Settings(
        {"name": "smclipy", "path_to_music_folder": str(tmp_path / "Music")},
    )
    monkeypatch.setattr(config, "_settings", s)
    return s
