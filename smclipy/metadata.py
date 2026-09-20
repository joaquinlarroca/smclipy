import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, NamedTuple

import smclipy.config as config_module
import smclipy.db as db
from smclipy.config import Settings, settings
from smclipy.formats import (
    Track,
    audio_ext,
    format_of,
    open_for_tagging,
)
from smclipy.helpers import (
    distinct_authors,
    normalize_author,
    sanitize_filename,
    split_authors,
)
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
    changes: tuple["ScanChange", ...] = ()


class ScanChange(NamedTuple):
    """A single song-level change made during a scan."""

    status: str
    uuid: str
    path: str
    old_path: str | None = None


class Snapshot(NamedTuple):
    """A pre-change copy of a song's tag profile (and cover art, on disk)."""

    at: str
    source: str
    title: str
    artists: str
    album: str
    date: str
    genre: str
    album_artist: str
    track_number: str
    cover: Path | None
    path: Path


_TEMP_MARKER = ".smclipy-tmp"


def _iter_music_paths() -> Iterator[Path]:
    """Every file under the music folder, excluding hidden directories and the
    smclipy state that lives inside it (scripts, covers, .temp, backups).

    Subfolders are valid library locations, but the smclipy folder's own
    state plus hidden system directories (``.stfolder``, ``.stversions``)
    must never be scanned as music.
    """
    s: Settings = settings()
    folder: Path = s.music_folder
    if not folder.is_dir():
        return
    try:
        script_rel: Path = s.script_folder.relative_to(folder)
    except ValueError:
        script_rel = Path("")
    prune_script: bool = bool(script_rel.parts)
    script_root_name: str | None = script_rel.parts[0] if prune_script else None
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if prune_script and Path(root) == folder and script_root_name is not None:
            dirs[:] = [d for d in dirs if d != script_root_name]
        root_path: Path = Path(root)
        for name in files:
            yield root_path.joinpath(name)


def iter_audio_files() -> Iterator[Path]:
    """Every supported audio file in the music folder, sorted by relative path.

    Subfolders are scanned, but the smclipy state inside the music folder
    (scripts, covers, .temp, backups) and hidden directories are skipped.
    In-progress atomic tag edits (``.<name>.smclipy-tmp.<ext>``) are skipped
    so an interrupted write can never be mistaken for a library song.
    """
    s: Settings = settings()
    folder: Path = s.music_folder
    files: list[Path] = []
    for file in _iter_music_paths():
        if _TEMP_MARKER in file.name:
            continue
        if file.is_file() and format_of(file) is not None:
            files.append(file)
    files.sort(key=lambda path: path.relative_to(folder).as_posix())
    yield from files


def _edit_track_atomically(file: Path, edit: Callable[[Track], None]) -> bool:
    """Apply ``edit`` to a copy of ``file``, then swap it in with os.replace.

    Writing tags in place means an interrupted or failed save can corrupt the
    library file. Editing a sibling temp copy and replacing the original only
    once the save succeeds keeps the original intact on any failure.
    """
    temp: Path = file.with_name(f".{file.stem}{_TEMP_MARKER}{file.suffix}")
    try:
        shutil.copy2(file, temp)
    except OSError as exc:
        print(f"Warning: could not copy '{file.name}': {exc}", file=sys.stderr)
        return False
    track: Track | None = Track.open(temp)
    if track is None:
        with suppress(OSError):
            temp.unlink()
        print(
            f"Warning: could not read '{file.name}', skipping update", file=sys.stderr
        )
        return False
    try:
        edit(track)
        track.save()
    except Exception as exc:
        with suppress(OSError):
            temp.unlink()
        print(f"Warning: could not save '{file.name}': {exc}", file=sys.stderr)
        return False
    try:
        os.replace(str(temp), str(file))
    except OSError as exc:
        with suppress(OSError):
            temp.unlink()
        print(f"Warning: could not replace '{file.name}': {exc}", file=sys.stderr)
        return False
    return True


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

    def edit(track: Track) -> None:
        track.write_uuid(song_uuid)

    if not _edit_track_atomically(file, edit):
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


