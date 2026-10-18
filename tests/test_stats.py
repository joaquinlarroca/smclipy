import argparse
import json

import pytest

import smclipy.db as db
from smclipy.stats import _format_duration, cmd_stats


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _seed() -> None:
    db.insert_song(
        current_path="a.mp3",
        title="A",
        artists="Artist One",
        album="Album X",
        duration=100.0,
    )
    db.insert_song(
        current_path="b.mp3",
        title="B",
        artists="Artist One, Artist Two",
        album="Album X",
        duration=200.0,
    )
    db.insert_song(
        current_path="c.mp3",
        title="C",
        artists="Artist Two",
        album="Album Y",
        duration=300.0,
    )
    gone = db.insert_song(current_path="d.mp3", title="D", duration=50.0)
    db.mark_song_missing(gone)


def test_format_duration():
    assert _format_duration(0) == "0s"
    assert _format_duration(30) == "30s"
    assert _format_duration(90) == "1m 30s"
    assert _format_duration(3600) == "1h 0m"
    assert _format_duration(3660) == "1h 1m"
    assert _format_duration(86400 + 4 * 3600 + 12 * 60) == "1d 4h 12m"


def test_stats_reports_totals_and_rankings(app_settings, capsys):
    _seed()
    cmd_stats(_args(json=False, top=10, no_scan=True))
    out = capsys.readouterr().out
    assert "Total songs: 3" in out
    assert "Total albums: 2" in out
    assert "Total artists: 2" in out
    assert "Total playtime: 10m 0s" in out
    assert "Album X (2)" in out
    assert "Album Y (1)" in out
    assert "Artist One (2)" in out
    assert "Artist Two (2)" in out
    assert "Album X" in out


def test_stats_excludes_missing_songs(app_settings, capsys):
    _seed()
    cmd_stats(_args(json=True, top=10, no_scan=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_songs"] == 3
    assert payload["total_albums"] == 2
    assert payload["total_artists"] == 2
    assert payload["total_duration_seconds"] == 600


def test_stats_json_limits_rankings(app_settings, capsys):
    _seed()
    cmd_stats(_args(json=True, top=1, no_scan=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_songs"] == 3
    assert payload["total_duration_seconds"] == 600
    assert payload["total_duration_human"] == "10m 0s"
    assert payload["top_albums"] == [{"album": "Album X", "count": 2}]
    assert payload["top_artists"] == [{"artist": "Artist One", "count": 2}]


def test_stats_counts_unknown_durations(app_settings, capsys):
    db.insert_song(current_path="a.mp3", title="A", duration=None)
    db.insert_song(current_path="b.mp3", title="B", duration=60.0)
    cmd_stats(_args(json=True, top=10, no_scan=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_songs"] == 2
    assert payload["total_albums"] == 0
    assert payload["total_artists"] == 0
    assert payload["total_duration_seconds"] == 60
    assert payload["unknown_duration"] == 1


def test_stats_scans_by_default(app_settings, monkeypatch):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    calls: list[int] = []
    monkeypatch.setattr("smclipy.stats.scan_library", lambda *a, **k: calls.append(1))
    cmd_stats(_args(json=True, top=10, no_scan=False))
    assert calls == [1]


def test_stats_no_scan_skips_scan(app_settings, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr("smclipy.stats.scan_library", lambda *a, **k: calls.append(1))
    cmd_stats(_args(json=True, top=10, no_scan=True))
    assert calls == []


def test_stats_missing_music_folder_fails_without_scanning(app_settings, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr("smclipy.stats.scan_library", lambda *a, **k: calls.append(1))
    with pytest.raises(SystemExit) as excinfo:
        cmd_stats(_args(json=False, top=10, no_scan=False))
    assert excinfo.value.code == 1
    assert calls == []


def test_stats_missing_music_folder_reports_json_error(
    app_settings, monkeypatch, capsys
):
    monkeypatch.setattr("smclipy.stats.scan_library", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        cmd_stats(_args(json=True, top=10, no_scan=False))
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "music_folder_not_found"


def test_stats_top_zero_clamps_to_one(app_settings, capsys):
    _seed()
    cmd_stats(_args(json=True, top=0, no_scan=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["top_albums"] == [{"album": "Album X", "count": 2}]
    assert payload["top_artists"] == [{"artist": "Artist One", "count": 2}]
