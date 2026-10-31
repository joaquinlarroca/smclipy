import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from mutagen.easyid3 import EasyID3

import smclipy.db as db
import smclipy.findsource as findsource
from smclipy import search, tag
from smclipy.downloader import SourceCandidate, SourceSearchError
from smclipy.helpers import join_authors
from smclipy.metadata import scan_library

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Song", artist="Artist", album="Album") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["album"] = [album]
    audio.save(path)


def make_song(app_settings, name="Artist-Song.mp3", **tags):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / name
    write_tagged_mp3(path, **tags)
    scan_library()
    return tag.LibrarySong(path)


def youtube_candidate(url="https://www.youtube.com/watch?v=abcdefghijk"):
    return SourceCandidate(
        provider="youtube",
        title="Song",
        uploader="Artist",
        duration=120.0,
        url=url,
    )


def soundcloud_candidate(url="https://soundcloud.com/user/track"):
    return SourceCandidate(
        provider="soundcloud",
        title="Song",
        uploader="User",
        duration=None,
        url=url,
    )


def test_process_song_auto_saves_first_result(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    candidate = youtube_candidate()
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is True

    row = db.get_song_by_uuid(song.uuid)
    assert row is not None
    assert row["source_url"] == candidate.url
    assert row["source_status"] == db.SOURCE_STATUS_SAVED
    assert report[0]["outcome"] == "saved"
    assert report[0]["provider"] == "youtube"
    assert report[0]["url"] == candidate.url
    events = db.get_events(song.uuid)
    assert any(
        event["event"] == "source" and candidate.url in event["detail"]
        for event in events
    )


def test_process_song_save_rolls_back_when_event_logging_fails(
    monkeypatch, app_settings
):
    song = make_song(app_settings)
    assert song.uuid is not None
    candidate = youtube_candidate()
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    def boom(*args, **kwargs):
        raise RuntimeError("event log failed")

    monkeypatch.setattr(db, "log_event", boom)

    with pytest.raises(RuntimeError):
        findsource.process_song(song, mode="auto")

    row = db.get_song_by_uuid(song.uuid)
    assert row["source_url"] is None
    assert row["source_status"] is None


def test_process_song_falls_back_to_next_provider(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    calls: list[str] = []
    candidate = soundcloud_candidate()

    def fake_search(provider, query):
        calls.append(provider)
        return [] if provider == "youtube" else [candidate]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    assert findsource.process_song(song, mode="auto") is True
    assert calls == ["youtube", "soundcloud"]


def test_process_song_falls_back_when_provider_errors(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    assert song.uuid is not None
    candidate = soundcloud_candidate()

    def fake_search(provider, query):
        if provider == "youtube":
            raise SourceSearchError("youtube search failed: boom")
        return [candidate]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    assert findsource.process_song(song, mode="auto") is True
    assert "youtube search failed" in capsys.readouterr().err
    assert db.get_song_by_uuid(song.uuid)["source_url"] == candidate.url


def test_process_song_reports_not_found(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "search_source", lambda provider, query: [])
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False

    assert report[0]["outcome"] == "not_found"
    row = db.get_song_by_uuid(song.uuid)
    assert row["source_url"] is None
    assert row["source_status"] == db.SOURCE_STATUS_NOT_FOUND


def test_process_song_reports_unavailable_when_all_providers_fail(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)

    def fake_search(provider, query):
        raise SourceSearchError(f"{provider} failed")

    monkeypatch.setattr(findsource, "search_source", fake_search)
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False

    assert report[0]["outcome"] == "unavailable"
    assert report[0]["all_providers_failed"] is True
    assert (
        db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_UNAVAILABLE
    )
    assert "every configured source provider failed" in capsys.readouterr().out


def test_process_song_reports_unavailable_when_search_is_incomplete(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)

    def fake_search(provider, query):
        if provider == "youtube":
            return []
        raise SourceSearchError("soundcloud failed")

    monkeypatch.setattr(findsource, "search_source", fake_search)
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False

    assert report[0]["outcome"] == "unavailable"
    assert report[0]["all_providers_failed"] is False
    assert (
        db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_UNAVAILABLE
    )
    out = capsys.readouterr().out
    assert "at least one provider failed" in out
    assert "every configured source provider failed" not in out


def test_process_song_partial_failure_rejects_as_unavailable(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    candidate = SourceCandidate(
        provider="soundcloud",
        title="Completely Unrelated",
        uploader="Nobody",
        duration=None,
        url="https://soundcloud.com/user/different",
    )

    def fake_search(provider, query):
        if provider == "youtube":
            raise SourceSearchError("youtube failed")
        return [candidate]

    monkeypatch.setattr(findsource, "search_source", fake_search)
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False

    assert report[0]["outcome"] == "unavailable"
    assert report[0]["all_providers_failed"] is False
    assert report[0]["rejected_count"] == 1
    assert (
        db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_UNAVAILABLE
    )
    assert "at least one provider failed" in capsys.readouterr().out


def test_process_song_interactive_picks_candidate(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    candidates = [youtube_candidate(), soundcloud_candidate()]
    monkeypatch.setattr(findsource, "search_source", lambda provider, query: candidates)
    monkeypatch.setattr(
        findsource, "prompt_source_selection", lambda values, **kwargs: 1
    )
    report: list[dict] = []

    assert findsource.process_song(song, report=report) is True

    assert db.get_song_by_uuid(song.uuid)["source_url"] == candidates[1].url
    assert report[0]["provider"] == "soundcloud"


def test_process_song_interactive_skip_saves_nothing(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )
    monkeypatch.setattr(
        findsource, "prompt_source_selection", lambda values, **kwargs: None
    )
    report: list[dict] = []

    assert findsource.process_song(song, report=report) is False

    assert report[0]["outcome"] == "skipped"
    row = db.get_song_by_uuid(song.uuid)
    assert row["source_url"] is None
    assert row["source_status"] == db.SOURCE_STATUS_SKIPPED


def test_process_song_interactive_shows_song_duration(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 100.0)
    seen: dict[str, float | None] = {}

    def fake_selection(values, **kwargs):
        seen["song_duration"] = kwargs.get("song_duration")
        return 0

    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )
    monkeypatch.setattr(findsource, "prompt_source_selection", fake_selection)

    assert findsource.process_song(song) is True

    out = capsys.readouterr().out
    assert "=== Song - Artist (1:40) ===" in out
    assert seen["song_duration"] == 100.0


def test_process_song_skips_existing_source_unless_forced(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    assert song.uuid is not None
    old_source = "https://old.example/song"
    db.update_song_metadata(song.uuid, source_url=old_source)
    calls: list[str] = []
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: calls.append(provider) or [],
    )
    report: list[dict] = []

    assert (
        findsource.process_song(
            song,
            mode="auto",
            has_source=True,
            existing_source=old_source,
            report=report,
        )
        is False
    )
    assert calls == []
    assert report[0]["outcome"] == "already_has_source"
    assert report[0]["url"] == old_source
    out = capsys.readouterr().out
    assert "=== Song - Artist" in out
    assert "Skipped: already has a source URL" in out
    assert "Searching source for" not in out

    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )
    assert (
        findsource.process_song(song, mode="auto", has_source=True, force=True) is True
    )
    assert db.get_song_by_uuid(song.uuid)["source_url"] == youtube_candidate().url


def test_process_song_force_same_source_reports_unchanged(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    candidate = youtube_candidate()
    db.update_song_metadata(song.uuid, source_url=candidate.url)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )
    report: list[dict] = []

    assert (
        findsource.process_song(
            song,
            mode="auto",
            force=True,
            has_source=True,
            existing_source=candidate.url,
            report=report,
        )
        is False
    )

    assert report[0]["outcome"] == "unchanged"
    assert report[0]["url"] == candidate.url
    assert db.get_song_by_uuid(song.uuid)["source_url"] == candidate.url
    assert not any(event["event"] == "source" for event in db.get_events(song.uuid))


def test_process_song_without_uuid_reports_error(app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    path = music / "No-UUID.mp3"
    write_tagged_mp3(path)
    song = tag.LibrarySong(path)
    assert song.uuid is None
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False
    assert report[0]["outcome"] == "error"
    out = capsys.readouterr().out
    assert "=== Song - Artist" in out
    assert "Skipped: no tracking UUID" in out


def test_process_song_auto_prints_song_header(monkeypatch, app_settings, capsys):
    song = make_song(app_settings)
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 100.0)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )

    assert findsource.process_song(song, mode="auto", position=2, total=4) is True

    out = capsys.readouterr().out
    assert "[2/4] === Song - Artist (1:40) ===" in out
    assert "Searching source for" not in out


def test_process_song_interactive_skip_prints_reason(monkeypatch, app_settings, capsys):
    song = make_song(app_settings)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )
    monkeypatch.setattr(
        findsource, "prompt_source_selection", lambda values, **kwargs: None
    )

    assert findsource.process_song(song) is False

    out = capsys.readouterr().out
    assert "=== Song - Artist" in out
    assert "Skipped: no source selected." in out


def test_cmd_findsource_skip_skipped_prints_song_and_reason(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings, title="Clocking", artist="Zozo")
    assert song.uuid is not None
    db.record_source_status(song.uuid, db.SOURCE_STATUS_REJECTED)
    searched: list[str] = []
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 43.0)
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: searched.append(query) or [],
    )

    findsource.cmd_findsource(
        argparse.Namespace(auto=True, all=True, json=False, skip_skipped=True)
    )

    out = capsys.readouterr().out
    assert "[1/1] === Clocking - Zozo (0:43) ===" in out
    assert "Skipped: no result matched the artist or title on a previous run" in out
    assert searched == []


