"""Library playlists stored in the tracking database.

Playlists reference songs by their tracking UUID, so a song keeps its
playlist slot through renames and relocations. Contents can be exported
to ``.m3u`` or ``.json`` and re-imported (resolving entries back to
tracked songs), and can be ordered by hand or sorted by common metadata.
"""

import json
import os
import shlex
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import smclipy.db as db
from smclipy.config import settings
from smclipy.helpers import display_authors, sanitize_filename, split_authors
from smclipy.metadata import scan_library
from smclipy.ui import prompt_position_selection, prompt_song_selection

M3U_HEADER = "#EXTM3U"
JSON_PLAYLIST_VERSION = 2

SORT_KEYS: dict[str, str] = {
    "title": "title",
    "artists": "artist",
    "album": "album",
    "path": "path",
    "added": "date added",
}


@dataclass(frozen=True)
class PlaylistSongMeta:
    """Metadata embedded in a playlist JSON entry for a missing song."""

    uuid: str | None = None
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    date: str = ""
    track_number: str = ""
    source_url: str = ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ]


def entry_source_url(entry: Any) -> str | None:
    """The download URL carried by a playlist entry, if any."""
    if not isinstance(entry, dict):
        return None
    source_url: str = _text(entry.get("source_url"))
    return source_url or None


def song_meta_from_entry(entry: Any) -> PlaylistSongMeta:
    """Read the v2 metadata fields from one playlist entry.

    ``artists_list`` (written by newer exports) is preferred because the
    human-facing ``artists`` string cannot represent names containing commas
    unambiguously. Playlists from other tools still fall back to splitting
    ``artists``.
    """
    if not isinstance(entry, dict):
        return PlaylistSongMeta()
    artists: list[str] = _text_list(entry.get("artists_list"))
    if not artists:
        artists = split_authors(_text(entry.get("artists")))
    raw_genres: Any = entry.get("genres")
    genre: str = (
        "; ".join(_text_list(raw_genres))
        if isinstance(raw_genres, list)
        else "; ".join(_genres(raw_genres))
    )
    return PlaylistSongMeta(
        uuid=_text(entry.get("uuid")) or None,
        title=_text(entry.get("title")),
        artists=artists,
        album=_text(entry.get("album")),
        album_artist=_text(entry.get("album_artist")),
        genre=genre,
        date=_text(entry.get("date")),
        track_number=_text(entry.get("track_number")),
        source_url=_text(entry.get("source_url")),
    )


def _fallback_title(row: sqlite3.Row) -> str:
    path: str = str(row["current_path"] or row["first_seen_path"] or "")
    return Path(path).stem or "(unknown title)"


def _song_label(row: sqlite3.Row, *, extinf: bool = False) -> str:
    """``Title - Artists`` for listing, ``Artists - Title`` for EXTINF."""
    title: str = str(row["title"] or "") or _fallback_title(row)
    artists: str = display_authors(str(row["artists"] or ""))
    if extinf:
        return f"{artists} - {title}" if artists else title
    return f"{title} - {artists}" if artists else title


def _export_path(
    row: sqlite3.Row,
    music_folder: Path,
    export_dir: Path,
    *,
    absolute: bool,
) -> str | None:
    rel: Any = row["current_path"]
    if not rel:
        return None
    resolved: Path = music_folder.joinpath(str(rel)).resolve()
    if absolute:
        return str(resolved)
    return os.path.relpath(str(resolved), str(export_dir.resolve()))


def _m3u_label(row: sqlite3.Row) -> str:
    """The EXTINF label, flattened to one line (newlines would break the tag)."""
    return " ".join(_song_label(row, extinf=True).splitlines())


def serialize_m3u(
    rows: list[sqlite3.Row],
    export_dir: Path,
    music_folder: Path,
    *,
    absolute: bool = False,
) -> str:
    """Render rows as an ``.m3u`` file; missing songs are skipped."""
    lines: list[str] = [M3U_HEADER]
    for row in rows:
        path: str | None = _export_path(
            row, music_folder, export_dir, absolute=absolute
        )
        if path is None:
            continue
        lines.append(f"#EXTINF:-1,{_m3u_label(row)}")
        lines.append(path)
    return "\n".join(lines) + "\n"


