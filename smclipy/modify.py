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
from smclipy.config import init
from smclipy.helpers import distinct_authors, split_authors
from smclipy.metadata import (
    apply_tag_update,
    has_cover,
    remove_cover,
    scan_library,
    snapshot_song,
)
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
    COVER_FIELD,
]


def _resolve_text(raw: str, default_shown: str) -> str | None:
    """Interpret a text-field answer: None keeps, "" clears, str sets."""
    if raw == default_shown:
        return None
    if raw == CLEAR_TOKEN:
        return ""
    if not raw.strip():
        return None
    return raw.strip()


def _resolve_artists(raw: str, default_shown: str) -> list[str] | None:
    """Interpret an artist answer: None keeps, [] clears, list sets."""
    if raw == default_shown:
        return None
    if raw == CLEAR_TOKEN:
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
    if raw == CLEAR_TOKEN:
        return "remove"
    path: Path = Path(raw.strip())
    return path if _valid_cover_image(path) else None


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
    changed_fields: set[str] = set()

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
        return set()

    # Record the pre-change profile so a mistaken edit can be undone with the
    # `restore` command.
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
        # The only requested change was removing a cover, which failed: report
        # the write as failed so the caller can surface it.
        return None if removing else set()

    db.add_authors(
        distinct_authors(artists or [], split_authors(current.get("artists", "")))
    )
    if song.uuid is not None:
        db.update_song_metadata(
            song.uuid,
            title=title if "title" in changed_fields else None,
            artists=", ".join(artists or []) if "artists" in changed_fields else None,
            album=album if "album" in changed_fields else None,
            date=date if "date" in changed_fields else None,
            genre=genre if "genre" in changed_fields else None,
            album_artist=album_artist if "album_artist" in changed_fields else None,
            track_number=(track_number if "track_number" in changed_fields else None),
            has_cover=has_cover(song.path) if COVER_FIELD in changed_fields else None,
        )
        db.log_event(
            song.uuid,
            "modified",
            {
                "fields": sorted(changed_fields),
                "title": title or current.get("title") or song.path.stem,
                "artists": ", ".join(artists or [])
                if "artists" in changed_fields
                else current.get("artists", ""),
            },
        )
        if reset_mb:
            db.reset_mb_status(song.uuid)
            db.log_event(song.uuid, "musicbrainz", {"status": "reset"})
    return changed_fields


def cmd_modify(args: object) -> None:
    reset_mb: bool = bool(getattr(args, "reset_mb", False))
    init()
    print("Scanning music files...")
    scan_library()

    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library to modify.")
        return

    print(f"Found {len(songs)} audio file(s) in the library.")
    print("\nSongs in your library:")
    for index, song in enumerate(songs, start=1):
        line: str = song.display_name
        album: str = song.current.get("album", "")
        if album:
            line += f" [{album}]"
        print(f"{index:3}. {line}")

    selected: list[int] = prompt_song_selection(len(songs))
    if not selected:
        return

    field_pairs: list[tuple[str, str]] = [
        (field, TAG_FIELD_LABELS.get(field, field)) for field in FIELDS_ORDER
    ]
    chosen: list[str] | None = prompt_field_selection(field_pairs)
    if not chosen:
        print("Nothing to modify.")
        return

    multi: bool = len(selected) > 1
    first_song: LibrarySong = songs[selected[0]]
    reference: dict[str, str] = first_song.current
    authors_known: list[str] = db.get_authors() + split_authors(
        reference.get("artists", "")
    )
    values: dict[str, Any] = {}
    try:
        for field in chosen:
            if field == COVER_FIELD:
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
                    label = f"{label} (applies to {len(selected)} songs)"
                default: str = reference.get(field, "")
                values[field] = _resolve_text(
                    prompt_field_value(label, default), default
                )
    except KeyboardInterrupt:
        print("\nInterrupted, exiting...")
        raise SystemExit(130) from None

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
        else:
            print(f"[{position}/{total}] '{target.path.name}' needs no changes.")
    print(f"\nDone. Updated {updated} song(s).")
