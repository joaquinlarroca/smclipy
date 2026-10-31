"""Read and edit smclipy v2 playlist JSON files.

Both ``findsource --file`` and ``modify --file`` operate on playlist entries
without importing them into the library. This module keeps the document shape,
resolves entries to tracked songs when possible, and rewrites the JSON
atomically.
"""

import json
import os
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import smclipy.db as db
from smclipy.config import settings
from smclipy.helpers import display_authors, join_authors
from smclipy.playlists import (
    _index_songs,
    _resolve_entry,
    entry_source_url,
    parse_json_v2_payload,
    song_meta_from_entry,
)


class PlaylistFileError(RuntimeError):
    """The playlist file could not be read or is not a valid v2 file."""


class PlaylistWriteError(RuntimeError):
    """The playlist file could not be written."""


@dataclass
class PlaylistDocument:
    """A parsed v2 playlist, keeping the payload for in-place edits."""

    path: Path
    payload: dict[str, Any]
    name: str | None
    entries: list[Any]


@dataclass
class PlaylistEntrySong:
    """A playlist entry shaped like the library songs the UI already handles."""

    entry: dict[str, Any]
    index: int
    current: dict[str, str]
    path: Path
    uuid: str | None
    downloaded: bool
    source_url: str
    row: sqlite3.Row | None = None

    @property
    def display_name(self) -> str:
        title: str = self.current.get("title") or self.path.stem
        artists: str = display_authors(self.current.get("artists", ""))
        if artists:
            return f"{title} - {artists}"
        return title


def load_playlist_document(path: Path) -> PlaylistDocument:
    """Read and validate a v2 playlist JSON file."""
    try:
        text: str = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise PlaylistFileError(f"could not read '{path}': {exc}") from exc
    try:
        payload, name, entries = parse_json_v2_payload(text)
    except ValueError as exc:
        raise PlaylistFileError(f"invalid playlist '{path}': {exc}") from exc
    return PlaylistDocument(path=path, payload=payload, name=name, entries=entries)


def write_playlist_document(document: PlaylistDocument) -> None:
    """Atomically rewrite the playlist JSON in the standard v2 shape."""
    temp: Path = document.path.with_name(f".{document.path.name}.smclipy-tmp")
    try:
        temp.write_text(json.dumps(document.payload, indent=2) + "\n", encoding="utf-8")
        os.replace(str(temp), str(document.path))
    except OSError as exc:
        with suppress(OSError):
            temp.unlink()
        raise PlaylistWriteError(f"could not write '{document.path}': {exc}") from exc


def _entry_raw_path(entry: dict[str, Any]) -> str:
    raw = entry.get("path")
    return raw.strip() if isinstance(raw, str) else ""


def _entry_audio_path(
    entry: dict[str, Any], base_dir: Path, row: sqlite3.Row | None
) -> Path | None:
    if row is not None and row["current_path"]:
        candidate: Path = settings().music_folder.joinpath(str(row["current_path"]))
        if candidate.is_file():
            return candidate
    raw: str = _entry_raw_path(entry)
    if not raw:
        return None
    path: Path = Path(raw)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    candidates.append(base_dir.joinpath(path))
    candidates.append(settings().music_folder.joinpath(path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _row_text(row: sqlite3.Row | None, field: str) -> str:
    if row is None:
        return ""
    value: Any = row[field]
    return str(value or "")


def _entry_current(
    entry: dict[str, Any],
    index: int,
    row: sqlite3.Row | None,
    path: Path | None,
) -> dict[str, str]:
    meta = song_meta_from_entry(entry)

    def fallback(field: str, meta_value: str) -> str:
        return meta_value or _row_text(row, field)

    title: str = fallback("title", meta.title)
    if not title:
        title = path.stem if path is not None else ""
    if not title:
        title = f"(entry {index})"
    artists: str = (
        join_authors(meta.artists) if meta.artists else _row_text(row, "artists")
    )
    return {
        "title": title,
        "artists": artists,
        "album": fallback("album", meta.album),
        "date": fallback("date", meta.date),
        "genre": fallback("genre", meta.genre),
        "album_artist": fallback("album_artist", meta.album_artist),
        "track_number": fallback("track_number", meta.track_number),
        "source_url": entry_source_url(entry) or "",
    }


def build_entry_songs(
    document: PlaylistDocument, *, resolve: bool = True
) -> list[PlaylistEntrySong]:
    """Adapt every playlist entry to the shared song/UI interface."""
    by_uuid: dict[str, str] = {}
    by_abs: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    if resolve:
        by_uuid, by_abs, by_name = _index_songs()
    music_folder: Path = settings().music_folder
    songs: list[PlaylistEntrySong] = []
    for index, entry in enumerate(document.entries, start=1):
        if not isinstance(entry, dict):
            raise PlaylistFileError(f"playlist entry {index} is not an object")
        resolved: str | None = None
        row: sqlite3.Row | None = None
        if resolve:
            resolved = _resolve_entry(
                entry, music_folder, document.path.parent, by_uuid, by_abs, by_name
            )
            if resolved is not None:
                row = db.get_song_by_uuid(resolved)
        path: Path | None = _entry_audio_path(entry, document.path.parent, row)
        current: dict[str, str] = _entry_current(entry, index, row, path)
        songs.append(
            PlaylistEntrySong(
                entry=entry,
                index=index,
                current=current,
                path=path if path is not None else Path(current["title"]),
                uuid=resolved,
                downloaded=bool(row is not None and row["current_path"]),
                source_url=entry_source_url(entry) or "",
                row=row,
            )
        )
    return songs


def set_entry_source_url(entry: dict[str, Any], url: str) -> bool:
    """Store ``url`` on a playlist entry, returning whether it changed."""
    current: str = entry_source_url(entry) or ""
    if current == url:
        return False
    entry["source_url"] = url
    return True


def apply_entry_values(entry: dict[str, Any], values: dict[str, Any]) -> set[str]:
    """Apply collected modify values to one playlist entry."""
    changed: set[str] = set()
    for field in (
        "title",
        "album",
        "date",
        "album_artist",
        "track_number",
        "source_url",
    ):
        value: Any = values.get(field)
        if value is None:
            continue
        text: str = str(value)
        if str(entry.get(field, "")) != text:
            entry[field] = text
            changed.add(field)

    artists: Any = values.get("artists")
    if artists is not None:
        new_artists: list[str] = list(artists)
        old_artists: list[str] = song_meta_from_entry(entry).artists
        if sorted(new_artists) != sorted(old_artists):
            entry["artists_list"] = new_artists
            entry["artists"] = display_authors(join_authors(new_artists))
            changed.add("artists")

    genre: Any = values.get("genre")
    if genre is not None:
        new_genres: list[str] = [
            part.strip() for part in str(genre).split(";") if part.strip()
        ]
        old_genres: list[str] = [
            part.strip()
            for part in song_meta_from_entry(entry).genre.split(";")
            if part.strip()
        ]
        if new_genres != old_genres:
            entry["genres"] = new_genres
            changed.add("genre")
    return changed