def serialize_m3u_link(rows: list[sqlite3.Row]) -> str:
    """Render rows as a link ``.m3u`` playlist: one source URL per line.

    Each URL is preceded by an ``#EXTINF`` line carrying the song's artists
    and title. Songs without a recorded source URL are skipped.
    """
    entries: list[str] = []
    for row in rows:
        url: str = str(row["source_url"] or "").strip()
        if not url:
            continue
        entries.append(f"#EXTINF:-1,{_m3u_label(row)}")
        entries.append(url)
    if not entries:
        return ""
    return "\n".join([M3U_HEADER, *entries]) + "\n"


def _genres(value: Any) -> list[str]:
    """Split the stored ``"; "``-joined genre string into a clean list."""
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def serialize_json(
    name: str,
    rows: list[sqlite3.Row],
    music_folder: Path,
    *,
    export_dir: Path | None = None,
    absolute: bool = False,
) -> str:
    """Render rows as the smclipy playlist JSON format; missing songs are
    skipped like they are in the m3u export.

    Relative paths are written relative to ``export_dir`` (the directory of
    the exported file), matching the m3u format and the CLI docs, so import
    resolves them the same way.
    """
    songs: list[dict[str, Any]] = []
    for row in rows:
        path: str | None = _export_path(
            row, music_folder, export_dir or music_folder, absolute=absolute
        )
        if path is None:
            continue
        songs.append(
            {
                "uuid": str(row["uuid"]),
                "title": str(row["title"] or ""),
                "artists": display_authors(str(row["artists"] or "")),
                "artists_list": split_authors(str(row["artists"] or "")),
                "album": str(row["album"] or ""),
                "album_artist": str(row["album_artist"] or ""),
                "genres": _genres(row["genre"]),
                "date": str(row["date"] or ""),
                "track_number": str(row["track_number"] or ""),
                "source_url": str(row["source_url"] or ""),
                "path": path,
            }
        )
    payload: dict[str, Any] = {
        "smclipy_playlist": JSON_PLAYLIST_VERSION,
        "name": name,
        "songs": songs,
    }
    return json.dumps(payload, indent=2) + "\n"


def parse_m3u(text: str) -> list[str]:
    """Extract the entry paths/URLs from an ``.m3u`` file."""
    entries: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line.replace("\\", "/"))
    return entries


def _parse_json_payload(text: str) -> dict[str, Any]:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid playlist JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("invalid playlist JSON: expected an object")
    return data


def _payload_entries(data: dict[str, Any]) -> tuple[str | None, list[Any]]:
    songs: Any = data.get("songs")
    if not isinstance(songs, list):
        raise ValueError("invalid playlist JSON: expected a 'songs' list")
    raw_name: Any = data.get("name")
    name: str | None = (
        str(raw_name) if isinstance(raw_name, str) and raw_name.strip() else None
    )
    return name, songs


def parse_json(text: str) -> tuple[str | None, list[Any]]:
    """Parse the smclipy playlist JSON format into (name, entries)."""
    return _payload_entries(_parse_json_payload(text))


def parse_json_v2_payload(
    text: str,
) -> tuple[dict[str, Any], str | None, list[Any]]:
    """Parse a v2 playlist while keeping the full payload for editing."""
    data: dict[str, Any] = _parse_json_payload(text)
    raw_version: Any = data.get("smclipy_playlist")
    try:
        version: int = int(raw_version)
    except (TypeError, ValueError):
        raise ValueError(
            "invalid playlist JSON: missing 'smclipy_playlist' version"
        ) from None
    if version != JSON_PLAYLIST_VERSION:
        raise ValueError(
            f"unsupported playlist version {version}; expected {JSON_PLAYLIST_VERSION}"
        )
    name, entries = _payload_entries(data)
    return data, name, entries


