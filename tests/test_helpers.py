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
    assert split_authors("A,B\\C") == ["A", "B", "C"]


def test_join_authors_round_trips_names_with_separators():
    from smclipy.helpers import display_authors, join_authors

    names = ["Earth, Wind & Fire", "AC\\DC"]
    joined = join_authors(names)
    assert joined == "Earth\\, Wind & Fire, AC\\\\DC"
    assert split_authors(joined) == names
    assert display_authors(joined) == "Earth, Wind & Fire, AC\\DC"

    legacy = join_authors(names, separator="\\")
    assert split_authors(legacy) == names


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


def test_sanitize_filename_caps_byte_length():
    capped = sanitize_filename("a" * 400)
    assert len(capped) == 200

    # Multi-byte names must be truncated on a character boundary, not mid-codepoint.
    accented = sanitize_filename("é" * 200)
    assert len(accented.encode("utf-8")) <= 200
    assert accented == "é" * 100


def test_split_authors_keeps_escaped_separators_and_legacy_joins():
    assert split_authors("Earth\\, Wind & Fire") == ["Earth, Wind & Fire"]
    assert split_authors("AC\\\\DC") == ["AC\\DC"]
    # Older versions joined with a bare backslash, so legacy data still splits.
    assert split_authors("Aice roomNor") == ["Aice roomNor"]
    assert split_authors("Alpha\\Beta") == ["Alpha", "Beta"]


def test_sanitize_filename_caps_length_and_keeps_alnum():
    from smclipy.helpers import sanitize_filename

    long_name = sanitize_filename("A" * 400)
    assert len(long_name.encode("utf-8")) <= 200
    assert long_name == "A" * 200
    assert sanitize_filename("  ..  ") == ""
