"""The ``restore`` command: undo tag changes from recorded backups."""

from datetime import datetime
from typing import Any, NamedTuple

from smclipy.config import init
from smclipy.helpers import display_authors
from smclipy.metadata import (
    Snapshot,
    list_snapshots,
    restore_song_from_snapshot,
    scan_library,
)
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import (
    TAG_FIELD_LABELS,
    prompt_confirm,
    prompt_song_selection,
)

_FIELD_ORDER: tuple[str, ...] = (
    "title",
    "artists",
    "album",
    "date",
    "genre",
    "album_artist",
    "track_number",
)

_SOURCE_LABELS: dict[str, str] = {
    "tag": "Before tag",
    "modify": "Before modify",
    "crop": "Before crop",
    "dedupe": "Before dedupe",
    "restore": "Before restore",
}

_MAX_VALUE_WIDTH = 40
_DEFAULT_LIMIT = 25


class RestorePoint(NamedTuple):
    """One snapshot paired with the song it belongs to and its diff."""

    song: LibrarySong
    snapshot: Snapshot
    changes: list[tuple[str, str, str]]
    cover_change: str | None


def _source_label(source: str) -> str:
    if source in _SOURCE_LABELS:
        return _SOURCE_LABELS[source]
    return f"Before {source}" if source else "Backup"