def parse_json_v2(text: str) -> tuple[str | None, list[Any]]:
    """Parse a playlist JSON file that must use the current v2 format."""
    _data, name, entries = parse_json_v2_payload(text)
    return name, entries


def _format_from_filename(filename: str) -> str:
    ext: str = Path(filename).suffix.lower().lstrip(".")
    if ext == "m3u-link":
        return "m3u-link"
    if ext in ("m3u", "m3u8"):
        return "m3u"
    if ext == "json":
        return "json"
    return ""


def _index_songs() -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """UUID/absolute-path/basename lookup tables for import.

    The paths point into the music folder; each basename maps to the list of
    *distinct* UUIDs that share it, so ambiguity stays detectable (the same
    song usually appears under both its current and first-seen path, which
    must not count as two matches). Built once so entry resolution never
    hits the database per entry.
    """
    by_uuid: dict[str, str] = {}
    by_abs: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    music_folder: Path = settings().music_folder
    for row in db.list_songs():
        uuid: str = str(row["uuid"])
        by_uuid[uuid] = uuid
        name_indexed: set[str] = set()
        for rel in (row["current_path"], row["first_seen_path"]):
            if not rel:
                continue
            by_abs.setdefault(str(music_folder.joinpath(str(rel)).resolve()), uuid)
            name: str = Path(str(rel)).name
            if name not in name_indexed:
                name_indexed.add(name)
                by_name.setdefault(name, []).append(uuid)
    return by_uuid, by_abs, by_name


def _resolve_entry(
    entry: Any,
    music_folder: Path,
    base_dir: Path,
    by_uuid: dict[str, str],
    by_abs: dict[str, str],
    by_name: dict[str, list[str]],
) -> str | None:
    if isinstance(entry, dict):
        uid: Any = entry.get("uuid")
        if isinstance(uid, str) and uid and by_uuid.get(uid) is not None:
            return uid
        raw = entry.get("path")
    else:
        raw = entry
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.replace("\\", "/")
    path: Path = Path(raw)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    candidates.append(base_dir / path)
    candidates.append(music_folder / path)
    for candidate in candidates:
        uuid = by_abs.get(str(Path(os.path.normpath(str(candidate))).resolve()))
        if uuid is not None:
            return uuid
    matches: list[str] = by_name.get(path.name, [])
    if len(matches) == 1:
        return matches[0]
    return None


def _require_playlist(name: str) -> sqlite3.Row:
    row = db.get_playlist(name)
    if row is None:
        print(f"Error: playlist '{name}' not found.", file=sys.stderr)
        raise SystemExit(1)
    return row


def cmd_playlist_create(args: Any) -> None:
    name: str = args.name.strip()
    if not name:
        print("Error: playlist name cannot be empty.", file=sys.stderr)
        raise SystemExit(1)
    if db.create_playlist(name, str(args.description or "")):
        print(f"Playlist '{name}' created.")
    else:
        print(f"Error: playlist '{name}' already exists.", file=sys.stderr)
        raise SystemExit(1)


def cmd_playlist_rename(args: Any) -> None:
    old_name: str = args.name.strip()
    new_name: str = args.new_name.strip()
    if not old_name or not new_name:
        print("Error: playlist names cannot be empty.", file=sys.stderr)
        raise SystemExit(1)
    if old_name == new_name:
        print(f"Playlist '{old_name}' already has that name.")
        return
    if db.rename_playlist(old_name, new_name):
        print(f"Renamed '{old_name}' to '{new_name}'.")
    elif db.get_playlist(old_name) is None:
        print(f"Error: playlist '{old_name}' not found.", file=sys.stderr)
        raise SystemExit(1)
    else:
        print(f"Error: playlist '{new_name}' already exists.", file=sys.stderr)
        raise SystemExit(1)


