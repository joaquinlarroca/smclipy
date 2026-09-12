import pytest

import smclipy.config as config
import smclipy.musicbrainz as musicbrainz


@pytest.fixture
def app_settings(tmp_path, monkeypatch):
    s = config.Settings(
        {"name": "smclipy", "path_to_music_folder": str(tmp_path / "Music")},
    )
    monkeypatch.setattr(config, "_settings", s)
    return s


@pytest.fixture(autouse=True)
def _no_musicbrainz_throttle(monkeypatch):
    monkeypatch.setattr(musicbrainz, "_MIN_REQUEST_INTERVAL", 0.0)
