"""The ``restore`` command: undo tag changes from recorded backups."""

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

    Snapshots are written automatically before every ``tag``, ``modify``,
    ``crop``, and ``dedupe`` tag change, so a mistaken match, edit, or merge
    can be rolled back. Filename renames made by ``dedupe`` are not undone.
    The chosen snapshot is applied straight to the file and database; tracking
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
    for position, song in enumerate(songs):
        if song.uuid and list_snapshots(song.uuid):
            restorable.append(position)
    if not restorable:
        print(
            "No backups found. Backups are recorded automatically before every "
            "`tag`, `modify`, `crop`, or `dedupe` change."
        )
        return

    # Only songs with backups are listed, so the picker must be numbered
    # against that list — numbering them by library position made the number
    # you typed point at a different (often backup-less) song.
    print("Songs with recorded backups:")
    for number, restorable_index in enumerate(restorable, start=1):
        print(f"{number}. {songs[restorable_index].display_name}")
    picks: list[int] = prompt_song_selection(len(restorable))
    if not picks:
        print("Nothing to restore.")
        return

    restored: int = 0
    failed: int = 0
    for pick in picks:
        song_index: int = restorable[pick]
        current_song: LibrarySong = songs[song_index]
        assert current_song.uuid is not None
        snapshots: list[Snapshot] = list_snapshots(current_song.uuid)
        print(f"\n[{pick + 1}] {current_song.display_name}")
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

    print(f"\nRestore complete: {restored} restored, {failed} failed.")