def cmd_playlist_delete(args: Any) -> None:
    name: str = args.name.strip()
    if db.delete_playlist(name):
        print(f"Deleted playlist '{name}'.")
    else:
        print(f"Error: playlist '{name}' not found.", file=sys.stderr)
        raise SystemExit(1)


def cmd_playlist_list(args: Any) -> None:
    rows = db.list_playlists()
    if getattr(args, "json", False):
        print(
            json.dumps(
                [
                    {
                        "name": row["name"],
                        "description": row["description"] or "",
                        "song_count": int(row["song_count"]),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                    for row in rows
                ],
                indent=2,
            )
        )
        return
    if not rows:
        print("No playlists yet. Create one with `smclipy playlist create <name>`.")
        return
    for row in rows:
        count: int = int(row["song_count"])
        if count:
            suffix: str = f" ({count} song{'s' if count != 1 else ''})"
        else:
            suffix = " (empty)"
        print(f"{row['name']}{suffix}")


def cmd_playlist_show(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    rows = db.get_playlist_songs(name)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "name": name,
                    "songs": [
                        {
                            "position": index,
                            "uuid": row["uuid"],
                            "title": str(row["title"] or "") or _fallback_title(row),
                            "artists": display_authors(str(row["artists"] or "")),
                            "album": row["album"] or "",
                            "path": row["current_path"],
                            "missing": not row["current_path"],
                        }
                        for index, row in enumerate(rows, start=1)
                    ],
                },
                indent=2,
            )
        )
        return
    print(f"Playlist '{name}' ({len(rows)} song{'s' if len(rows) != 1 else ''}):")
    if rows:
        for index, row in enumerate(rows, start=1):
            missing: str = " (missing)" if not row["current_path"] else ""
            print(f"{index:3}. {_song_label(row)}{missing}")
    else:
        print("  (empty)")


def _scan_and_list_songs() -> list[sqlite3.Row]:
    print("Scanning music files...")
    scan_library()
    return db.list_songs()