def _parse_at(at: str) -> datetime | None:
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def _relative_time(moment: datetime) -> str:
    seconds = int((datetime.now() - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 604800:
        return f"{seconds // 86400}d ago"
    return moment.strftime("%Y-%m-%d")


def _format_at(at: str) -> tuple[str, str]:
    """Return ``(absolute, relative)`` labels for a snapshot timestamp."""
    moment = _parse_at(at)
    if moment is None:
        return at, ""
    return moment.strftime("%Y-%m-%d %H:%M"), _relative_time(moment)


def _format_value(field: str, value: str) -> str:
    if not value:
        return "(empty)"
    text = display_authors(value) if field == "artists" else value
    if len(text) > _MAX_VALUE_WIDTH:
        text = text[: _MAX_VALUE_WIDTH - 3] + "..."
    return text


def _snapshot_changes(
    current: dict[str, str], current_has_cover: bool, snapshot: Snapshot
) -> tuple[list[tuple[str, str, str]], str | None]:
    changes: list[tuple[str, str, str]] = []
    for field in _FIELD_ORDER:
        before = current.get(field, "")
        after = getattr(snapshot, field)
        if before != after:
            changes.append((field, before, after))

    cover_change: str | None = None
    snapshot_has_cover = snapshot.cover is not None
    if snapshot_has_cover and not current_has_cover:
        cover_change = "added from backup"
    elif snapshot_has_cover and current_has_cover:
        cover_change = "restored from backup"
    elif not snapshot_has_cover and current_has_cover:
        cover_change = "removed"
    return changes, cover_change


def _summarize(changes: list[tuple[str, str, str]], cover_change: str | None) -> str:
    parts: list[str] = [
        f"{TAG_FIELD_LABELS.get(field, field)}: "
        f"{_format_value(field, before)} -> {_format_value(field, after)}"
        for field, before, after in changes
    ]
    if cover_change is not None:
        parts.append(f"Cover: {cover_change}")
    if len(parts) > 2:
        return "; ".join(parts[:2]) + f"; +{len(parts) - 2} more"
    return "; ".join(parts)


def _collect_restore_points(songs: list[LibrarySong]) -> list[RestorePoint]:
    points: list[RestorePoint] = []
    for song in songs:
        if song.uuid is None:
            continue
        for snapshot in list_snapshots(song.uuid):
            changes, cover_change = _snapshot_changes(
                song.current, song.has_cover, snapshot
            )
            if not changes and cover_change is None:
                continue
            points.append(RestorePoint(song, snapshot, changes, cover_change))
    points.sort(key=lambda point: point.snapshot.at, reverse=True)
    return points


def _newest_per_song(points: list[RestorePoint]) -> list[RestorePoint]:
    newest: dict[str, RestorePoint] = {}
    for point in points:
        song_uuid = point.song.uuid
        assert song_uuid is not None
        current = newest.get(song_uuid)
        if current is None or point.snapshot.at > current.snapshot.at:
            newest[song_uuid] = point

    chosen: list[RestorePoint] = []
    seen: set[str] = set()
    for point in points:
        song_uuid = point.song.uuid
        assert song_uuid is not None
        if song_uuid in seen:
            continue
        seen.add(song_uuid)
        chosen.append(newest[song_uuid])
    return chosen


def _print_point(number: int, point: RestorePoint) -> None:
    absolute, relative = _format_at(point.snapshot.at)
    age = relative or absolute
    print(
        f"  {number}. {age:<10} {point.song.display_name}"
        f"  ({_source_label(point.snapshot.source)})"
        f"  {_summarize(point.changes, point.cover_change)}"
    )


def _print_preview(number: int, total: int, point: RestorePoint) -> None:
    absolute, relative = _format_at(point.snapshot.at)
    age = f", {relative}" if relative else ""
    print(f"\nRestore point {number}/{total}")
    print(f"  Song:   {point.song.display_name}")
    print(f"  Backup: {_source_label(point.snapshot.source)} ({absolute}{age})")
    print("  Changes:")
    for field, before, after in point.changes:
        print(
            f"    {TAG_FIELD_LABELS.get(field, field)}: "
            f"{_format_value(field, before)} -> {_format_value(field, after)}"
        )
    if point.cover_change is not None:
        print(f"    Cover: {point.cover_change}")


def cmd_restore(args: Any) -> None:
    """Browse recorded restore points and roll a song back to one.

    Snapshots are written automatically before every ``tag``, ``modify``,
    ``crop``, and ``dedupe`` change, so a mistaken match, edit, or merge can be
    rolled back. Each snapshot is shown newest first with the values it would
    restore. Filename renames made by ``dedupe`` are not undone. The chosen
    snapshot is applied straight to the file and database; tracking UUID,
    download history, and MusicBrainz status are left untouched.
    """
    init()
    print("Scanning music files...")
    scan_library()

    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library to restore.")
        return

    points: list[RestorePoint] = _collect_restore_points(songs)
    if not points:
        print(
            "No backups with changes found. Backups are recorded automatically "
            "before every `tag`, `modify`, `crop`, or `dedupe` change."
        )
        return

    show_all = bool(getattr(args, "all", False))
    raw_limit = int(getattr(args, "limit", _DEFAULT_LIMIT) or _DEFAULT_LIMIT)
    limit = 0 if show_all else max(1, raw_limit)
    shown: list[RestorePoint] = points if limit == 0 else points[:limit]

    song_count = len({point.song.uuid for point in points})
    print(
        f"Found {len(points)} restore point{'s' if len(points) != 1 else ''} "
        f"across {song_count} song{'s' if song_count != 1 else ''}."
    )
    if len(shown) < len(points):
        print(f"Showing the newest {len(shown)}; use --all to see the rest.")
    print("\nRestore points (newest first):")
    for number, point in enumerate(shown, start=1):
        _print_point(number, point)

    picks: list[int] = prompt_song_selection(len(shown))
    if not picks:
        print("Nothing to restore.")
        return

    chosen = _newest_per_song([shown[pick] for pick in picks])
    ignored = len(dict.fromkeys(picks)) - len(chosen)
    if ignored:
        print(
            f"Ignoring {ignored} older selection"
            f"{'s' if ignored != 1 else ''}: only the newest restore point per "
            "song is applied."
        )

    restored: int = 0
    failed: int = 0
    for number, point in enumerate(chosen, start=1):
        _print_preview(number, len(chosen), point)
        if not prompt_confirm("Apply this restore? (default: No)", default=False):
            print("Skipped.")
            continue
        absolute, _ = _format_at(point.snapshot.at)
        if restore_song_from_snapshot(point.song.path, point.snapshot):
            print(
                f"Restored '{point.song.display_name}' from "
                f"{_source_label(point.snapshot.source)} ({absolute})."
            )
            restored += 1
        else:
            print(f"Failed to restore '{point.song.display_name}'.")
            failed += 1

    print(f"\nRestore complete: {restored} restored, {failed} failed.")