def test_cmd_findsource_search_filters_songs(monkeypatch, app_settings, capsys):
    make_song(app_settings, "Artist-One.mp3", title="One", artist="A", album="First")
    make_song(app_settings, "Artist-Two.mp3", title="Two", artist="B", album="Second")
    seen: dict[str, int] = {}
    candidate = youtube_candidate()

    def fake_selection(count):
        seen["count"] = count
        return [0]

    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "prompt_song_selection", fake_selection)
    monkeypatch.setattr(
        findsource, "prompt_source_selection", lambda values, **kwargs: 0
    )
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    findsource.cmd_findsource(argparse.Namespace(search="second"))

    out = capsys.readouterr().out
    assert "Showing 1 of 2 song(s) matching 'second'." in out
    assert seen["count"] == 1
    row = db.get_song_by_path("Artist-Two.mp3")[0]
    assert row["source_url"] == candidate.url
    assert db.get_song_by_path("Artist-One.mp3")[0]["source_url"] is None


def test_cmd_findsource_specific_autocompletes_and_filters(
    monkeypatch, app_settings, capsys
):
    make_song(app_settings, "Artist-One.mp3", title="One", artist="A", album="First")
    make_song(app_settings, "Artist-Two.mp3", title="Two", artist="B", album="Second")
    prompts: list[tuple[str, list[str]]] = []
    candidate = SourceCandidate(
        provider="youtube",
        title="Two",
        uploader="B",
        duration=None,
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )

    def fake_prompt(label, values, default=""):
        prompts.append((label, values))
        return "second"

    monkeypatch.setattr(search, "prompt_search_value", fake_prompt)
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    findsource.cmd_findsource(argparse.Namespace(auto=True, all=True, specific="album"))

    out = capsys.readouterr().out
    assert prompts == [("Album", ["First", "Second"])]
    assert "Showing 1 of 2 song(s) with album 'second'." in out
    assert db.get_song_by_path("Artist-Two.mp3")[0]["source_url"] == candidate.url
    assert db.get_song_by_path("Artist-One.mp3")[0]["source_url"] is None


