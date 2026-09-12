import pytest

import smclipy.ui as ui


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
    assert captured["default"] == "Skip this song"
    assert captured["options"] == [
        ("Skip this song", "Skip this song"),
        ("1", "Twenty One Pilots - Choker | Trench (2018)"),
        ("2", "Korn - Korn | Korn (1994)"),
    ]


def test_prompt_match_selection_returns_picked_index(monkeypatch):
    _patch_choice(monkeypatch, "3")
    matches = [_fake_match() for _ in range(5)]
    assert ui.prompt_match_selection(matches) == 2


def test_prompt_match_selection_invalid_value_skips(monkeypatch):
    _patch_choice(monkeypatch, "not a number")
    assert ui.prompt_match_selection([_fake_match()]) is None


def test_collect_tag_changes_only_changed_fields():
    current = {"title": "Old", "artists": "One", "album": "A"}
    intended = {"title": "New", "artists": "One", "album": "A", "date": "2001"}
    assert ui.collect_tag_changes(current, intended) == [
        ("title", "Old", "New"),
        ("date", "", "2001"),
    ]


def _patch_checkboxlist(monkeypatch, result):
    captured = {}

    class FakeApp:
        def run(self):
            return result

    def fake(**kwargs):
        captured.update(kwargs)
        return FakeApp()

    monkeypatch.setattr(ui, "_tag_checkbox_dialog", fake)
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
