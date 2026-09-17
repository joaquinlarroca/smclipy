import os
import uuid
from collections.abc import Iterator
from enum import Enum, auto
from pathlib import Path
from typing import Any, NamedTuple

from mutagen.id3 import APIC

import smclipy.db as db
from smclipy.config import Settings, settings
from smclipy.formats import (
    Track,
    audio_ext,
    format_of,
    image_mime,
    open_for_tagging,
)
from smclipy.helpers import normalize_author, sanitize_filename, split_authors
from smclipy.ui import prompt_overwrite


class SaveResult(Enum):
    """Outcome of moving a tagged track into the library."""

    SAVED = auto()
    SKIPPED = auto()
    FAILED = auto()


class ScanSummary(NamedTuple):
    """What a library scan changed in the tracking database."""

    added: int = 0
    renamed: int = 0
    missing: int = 0
    changed: int = 0


def iter_audio_files() -> Iterator[Path]:
    """Every supported audio file in the music folder, sorted by name."""
    folder: Path = settings().music_folder
    if not folder.is_dir():
        return
    for file in sorted(folder.iterdir()):
        if file.is_file() and format_of(file) is not None:
            yield file


def read_song_uuid(file: Path) -> str | None:
    """Read the smclipy tracking UUID from an audio file's tags."""
    track: Track | None = Track.open(file)
    if track is None:
        return None
    return track.read_uuid()


def write_song_uuid(file: Path, song_uuid: str | None = None) -> str | None:
    """Write a tracking UUID into the file's tags and return it."""
    if song_uuid is None:
        song_uuid = str(uuid.uuid4())
    track: Track | None = Track.open(file)
    if track is None:
        return None
    try:
        track.write_uuid(song_uuid)
        track.save()
    except Exception as exc:
        print(f"Warning: could not write tracking id to '{file.name}': {exc}")
        return None
    return song_uuid


def _iter_tagged_files() -> Iterator[tuple[Track, str, list[str]]]:
    for file in iter_audio_files():
        track: Track | None = Track.open(file)
        if track is None:
            continue
        profile, _ = track.read_profile()
        yield track, profile["title"], track.read_artists()


def _matches_tags(title: str, artists: list[str], row: Any) -> bool:
    """Whether a brand-new file at a tracked path plausibly is the tracked song.

    Prevents handing a tracked UUID to a different song dropped onto an old
    path after a rename: only backfill when title and artists agree with the
    recorded row (file artists may be a subset of the recorded ones).
    """
    if row["title"] and title.casefold().strip() != row["title"].casefold().strip():
        return False
    if row["artists"]:
        known_set = {normalize_author(a) for a in split_authors(row["artists"])}
        file_set = {normalize_author(a) for a in artists}
        known_set.discard("")
        file_set.discard("")
        if file_set and not file_set <= known_set:
            return False
    return True


