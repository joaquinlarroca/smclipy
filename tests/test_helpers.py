from smclipy.helpers import (
    get_list_from_split_str,
    get_unique_items,
    normalize_author,
    resolve_known_authors,
    sanitize_filename,
)


def test_sanitize_filename_removes_illegal_chars():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"


def test_sanitize_filename_strips_dots_and_spaces():
    assert sanitize_filename("  song.  ") == "song"


def test_get_list_from_split_str():
    assert get_list_from_split_str("A\\B\\C", "\\") == ["A", "B", "C"]
    assert get_list_from_split_str("A\\\\B", "\\") == ["A", "B"]
    assert get_list_from_split_str("A\\", "\\") == ["A"]


def test_get_list_from_split_str_ignores_empty():
    assert get_list_from_split_str("\\A\\", "\\") == ["A"]


def test_get_unique_items():
    assert get_unique_items(["A", "B", "C"], ["A", "C"]) == ["B"]


def test_get_unique_items_keeps_order():
    assert get_unique_items(["C", "A"], ["A"]) == ["C"]


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
