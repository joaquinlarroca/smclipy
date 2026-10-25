"""Filtering helpers shared by the ``modify`` and ``tag`` song pickers.

The filters never touch the files or the database: they narrow an in-memory
list of library songs before the user picks which ones to edit.
"""

import sys
from pathlib import Path
from typing import Protocol

from smclipy.helpers import normalize_author, split_authors
from smclipy.ui import TAG_FIELD_LABELS, prompt_search_value

FIELD_ALIASES: dict[str, str] = {
    "title": "title",
    "album": "album",
    "artist": "artists",
    "artists": "artists",
}
"""Accepted ``--specific`` field names mapped to their canonical name."""

SPECIFIC_FIELDS: tuple[str, ...] = ("title", "album", "artists", "artist")


class SearchableSong(Protocol):
    """The slice of :class:`smclipy.tag.LibrarySong` these helpers need."""

    current: dict[str, str]
    path: Path


def normalize_field(field: str) -> str | None:
    """Return the canonical field name, or None when ``field`` is unknown."""
    return FIELD_ALIASES.get(str(field).strip().casefold())


def song_title(song: SearchableSong) -> str:
    """The song's title, falling back to its file stem like the UI does."""
    return (song.current.get("title") or "").strip() or song.path.stem


def song_artists(song: SearchableSong) -> list[str]:
    return split_authors(song.current.get("artists", ""))


def filter_by_query[SongT: SearchableSong](
    songs: list[SongT], query: str
) -> list[SongT]:
    """Keep songs whose title, album, or any artist contains ``query``.

    The match is case-insensitive and uses substring semantics, so a partial
    album or artist name is enough.
    """
    needle: str = str(query).strip().casefold()
    if not needle:
        return []
    matches: list[SongT] = []
    for song in songs:
        haystacks: list[str] = [
            song_title(song),
            song.current.get("album", ""),
            *song_artists(song),
        ]
        if any(needle in value.casefold() for value in haystacks):
            matches.append(song)
    return matches


def field_values[SongT: SearchableSong](songs: list[SongT], field: str) -> list[str]:
    """Existing values for ``field``, deduplicated and alphabetically sorted.

    Artists are offered individually, not as the joined artist string, and
    duplicates that differ only in case or spacing keep their first spelling.
    """
    canonical: str | None = normalize_field(field)
    if canonical is None:
        return []
    seen: set[str] = set()
    values: list[str] = []
    for song in songs:
        if canonical == "artists":
            candidates: list[str] = song_artists(song)
        elif canonical == "title":
            candidates = [song_title(song)]
        else:
            candidates = [song.current.get(canonical, "")]
        for candidate in candidates:
            value: str = candidate.strip()
            key: str = (
                normalize_author(value) if canonical == "artists" else value.casefold()
            )
            if not value or key in seen:
                continue
            seen.add(key)
            values.append(value)
    values.sort(key=str.casefold)
    return values


def filter_by_field[SongT: SearchableSong](
    songs: list[SongT], field: str, value: str
) -> list[SongT]:
    """Keep songs whose ``field`` exactly equals ``value``.

    Title and album compare case-insensitively; artists match when any one of
    the song's artists equals the chosen name (case/whitespace-insensitive).
    """
    canonical: str | None = normalize_field(field)
    target: str = str(value).strip()
    if canonical is None or not target:
        return []
    if canonical == "artists":
        wanted: str = normalize_author(target)
        return [
            song
            for song in songs
            if any(normalize_author(artist) == wanted for artist in song_artists(song))
        ]
    folded: str = target.casefold()
    matches: list[SongT] = []
    for song in songs:
        if canonical == "title":
            candidate: str = song_title(song)
        else:
            candidate = song.current.get(canonical, "")
        if candidate.strip().casefold() == folded:
            matches.append(song)
    return matches


def filter_specific[SongT: SearchableSong](
    songs: list[SongT], raw_field: str
) -> list[SongT]:
    """Autocomplete an existing field value and keep the exact matches."""
    field: str | None = normalize_field(raw_field)
    if field is None:
        print(
            f"error: unsupported --specific field '{raw_field}' "
            "(use title, album, or artists).",
            file=sys.stderr,
        )
        return []
    values: list[str] = field_values(songs, field)
    if not values:
        print(f"No songs in the library have a {field} value to pick from.")
        return []
    label: str = TAG_FIELD_LABELS.get(field, field.capitalize())
    answer: str = prompt_search_value(label, values).strip()
    if not answer:
        print("No songs selected.")
        return []
    total: int = len(songs)
    matches: list[SongT] = filter_by_field(songs, field, answer)
    if not matches:
        print(f"No songs found with {label.lower()} '{answer}'.")
        return []
    print(f"Showing {len(matches)} of {total} song(s) with {label.lower()} '{answer}'.")
    return matches


def apply_filters[SongT: SearchableSong](
    songs: list[SongT], args: object
) -> tuple[list[SongT], bool]:
    """Narrow ``songs`` by ``--search``/``--specific``.

    Returns the matching songs plus whether a filter was applied (used by the
    callers to change their list heading). An empty result means the caller
    should stop and do nothing.
    """
    search_raw: object = getattr(args, "search", None)
    specific_raw: object = getattr(args, "specific", None)
    if search_raw is not None:
        query: str = str(search_raw).strip()
        if not query:
            print("error: --search needs non-empty text.", file=sys.stderr)
            raise SystemExit(2)
        total: int = len(songs)
        matches: list[SongT] = filter_by_query(songs, query)
        if not matches:
            print(f"No songs match '{query}'.")
            return [], True
        print(f"Showing {len(matches)} of {total} song(s) matching '{query}'.")
        return matches, True
    if specific_raw is not None:
        if normalize_field(str(specific_raw)) is None:
            print(
                f"error: unsupported --specific field '{specific_raw}' "
                "(use title, album, or artists).",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return filter_specific(songs, str(specific_raw)), True
    return songs, False
