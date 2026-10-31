import pytest
from prompt_toolkit.formatted_text import to_formatted_text

import smclipy.ui as ui
from smclipy.downloader import SourceCandidate


def _patch_prompt(monkeypatch, answers):
    it = iter(answers)

    def fake_prompt(message, **kwargs):
        return next(it)

    monkeypatch.setattr(ui, "prompt", fake_prompt)


def test_prompt_song_selection_all(monkeypatch):
    _patch_prompt(monkeypatch, ["all"])
    assert ui.prompt_song_selection(3) == [0, 1, 2]


def test_prompt_song_selection_all_case_insensitive(monkeypatch):
    _patch_prompt(monkeypatch, ["ALL"])
    assert ui.prompt_song_selection(3) == [0, 1, 2]


def test_prompt_song_selection_numbers(monkeypatch):
    _patch_prompt(monkeypatch, ["1,3"])
    assert ui.prompt_song_selection(5) == [0, 2]


def test_prompt_song_selection_dedupes_and_preserves_order(monkeypatch):
    _patch_prompt(monkeypatch, ["3,1,3"])
    assert ui.prompt_song_selection(3) == [2, 0]


def test_prompt_song_selection_tolerates_spaces_and_trailing_comma(monkeypatch):
    _patch_prompt(monkeypatch, [" 2 , 1, "])
    assert ui.prompt_song_selection(3) == [1, 0]


def test_prompt_song_selection_cancel_q(monkeypatch):
    _patch_prompt(monkeypatch, ["q"])
    assert ui.prompt_song_selection(3) == []


def test_prompt_song_selection_cancel_blank(monkeypatch):
    _patch_prompt(monkeypatch, [""])
    assert ui.prompt_song_selection(3) == []


def test_prompt_song_selection_cancel_quit_word(monkeypatch):
    _patch_prompt(monkeypatch, ["quit"])
    assert ui.prompt_song_selection(3) == []


def test_prompt_song_selection_retries_on_invalid(monkeypatch, capsys):
    _patch_prompt(monkeypatch, ["abc", "1"])
    assert ui.prompt_song_selection(3) == [0]
    assert "Invalid selection" in capsys.readouterr().out


def test_prompt_song_selection_retries_on_out_of_range(monkeypatch, capsys):
    _patch_prompt(monkeypatch, ["4", "2"])
    assert ui.prompt_song_selection(3) == [1]
    assert "out of range" in capsys.readouterr().out


def test_prompt_song_selection_range(monkeypatch):
    _patch_prompt(monkeypatch, ["2-4"])
    assert ui.prompt_song_selection(5) == [1, 2, 3]


def test_prompt_song_selection_ranges_and_singles(monkeypatch):
    _patch_prompt(monkeypatch, ["1-2,5,7-8"])
    assert ui.prompt_song_selection(8) == [0, 1, 4, 6, 7]


def test_prompt_song_selection_range_with_spaces(monkeypatch):
    _patch_prompt(monkeypatch, [" 1 - 3 , 5 "])
    assert ui.prompt_song_selection(6) == [0, 1, 2, 4]


def test_prompt_song_selection_range_overlap_dedupes(monkeypatch):
    _patch_prompt(monkeypatch, ["1-3,2-5"])
    assert ui.prompt_song_selection(5) == [0, 1, 2, 3, 4]


def test_prompt_song_selection_range_reversed_retries(monkeypatch, capsys):
    _patch_prompt(monkeypatch, ["5-2", "2"])
    assert ui.prompt_song_selection(5) == [1]
    assert "Invalid selection" in capsys.readouterr().out


def test_prompt_song_selection_range_out_of_bounds_retries(monkeypatch, capsys):
    _patch_prompt(monkeypatch, ["1-6", "1-3"])
    assert ui.prompt_song_selection(5) == [0, 1, 2]
    assert "out of range" in capsys.readouterr().out