def _cleanup_orphan_temp_files() -> None:
    """Remove leftover atomic-edit temp copies from interrupted runs.

    ``_edit_track_atomically`` writes a sibling ``.<name>.smclipy-tmp.<ext>``
    copy and swaps it over the original, deleting it on handled failures. A
    hard kill (crash/SIGKILL) can leave such a copy behind forever; the
    original file is always the authority, so leftover copies are safe to
    remove whenever a scan runs.
    """
    folder: Path = settings().music_folder
    if not folder.is_dir():
        return
    removed: int = 0
    for temp_file in _iter_music_paths():
        if not temp_file.name.startswith("."):
            continue
        if _TEMP_MARKER in temp_file.name and temp_file.is_file():
            with suppress(OSError):
                temp_file.unlink()
                removed += 1
    if removed:
        print(
            f"Warning: removed {removed} leftover temp file(s) from interrupted "
            "tag edits.",
            file=sys.stderr,
        )


def scan_library(collect_details: bool = False) -> ScanSummary:
    """Reconcile the database with the music folder.

    Every audio file is keyed by its tracking UUID: new files get a UUID
    written and a row inserted, renamed files keep their row (a
    ``renamed`` event is logged), and files that vanished are marked
    missing.

    When ``collect_details`` is true the returned summary also carries the
    individual changes, for machine-readable reporting.
    """
    s: Settings = settings()
    _cleanup_orphan_temp_files()
    seen: set[str] = set()
    authors_seen: set[str] = set()
    added = renamed = missing = changed = 0
    changes: list[ScanChange] = []
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
                if collect_details:
                    changes.append(ScanChange("added", song_uuid, rel))
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
                if collect_details:
                    changes.append(ScanChange("renamed", song_uuid, rel, current))
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
                    if collect_details:
                        changes.append(ScanChange("changed", song_uuid, rel))
        for row in db.list_songs(conn):
            current = row["current_path"]
            if current is not None and current not in seen:
                db.mark_song_missing(row["uuid"], conn=conn)
                db.log_event(row["uuid"], "missing", {"path": current}, conn=conn)
                missing += 1
                if collect_details:
                    changes.append(ScanChange("missing", row["uuid"], current))
        db.add_authors(sorted(authors_seen), conn=conn)
    return ScanSummary(
        added, renamed, missing, changed, tuple(changes) if collect_details else ()
    )


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
        print(f"Warning: could not read '{file.name}', skipping cover", file=sys.stderr)
        return None
    return save_image(sanitize_filename(save_as), track, save_to_path)


def _open_audio(file: Path) -> Track | None:
    return Track.open(file)


def library_target_path(file: Path, title: str, authors_list: list[str]) -> Path | None:
    """The ``artist-title<ext>`` library path a tagged track lands on, or None
    when the artist/title cannot form a valid filename."""
    extension: str = audio_ext(format_of(file) or settings().audio_format)
    target_name: str = sanitize_filename(f"{authors_list[0]}-{title}{extension}")
    if not target_name:
        return None
    return settings().music_folder.joinpath(target_name)


def save_song_temp_to_main(
    file: Path,
    image: Path | None,
    title: str,
    authors: str,
    album: str,
    *,
    overwrite: bool | None = None,
    song_uuid: str | None = None,
) -> tuple[SaveResult, Path | None]:
    """Move a tagged track into the library under ``artist-title``.

    When the target name already exists, ``overwrite`` decides without
    prompting: ``True`` replaces the file, ``False`` skips it (the caller's
    existing copy is kept), and ``None`` asks the user. Headless runs pass
    ``False`` so a cron batch never hangs on an interactive prompt.

    ``song_uuid`` lets a download that overwrites a tracked file reuse the
    existing song's tracking UUID (and its database row) instead of inserting
    a phantom duplicate row.
    """
    authors_list: list[str] = split_authors(authors)
    if not authors_list:
        print("Warning: no artists provided, skipping save", file=sys.stderr)
        return SaveResult.FAILED, None
    target: Path | None = library_target_path(file, title, authors_list)
    if target is None:
        print(
            "Warning: artist/title produced an invalid filename, skipping save",
            file=sys.stderr,
        )
        return SaveResult.FAILED, None
    if target.is_file():
        print(f"Warning: '{target.name}' already exists", file=sys.stderr)
        if overwrite is None:
            if not prompt_overwrite():
                return SaveResult.SKIPPED, None
        elif not overwrite:
            return SaveResult.SKIPPED, None
    if not _tag_file(file, title, authors_list, album, image, song_uuid=song_uuid):
        return SaveResult.FAILED, None
    os.replace(str(file), str(target))
    return SaveResult.SAVED, target


