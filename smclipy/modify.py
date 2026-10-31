"""Manual tag editing for library songs (the `modify` command).

Lets the user pick songs (by number or range) and edit any of their tags by
hand, applied to every selected song at once: typing a value sets it, the
shown default (or a blank line) keeps the field unchanged, and ``/clear``
empties it.
"""

import sys
from pathlib import Path
from typing import Any

from PIL import Image

import smclipy.db as db
from smclipy.config import init, settings
from smclipy.formats import SUPPORTED_FORMATS, format_of
from smclipy.helpers import distinct_authors, join_authors, split_authors
from smclipy.metadata import (
    apply_tag_update,
    has_cover,
    remove_cover,
    scan_library,
    snapshot_song,
)
from smclipy.playlist_file import (
    PlaylistDocument,
    PlaylistEntrySong,
    PlaylistFileError,
    PlaylistWriteError,
    apply_entry_values,
    build_entry_songs,
    load_playlist_document,
    write_playlist_document,
)
from smclipy.search import apply_filters
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import (
    TAG_FIELD_LABELS,
    prompt_authors,
    prompt_field_selection,
    prompt_field_value,
    prompt_song_selection,
)

CLEAR_TOKEN = "/clear"
COVER_FIELD = "cover"

FIELDS_ORDER: list[str] = [
    "title",
    "artists",
    "album",
    "date",
    "genre",
    "album_artist",
    "track_number",
    "source_url",
    COVER_FIELD,
]

PLAYLIST_FIELDS_ORDER: list[str] = [
    field for field in FIELDS_ORDER if field != COVER_FIELD
]


def _resolve_text(raw: str, default_shown: str) -> str | None:
    """Interpret a text-field answer: None keeps, "" clears, str sets."""
    if raw == default_shown:
        return None
    if raw.strip() == CLEAR_TOKEN:
        return ""
    if not raw.strip():
        return None
    return raw.strip()


def _resolve_artists(raw: str, default_shown: str) -> list[str] | None:
    """Interpret an artist answer: None keeps, [] clears, list sets."""
    if raw == default_shown:
        return None
    if raw.strip() == CLEAR_TOKEN:
        return []
    artists: list[str] = split_authors(raw)
    if not artists:
        return None
    return artists


def _valid_cover_image(path: Path) -> bool:
    """Whether ``path`` really is a readable image file, printing a warning."""
    if not path.is_file():
        print(
            f"Warning: '{path}' is not a readable file; keeping the cover.",
            file=sys.stderr,
        )
        return False
    try:
        with Image.open(path) as image:
            image.load()
    except Exception:
        print(
            f"Warning: '{path}' is not a valid image; keeping the cover.",
            file=sys.stderr,
        )
        return False
    return True


def prompt_cover() -> Path | str | None:
    """Ask for a cover image. None keeps, "remove" clears, Path embeds."""
    raw: str = prompt_field_value(
        "Cover image path (blank keeps, '/clear' removes)", default=""
    )
    if not raw.strip():
        return None
    if raw.strip() == CLEAR_TOKEN:
        return "remove"
    path: Path = Path(raw.strip()).expanduser()
    return path if _valid_cover_image(path) else None


def _reference_for_song(song: LibrarySong, chosen: list[str]) -> dict[str, str]:
    reference: dict[str, str] = dict(song.current)
    if "source_url" in chosen and song.uuid is not None:
        row = db.get_song_by_uuid(song.uuid)
        if row is not None:
            reference["source_url"] = str(row["source_url"] or "")
    return reference


def _collect_values(
    reference: dict[str, str],
    chosen: list[str],
    *,
    count: int,
    allow_cover: bool,
) -> dict[str, Any]:
    """Prompt once for the values applied to every selected target."""
    multi: bool = count > 1
    authors_known: list[str] = db.get_authors() + split_authors(
        reference.get("artists", "")
    )
    values: dict[str, Any] = {}
    try:
        for field in chosen:
            if field == COVER_FIELD:
                if allow_cover:
                    values[COVER_FIELD] = prompt_cover()
            elif field == "artists":
                default_artists: str = reference.get("artists", "")
                raw_artists: str = prompt_authors(
                    authors_known, default=default_artists
                )
                values["artists"] = _resolve_artists(raw_artists, default_artists)
            else:
                label: str = TAG_FIELD_LABELS.get(field, field)
                if multi:
                    label = f"{label} (applies to {count} songs)"
                default: str = reference.get(field, "")
                values[field] = _resolve_text(
                    prompt_field_value(label, default), default
                )
    except KeyboardInterrupt:
        print("\nInterrupted, exiting...")
        raise SystemExit(130) from None
    return values


