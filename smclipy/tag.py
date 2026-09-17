"""Interactive library tagging using MusicBrainz metadata."""

from contextlib import suppress
from pathlib import Path

import smclipy.db as db
from smclipy.config import COVER_FIELD, TAG_FIELDS, Settings, init, settings
from smclipy.helpers import distinct_authors, resolve_known_authors, split_authors
from smclipy.images import display_image
from smclipy.metadata import (
    apply_tag_update,
    has_cover,
    iter_audio_files,
    read_song_profile,
    read_song_uuid,
    scan_library,
)
from smclipy.musicbrainz import MusicBrainzMatch, fetch_cover_art, search_recordings
from smclipy.ui import (
    collect_tag_changes,
    prompt_match_selection,
    prompt_song_selection,
    prompt_tag_changes,
)

TAG_COVER_PREFIX = "tag-cover-"


class LibrarySong:
    """An audio file already present in the music folder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.uuid: str | None = read_song_uuid(path)
        self.current, self.has_cover = read_song_profile(path)

    @property
    def display_name(self) -> str:
        title: str = self.current.get("title") or self.path.stem
        artists: str = self.current.get("artists", "")
        if artists:
            return f"{title} - {artists}"
        return title


def list_library_songs() -> list[LibrarySong]:
    songs: list[LibrarySong] = []
    for file in iter_audio_files():
        songs.append(LibrarySong(file))
    return songs


def _enabled_fields() -> frozenset[str]:
    return TAG_FIELDS.intersection(settings().tag_fields)


def _intend_title(match: MusicBrainzMatch, current: dict[str, str]) -> str | None:
    if "title" not in _enabled_fields() or not match.title:
        return None
    return match.title if match.title != current.get("title") else None


def _intend_artists(
    match: MusicBrainzMatch, current: dict[str, str]
) -> list[str] | None:
    if "artists" not in _enabled_fields() or not match.artists:
        return None
    current_artists: list[str] = split_authors(current.get("artists", ""))
    known: list[str] = db.get_authors() + current_artists
    intended: list[str] = resolve_known_authors(match.artists, known)
    if sorted(intended) == sorted(current_artists):
        return None
    return intended


def _intend_album(match: MusicBrainzMatch, current: dict[str, str]) -> str | None:
    if "album" not in _enabled_fields() or not match.album:
        return None
    if (
        not settings().write_album_if_same_as_title
        and match.album.casefold() == match.title.casefold()
    ):
        return None
    return match.album if match.album != current.get("album") else None


def _intend_date(match: MusicBrainzMatch, current: dict[str, str]) -> str | None:
    if "date" not in _enabled_fields() or not match.date:
        return None
    return match.date if match.date != current.get("date") else None


def _intend_album_artist(
    match: MusicBrainzMatch, current: dict[str, str]
) -> str | None:
    if "album_artist" not in _enabled_fields() or not match.album_artist:
        return None
    if match.album_artist != current.get("album_artist"):
        return match.album_artist
    return None


def _intend_track_number(
    match: MusicBrainzMatch, current: dict[str, str]
) -> str | None:
    if "track_number" not in _enabled_fields() or not match.track_number:
        return None
    return (
        match.track_number
        if match.track_number != current.get("track_number")
        else None
    )


def _intended_profile(
    match: MusicBrainzMatch, current: dict[str, str]
) -> dict[str, str]:
    return {
        "title": _intend_title(match, current) or current.get("title", ""),
        "artists": ", ".join(
            _intend_artists(match, current) or split_authors(current.get("artists", ""))
        ),
        "album": _intend_album(match, current) or current.get("album", ""),
        "date": _intend_date(match, current) or current.get("date", ""),
        "album_artist": _intend_album_artist(match, current)
        or current.get("album_artist", ""),
        "track_number": _intend_track_number(match, current)
        or current.get("track_number", ""),
    }


def _search_query(song: LibrarySong) -> tuple[str, list[str], str | None]:
    artists: list[str] = split_authors(song.current.get("artists", ""))
    album: str | None = song.current.get("album", "") or None
    return song.current.get("title") or song.path.stem, artists, album


def _record_mb_outcome(song_uuid: str | None, status: str) -> None:
    if song_uuid is None:
        return
    db.record_mb_status(song_uuid, status)
    db.log_event(song_uuid, "musicbrainz", {"status": status})


def _record_already_matching(song: LibrarySong, match: MusicBrainzMatch) -> None:
    """Record a successful match that needed no file changes.

    The enabled tag fields already match MusicBrainz, so the song is marked
    tagged (with its recording ids) instead of skipped, and won't be
    re-offered on the next tag run.
    """
    if song.uuid is None:
        return
    db.record_mb_status(
        song.uuid,
        db.MB_STATUS_TAGGED,
        recording_id=match.recording_id or None,
        release_group_id=match.release_group_id,
        tagged=True,
    )
    db.log_event(
        song.uuid,
        "tagged",
        {
            "fields": [],
            "already_matching": True,
            "title": song.current.get("title") or song.path.stem,
            "artists": song.current.get("artists", ""),
        },
    )


def process_song(
    song: LibrarySong,
    position: int | None = None,
    total: int | None = None,
    *,
    mode: str = "interactive",
) -> bool:
    """Fetch candidates, pick one, and apply confirmed changes.

    ``mode`` selects the interaction level:
    - ``"interactive"``: let the user pick a candidate and confirm the changes.
    - ``"semi"``: auto-pick the top candidate but still confirm the changes.
    - ``"auto"``: auto-pick the top candidate and apply all configured fields
      without asking.
    """
    s: Settings = settings()
    title, artists, album = _search_query(song)
    progress: str = f"[{position}/{total}] " if position is not None and total else ""
    if mode == "auto":
        print(f"{progress}Auto-tagging '{song.display_name}'...")
    else:
        print(f"\n{progress}=== {song.display_name} ===\n")
    matches: list[MusicBrainzMatch] = search_recordings(title, artists, album)
    if not matches:
        print("No MusicBrainz matches found, skipping.")
        _record_mb_outcome(song.uuid, db.MB_STATUS_NOT_FOUND)
        return False

    if mode == "interactive":
        selected: int | None = prompt_match_selection(matches)
        if selected is None:
            print("Skipped.")
            _record_mb_outcome(song.uuid, db.MB_STATUS_SKIPPED)
            return False
    else:
        selected = 0
    match: MusicBrainzMatch = matches[selected]
    if mode != "interactive":
        print(f"Using top match: {', '.join(match.artists)} - '{match.title}'.")

    cover_path: Path | None = None
    cover_pending = False
    if COVER_FIELD in _enabled_fields() and match.release_group_id:
        if song.has_cover:
            print("This song already has a cover. Skipping cover fetch.")
        else:
            cover_path = fetch_cover_art(
                match.release_group_id,
                s.temp_folder.joinpath(f"{TAG_COVER_PREFIX}{match.recording_id}.jpg"),
            )
            cover_pending = cover_path is not None
            if cover_path and mode != "auto":
                display_image(cover_path)

    intended: dict[str, str] = _intended_profile(match, song.current)
    changes: list[tuple[str, str, str]] = collect_tag_changes(song.current, intended)
    if not changes and not cover_pending:
        print("Tags already match MusicBrainz; marking song as tagged.")
        _record_already_matching(song, match)
        return False
    to_apply: list[str] | None = None
    if mode != "auto":
        to_apply = prompt_tag_changes(changes, cover_pending=cover_pending)
    try:
        fields: set[str] | None
        if mode == "auto":
            fields = None
        elif to_apply:
            fields = set(to_apply)
        else:
            print("Skipped.")
            _record_mb_outcome(song.uuid, db.MB_STATUS_SKIPPED)
            return False
        _apply(song, match, cover_path, fields)
    finally:
        if cover_path is not None:
            with suppress(OSError):
                cover_path.unlink()
    return True


def _apply(
    song: LibrarySong,
    match: MusicBrainzMatch,
    cover_path: Path | None,
    fields: set[str] | None = None,
) -> None:
    if fields is None:
        fields = set(_enabled_fields())
        if cover_path is not None:
            fields.add(COVER_FIELD)
    current: dict[str, str] = song.current
    artists: list[str] | None
    if "artists" in fields:
        artists = _intend_artists(match, current) or split_authors(
            current.get("artists", "")
        )
    else:
        artists = None
    title: str | None = _intend_title(match, current) if "title" in fields else None
    album: str | None = _intend_album(match, current) if "album" in fields else None
    date: str | None = _intend_date(match, current) if "date" in fields else None
    album_artist: str | None = (
        _intend_album_artist(match, current) if "album_artist" in fields else None
    )
    track_number: str | None = (
        _intend_track_number(match, current) if "track_number" in fields else None
    )
    ok: bool = apply_tag_update(
        song.path,
        title=title,
        artists=artists,
        album=album,
        date=date,
        album_artist=album_artist,
        track_number=track_number,
        image=cover_path if "cover" in fields else None,
    )
    if not ok:
        print(f"Warning: could not apply changes to '{song.path.name}'.")
        return
    previous_artists: list[str] = split_authors(current.get("artists", ""))
    new_artists: list[str] = distinct_authors(artists or [], previous_artists)
    db.add_authors(new_artists)
    new_title: str = title or current.get("title") or song.path.stem
    if song.uuid is not None:
        db.update_song_metadata(
            song.uuid,
            title=title,
            artists=", ".join(artists) if artists is not None else None,
            album=album,
            date=date,
            album_artist=album_artist,
            track_number=track_number,
            has_cover=has_cover(song.path) if COVER_FIELD in fields else None,
        )
        db.record_mb_status(
            song.uuid,
            db.MB_STATUS_TAGGED,
            recording_id=match.recording_id or None,
            release_group_id=match.release_group_id,
            tagged=True,
        )
        db.log_event(
            song.uuid,
            "tagged",
            {
                "fields": sorted(fields),
                "title": new_title,
                "artists": ", ".join(artists) if artists is not None else "",
            },
        )
    print(f"Updated '{song.path.name}'.")


def _cleanup_tag_cover_files() -> None:
    """Remove leftover tag-cover files, including from interrupted runs."""
    temp_folder: Path = settings().temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob(f"{TAG_COVER_PREFIX}*"):
        with suppress(OSError):
            temp_file.unlink()


def cmd_tag(args: object) -> None:
    auto: bool = bool(getattr(args, "auto", False))
    semi: bool = bool(getattr(args, "semi", False))
    mode: str = "auto" if auto else "semi" if semi else "interactive"
    if mode == "auto":
        print(
            "Auto mode: the top MusicBrainz match will be applied to each "
            "selected song with no further prompts."
        )
    elif mode == "semi":
        print(
            "Semi-auto mode: the top MusicBrainz match is picked per song, "
            "but you still review the changes before applying."
        )
    init()
    print("Scanning music files...")
    scan_library()

    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library to tag.")
        return

    enabled: frozenset[str] = _enabled_fields()
    print(f"Found {len(songs)} audio file(s) in the library.")
    if enabled:
        print(f"Fields to auto-fill: {', '.join(sorted(enabled))}")
    else:
        print("Warning: no tag fields are enabled (config 'tag_fields' is empty).")
        print("Add fields such as 'title', 'artists', 'album' to tag song metadata.")
    print("\nSongs in your library:")
    for index, song in enumerate(songs, start=1):
        print(f"{index:3}. {song.display_name}")

    selected: list[int] = prompt_song_selection(len(songs))
    updated = 0
    skipped = 0
    tagged_by_uuid: dict[str, bool] = {
        row["uuid"]: bool(row["tagged"]) for row in db.list_songs()
    }
    try:
        if not selected:
            return
        for index, position in enumerate(selected, start=1):
            song = songs[position]
            if song.uuid is not None and tagged_by_uuid.get(song.uuid):
                print(f"Skipping '{song.display_name}': already tagged before.")
                skipped += 1
                continue
            try:
                if process_song(song, position=index, total=len(selected), mode=mode):
                    updated += 1
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                raise SystemExit(130) from None
            except Exception as exc:
                print(f"Warning: could not process '{song.display_name}': {exc!r}")
    finally:
        _cleanup_tag_cover_files()
    message: str = f"\nDone. Updated {updated} song(s)."
    if skipped:
        message += f" Skipped {skipped} already-tagged song(s)."
    print(message)