def test_cmd_findsource_json_report(monkeypatch, app_settings, capsys):
    make_song(app_settings)
    candidate = youtube_candidate()
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    findsource.cmd_findsource(argparse.Namespace(auto=True, all=True, json=True))

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["found"] == 1
    assert report["skipped"] == 0
    assert report["songs"][0]["outcome"] == "saved"
    assert report["songs"][0]["url"] == candidate.url


def test_cmd_findsource_already_sourced_counts_skipped(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    assert song.uuid is not None
    db.update_song_metadata(song.uuid, source_url="https://old.example/song")
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [youtube_candidate()]
    )

    findsource.cmd_findsource(argparse.Namespace(auto=True, all=True, json=True))

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 0
    assert report["skipped"] == 1
    assert report["songs"][0]["outcome"] == "already_has_source"
    assert report["songs"][0]["url"] == "https://old.example/song"


def test_cmd_findsource_json_exits_on_song_error(monkeypatch, app_settings, capsys):
    song = make_song(app_settings)
    assert song.uuid is not None

    def boom(provider, query):
        raise RuntimeError("search exploded")

    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "search_source", boom)

    with pytest.raises(SystemExit) as excinfo:
        findsource.cmd_findsource(argparse.Namespace(auto=True, all=True, json=True))

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["songs"][0]["outcome"] == "error"
    assert "search exploded" in captured.err
    assert db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_ERROR