def _field_pairs(fields: list[str]) -> list[tuple[str, str]]:
    return [(field, TAG_FIELD_LABELS.get(field, field)) for field in fields]


def _inside_music_folder(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(settings().music_folder.resolve())
    except (OSError, ValueError):
        return False


def _run_playlist_modify(
    path: Path,
    args: object,
    *,
    reset_mb: bool,
) -> None:
    if reset_mb:
        print(
            "Error: --reset-mb only applies to audio files, not playlist JSON.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        document: PlaylistDocument = load_playlist_document(path)
    except PlaylistFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        songs: list[PlaylistEntrySong] = build_entry_songs(document, resolve=False)
    except PlaylistFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if not songs:
        print(f"No entries found in '{path.name}'.")
        return

    songs, filtered = apply_filters(songs, args)
    if not songs:
        return
    if filtered:
        print(f"Found {len(songs)} matching playlist entr(y/ies).")
    else:
        print(f"Found {len(songs)} playlist entr(y/ies).")
    label: str = document.name or path.stem
    print(f"\nPlaylist '{label}' entries:")
    for index, song in enumerate(songs, start=1):
        line: str = song.display_name
        album: str = song.current.get("album", "")
        if album:
            line += f" [{album}]"
        print(f"{index:3}. {line}")

    selected: list[int] = prompt_song_selection(len(songs))
    if not selected:
        return

    chosen: list[str] | None = prompt_field_selection(
        _field_pairs(PLAYLIST_FIELDS_ORDER)
    )
    if not chosen:
        print("Nothing to modify.")
        return

    reference: dict[str, str] = dict(songs[selected[0]].current)
    values: dict[str, Any] = _collect_values(
        reference, chosen, count=len(selected), allow_cover=False
    )

    updated: int = 0
    total: int = len(selected)
    for position, index in enumerate(selected, start=1):
        entry: PlaylistEntrySong = songs[index]
        try:
            changed: set[str] = apply_entry_values(entry.entry, values)
        except Exception as exc:
            print(
                f"Warning: could not modify playlist entry {entry.index} "
                f"'{entry.display_name}': {exc!r}",
                file=sys.stderr,
            )
            continue
        if changed:
            updated += 1
            print(
                f"[{position}/{total}] Updated entry {entry.index} "
                f"'{entry.display_name}'."
            )
        else:
            print(
                f"[{position}/{total}] Entry {entry.index} "
                f"'{entry.display_name}' needs no changes."
            )

    if updated:
        try:
            write_playlist_document(document)
        except PlaylistWriteError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
    print(f"\nDone. Updated {updated} playlist entr(y/ies).")


def _run_audio_file_modify(
    path: Path,
    args: object,
    *,
    reset_mb: bool,
) -> None:
    if getattr(args, "search", None) or getattr(args, "specific", None):
        print(
            "Error: --search/--specific only apply to library or playlist "
            "selections, not a single --file audio path.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not path.is_file():
        print(f"Error: '{path}' is not a readable file.", file=sys.stderr)
        raise SystemExit(1)
    if format_of(path) is None:
        print(
            f"Error: '{path}' is not a supported audio file "
            f"({', '.join(SUPPORTED_FORMATS)}).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    inside: bool = _inside_music_folder(path)
    if inside:
        print("Scanning music files...")
        scan_library()
    else:
        print(
            "Warning: this file is outside the music folder; its tags will be "
            "edited but it may not be tracked in the database.",
            file=sys.stderr,
        )
    try:
        song: LibrarySong = LibrarySong(path)
    except Exception as exc:
        print(f"Error: could not read '{path}': {exc!r}", file=sys.stderr)
        raise SystemExit(1) from None
    if inside and (song.uuid is None or db.get_song_by_uuid(song.uuid) is None):
        print(
            "Warning: this file is not tracked in the database; tags will be "
            "edited but tracking rows will not be created.",
            file=sys.stderr,
        )

    chosen: list[str] | None = prompt_field_selection(_field_pairs(FIELDS_ORDER))
    if not chosen:
        print("Nothing to modify.")
        return
    reference: dict[str, str] = _reference_for_song(song, chosen)
    values: dict[str, Any] = _collect_values(
        reference, chosen, count=1, allow_cover=True
    )
    try:
        changed: set[str] | None = _edit_song(song, values, reset_mb=reset_mb)
    except KeyboardInterrupt:
        print("\nInterrupted, exiting...")
        raise SystemExit(130) from None
    except Exception as exc:
        print(
            f"Warning: could not modify '{song.display_name}': {exc!r}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    if changed:
        print(f"Updated '{song.path.name}'.")
    elif changed is None:
        print(f"Could not modify '{song.path.name}'.")
        raise SystemExit(1)
    elif reset_mb and song.uuid is not None:
        print(f"'{song.path.name}' already matched; MusicBrainz status reset.")
    else:
        print(f"'{song.path.name}' needs no changes.")


def _edit_song(
    song: LibrarySong,
    values: dict[str, Any],
    *,
    reset_mb: bool,
) -> set[str] | None:
    """Apply the gathered changes to one song and sync the tracking database.

    Returns the set of fields actually changed, an empty set when nothing was
    done, or ``None`` when the file write failed.
    """
    current: dict[str, str] = song.current
    title: str | None = None
    artists: list[str] | None = None
    album: str | None = None
    date: str | None = None
    genre: str | None = None
    album_artist: str | None = None
    track_number: str | None = None
    new_source_url: str | None = None
    changed_fields: set[str] = set()

    source_value: Any = values.get("source_url")
    if source_value is not None:
        row = None
        if song.uuid is not None:
            row = db.get_song_by_uuid(song.uuid)
        if row is None:
            if source_value:
                print(
                    f"Warning: cannot set the source URL of '{song.path.name}': "
                    "the song is not tracked in the database.",
                    file=sys.stderr,
                )
        else:
            current_source_url: str = str(row["source_url"] or "").strip()
            if source_value != current_source_url:
                new_source_url = source_value
                changed_fields.add("source_url")

    for field in ("title", "album", "date", "genre", "album_artist", "track_number"):
        value = values.get(field)
        if value is None or value == current.get(field, ""):
            continue
        if field == "title":
            title = value
        elif field == "album":
            album = value
        elif field == "date":
            date = value
        elif field == "genre":
            genre = value
        elif field == "album_artist":
            album_artist = value
        else:
            track_number = value
        changed_fields.add(field)

    artists_value = values.get("artists")
    if artists_value is not None and sorted(artists_value) != sorted(
        split_authors(current.get("artists", ""))
    ):
        artists = artists_value
        changed_fields.add("artists")

    cover_action: Any = values.get(COVER_FIELD)  # None | "remove" | Path
    removing: bool = cover_action == "remove"
    replacing: bool = isinstance(cover_action, Path)
    if not changed_fields and not replacing and not (removing and song.has_cover):
        # `--reset-mb` asks for a retag even when the user retyped the same
        # values, so it must still take effect when nothing changed.
        if reset_mb and song.uuid is not None:
            db.reset_mb_status(song.uuid)
            db.log_event(song.uuid, "musicbrainz", {"status": "reset"})
        return set()

    needs_file_write: bool = (
        bool(changed_fields - {"source_url"})
        or replacing
        or (removing and song.has_cover)
    )
    if needs_file_write:
        # Record the pre-change profile so a mistaken edit can be undone with
        # the `restore` command. Source URLs only live in the database, so a
        # source-only edit neither writes the file nor creates a snapshot.
        snapshot_song(song.path, song.uuid, "modify")
        ok: bool = apply_tag_update(
            song.path,
            title=title,
            artists=artists,
            album=album,
            date=date,
            genre=genre,
            album_artist=album_artist,
            track_number=track_number,
            image=cover_action if replacing else None,
        )
        if not ok:
            print(
                f"Warning: could not apply changes to '{song.path.name}'.",
                file=sys.stderr,
            )
            return None

        if replacing:
            changed_fields.add(COVER_FIELD)
        elif removing and song.has_cover:
            if remove_cover(song.path):
                changed_fields.add(COVER_FIELD)
            else:
                print(
                    f"Warning: could not remove the cover of '{song.path.name}'.",
                    file=sys.stderr,
                )
        if not changed_fields:
            # The only requested change was removing a cover, which failed:
            # report the write as failed so the caller can surface it.
            return None if removing else set()

    if "artists" in changed_fields:
        db.add_authors(
            distinct_authors(artists or [], split_authors(current.get("artists", "")))
        )
    if song.uuid is not None:
        db.update_song_metadata(
            song.uuid,
            title=title if "title" in changed_fields else None,
            artists=(
                join_authors(artists or []) if "artists" in changed_fields else None
            ),
            album=album if "album" in changed_fields else None,
            date=date if "date" in changed_fields else None,
            genre=genre if "genre" in changed_fields else None,
            album_artist=album_artist if "album_artist" in changed_fields else None,
            track_number=(track_number if "track_number" in changed_fields else None),
            source_url=(new_source_url if "source_url" in changed_fields else None),
            has_cover=has_cover(song.path) if COVER_FIELD in changed_fields else None,
        )
        if "source_url" in changed_fields:
            db.reset_source_status(song.uuid)
        detail: dict[str, Any] = {
            "fields": sorted(changed_fields),
            "title": title or current.get("title") or song.path.stem,
            "artists": ", ".join(artists or [])
            if "artists" in changed_fields
            else current.get("artists", ""),
        }
        if "source_url" in changed_fields:
            detail["source_url"] = new_source_url
        db.log_event(song.uuid, "modified", detail)
        if reset_mb:
            db.reset_mb_status(song.uuid)
            db.log_event(song.uuid, "musicbrainz", {"status": "reset"})
    return changed_fields


def cmd_modify(args: object) -> None:
    reset_mb: bool = bool(getattr(args, "reset_mb", False))
    init()

    file_arg: Any = getattr(args, "file", None)
    if file_arg:
        path: Path = Path(str(file_arg)).expanduser()
        if path.suffix.casefold() == ".json":
            _run_playlist_modify(path, args, reset_mb=reset_mb)
        else:
            _run_audio_file_modify(path, args, reset_mb=reset_mb)
        return

    print("Scanning music files...")
    scan_library()

    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library to modify.")
        return

    songs, filtered = apply_filters(songs, args)
    if not songs:
        return

    if filtered:
        print(f"Found {len(songs)} matching audio file(s).")
    else:
        print(f"Found {len(songs)} audio file(s) in the library.")
    print("\nMatching songs:" if filtered else "\nSongs in your library:")
    for index, song in enumerate(songs, start=1):
        line: str = song.display_name
        album: str = song.current.get("album", "")
        if album:
            line += f" [{album}]"
        print(f"{index:3}. {line}")

    selected: list[int] = prompt_song_selection(len(songs))
    if not selected:
        return

    chosen: list[str] | None = prompt_field_selection(_field_pairs(FIELDS_ORDER))
    if not chosen:
        print("Nothing to modify.")
        return

    first_song: LibrarySong = songs[selected[0]]
    reference: dict[str, str] = _reference_for_song(first_song, chosen)
    values: dict[str, Any] = _collect_values(
        reference, chosen, count=len(selected), allow_cover=True
    )

    updated: int = 0
    total: int = len(selected)
    for position, index in enumerate(selected, start=1):
        target: LibrarySong = songs[index]
        try:
            changed: set[str] | None = _edit_song(target, values, reset_mb=reset_mb)
        except KeyboardInterrupt:
            print("\nInterrupted, exiting...")
            raise SystemExit(130) from None
        except Exception as exc:
            print(
                f"Warning: could not modify '{target.display_name}': {exc!r}",
                file=sys.stderr,
            )
            continue
        if changed:
            updated += 1
            print(f"[{position}/{total}] Updated '{target.path.name}'.")
        elif changed is None:
            print(f"[{position}/{total}] Could not modify '{target.path.name}'.")
        elif reset_mb and target.uuid is not None:
            print(
                f"[{position}/{total}] '{target.path.name}' already matched; "
                "MusicBrainz status reset."
            )
        else:
            print(f"[{position}/{total}] '{target.path.name}' needs no changes.")
    print(f"\nDone. Updated {updated} song(s).")