def _tag_file(
    target: Path,
    title: str,
    authors: list[str],
    album: str,
    image: Path | None,
    *,
    song_uuid: str | None = None,
) -> bool:
    track: Track | None = _open_audio(target)
    if track is None:
        print(
            f"Warning: could not read '{target.name}', skipping save", file=sys.stderr
        )
        return False
    if config_module._settings is not None and (
        config_module._settings.clean_unwanted_tags
    ):
        track.strip_unknown_tags()
    track.set_field("title", title)
    track.set_artists(authors)
    track.set_field("album", album)
    if song_uuid is not None:
        track.write_uuid(song_uuid)
    elif track.read_uuid() is None:
        track.write_uuid(str(uuid.uuid4()))
    track.set_cover(image)
    try:
        track.save()
    except Exception as exc:
        print(f"Warning: could not save '{target.name}': {exc}", file=sys.stderr)
        return False
    return True


def read_song_profile(file: Path) -> tuple[dict[str, str], bool]:
    """Read an audio file's tag profile and whether it has embedded cover art."""
    track: Track | None = Track.open(file)
    if track is None:
        print(
            f"Warning: could not read '{file.name}', skipping tag read", file=sys.stderr
        )
        return {}, False
    return track.read_profile()


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

    def edit(track: Track) -> None:
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

    return _edit_track_atomically(file, edit)


def change_cover(image: Path, file: Path) -> None:
    def edit(track: Track) -> None:
        track.set_cover(image)

    _edit_track_atomically(file, edit)


def remove_cover(file: Path) -> bool:
    """Strip embedded cover art from an audio file (atomic write)."""

    def edit(track: Track) -> None:
        track._clear_cover()

    return _edit_track_atomically(file, edit)