def test_record_error_status_keeps_a_saved_source(app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    db.update_song_metadata(song.uuid, source_url="https://saved.example/song")
    db.record_source_status(song.uuid, db.SOURCE_STATUS_SAVED)

    findsource._record_error_status(song)

    row = db.get_song_by_uuid(song.uuid)
    assert row["source_url"] == "https://saved.example/song"
    assert row["source_status"] == db.SOURCE_STATUS_SAVED


def test_cmd_findsource_empty_library_prints_message(monkeypatch, app_settings, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(findsource, "scan_library", lambda: None)

    findsource.cmd_findsource(argparse.Namespace())

    assert "No audio files found" in capsys.readouterr().out


def _source(
    *,
    url: str,
    uploader: str,
    duration: float | None,
    provider: str = "youtube",
) -> SourceCandidate:
    return SourceCandidate(
        provider=provider,
        title="Song",
        uploader=uploader,
        duration=duration,
        url=url,
    )


def test_author_matching_ignores_noisy_uploaders():
    assert findsource._name_score("Artist", "Artist - Topic") == 2
    assert findsource._name_score("Twenty One Pilots", "TwentyOnePilotsVEVO") == 2
    assert findsource._name_score("Björk", "Bjork") == 2
    assert findsource._name_score("Artist", "Artist Live") == 1
    assert findsource._name_score("Artist", "Someone Else") == 0


def test_author_matching_strips_noise_suffixes_in_any_order():
    assert findsource._name_score("Artist", "Artist Official Music") == 2
    assert findsource._name_score("Artist", "ArtistTopicMusic") == 2
    assert findsource._name_score("Artist", "Artist Music Official") == 2
    assert findsource._name_score("Artist", "ArtistVEVO Official") == 2


def test_author_matching_preserves_non_latin_scripts():
    assert findsource._clean_author("宇多田ヒカル") == "宇多田ヒカル"
    assert findsource._name_score("宇多田ヒカル", "宇多田ヒカル - Topic") == 2
    assert findsource._name_score("Мельница", "Мельница") == 2


def test_author_matching_rejects_tiny_containment_matches():
    assert findsource._name_score("Artist", "A") == 0
    assert findsource._name_score("Zozo", "o") == 0
    assert findsource._name_score("Artist", "Art") == 1


def test_rank_candidates_prefers_duration_over_author(app_settings):
    song = make_song(app_settings)
    author_match = _source(url="author", uploader="Artist", duration=300.0)
    duration_match = _source(url="duration", uploader="Other", duration=100.0)

    ranked = findsource._rank_candidates(song, [author_match, duration_match], 100.0)

    assert [candidate.url for candidate in ranked] == ["duration", "author"]


def test_rank_candidates_uses_author_as_secondary(app_settings):
    song = make_song(app_settings)
    wrong_author = _source(url="wrong", uploader="Other", duration=100.0)
    right_author = _source(url="right", uploader="Artist", duration=101.0)

    ranked = findsource._rank_candidates(song, [wrong_author, right_author], 100.0)

    assert [candidate.url for candidate in ranked] == ["right", "wrong"]


def test_rank_candidates_uses_provider_order_as_tiebreaker(app_settings):
    song = make_song(app_settings)
    soundcloud = _source(
        url="sc", uploader="Artist", duration=100.0, provider="soundcloud"
    )
    youtube = _source(url="yt", uploader="Artist", duration=100.0)

    ranked = findsource._rank_candidates(song, [soundcloud, youtube], 100.0)

    assert [candidate.url for candidate in ranked] == ["yt", "sc"]


def test_rank_candidates_is_stable_when_scores_tie(app_settings):
    song = make_song(app_settings)
    first = _source(url="first", uploader="Other", duration=None)
    second = _source(url="second", uploader="Other", duration=None)

    ranked = findsource._rank_candidates(song, [first, second], 100.0)

    assert [candidate.url for candidate in ranked] == ["first", "second"]


def test_process_song_auto_ranks_duration_match(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 100.0)
    wrong_duration = _source(
        url="https://wrong.example", uploader="Artist", duration=250.0
    )
    right_duration = _source(
        url="https://right.example", uploader="Other", duration=100.0
    )
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: [wrong_duration, right_duration],
    )

    assert findsource.process_song(song, mode="auto") is True

    assert db.get_song_by_uuid(song.uuid)["source_url"] == right_duration.url


def test_process_song_auto_ranks_author_without_duration(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: None)
    wrong_author = _source(
        url="https://wrong.example", uploader="Other", duration=250.0
    )
    right_author = _source(
        url="https://right.example", uploader="Artist", duration=250.0
    )
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: [wrong_author, right_author],
    )

    assert findsource.process_song(song, mode="auto") is True

    assert db.get_song_by_uuid(song.uuid)["source_url"] == right_author.url


def test_process_song_interactive_lists_ranked_candidates(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 100.0)
    author_match = _source(
        url="https://author.example", uploader="Artist", duration=250.0
    )
    duration_match = _source(
        url="https://duration.example", uploader="Other", duration=100.0
    )
    seen: list[str] = []

    def fake_selection(values, **kwargs):
        seen.extend(candidate.url for candidate in values)
        return 0

    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: [author_match, duration_match],
    )
    monkeypatch.setattr(findsource, "prompt_source_selection", fake_selection)

    assert findsource.process_song(song) is True

    assert seen == [duration_match.url, author_match.url]
    assert db.get_song_by_uuid(song.uuid)["source_url"] == duration_match.url


def test_find_candidates_stops_at_first_provider(monkeypatch, app_settings):
    youtube = youtube_candidate()
    soundcloud = soundcloud_candidate()
    calls: list[str] = []

    def fake_search(provider, query):
        calls.append(provider)
        return [youtube] if provider == "youtube" else [soundcloud]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    candidates, unavailable, all_failed = findsource._find_candidates("query")

    assert calls == ["youtube"]
    assert [candidate.url for candidate in candidates] == [youtube.url]
    assert unavailable is False
    assert all_failed is False