def test_prompt_song_selection_keyboard_interrupt_exits(monkeypatch, capsys):
    def ctrl_c(message, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(ui, "prompt", ctrl_c)
    with pytest.raises(SystemExit) as excinfo:
        ui.prompt_song_selection(5)
    assert excinfo.value.code == 130
    assert "Interrupted" in capsys.readouterr().out


def _patch_choice(monkeypatch, result):
    captured = {}

    def fake_choice(message, *, options, default=None, **kwargs):
        captured["message"] = message
        captured["options"] = options
        captured["default"] = default
        return result

    monkeypatch.setattr(ui, "choice", fake_choice)
    return captured


def _fake_match(
    title="Choker",
    artists=("Twenty One Pilots",),
    album="Trench",
    date="2018",
):
    return type(
        "Match",
        (),
        {"title": title, "artists": list(artists), "album": album, "date": date},
    )()


def test_prompt_match_selection_shows_metadata_labels(monkeypatch):
    captured = _patch_choice(monkeypatch, "Skip this song")
    matches = [
        _fake_match(),
        _fake_match(title="Korn", artists=("Korn",), album="Korn", date="1994"),
    ]
    assert ui.prompt_match_selection(matches) is None
    assert captured["default"] == "1"
    assert captured["options"] == [
        ("Skip this song", "Skip this song"),
        ("1", "Twenty One Pilots - Choker | Trench (2018)"),
        ("2", "Korn - Korn | Korn (1994)"),
    ]


def test_prompt_match_selection_defaults_to_skip_when_no_matches(monkeypatch):
    captured = _patch_choice(monkeypatch, "Skip this song")
    assert ui.prompt_match_selection([]) is None
    assert captured["default"] == "Skip this song"


def test_prompt_match_selection_returns_picked_index(monkeypatch):
    _patch_choice(monkeypatch, "3")
    matches = [_fake_match() for _ in range(5)]
    assert ui.prompt_match_selection(matches) == 2


def test_prompt_match_selection_invalid_value_skips(monkeypatch):
    _patch_choice(monkeypatch, "not a number")
    assert ui.prompt_match_selection([_fake_match()]) is None


def test_collect_tag_changes_only_changed_fields():
    current = {"title": "Old", "artists": "One", "album": "A"}
    intended = {
        "title": "New",
        "artists": "One",
        "album": "A",
        "date": "2001",
        "genre": "Rock",
    }
    assert ui.collect_tag_changes(current, intended) == [
        ("title", "Old", "New"),
        ("date", "", "2001"),
        ("genre", "", "Rock"),
    ]


def _patch_checkboxlist(monkeypatch, result):
    captured = {}

    class FakeApp:
        def run(self):
            return result

    def fake(**kwargs):
        captured.update(kwargs)
        return FakeApp()

    monkeypatch.setattr(ui, "_checkbox_dialog", fake)
    return captured


def test_prompt_tag_changes_checks_all_by_default(monkeypatch):
    captured = _patch_checkboxlist(monkeypatch, ["album", "date", "cover"])
    changes = [("album", "", "Scaled and Icy"), ("date", "", "2021-05-21")]
    result = ui.prompt_tag_changes(changes, cover_pending=True)
    assert result == ["album", "date", "cover"]
    assert captured["default_values"] == ["album", "date", "cover"]
    assert captured["values"] == [
        ("album", "Album: (empty) -> Scaled and Icy"),
        ("date", "Release date: (empty) -> 2021-05-21"),
        ("cover", "Cover image"),
    ]


def test_prompt_tag_changes_cancel_returns_none(monkeypatch):
    _patch_checkboxlist(monkeypatch, None)
    assert ui.prompt_tag_changes([("album", "", "Scaled and Icy")]) is None


def test_prompt_tag_changes_empty_returns_empty():
    assert ui.prompt_tag_changes([]) == []


def test_prompt_field_selection_unchecked_by_default(monkeypatch):
    captured = _patch_checkboxlist(monkeypatch, ["title", "album"])
    result = ui.prompt_field_selection(
        [("title", "Title"), ("artists", "Artist(s)"), ("album", "Album")]
    )
    assert result == ["title", "album"]
    assert captured["default_values"] == []
    assert captured["values"] == [
        ("title", "Title"),
        ("artists", "Artist(s)"),
        ("album", "Album"),
    ]
    assert "Select fields to modify" in captured["title"]


def test_prompt_field_selection_cancel_returns_none(monkeypatch):
    _patch_checkboxlist(monkeypatch, None)
    assert ui.prompt_field_selection([("title", "Title")]) is None


def test_prompt_field_selection_empty_is_cancel():
    assert ui.prompt_field_selection([]) == []


def test_prompt_field_value_prefills_default(monkeypatch):
    captured = {}

    def fake_prompt(message, **kwargs):
        captured.update(message=message, kwargs=kwargs)
        return "New Value"

    monkeypatch.setattr(ui, "prompt", fake_prompt)
    assert ui.prompt_field_value("Title", default="Old Title") == "New Value"
    assert captured["message"] == "Title [Old Title]: "
    assert captured["kwargs"]["default"] == "Old Title"


def test_prompt_field_value_no_default_no_suffix(monkeypatch):
    def fake_prompt(message, **kwargs):
        assert message == "Genre: "
        return ""

    monkeypatch.setattr(ui, "prompt", fake_prompt)
    assert ui.prompt_field_value("Genre") == ""


def test_prompt_canonical_spelling_builds_merge_dialog(monkeypatch):
    captured = {}

    class FakeApp:
        def run(self):
            return "Artist"

    def fake_dialog(**kwargs):
        captured.update(kwargs)
        return FakeApp()

    monkeypatch.setattr(ui, "radiolist_dialog", fake_dialog)

    result = ui.prompt_canonical_spelling(
        "artist 'artist'", [("Artist", 2), ("artist", 1)], "Artist"
    )

    assert result == "Artist"
    assert captured["values"] == [
        ("Artist", "Artist  (2 songs)"),
        ("artist", "artist  (1 song)"),
    ]
    assert captured["default"] == "Artist"
    assert captured["ok_text"] == "Merge"
    assert captured["cancel_text"] == "Skip"
    assert "artist 'artist'" in captured["title"]


def test_prompt_canonical_spelling_returns_none_when_skipped(monkeypatch):
    class FakeApp:
        def run(self):
            return None

    monkeypatch.setattr(ui, "radiolist_dialog", lambda **kwargs: FakeApp())

    assert ui.prompt_canonical_spelling("album 'x'", [("X", 1), ("x", 1)], "X") is None


def _source_candidate(duration: float | None, title: str = "Song") -> SourceCandidate:
    return SourceCandidate(
        provider="youtube",
        title=title,
        uploader="Artist",
        duration=duration,
        url="https://www.youtube.com/watch?v=abcdefghijk",
    )


def _label_text(label) -> str:
    return "".join(part[1] for part in to_formatted_text(label))


def test_format_duration_minutes_and_hours():
    assert ui.format_duration(100) == "1:40"
    assert ui.format_duration(0) == "0:00"
    assert ui.format_duration(3671) == "1:01:11"


def test_prompt_source_selection_highlights_matching_duration(monkeypatch):
    captured = _patch_choice(monkeypatch, "1")
    candidates = [_source_candidate(100.0), _source_candidate(200.0)]

    assert ui.prompt_source_selection(candidates, song_duration=101.0) == 0

    texts = [_label_text(label) for _value, label in captured["options"]]
    assert "same length" not in texts[0]
    assert "same length" in texts[1]
    assert "same length" not in texts[2]
    assert captured["message"] == "Pick a source for this song (1:41):"


def test_prompt_source_selection_no_highlight_without_close_duration(monkeypatch):
    captured = _patch_choice(monkeypatch, "1")

    assert (
        ui.prompt_source_selection([_source_candidate(200.0)], song_duration=100.0) == 0
    )

    texts = [_label_text(label) for _value, label in captured["options"]]
    assert all("same length" not in text for text in texts)
    assert captured["message"] == "Pick a source for this song (1:40):"


def test_prompt_source_selection_escapes_title_in_highlight(monkeypatch):
    captured = _patch_choice(monkeypatch, "1")
    candidates = [_source_candidate(100.0, title="Song <A> & B")]

    ui.prompt_source_selection(candidates, song_duration=100.0)

    text = _label_text(captured["options"][1][1])
    assert "Song <A> & B" in text
    assert "same length" in text


def test_prompt_source_selection_marks_unmatched_candidates(monkeypatch):
    captured = _patch_choice(monkeypatch, "2")
    candidates = [
        _source_candidate(100.0, title="Wrong"),
        _source_candidate(200.0, title="Song"),
    ]

    assert (
        ui.prompt_source_selection(
            candidates, song_duration=100.0, matches=[False, True]
        )
        == 1
    )

    assert captured["default"] == "2"
    texts = [_label_text(label) for _value, label in captured["options"]]
    assert "no artist/title match" not in texts[0]
    assert "no artist/title match" in texts[1]
    assert "same length" not in texts[1]
    assert "no artist/title match" not in texts[2]


def test_prompt_source_selection_highlights_skip_when_nothing_matches(monkeypatch):
    captured = _patch_choice(monkeypatch, "Skip this song")
    candidates = [_source_candidate(100.0, title="Wrong")]

    assert (
        ui.prompt_source_selection(candidates, song_duration=100.0, matches=[False])
        is None
    )

    assert captured["default"] == "Skip this song"
    skip_text = _label_text(captured["options"][0][1])
    assert "no artist/title match" in skip_text
    candidate_text = _label_text(captured["options"][1][1])
    assert "no artist/title match" in candidate_text
    assert "same length" not in candidate_text


def test_prompt_source_selection_defaults_to_first_match(monkeypatch):
    captured = _patch_choice(monkeypatch, "2")
    candidates = [
        _source_candidate(100.0, title="Wrong"),
        _source_candidate(200.0, title="Song"),
    ]

    ui.prompt_source_selection(candidates, matches=[False, True])

    assert captured["default"] == "2"


def test_prompt_source_selection_rejects_mismatched_flags(monkeypatch):
    _patch_choice(monkeypatch, "1")

    with pytest.raises(ValueError, match="one entry per candidate"):
        ui.prompt_source_selection([_source_candidate(100.0)], matches=[])
