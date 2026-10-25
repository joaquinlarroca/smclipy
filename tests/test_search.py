from pathlib import Path
from types import SimpleNamespace

import pytest

from smclipy import search


def make_song(
    title: str, artists: str = "", album: str = "", stem: str = "song"
) -> SimpleNamespace:
    return SimpleNamespace(
        path=Path(f"{stem}.mp3"),
        current={"title": title, "artists": artists, "album": album},
    )


def test_filter_by_query_matches_title_album_and_artist():
    songs = [
        make_song("Abbey Road", "The Beatles", "Abbey Road"),
        make_song("Other", "Someone", "Different"),
    ]

    assert search.filter_by_query(songs, "abbey") == [songs[0]]
    assert search.filter_by_query(songs, "beatles") == [songs[0]]
    assert search.filter_by_query(songs, "different") == [songs[1]]
    assert search.filter_by_query(songs, "nomatch") == []


def test_filter_by_query_is_case_insensitive_and_uses_substrings():
    song = make_song("Hello World", "Some Artist", "Some Album")

    assert search.filter_by_query([song], "WORLD") == [song]
    assert search.filter_by_query([song], "me art") == [song]


def test_filter_by_query_uses_stem_when_title_missing():
    song = make_song("", "Artist", "", stem="Fallback Title")

    assert search.filter_by_query([song], "fallback") == [song]


def test_filter_by_query_blank_matches_nothing():
    song = make_song("Anything")

    assert search.filter_by_query([song], "   ") == []


def test_field_values_dedupes_and_sorts_artists():
    songs = [
        make_song("A", "Beta, Alpha", "Z"),
        make_song("B", "alpha", "Y"),
        make_song("C", "", "x"),
    ]

    assert search.field_values(songs, "artists") == ["Alpha", "Beta"]
    assert search.field_values(songs, "artist") == ["Alpha", "Beta"]
    assert search.field_values(songs, "album") == ["x", "Y", "Z"]


def test_field_values_title_falls_back_to_stem():
    song = make_song("", stem="Stem Title")

    assert search.field_values([song], "title") == ["Stem Title"]


def test_normalize_field_accepts_aliases_and_rejects_unknown():
    assert search.normalize_field("artist") == "artists"
    assert search.normalize_field("ALBUM") == "album"
    assert search.normalize_field("bogus") is None


def test_filter_by_field_exact_title_album_and_artist():
    songs = [
        make_song("Song", "Alpha, Beta", "Album One"),
        make_song("Song", "Gamma", "Album Two"),
    ]

    assert search.filter_by_field(songs, "title", "song") == songs
    assert search.filter_by_field(songs, "album", "album one") == [songs[0]]
    assert search.filter_by_field(songs, "artists", "beta") == [songs[0]]
    assert search.filter_by_field(songs, "artist", "gamma") == [songs[1]]
    assert search.filter_by_field(songs, "artists", " beta ") == [songs[0]]
    assert search.filter_by_field(songs, "album", "nope") == []


def test_apply_filters_search_narrows_and_reports(monkeypatch, capsys):
    songs = [
        make_song("Abbey Road", "The Beatles", "Abbey Road"),
        make_song("Other", "Someone", "Different"),
    ]

    matches, filtered = search.apply_filters(
        songs, SimpleNamespace(search="beatles", specific=None)
    )

    assert matches == [songs[0]]
    assert filtered is True
    assert "Showing 1 of 2 song(s) matching 'beatles'." in capsys.readouterr().out


def test_apply_filters_without_flags_keeps_everything():
    songs = [make_song("One"), make_song("Two")]

    matches, filtered = search.apply_filters(songs, SimpleNamespace())

    assert matches == songs
    assert filtered is False


def test_apply_filters_search_no_match_returns_empty(capsys):
    songs = [make_song("One")]

    matches, filtered = search.apply_filters(songs, SimpleNamespace(search="zzz"))

    assert matches == []
    assert filtered is True
    assert "No songs match 'zzz'." in capsys.readouterr().out


def test_apply_filters_rejects_blank_search(capsys):
    songs = [make_song("One")]

    with pytest.raises(SystemExit) as excinfo:
        search.apply_filters(songs, SimpleNamespace(search="   "))

    assert excinfo.value.code == 2
    assert "--search needs non-empty text" in capsys.readouterr().err


def test_apply_filters_rejects_unknown_specific_field(capsys):
    songs = [make_song("One")]

    with pytest.raises(SystemExit) as excinfo:
        search.apply_filters(songs, SimpleNamespace(specific="genre"))

    assert excinfo.value.code == 2
    assert "unsupported --specific field 'genre'" in capsys.readouterr().err


def test_apply_filters_specific_uses_autocomplete(monkeypatch, capsys):
    songs = [
        make_song("One", "Alpha", "First"),
        make_song("Two", "Beta", "Second"),
    ]
    prompts: list[tuple[str, list[str]]] = []

    def fake_prompt(label: str, values: list[str], default: str = "") -> str:
        prompts.append((label, values))
        return "beta"

    monkeypatch.setattr(search, "prompt_search_value", fake_prompt)

    matches, filtered = search.apply_filters(songs, SimpleNamespace(specific="artists"))

    assert matches == [songs[1]]
    assert filtered is True
    assert prompts == [("Artist(s)", ["Alpha", "Beta"])]
    assert "Showing 1 of 2 song(s) with artist(s) 'beta'." in capsys.readouterr().out


def test_apply_filters_specific_without_matching_value(monkeypatch, capsys):
    songs = [make_song("One", "Alpha", "First")]
    monkeypatch.setattr(
        search, "prompt_search_value", lambda label, values, default="": "nobody"
    )

    matches, filtered = search.apply_filters(songs, SimpleNamespace(specific="artists"))

    assert matches == []
    assert filtered is True
    assert "No songs found with artist(s) 'nobody'." in capsys.readouterr().out


def test_apply_filters_specific_blank_cancels(monkeypatch, capsys):
    songs = [make_song("One", "Alpha", "First")]
    monkeypatch.setattr(
        search, "prompt_search_value", lambda label, values, default="": "  "
    )

    matches, filtered = search.apply_filters(songs, SimpleNamespace(specific="title"))

    assert matches == []
    assert filtered is True
    assert "No songs selected." in capsys.readouterr().out