def test_find_candidates_dedupes_within_first_provider(monkeypatch, app_settings):
    first = youtube_candidate(url="https://same.example")
    duplicate = youtube_candidate(url="https://same.example")
    calls: list[str] = []

    def fake_search(provider, query):
        calls.append(provider)
        return [first, duplicate]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    candidates, unavailable, all_failed = findsource._find_candidates("query")

    assert calls == ["youtube"]
    assert [candidate.url for candidate in candidates] == ["https://same.example"]
    assert unavailable is False
    assert all_failed is False


def test_find_candidates_all_providers_merges_and_dedupes(monkeypatch, app_settings):
    youtube = youtube_candidate(url="https://same.example")
    duplicate = soundcloud_candidate(url="https://same.example")
    soundcloud = soundcloud_candidate(url="https://sc.example")

    def fake_search(provider, query):
        return [youtube] if provider == "youtube" else [duplicate, soundcloud]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    candidates, unavailable, all_failed = findsource._find_candidates(
        "query", all_providers=True
    )

    assert [candidate.url for candidate in candidates] == [
        "https://same.example",
        "https://sc.example",
    ]
    assert unavailable is False
    assert all_failed is False


def test_find_candidates_all_providers_partial_failure(monkeypatch, app_settings):
    soundcloud = soundcloud_candidate()

    def fake_search(provider, query):
        if provider == "youtube":
            raise SourceSearchError("youtube failed")
        return [soundcloud]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    candidates, unavailable, all_failed = findsource._find_candidates(
        "query", all_providers=True
    )

    assert [candidate.url for candidate in candidates] == [soundcloud.url]
    assert unavailable is True
    assert all_failed is False


def test_process_song_all_providers_searches_every_provider(monkeypatch, app_settings):
    song = make_song(app_settings)
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 100.0)
    youtube = _source(url="https://yt.example", uploader="Artist", duration=250.0)
    soundcloud = _source(
        url="https://sc.example",
        uploader="Other",
        duration=100.0,
        provider="soundcloud",
    )
    calls: list[str] = []

    def fake_search(provider, query):
        calls.append(provider)
        return [youtube] if provider == "youtube" else [soundcloud]

    monkeypatch.setattr(findsource, "search_source", fake_search)

    assert findsource.process_song(song, mode="auto", all_providers=True) is True

    assert calls == ["youtube", "soundcloud"]
    assert db.get_song_by_uuid(song.uuid)["source_url"] == soundcloud.url


def test_cmd_findsource_passes_all_providers(monkeypatch, app_settings):
    make_song(app_settings)
    seen: dict[str, object] = {}

    def fake_process(song, **kwargs):
        seen.update(kwargs)
        return True

    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "process_song", fake_process)

    findsource.cmd_findsource(
        argparse.Namespace(auto=True, all=True, all_providers=True)
    )

    assert seen["all_providers"] is True