def cmd_playlist_add(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    songs = [row for row in _scan_and_list_songs() if row["current_path"]]
    if not songs:
        print("No songs in the library to add.")
        return
    present: set[str] = {str(row["uuid"]) for row in db.get_playlist_songs(name)}
    print(f"\nSongs in your library ('[in playlist]' songs are already in '{name}'):")
    for index, row in enumerate(songs, start=1):
        marker: str = " [in playlist]" if str(row["uuid"]) in present else ""
        print(f"{index:3}. {_song_label(row)}{marker}")

    selected: list[int] = prompt_song_selection(len(songs))
    added = already = 0
    for position in selected:
        row = songs[position]
        if db.add_song_to_playlist(name, str(row["uuid"])):
            added += 1
        else:
            already += 1
    if selected:
        message: str = f"Added {added} song(s) to '{name}'."
        if already:
            message += f" Skipped {already} already present."
        print(message)


def cmd_playlist_remove(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    rows = db.get_playlist_songs(name)
    if not rows:
        print(f"Playlist '{name}' has no songs to remove.")
        return
    print(f"\nSongs in '{name}':")
    for index, row in enumerate(rows, start=1):
        print(f"{index:3}. {_song_label(row)}")

    selected: list[int] = prompt_song_selection(len(rows))
    removed = 0
    for position in selected:
        row = rows[position]
        if db.remove_song_from_playlist(name, str(row["uuid"])):
            removed += 1
    if selected:
        print(f"Removed {removed} song(s) from '{name}'.")


def cmd_playlist_move(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    rows = db.get_playlist_songs(name)
    if len(rows) < 2:
        print(f"Playlist '{name}' needs at least two songs to reorder.")
        return
    print(f"\nCurrent order of '{name}':")
    for index, row in enumerate(rows, start=1):
        print(f"{index:3}. {_song_label(row)}")

    song_index: int | None = prompt_position_selection(
        len(rows), "Pick a song to move (1-N), '0' or 'q' to cancel: "
    )
    if song_index is None:
        return
    target: int | None = prompt_position_selection(
        len(rows), f"Move it to a position (1-{len(rows)}), '0' or 'q' to cancel: "
    )
    if target is None:
        return
    row = rows[song_index]
    db.move_playlist_song(name, str(row["uuid"]), target + 1)
    print(f"\nMoved '{_song_label(row, extinf=True)}' to position {target + 1}.")
    print(f"New order of '{name}':")
    for index, moved in enumerate(db.get_playlist_songs(name), start=1):
        print(f"{index:3}. {_song_label(moved)}")


def cmd_playlist_sort(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    rows = db.get_playlist_songs(name)
    if len(rows) < 2:
        print(f"Playlist '{name}' needs at least two songs to sort.")
        return
    direction: str = "descending" if args.reverse else "ascending"
    db.sort_playlist(name, key=args.key, reverse=bool(args.reverse))
    print(f"\nSorted '{name}' by {SORT_KEYS.get(args.key, args.key)} ({direction}).")
    for index, row in enumerate(db.get_playlist_songs(name), start=1):
        print(f"{index:3}. {_song_label(row)}")


def _infer_export_format(args: Any) -> str:
    """An explicit -f/--format wins; otherwise infer from --output; else m3u."""
    explicit: str = getattr(args, "format", "") or ""
    if explicit:
        return explicit
    inferred: str = _format_from_filename(args.output or "")
    return inferred or "m3u"


def cmd_playlist_export(args: Any) -> None:
    name: str = args.name.strip()
    _require_playlist(name)
    rows = db.get_playlist_songs(name)
    if not rows:
        print(f"Playlist '{name}' is empty; nothing to export.")
        return
    s = settings()
    if not bool(getattr(args, "no_scan", False)):
        if not s.music_folder.is_dir():
            print(
                f"Error: music folder not found at '{s.music_folder}'.", file=sys.stderr
            )
            raise SystemExit(1)
        print("Scanning music files...")
        scan_library()
        rows = db.get_playlist_songs(name)
    fmt: str = _infer_export_format(args)
    default_ext: str = "m3u" if fmt == "m3u-link" else fmt
    out_path: Path = (
        Path(args.output).expanduser()
        if args.output
        else Path(f"{sanitize_filename(name) or 'playlist'}.{default_ext}")
    )
    if fmt == "m3u-link":
        missing_count: int = sum(
            1 for row in rows if not str(row["source_url"] or "").strip()
        )
        content: str = serialize_m3u_link(rows)
    else:
        missing_count = sum(1 for row in rows if not row["current_path"])
    if fmt == "m3u":
        content = serialize_m3u(
            rows,
            out_path.parent,
            s.music_folder,
            absolute=bool(args.absolute),
        )
    elif fmt == "json":
        content = serialize_json(
            name,
            rows,
            s.music_folder,
            export_dir=out_path.parent,
            absolute=bool(args.absolute),
        )
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"Error: could not write '{out_path}': {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    exported: int = len(rows) - missing_count
    message: str = (
        f"Exported playlist '{name}' to {out_path} ({exported} song"
        f"{'s' if exported != 1 else ''})."
    )
    if missing_count:
        reason: str = "missing" if fmt != "m3u-link" else "sourceless"
        message += (
            f" Skipped {missing_count} {reason} song"
            f"{'s' if missing_count != 1 else ''}."
        )
    print(message)


def _print_download_hint(
    fmt: str,
    path: Path,
    missing_entries: list[Any],
    *,
    stream: TextIO | None = None,
) -> None:
    """Suggest the download command that can fetch missing JSON entries."""
    if fmt != "json" or not missing_entries:
        return
    out: TextIO = stream or sys.stdout
    with_source: list[Any] = [
        entry for entry in missing_entries if entry_source_url(entry)
    ]
    if not with_source:
        print(
            "Hint: those songs have no source URL, so they cannot be "
            "downloaded automatically.",
            file=out,
        )
        return
    command: str = f"smclipy download --file {shlex.quote(str(path))}"
    total: int = len(missing_entries)
    if len(with_source) == total:
        print(
            f"Hint: run `{command}` to download them and apply the playlist "
            "metadata automatically.",
            file=out,
        )
    else:
        print(
            f"Hint: run `{command}` to download {len(with_source)} of them and "
            f"apply the playlist metadata automatically; the other "
            f"{total - len(with_source)} have no source URL.",
            file=out,
        )


def cmd_playlist_import(args: Any) -> None:
    path: Path = Path(args.file).expanduser()
    if not path.is_file():
        print(f"Error: '{args.file}' not found.", file=sys.stderr)
        raise SystemExit(1)
    fmt: str = _format_from_filename(path.name)
    if not fmt:
        print(
            "Error: unsupported playlist file. Use .m3u/.m3u8 or .json.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        text: str = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        print(f"Error: could not read '{path}': {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    json_name: str | None
    entries: list[Any]
    if fmt == "json":
        try:
            json_name, entries = parse_json(text)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
    else:
        json_name = None
        entries = parse_m3u(text)
    if not entries:
        print(f"No playable entries found in '{path.name}'.")
        return

    name: str = (args.name or json_name or path.stem).strip()
    if not name:
        print(
            "Error: could not determine a playlist name; pass --name.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    playlist = db.get_playlist(name)
    if playlist is not None and not args.replace:
        print(
            f"Error: playlist '{name}' already exists; pass --replace to "
            "overwrite its contents.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("Scanning music files...")
    scan_library()
    music_folder: Path = settings().music_folder
    by_uuid, by_abs, by_name = _index_songs()
    uuids: list[str] = []
    unresolved: list[Any] = []
    missing_files: list[Any] = []
    for entry in entries:
        uuid: str | None = _resolve_entry(
            entry, music_folder, path.parent, by_uuid, by_abs, by_name
        )
        if uuid is None:
            unresolved.append(entry)
            continue
        row = db.get_song_by_uuid(uuid)
        if row is not None and not row["current_path"]:
            missing_files.append(entry)
        if uuid not in uuids:
            uuids.append(uuid)
    not_found_entries: list[Any] = unresolved + missing_files
    not_found: int = len(not_found_entries)

    if not uuids:
        # Nothing resolved (wrong folder, renamed library): creating an empty
        # playlist or clearing a populated one would silently destroy data.
        action_word: str = "replace" if playlist is not None else "create"
        print(
            f"Error: {not_found} song(s) not found in your library; refusing to "
            f"{action_word} playlist '{name}' from '{path.name}'.",
            file=sys.stderr,
        )
        _print_download_hint(fmt, path, not_found_entries, stream=sys.stderr)
        raise SystemExit(1)

    with db.transaction() as conn:
        if playlist is None:
            db.create_playlist(name, conn=conn)
        if args.replace:
            db.set_playlist_songs(name, uuids, conn=conn)
        else:
            for uuid in uuids:
                db.add_song_to_playlist(name, uuid, conn=conn)

    message: str = (
        f"Imported {len(uuids)} song(s) into playlist '{name}' from '{path.name}'."
    )
    if not_found:
        message += f" {not_found} song(s) not found in your library."
    print(message)
    _print_download_hint(fmt, path, not_found_entries)


_ACTIONS: dict[str, Any] = {
    "create": cmd_playlist_create,
    "rename": cmd_playlist_rename,
    "delete": cmd_playlist_delete,
    "list": cmd_playlist_list,
    "show": cmd_playlist_show,
    "add": cmd_playlist_add,
    "remove": cmd_playlist_remove,
    "move": cmd_playlist_move,
    "sort": cmd_playlist_sort,
    "export": cmd_playlist_export,
    "import": cmd_playlist_import,
}


def cmd_playlist(args: Any) -> None:
    action: str = getattr(args, "playlist_action", "")
    handler = _ACTIONS.get(action)
    if handler is None:
        print(f"Error: unknown playlist action {action!r}.", file=sys.stderr)
        raise SystemExit(1)
    handler(args)