def scan_library() -> ScanSummary:
    """Reconcile the database with the music folder.

    Every audio file is keyed by its tracking UUID: new files get a UUID
    written and a row inserted, renamed files keep their row (a
    ``renamed`` event is logged), and files that vanished are marked
    missing.
    """
    s: Settings = settings()
    seen: set[str] = set()
    authors_seen: set[str] = set()
    added = renamed = missing = changed = 0
    with db.transaction() as conn:
        known: dict[str, Any] = {}
        by_path: dict[str, str] = {}
        for row in db.list_songs(conn):
            known[row["uuid"]] = row
            if row["current_path"]:
                by_path.setdefault(row["current_path"], row["uuid"])
            elif row["first_seen_path"]:
                # A missing song (no current_path) returning to its original
                # location reclaims its old UUID; an owned path never hands
                # its UUID to a different file placed there later.
                by_path.setdefault(row["first_seen_path"], row["uuid"])
        for file in iter_audio_files():
            rel: str = file.relative_to(s.music_folder).as_posix()
            seen.add(rel)
            track: Track | None = open_for_tagging(file)
            if track is None:
                continue
            profile: dict[str, str]
            has_cover: bool
            profile, has_cover = track.read_profile()
            title: str = profile["title"]
            artists_list: list[str] = track.read_artists()
            artists_str: str = ", ".join(artists_list)
            authors_seen.update(artists_list)
            song_uuid: str | None = track.read_uuid()
            if song_uuid is None:
                candidate: str | None = by_path.get(rel)
                if candidate is not None and not _matches_tags(
                    title, artists_list, known[candidate]
                ):
                    candidate = None
                song_uuid = write_song_uuid(file, candidate)
                if song_uuid is None:
                    continue
            existing: Any = known.get(song_uuid)
            if existing is None:
                db.insert_song(
                    conn=conn,
                    song_uuid=song_uuid,
                    current_path=rel,
                    title=title,
                    artists=artists_str,
                    album=profile["album"],
                    date=profile["date"],
                    genre=profile["genre"],
                    album_artist=profile["album_artist"],
                    track_number=profile["track_number"],
                    has_cover=has_cover,
                )
                db.log_event(song_uuid, "appeared", {"path": rel}, conn=conn)
                added += 1
                continue
            current: str | None = existing["current_path"]
            if current is not None and current != rel:
                db.set_song_path(song_uuid, rel, conn=conn)
                db.log_event(
                    song_uuid,
                    "renamed",
                    {"old_path": current, "new_path": rel},
                    conn=conn,
                )
                db.update_song_metadata(
                    song_uuid,
                    title=title,
                    artists=artists_str,
                    album=profile["album"],
                    date=profile["date"],
                    genre=profile["genre"],
                    album_artist=profile["album_artist"],
                    track_number=profile["track_number"],
                    has_cover=has_cover,
                    conn=conn,
                )
                renamed += 1
            else:
                db.touch_song(song_uuid, conn=conn)
                if (
                    existing["title"] != title
                    or (existing["artists"] or "") != artists_str
                    or (existing["album"] or "") != (profile["album"] or "")
                    or (existing["date"] or "") != (profile["date"] or "")
                    or (existing["genre"] or "") != (profile["genre"] or "")
                    or (existing["album_artist"] or "")
                    != (profile["album_artist"] or "")
                    or (existing["track_number"] or "")
                    != (profile["track_number"] or "")
                    or bool(existing["has_cover"]) != has_cover
                ):
                    db.update_song_metadata(
                        song_uuid,
                        title=title,
                        artists=artists_str,
                        album=profile["album"],
                        date=profile["date"],
                        genre=profile["genre"],
                        album_artist=profile["album_artist"],
                        track_number=profile["track_number"],
                        has_cover=has_cover,
                        conn=conn,
                    )
                    changed += 1
        for row in db.list_songs(conn):
            current = row["current_path"]
            if current is not None and current not in seen:
                db.mark_song_missing(row["uuid"], conn=conn)
                db.log_event(row["uuid"], "missing", {"path": current}, conn=conn)
                missing += 1
        db.add_authors(sorted(authors_seen), conn=conn)
    return ScanSummary(added, renamed, missing, changed)


def save_all_covers() -> None:
    s: Settings = settings()
    for track, title, artists in _iter_tagged_files():
        if not artists or not track.has_cover():
            continue
        save_image(sanitize_filename(f"{artists[0]}-{title}"), track, s.covers_folder)


def get_image_from_file(file: Path, save_to_path: Path, save_as: str) -> Path | None:
    if not file.is_file():
        return None
    track: Track | None = Track.open(file)
    if track is None:
        print(f"Warning: could not read '{file.name}', skipping cover")
        return None
    return save_image(sanitize_filename(save_as), track, save_to_path)


def _open_audio(file: Path) -> Track | None:
    return Track.open(file)


def save_song_temp_to_main(
    file: Path, image: Path | None, title: str, authors: str, album: str
) -> tuple[SaveResult, Path | None]:
    authors_list: list[str] = split_authors(authors)
    if not authors_list:
        print("Warning: no artists provided, skipping save")
        return SaveResult.FAILED, None
    extension: str = audio_ext(format_of(file) or settings().audio_format)
    target_name: str = sanitize_filename(f"{authors_list[0]}-{title}{extension}")
    if not target_name:
        print("Warning: artist/title produced an invalid filename, skipping save")
        return SaveResult.FAILED, None
    target: Path = settings().music_folder.joinpath(target_name)
    if target.is_file():
        print(f"Warning: '{target.name}' already exists")
        if not prompt_overwrite():
            return SaveResult.SKIPPED, None
    if not _tag_file(file, title, authors_list, album, image):
        return SaveResult.FAILED, None
    os.replace(str(file), str(target))
    return SaveResult.SAVED, target


def _tag_file(
    target: Path, title: str, authors: list[str], album: str, image: Path | None
) -> bool:
    track: Track | None = _open_audio(target)
    if track is None:
        print(f"Warning: could not read '{target.name}', skipping save")
        return False
    track.strip_unknown_tags()
    track.set_field("title", title)
    track.set_artists(authors)
    track.set_field("album", album)
    if track.read_uuid() is None:
        track.write_uuid(str(uuid.uuid4()))
    track.set_cover(image)
    try:
        track.save()
    except Exception as exc:
        print(f"Warning: could not save '{target.name}': {exc}")
        return False
    return True