def test_cmd_findsource_skip_skipped_filters_previous_outcomes(
    monkeypatch, app_settings, capsys
):
    skipped = make_song(app_settings, "Artist-One.mp3", title="One", artist="A")
    missing = make_song(app_settings, "Artist-Two.mp3", title="Two", artist="B")
    fresh = make_song(app_settings, "Artist-Three.mp3", title="Three", artist="C")
    assert (
        skipped.uuid is not None and missing.uuid is not None and fresh.uuid is not None
    )
    db.record_source_status(skipped.uuid, db.SOURCE_STATUS_SKIPPED)
    db.record_source_status(missing.uuid, db.SOURCE_STATUS_NOT_FOUND)
    searched: list[str] = []
    candidate = SourceCandidate(
        provider="youtube",
        title="Three",
        uploader="C",
        duration=None,
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )

    def fake_search(provider, query):
        searched.append(query)
        return [candidate]

    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "search_source", fake_search)

    findsource.cmd_findsource(
        argparse.Namespace(auto=True, all=True, json=True, skip_skipped=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 1
    assert report["skipped"] == 2
    outcomes = {entry["display_name"]: entry["outcome"] for entry in report["songs"]}
    assert outcomes == {
        "One - A": "already_skipped",
        "Two - B": "already_skipped",
        "Three - C": "saved",
    }
    assert searched == ["C Three"]
    assert db.get_song_by_uuid(fresh.uuid)["source_url"] == candidate.url
    for song in (skipped, missing):
        assert db.get_song_by_uuid(song.uuid)["source_url"] is None


def test_cmd_findsource_without_skip_skipped_retries(monkeypatch, app_settings, capsys):
    song = make_song(app_settings)
    assert song.uuid is not None
    db.record_source_status(song.uuid, db.SOURCE_STATUS_NOT_FOUND)
    candidate = youtube_candidate()
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    findsource.cmd_findsource(argparse.Namespace(auto=True, all=True, json=True))

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 1
    assert report["songs"][0]["outcome"] == "saved"
    row = db.get_song_by_uuid(song.uuid)
    assert row["source_url"] == candidate.url
    assert row["source_status"] == db.SOURCE_STATUS_SAVED


def test_cmd_findsource_skip_skipped_retries_unavailable(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings)
    assert song.uuid is not None
    db.record_source_status(song.uuid, db.SOURCE_STATUS_UNAVAILABLE)
    candidate = youtube_candidate()
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )

    findsource.cmd_findsource(
        argparse.Namespace(auto=True, all=True, json=True, skip_skipped=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 1
    assert report["songs"][0]["outcome"] == "saved"


def test_title_score_matches_exact_and_noisy_titles():
    assert (
        findsource._title_score("Clocking", "CLOCKING JUMPERS IN BADDIES ROBLOX") == 2
    )
    assert (
        findsource._title_score(
            "Never Gonna Give You Up",
            "Rick Astley - Never Gonna Give You Up (Official Video)",
        )
        == 2
    )


def test_title_score_rejects_unrelated_lookalike():
    assert (
        findsource._title_score(
            "clocking", "CLOCKED OR BLOCKED? Official Trailer — Trailer 1"
        )
        == 0
    )
    assert (
        findsource._title_score(
            "clocking",
            "Final Fantasy Six, Pt. 31 - Decrepid Zozo and Clock Puzzle",
        )
        == 0
    )


def test_is_plausible_filters_the_clocking_example(app_settings):
    song = cast(
        tag.LibrarySong,
        SimpleNamespace(
            current={"title": "clocking", "artists": "Zozo"},
            path=Path("clocking.mp3"),
        ),
    )
    candidates = [
        SourceCandidate(
            "youtube",
            "★ ⋆˙⟡ CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
            "𝑮𝒊𝒈𝒊 ⋆ 🦌",
            45,
            "https://www.youtube.com/watch?v=unrelated1",
        ),
        SourceCandidate(
            "youtube",
            "ZoZo - TR-3B (Music Video)",
            "ZoZo",
            214,
            "https://www.youtube.com/watch?v=unrelated2",
        ),
        SourceCandidate(
            "youtube",
            "CLOCKING JUMPERS IN BADDIES ROBLOX !!",
            "zozo",
            402,
            "https://www.youtube.com/watch?v=unrelated3",
        ),
        SourceCandidate(
            "youtube",
            "TikTok Jamaica Media: Brit’s sisters takes out restraining order on Zozo?",
            "Clocked Tee",
            198,
            "https://www.youtube.com/watch?v=unrelated4",
        ),
        SourceCandidate(
            "youtube",
            "Final Fantasy Six, Pt. 31 - Decrepid Zozo and Clock Puzzle",
            "Genisis013",
            1202,
            "https://www.youtube.com/watch?v=unrelated5",
        ),
    ]

    assert [findsource._is_plausible(song, item) for item in candidates] == [
        False,
        True,
        True,
        False,
        False,
    ]


def test_title_score_accepts_typos_and_all_words_present():
    assert findsource._title_score("clocking", "Clockin (Lyrics)") >= 1
    assert findsource._title_score("give you up", "Never Gonna Give You Up") >= 1


def test_title_artist_names_parses_prefix_and_featured():
    assert findsource._title_artist_names("Zozo - Clocking feat. Artist2") == [
        "Zozo",
        "Artist2",
    ]
    assert findsource._title_artist_names("Zozo - Clocking featuring Artist2") == [
        "Zozo",
        "Artist2",
    ]
    assert findsource._title_artist_names("Zozo - Clocking ft. Artist2") == [
        "Zozo",
        "Artist2",
    ]
    assert findsource._title_artist_names("A & B - Song") == ["A", "B"]
    assert (
        findsource._title_artist_names(
            "TikTok Jamaica Media: Brit's sisters take out an order on Zozo? "
            "- Clocked Tee"
        )
        == []
    )


def test_artist_score_uses_title_artists_instead_of_uploader(app_settings):
    song = make_song(app_settings, title="Song", artist="Artist2")
    candidate = SourceCandidate(
        provider="youtube",
        title="Artist1 - Song feat. Artist2",
        uploader="Random Channel",
        duration=None,
        url="https://www.youtube.com/watch?v=featuredartist",
    )

    assert findsource._artist_score(song, candidate) == 2


def test_artist_score_matches_any_song_artist(app_settings):
    song = make_song(app_settings, title="Song", artist="Artist2")
    song.current["artists"] = join_authors(["Artist1", "Artist2"])
    candidate = SourceCandidate(
        provider="youtube",
        title="ZoZo - Unrelated",
        uploader="Artist1",
        duration=None,
        url="https://www.youtube.com/watch?v=secondartist",
    )

    assert findsource._artist_score(song, candidate) == 2


def test_is_plausible_rejects_duration_match_without_artist_or_title(app_settings):
    song = make_song(app_settings, title="clocking", artist="Zozo")
    candidate = SourceCandidate(
        provider="youtube",
        title="CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
        uploader="Gigi",
        duration=45.0,
        url="https://www.youtube.com/watch?v=unrelated",
    )

    assert findsource._is_plausible(song, candidate) is False


def test_process_song_auto_skips_unmatched_duration_match(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings, "Zozo-Clocking.mp3", title="clocking", artist="Zozo")
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 43.0)
    candidate = SourceCandidate(
        provider="youtube",
        title="CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
        uploader="Gigi",
        duration=45.0,
        url="https://www.youtube.com/watch?v=unrelated",
    )
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [candidate]
    )
    report: list[dict] = []

    assert findsource.process_song(song, mode="auto", report=report) is False

    out = capsys.readouterr().out
    assert "=== clocking - Zozo (0:43) ===" in out
    assert "Skipped: no candidate matched the artist or title" in out
    assert report[0]["outcome"] == "rejected"
    assert report[0]["rejected_count"] == 1
    row = db.get_song_by_uuid(song.uuid)
    assert row is not None
    assert row["source_url"] is None
    assert row["source_status"] == db.SOURCE_STATUS_REJECTED


def test_process_song_auto_ignores_unmatched_duration_match(monkeypatch, app_settings):
    song = make_song(app_settings, "Zozo-Clocking.mp3", title="clocking", artist="Zozo")
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 43.0)
    unmatched = SourceCandidate(
        provider="youtube",
        title="CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
        uploader="Gigi",
        duration=45.0,
        url="https://www.youtube.com/watch?v=unrelated",
    )
    plausible = SourceCandidate(
        provider="youtube",
        title="ZoZo - TR-3B (Music Video)",
        uploader="ZoZo",
        duration=214.0,
        url="https://www.youtube.com/watch?v=zozo",
    )
    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [unmatched, plausible]
    )

    assert findsource.process_song(song, mode="auto") is True

    assert db.get_song_by_uuid(song.uuid)["source_url"] == plausible.url


