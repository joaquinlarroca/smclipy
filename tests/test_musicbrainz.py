import io
from unittest.mock import Mock

from PIL import Image

import smclipy.musicbrainz as mb


def test_configure_agent_identifies_app_with_contact(monkeypatch):
    captured = {}
    monkeypatch.setattr(mb, "_AGENT_CONFIGURED", False)
    monkeypatch.setattr(
        mb.musicbrainzngs,
        "set_useragent",
        lambda app, version, contact: captured.update(
            app=app, version=version, contact=contact
        ),
    )
    mb._configure_agent()
    assert captured["app"] == "smclipy"
    assert captured["version"] == mb.__version__
    assert captured["contact"].startswith("https://")


def test_configure_agent_runs_once():
    mb._AGENT_CONFIGURED = True
    original = mb.musicbrainzngs.set_useragent
    try:
        mb.musicbrainzngs.set_useragent = Mock()
        mb._configure_agent()
        mb.musicbrainzngs.set_useragent.assert_not_called()
    finally:
        mb.musicbrainzngs.set_useragent = original


def sample_recording(
    *,
    title="Comfortably Numb",
    artist="Pink Floyd",
    score="100",
    disambiguation="",
    status="Official",
    album="The Wall",
    date="1979-11-30",
) -> dict:
    return {
        "id": "rec-1",
        "title": title,
        "ext:score": score,
        "disambiguation": disambiguation,
        "artist-credit": [{"name": artist}],
        "artist-credit-phrase": artist,
        "release-list": [
            {
                "id": "rel-1",
                "title": album,
                "status": status,
                "date": date,
                "artist-credit-phrase": artist,
                "release-group": {
                    "id": "rg-1",
                    "title": album,
                    "primary-type": "Album",
                },
                "medium-list": [
                    {
                        "position": "1",
                        "track-list": [{"number": "4", "title": title}],
                    }
                ],
            }
        ],
    }


def test_search_recordings_empty_title_returns_none(monkeypatch):
    called = []
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.search_recordings",
        lambda *a, **k: called.append(True) or {},
    )
    assert mb.search_recordings("   ", ["Artist"]) == []
    assert mb.search_recordings("", []) == []
    assert called == []


def test_search_recordings_returns_empty_on_webservice_error(monkeypatch):
    def boom(*args, **kwargs):
        raise mb.musicbrainzngs.NetworkError("boom")

    monkeypatch.setattr("smclipy.musicbrainz.musicbrainzngs.search_recordings", boom)
    assert mb.search_recordings("Title", ["Artist"]) == []


def test_search_recordings_strips_quotes_from_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.search_recordings",
        lambda **kwargs: seen.update(kwargs) or {"recording-list": []},
    )
    mb.search_recordings('No "Problem"', ['"Artist" Name'], album='Say "Hello"')
    assert (
        seen["query"] == '"No Problem" AND artist:"Artist Name" AND release:"Say Hello"'
    )


def test_search_recordings_parses_and_ranks(monkeypatch):
    cover = sample_recording(title="Comfortably Numb (Pink Floyd cover)")
    listing = sample_recording(
        disambiguation="live, 1980-02-01: concert",
        album="Live '80",
        date="1996",
    )
    studio = sample_recording()

    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.search_recordings",
        lambda **kwargs: {"recording-list": [cover, listing, studio]},
    )

    matches = mb.search_recordings("Comfortably Numb", ["Pink Floyd"])

    assert len(matches) == 3
    first = matches[0]
    assert first.title == "Comfortably Numb"
    assert first.artists == ["Pink Floyd"]
    assert first.album == "The Wall"
    assert first.date == "1979-11-30"
    assert first.album_artist == "Pink Floyd"
    assert first.track_number == "4"
    assert first.recording_id == "rec-1"
    assert first.release_group_id == "rg-1"


def test_match_missing_release_has_empty_fields(monkeypatch):
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.search_recordings",
        lambda **kwargs: {
            "recording-list": [
                {
                    "id": "rec-9",
                    "title": "Solo",
                    "ext:score": "80",
                    "disambiguation": "",
                }
            ]
        },
    )
    matches = mb.search_recordings("Solo", [])
    assert len(matches) == 1
    match = matches[0]
    assert match.album == ""
    assert match.date == ""
    assert match.album_artist == ""
    assert match.track_number == ""
    assert match.release_group_id is None


