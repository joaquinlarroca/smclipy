from smclipy.helpers import (
    distinct_authors,
    normalize_author,
    resolve_known_authors,
    sanitize_filename,
    split_authors,
)


def test_sanitize_filename_removes_illegal_chars():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"


def test_sanitize_filename_strips_dots_and_spaces():
    assert sanitize_filename("  song.  ") == "song"


def test_split_authors_splits_backslash_and_comma():
    assert split_authors("A\\B,C") == ["A", "B", "C"]
    assert split_authors("A\\\\B,,C") == ["A", "B", "C"]
    assert split_authors("A,B\\C") == ["A", "B", "C"]


def test_split_authors_strips_whitespace_and_drops_empty():
    assert split_authors(" A ,  B\\ ") == ["A", "B"]
    assert split_authors("A,") == ["A"]
    assert split_authors(",") == []
    assert split_authors("") == []


def test_normalize_author_ignores_case_and_spaces():
    assert normalize_author("Author 1") == "author1"
    assert normalize_author("  The  Beatles ") == "thebeatles"


def test_resolve_known_authors_prefers_known_form():
    assert resolve_known_authors(["Author 1"], ["author1"]) == ["author1"]


def test_resolve_known_authors_keeps_unknown_candidate():
    assert resolve_known_authors(["New Artist"], ["author1"]) == ["New Artist"]


def test_resolve_known_authors_mixed_and_dedupes():
    assert resolve_known_authors(
        ["Author 1", "Brand New", "AUTHOR 1"], ["author1"]
    ) == ["author1", "Brand New"]


def test_resolve_known_authors_empty_inputs():
    assert resolve_known_authors([], ["author1"]) == []
    assert resolve_known_authors(["A"], []) == ["A"]


def test_distinct_authors_keeps_new_ones():
    assert distinct_authors(["Guest", "Artist"], ["Artist"]) == ["Guest"]


def test_distinct_authors_ignores_case_and_spaces():
    assert distinct_authors(["artist", " Artist "], ["Artist"]) == []


def test_distinct_authors_keeps_first_spelling():
    assert distinct_authors(["The Beatles", "the beatles"], ["Other"]) == [
        "The Beatles"
    ]


def test_distinct_authors_drops_empty():
    assert distinct_authors(["A", "   ", ""], ["B"]) == ["A"]