def snapshot_song(file: Path, song_uuid: str | None, source: str) -> Path | None:
    """Snapshot a song's current tags and cover art before a mutating change.

    Snapshots live in ``<backups_folder>/<uuid>/`` as one JSON file (plus a
    ``.cover`` file when art is present) per change, so a mistaken ``tag``,
    ``modify``, or ``crop`` run can be undone with the ``restore`` command.
    Returns the snapshot path, or None when nothing could be recorded.
    """
    if song_uuid is None:
        return None
    track: Track | None = Track.open(file)
    if track is None:
        return None
    profile: dict[str, str]
    has_cover: bool
    profile, has_cover = track.read_profile()
    at: str = datetime.now().isoformat(timespec="microseconds")
    folder: Path = settings().backups_folder.joinpath(song_uuid)
    payload: dict[str, Any] = {
        "at": at,
        "source": source,
        "title": profile["title"],
        "artists": profile["artists"],
        "album": profile["album"],
        "date": profile["date"],
        "genre": profile["genre"],
        "album_artist": profile["album_artist"],
        "track_number": profile["track_number"],
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if has_cover:
            cover: tuple[str, bytes] | None = track.read_cover()
            if cover is not None:
                cover_name: str = f"{at}.cover{_mime_to_ext(cover[0])}"
                folder.joinpath(cover_name).write_bytes(cover[1])
                payload["cover"] = cover_name
        snap_path: Path = folder.joinpath(f"{at}.json")
        snap_path.write_text(json.dumps(payload), encoding="utf-8")
        return snap_path
    except OSError as exc:
        print(f"Warning: could not snapshot '{file.name}': {exc}", file=sys.stderr)
        return None


def list_snapshots(song_uuid: str) -> list[Snapshot]:
    """Every recorded snapshot for a song, oldest first and newest last."""
    folder: Path = settings().backups_folder.joinpath(song_uuid)
    if not folder.is_dir():
        return []
    snapshots: list[Snapshot] = []
    for json_file in sorted(folder.glob("*.json"), key=lambda path: path.name):
        try:
            payload: dict[str, Any] = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cover_name: str | None = payload.get("cover")
        cover: Path | None = None
        if isinstance(cover_name, str) and cover_name:
            candidate: Path = folder.joinpath(cover_name)
            cover = candidate if candidate.is_file() else None
        snapshots.append(
            Snapshot(
                at=str(payload.get("at", json_file.stem)),
                source=str(payload.get("source", "")),
                title=str(payload.get("title", "")),
                artists=str(payload.get("artists", "")),
                album=str(payload.get("album", "")),
                date=str(payload.get("date", "")),
                genre=str(payload.get("genre", "")),
                album_artist=str(payload.get("album_artist", "")),
                track_number=str(payload.get("track_number", "")),
                cover=cover,
                path=json_file,
            )
        )
    return snapshots


def restore_song_from_snapshot(file: Path, snapshot: Snapshot) -> bool:
    """Rewrite ``file`` to match a stored snapshot; returns False on failure.

    The restore is itself snapshotted first, so undoing an undo is possible.
    Only tag fields (title, artists, album, date, genre, album_artist,
    track_number, cover art) are restored: tracking UUID, download history,
    and MusicBrainz status are left as they are.
    """
    snapshot_song(file, read_song_uuid(file), "restore")
    artists: list[str] = split_authors(snapshot.artists)
    ok: bool = apply_tag_update(
        file,
        title=snapshot.title,
        artists=artists,
        album=snapshot.album,
        date=snapshot.date,
        genre=snapshot.genre,
        album_artist=snapshot.album_artist,
        track_number=snapshot.track_number,
        image=snapshot.cover,
    )
    if not ok:
        return False
    if snapshot.cover is None and has_cover(file) and not remove_cover(file):
        return False
    song_uuid: str | None = read_song_uuid(file)
    if song_uuid is not None:
        db.add_authors(distinct_authors(artists, db.get_authors()))
        db.update_song_metadata(
            song_uuid,
            title=snapshot.title,
            artists=snapshot.artists,
            album=snapshot.album,
            date=snapshot.date,
            genre=snapshot.genre,
            album_artist=snapshot.album_artist,
            track_number=snapshot.track_number,
            has_cover=has_cover(file),
        )
        db.log_event(
            song_uuid,
            "restored",
            {"at": snapshot.at, "source": snapshot.source},
        )
    return True


def backup_cover(path: Path) -> bool:
    """Copy a cover file into the backups folder before it is overwritten.

    The crop command overwrites the cover image in place (crop_image_1_to_1)
    and then re-embeds the result, destroying the original art; keeping the
    pre-crop copy makes a botched crop recoverable. Returns False when the
    copy could not be made.
    """
    if not path.is_file():
        return False
    dest: Path = settings().backups_folder.joinpath("covers")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        target: Path = dest.joinpath(f"{path.stem}{path.suffix}")
        if target.is_file():
            target = dest.joinpath(f"{path.stem}-{time.time_ns()}{path.suffix}")
        shutil.copy2(path, target)
        return True
    except OSError as exc:
        print(f"Warning: could not back up '{path.name}': {exc}", file=sys.stderr)
        return False


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
            print(f"Couldn't find '{stem}' cover", file=sys.stderr)
            return None
        mime: str = cover[0]
        data: bytes = cover[1]
    else:
        apic_key = next((key for key in song_file if key.startswith("APIC")), None)
        if not apic_key:
            print(f"Couldn't find '{stem}' cover", file=sys.stderr)
            return None
        artwork = song_file[apic_key]
        mime = artwork.mime
        data = artwork.data
    ext: str = _mime_to_ext(mime)
    known_mime: bool = mime in _MIME_TO_EXT
    if not known_mime:
        print(
            f"Warning: unrecognized cover mime '{mime}' for '{stem}', assuming PNG",
            file=sys.stderr,
        )
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
            if config_module._settings is not None:
                backup_cover(existing)
            target.write_bytes(data)
            print(f"Updated '{target.name}'")
            return target
        if not known_mime:
            if config_module._settings is not None:
                backup_cover(existing)
            existing.write_bytes(data)
            print(f"Updated '{existing.name}'")
            return existing
        target.write_bytes(data)
        if config_module._settings is not None:
            backup_cover(existing)
        existing.unlink()
        print(f"Fixed '{existing.name}' -> '{target.name}'")
        return target
    print(f"Saving '{target.name}'")
    target.write_bytes(data)
    return target