def test_rank_recording_tolerates_non_numeric_score():
    rec = sample_recording(score="N/A")
    rank = mb._rank_recording(rec)
    assert rank[2] == 0


def test_rank_recording_prefers_official_status():
    studio = sample_recording(status="Official")
    promo = sample_recording(status="Promotion")
    assert mb._rank_recording(studio) > mb._rank_recording(promo)

    bootleg = sample_recording(status="Bootleg")
    assert mb._rank_recording(studio) > mb._rank_recording(bootleg)


def test_search_recordings_survives_non_numeric_scores(monkeypatch):
    bad = sample_recording(title="Bad Score", score=str(1234.5))
    good = sample_recording(title="Plain", score="100")

    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.search_recordings",
        lambda **kwargs: {"recording-list": [bad, good]},
    )

    matches = mb.search_recordings("A Song", ["Artist"])
    assert len(matches) == 2


def test_fetch_cover_art_writes_image(monkeypatch, tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "JPEG")
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(return_value=buf.getvalue()),
    )
    dest = tmp_path / "cover.jpg"
    result = mb.fetch_cover_art("rg-1", dest)
    assert result == dest
    assert dest.read_bytes() == buf.getvalue()


def test_fetch_cover_art_returns_none_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(side_effect=mb.musicbrainzngs.ResponseError("Not found")),
    )
    dest = tmp_path / "cover.jpg"
    assert mb.fetch_cover_art("rg-1", dest) is None
    assert not dest.exists()


def test_fetch_cover_art_returns_none_on_empty_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(return_value=b""),
    )
    dest = tmp_path / "cover.jpg"
    assert mb.fetch_cover_art("rg-1", dest) is None
    assert not dest.exists()


def test_cover_art_error_type_is_webservice(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(side_effect=mb.musicbrainzngs.WebServiceError("boom")),
    )
    assert mb.fetch_cover_art("rg-1", tmp_path / "cover.jpg") is None


def test_fetch_cover_art_sniffs_real_extension(monkeypatch, tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, "PNG")
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(return_value=buf.getvalue()),
    )
    dest = tmp_path / "cover.jpg"
    result = mb.fetch_cover_art("rg-1", dest)
    assert result == tmp_path / "cover.png"
    assert result.read_bytes() == buf.getvalue()


def test_fetch_cover_art_skips_invalid_image_data(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "smclipy.musicbrainz.musicbrainzngs.get_release_group_image_front",
        Mock(return_value=b"not-an-image"),
    )
    dest = tmp_path / "cover.jpg"
    assert mb.fetch_cover_art("rg-1", dest) is None
    assert not dest.exists()


def test_artist_credit_prefers_canonical_artist_name():
    rec = {
        "artist-credit": [
            {"name": "Credit Name", "artist": {"name": "Canonical Name"}},
            {"artist": {"name": "Second"}},
            {"name": "Plain Only"},
        ]
    }
    assert mb._artist_credit(rec) == ["Canonical Name", "Second", "Plain Only"]


def test_release_details_prefers_track_on_second_medium():
    rec = sample_recording()
    release = rec["release-list"][0]
    release["medium-list"] = [
        {"position": "1", "track-list": [{"number": "1", "title": "First"}]},
        {
            "position": "2",
            "track-list": [{"number": "4", "title": "Comfortably Numb"}],
        },
    ]
    details = mb._release_details(release, "Comfortably Numb")
    assert details[3] == "4"


def test_release_details_does_not_guess_track_number():
    rec = sample_recording()
    release = rec["release-list"][0]
    details = mb._release_details(release, "Something Else")
    assert details[3] == ""


def test_throttle_enforces_minimum_interval(monkeypatch):
    import time

    monkeypatch.setattr(mb, "_MIN_REQUEST_INTERVAL", 1.0)
    monkeypatch.setattr(mb, "_last_request_at", 0.0)
    sleeps = []
    monkeypatch.setattr(mb.time, "sleep", lambda secs: sleeps.append(secs))

    mb._throttle()
    assert sleeps == []

    monkeypatch.setattr(mb, "_last_request_at", time.monotonic())
    mb._throttle()
    assert sleeps and sleeps[0] >= 0.9


def test_throttle_skips_sleep_when_disabled(monkeypatch):
    monkeypatch.setattr(mb, "_last_request_at", 0.0)
    sleeps = []
    monkeypatch.setattr(mb.time, "sleep", lambda secs: sleeps.append(secs))

    mb._throttle()
    mb._throttle()
    assert sleeps == []