def read_song_profile(file: Path) -> tuple[dict[str, str], bool]:
    """Read an audio file's tag profile and whether it has embedded cover art."""
    track: Track | None = Track.open(file)
    if track is None:
        print(f"Warning: could not read '{file.name}', skipping tag read")
        return {}, False
    return track.read_profile()


def read_song_tags(file: Path) -> dict[str, str]:
    """Read the full tag profile of an audio file into a field -> value mapping."""
    return read_song_profile(file)[0]


def has_cover(file: Path) -> bool:
    track: Track | None = Track.open(file)
    if track is None:
        return False
    return track.has_cover()


def apply_tag_update(
    file: Path,
    *,
    title: str | None = None,
    artists: list[str] | None = None,
    album: str | None = None,
    date: str | None = None,
    genre: str | None = None,
    album_artist: str | None = None,
    track_number: str | None = None,
    image: Path | None = None,
) -> bool:
    """Apply a partial tag update, only overwriting fields that are not None."""
    track: Track | None = _open_audio(file)
    if track is None:
        print(f"Warning: could not read '{file.name}', skipping update")
        return False
    if title is not None:
        track.set_field("title", title)
    if artists is not None:
        track.set_artists(artists)
    if album is not None:
        track.set_field("album", album)
    if date is not None:
        track.set_field("date", date)
    if genre is not None:
        track.set_field("genre", genre)
    if album_artist is not None:
        track.set_field("album_artist", album_artist)
    if track_number is not None:
        track.set_field("track_number", track_number)
    if image is not None:
        track.set_cover(image)
    try:
        track.save()
    except Exception as exc:
        print(f"Warning: could not save '{file.name}': {exc}")
        return False
    return True


def _get_image_mime(image: Path) -> str:
    return image_mime(image)


def _set_cover(audio: Any, image: Path | None) -> None:
    if isinstance(audio, Track):
        audio.set_cover(image)
        return
    if audio.tags is None:
        audio.add_tags()
    if image is None or not image.is_file():
        return
    tags = audio.tags
    if tags is None:
        raise RuntimeError("MP3 has no ID3 tag stream to embed cover into")
    tags.delall("APIC")
    tags.add(
        APIC(
            encoding=3,
            mime=_get_image_mime(image),
            type=3,
            desc="Cover",
            data=image.read_bytes(),
        )
    )


def change_cover(image: Path, file: Path) -> None:
    track: Track | None = _open_audio(file)
    if track is None:
        print(f"Warning: could not read '{file.name}', skipping cover re-embed")
        return
    track.set_cover(image)
    try:
        track.save()
    except Exception as exc:
        print(f"Warning: could not save '{file.name}': {exc}")


COVER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _mime_to_ext(mime: str) -> str:
    return _MIME_TO_EXT.get(mime, ".png")


def get_all_names() -> list[str]:
    return db.get_authors()


def _without_cover_extension(name: str) -> str:
    lowered: str = name.casefold()
    for extension in COVER_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def save_image(name: str, song_file: Any, path: Path) -> Path | None:
    stem: str = _without_cover_extension(name)
    if isinstance(song_file, Track):
        cover: tuple[str, bytes] | None = song_file.read_cover()
        if cover is None:
            print(f"Couldn't find '{stem}' cover")
            return None
        mime: str = cover[0]
        data: bytes = cover[1]
    else:
        apic_key = next((key for key in song_file if key.startswith("APIC")), None)
        if not apic_key:
            print(f"Couldn't find '{stem}' cover")
            return None
        artwork = song_file[apic_key]
        mime = artwork.mime
        data = artwork.data
    ext: str = _mime_to_ext(mime)
    known_mime: bool = mime in _MIME_TO_EXT
    if not known_mime:
        print(f"Warning: unrecognized cover mime '{mime}' for '{stem}', assuming PNG")
    target: Path = path.joinpath(f"{stem}{ext}")
    existing: Path | None = next(
        (
            path.joinpath(f"{stem}{extension}")
            for extension in COVER_EXTENSIONS
            if path.joinpath(f"{stem}{extension}").is_file()
        ),
        None,
    )
    if existing is not None:
        if existing == target:
            if existing.read_bytes() == data:
                return target
            target.write_bytes(data)
            print(f"Updated '{target.name}'")
            return target
        if not known_mime:
            existing.write_bytes(data)
            print(f"Updated '{existing.name}'")
            return existing
        target.write_bytes(data)
        existing.unlink()
        print(f"Fixed '{existing.name}' -> '{target.name}'")
        return target
    print(f"Saving '{target.name}'")
    target.write_bytes(data)
    return target