def test_process_song_interactive_marks_unmatched_candidates(monkeypatch, app_settings):
    song = make_song(app_settings, "Zozo-Clocking.mp3", title="clocking", artist="Zozo")
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 43.0)
    unmatched = SourceCandidate(
        provider="youtube",
        title="CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
        uploader="Gigi",
        duration=45.0,
        url="https://www.youtube.com/watch?v=unrelated",
    )
    plausible = SourceCandidate(
        provider="youtube",
        title="ZoZo - TR-3B (Music Video)",
        uploader="ZoZo",
        duration=214.0,
        url="https://www.youtube.com/watch?v=zozo",
    )
    seen: dict[str, object] = {}

    def fake_selection(values, **kwargs):
        seen["urls"] = [candidate.url for candidate in values]
        seen["matches"] = kwargs.get("matches")
        return None

    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [unmatched, plausible]
    )
    monkeypatch.setattr(findsource, "prompt_source_selection", fake_selection)

    assert findsource.process_song(song) is False

    assert seen["urls"] == [plausible.url, unmatched.url]
    assert seen["matches"] == [True, False]
    assert db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_SKIPPED


def test_process_song_interactive_still_prompts_when_nothing_matches(
    monkeypatch, app_settings
):
    song = make_song(app_settings, "Zozo-Clocking.mp3", title="clocking", artist="Zozo")
    assert song.uuid is not None
    monkeypatch.setattr(findsource, "read_song_duration", lambda path: 43.0)
    unmatched = SourceCandidate(
        provider="youtube",
        title="CLOCKED OR BLOCKED? Official Trailer — Trailer 1",
        uploader="Gigi",
        duration=45.0,
        url="https://www.youtube.com/watch?v=unrelated",
    )
    seen: dict[str, object] = {}

    def fake_selection(values, **kwargs):
        seen["count"] = len(values)
        seen["matches"] = kwargs.get("matches")
        return None

    monkeypatch.setattr(
        findsource, "search_source", lambda provider, query: [unmatched]
    )
    monkeypatch.setattr(findsource, "prompt_source_selection", fake_selection)

    assert findsource.process_song(song) is False

    assert seen == {"count": 1, "matches": [False]}
    assert db.get_song_by_uuid(song.uuid)["source_status"] == db.SOURCE_STATUS_SKIPPED


