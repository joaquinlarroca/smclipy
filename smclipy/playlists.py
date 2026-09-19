"""Library playlists stored in the tracking database.

Playlists reference songs by their tracking UUID, so a song keeps its
playlist slot through renames and relocations. Contents can be exported
to ``.m3u`` or ``.json`` and re-imported (resolving entries back to
tracked songs), and can be ordered by hand or sorted by common metadata.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import smclipy.db as db
from smclipy.config import settings
from smclipy.helpers import sanitize_filename
from smclipy.metadata import scan_library
from smclipy.ui import prompt_position_selection, prompt_song_selection

M3U_HEADER = "#EXTM3U"
JSON_PLAYLIST_VERSION = 1

SORT_KEYS: dict[str, str] = {
    "title": "title",
    "artists": "artist",
    "album": "album",
    "path": "path",
    "added": "date added",
}


def _fallback_title(row: sqlite3.Row) -> str:
    path: str = str(row["current_path"] or row["first_seen_path"] or "")
    return Path(path).stem or "(unknown title)"


def _song_label(row: sqlite3.Row, *, extinf: bool = False) -> str:
    """``Title - Artists`` for listing, ``Artists - Title`` for EXTINF."""
    title: str = str(row["title"] or "") or _fallback_title(row)
    artists: str = str(row["artists"] or "")
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


def serialize_m3u(
    name: str,
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
        lines.append(f"#EXTINF:-1,{_song_label(row, extinf=True)}")
        lines.append(path)
    return "\n".join(lines) + "\n"


def serialize_json(
    name: str,
    rows: list[sqlite3.Row],
    music_folder: Path,
    *,
    absolute: bool = False,
) -> str:
    """Render rows as the smclipy playlist JSON format; missing songs are
    skipped like they are in the m3u export."""
    songs: list[dict[str, str]] = []
    for row in rows:
        rel: Any = row["current_path"]
        if not rel:
            continue
        path: Path = music_folder.joinpath(str(rel))
        songs.append(
            {
                "uuid": str(row["uuid"]),
                "title": str(row["title"] or ""),
                "artists": str(row["artists"] or ""),
                "path": str(path.resolve()) if absolute else str(rel),
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


def parse_json(text: str) -> tuple[str | None, list[Any]]:
    """Parse the smclipy playlist JSON format into (name, entries)."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid playlist JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("invalid playlist JSON: expected an object")
    songs: Any = data.get("songs")
    if not isinstance(songs, list):
        raise ValueError("invalid playlist JSON: expected a 'songs' list")
    raw_name: Any = data.get("name")
    name: str | None = (
        str(raw_name) if isinstance(raw_name, str) and raw_name.strip() else None
    )
    return name, songs


def _format_from_filename(filename: str) -> str:
    ext: str = Path(filename).suffix.lower().lstrip(".")
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
                            "position": int(row["position"]),
                            "uuid": row["uuid"],
                            "title": str(row["title"] or "") or _fallback_title(row),
                            "artists": row["artists"] or "",
                            "album": row["album"] or "",
                            "path": row["current_path"],
                            "missing": not row["current_path"],
                        }
                        for row in rows
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
    songs = _scan_and_list_songs()
    if not songs:
        print("No songs in the library to add.")
        return
    present: set[str] = {str(row["uuid"]) for row in db.get_playlist_songs(name)}
    print(f"\nSongs in your library ('[in playlist]' songs are already in '{name}'):")
    for index, row in enumerate(songs, start=1):
        marker: str = " [in playlist]" if str(row["uuid"]) in present else ""
        missing: str = " (missing)" if not row["current_path"] else ""
        print(f"{index:3}. {_song_label(row)}{marker}{missing}")

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
    fmt: str = _infer_export_format(args)
    out_path: Path = (
        Path(args.output).expanduser()
        if args.output
        else Path(f"{sanitize_filename(name)}.{fmt}")
    )
    s = settings()
    missing_count: int = sum(1 for row in rows if not row["current_path"])
    if fmt == "m3u":
        content: str = serialize_m3u(
            name,
            rows,
            out_path.parent,
            s.music_folder,
            absolute=bool(args.absolute),
        )
    else:
        content = serialize_json(
            name, rows, s.music_folder, absolute=bool(args.absolute)
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
        message += (
            f" Skipped {missing_count} missing song{'s' if missing_count != 1 else ''}."
        )
    print(message)


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
    unresolved: int = 0
    for entry in entries:
        uuid: str | None = _resolve_entry(
            entry, music_folder, path.parent, by_uuid, by_abs, by_name
        )
        if uuid is None:
            unresolved += 1
            continue
        if uuid not in uuids:
            uuids.append(uuid)

    if playlist is None:
        db.create_playlist(name)
    if args.replace:
        db.set_playlist_songs(name, uuids)
    else:
        for uuid in uuids:
            db.add_song_to_playlist(name, uuid)

    message: str = (
        f"Imported {len(uuids)} song(s) into playlist '{name}' from '{path.name}'."
    )
    if unresolved:
        message += (
            f" {unresolved} entr{'y' if unresolved == 1 else 'ies'} did not "
            "match a library song."
        )
    print(message)


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
