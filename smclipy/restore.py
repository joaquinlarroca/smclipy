"""The ``restore`` command: undo tag changes from recorded backups."""

import sys
from typing import Any

from smclipy.config import init
from smclipy.metadata import (
    Snapshot,
    list_snapshots,
    read_song_profile,
    restore_song_from_snapshot,
    scan_library,
)
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import (
    prompt_confirm,
    prompt_position_selection,
    prompt_song_selection,
)


def cmd_restore(args: Any) -> None:
    """Pick song(s) and one of their recorded backups, then re-apply it.

    Snapshots are written automatically before every ``tag``, ``modify``, and
    ``crop`` change, so a mistaken match or edit can be rolled back. The
    chosen snapshot is applied straight to the file and database; tracking
    UUID, download history, and MusicBrainz status are left untouched.
    """
    init()
    print("Scanning music files...")
    scan_library()

    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library to restore.")
        return

    restorable: list[int] = []
    for index, song in enumerate(songs):
        if song.uuid and list_snapshots(song.uuid):
            restorable.append(index)
    if not restorable:
        print(
            "No backups found. Backups are recorded automatically before every "
            "`tag`, `modify`, or `crop` change."
        )
        return

    print("Songs with recorded backups:")
    for index, song in enumerate(songs, start=1):
        if index - 1 in restorable:
            print(f"{index}. {song.display_name}")
    selected: list[int] = prompt_song_selection(len(songs))

    without_backups: list[int] = [
        index for index in selected if index not in restorable
    ]
    if without_backups:
        print(
            "Skipping "
            + ", ".join(str(index + 1) for index in without_backups)
            + ": no backups recorded for those songs."
        )
    selected = [index for index in selected if index in restorable]
    if not selected:
        print("Nothing to restore.")
        return

    restored: int = 0
    failed: int = 0
    for index in selected:
        current_song: LibrarySong = songs[index]
        assert current_song.uuid is not None
        snapshots: list[Snapshot] = list_snapshots(current_song.uuid)
        print(f"\n[{index + 1}] {current_song.display_name}")
        current, _ = read_song_profile(current_song.path)
        for number, snapshot in enumerate(snapshots, start=1):
            changed: list[str] = [
                field
                for field, label in (
                    ("title", "Title"),
                    ("artists", "Artist(s)"),
                    ("album", "Album"),
                    ("date", "Date"),
                    ("genre", "Genre"),
                    ("album_artist", "Album artist"),
                    ("track_number", "Track number"),
                )
                if str(getattr(snapshot, field)) != current.get(field, "")
            ]
            changed_label: str = ", ".join(changed) if changed else "same as now"
            print(f"  {number}. {snapshot.at} [{snapshot.source}] ({changed_label})")
        chosen: int | None = prompt_position_selection(
            len(snapshots), "Pick a backup to restore (q to skip this song): "
        )
        if chosen is None:
            continue
        snapshot = snapshots[chosen]
        if not prompt_confirm(
            f"Restore '{current_song.display_name}' from {snapshot.at}? (default: No)",
            default=False,
        ):
            print("Skipped.")
            continue
        if restore_song_from_snapshot(current_song.path, snapshot):
            print(f"Restored '{current_song.display_name}' from {snapshot.at}.")
            restored += 1
        else:
            print(f"Failed to restore '{current_song.display_name}'.")
            failed += 1

    print(
        f"\nRestore complete: {restored} restored, {failed} failed.",
        file=sys.stderr,
    )