def test_cmd_findsource_skip_skipped_filters_rejected(
    monkeypatch, app_settings, capsys
):
    song = make_song(app_settings, "Zozo-Clocking.mp3", title="clocking", artist="Zozo")
    assert song.uuid is not None
    db.record_source_status(song.uuid, db.SOURCE_STATUS_REJECTED)
    searched: list[str] = []
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: searched.append(query) or [],
    )

    findsource.cmd_findsource(
        argparse.Namespace(auto=True, all=True, json=True, skip_skipped=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 0
    assert report["skipped"] == 1
    assert report["songs"][0]["outcome"] == "already_skipped"
    assert searched == []


def _write_playlist(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {"smclipy_playlist": 2, "name": "Test List", "songs": entries},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cmd_findsource_file_saves_tracked_and_untracked(
    monkeypatch, app_settings, capsys, tmp_path
):
    song = make_song(app_settings)
    assert song.uuid is not None
    missing_uuid = "missing-uuid"
    playlist = tmp_path / "list.json"
    _write_playlist(
        playlist,
        [
            {
                "uuid": song.uuid,
                "title": "Song",
                "artists": "Artist",
                "artists_list": ["Artist"],
                "source_url": "",
                "path": "Music/Artist-Song.mp3",
            },
            {
                "uuid": missing_uuid,
                "title": "Missing",
                "artists": "Nobody",
                "artists_list": ["Nobody"],
                "source_url": "",
                "path": "Music/Nobody-Missing.mp3",
            },
        ],
    )

    def fake_search(provider, query):
        if "Missing" in query:
            return [
                SourceCandidate(
                    provider="youtube",
                    title="Missing",
                    uploader="Nobody",
                    duration=None,
                    url="https://example.com/missing",
                )
            ]
        return [youtube_candidate()]

    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(findsource, "search_source", fake_search)

    findsource.cmd_findsource(
        argparse.Namespace(file=str(playlist), auto=True, all=True, json=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert report["found"] == 2
    assert [entry["outcome"] for entry in report["songs"]] == ["saved", "saved"]
    payload = json.loads(playlist.read_text(encoding="utf-8"))
    assert payload["songs"][0]["source_url"] == youtube_candidate().url
    assert payload["songs"][1]["source_url"] == "https://example.com/missing"
    assert db.get_song_by_uuid(song.uuid)["source_url"] == youtube_candidate().url
    assert db.get_song_by_uuid(missing_uuid) is None


def test_cmd_findsource_file_reconciles_without_searching(
    monkeypatch, app_settings, capsys, tmp_path
):
    song = make_song(app_settings)
    assert song.uuid is not None
    db.update_song_metadata(song.uuid, source_url="https://from-db.example/song")
    playlist = tmp_path / "list.json"
    _write_playlist(
        playlist,
        [
            {
                "uuid": song.uuid,
                "title": "Song",
                "artists": "Artist",
                "artists_list": ["Artist"],
                "source_url": "",
                "path": "Music/Artist-Song.mp3",
            }
        ],
    )
    searched: list[str] = []
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: searched.append(query) or [],
    )

    findsource.cmd_findsource(
        argparse.Namespace(file=str(playlist), auto=True, all=True, json=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert searched == []
    assert report["found"] == 0
    assert report["skipped"] == 1
    assert report["songs"][0]["outcome"] == "already_has_source"
    payload = json.loads(playlist.read_text(encoding="utf-8"))
    assert payload["songs"][0]["source_url"] == "https://from-db.example/song"


def test_cmd_findsource_file_invalid_playlist_exits(
    monkeypatch, app_settings, capsys, tmp_path
):
    playlist = tmp_path / "list.json"
    playlist.write_text('{"smclipy_playlist": 1, "songs": []}', encoding="utf-8")
    monkeypatch.setattr(findsource, "scan_library", lambda: None)

    with pytest.raises(SystemExit) as excinfo:
        findsource.cmd_findsource(
            argparse.Namespace(file=str(playlist), auto=True, all=True)
        )

    assert excinfo.value.code == 1
    assert "unsupported playlist version" in capsys.readouterr().err


def test_cmd_findsource_file_syncs_json_url_into_db(
    monkeypatch, app_settings, capsys, tmp_path
):
    song = make_song(app_settings)
    assert song.uuid is not None
    playlist = tmp_path / "list.json"
    _write_playlist(
        playlist,
        [
            {
                "uuid": song.uuid,
                "title": "Song",
                "artists": "Artist",
                "artists_list": ["Artist"],
                "source_url": "https://from-json.example/song",
                "path": "Music/Artist-Song.mp3",
            }
        ],
    )
    monkeypatch.setattr(findsource, "scan_library", lambda: None)
    monkeypatch.setattr(
        findsource,
        "search_source",
        lambda provider, query: pytest.fail("should not search"),
    )

    findsource.cmd_findsource(
        argparse.Namespace(file=str(playlist), auto=True, all=True, json=True)
    )

    report = json.loads(capsys.readouterr().out)
    assert report["skipped"] == 1
    assert db.get_song_by_uuid(song.uuid)["source_url"] == (
        "https://from-json.example/song"
    )
