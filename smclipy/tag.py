"""Interactive library tagging using MusicBrainz metadata."""

from contextlib import suppress
from pathlib import Path

from smclipy.config import COVER_FIELD, TAG_FIELDS, init, settings
from smclipy.helpers import distinct_authors, resolve_known_authors, split_authors
from smclipy.images import display_image
from smclipy.metadata import apply_tag_update, read_song_profile, scan_library
from smclipy.musicbrainz import MusicBrainzMatch, fetch_cover_art, search_recordings
from smclipy.storage import append_unique_lines, read_lines
from smclipy.ui import (
    collect_tag_changes,
    prompt_match_selection,
    prompt_song_selection,
    prompt_tag_changes,
)

TAG_COVER_PREFIX = "tag-cover-"


class LibrarySong:
    """An MP3 already present in the music folder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.current, self.has_cover = read_song_profile(path)

    @property
    def display_name(self) -> str:
        title = self.current.get("title") or self.path.stem
        artists = self.current.get("artists", "")
        if artists:
            return f"{title} - {artists}"
        return title


def list_library_songs() -> list[LibrarySong]:
    songs: list[LibrarySong] = []
    for file in sorted(settings().music_folder.glob("*.mp3")):
        if not file.is_file():
            continue
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
    current_artists = split_authors(current.get("artists", ""))
    known = read_lines(settings().authors_file) + current_artists
    intended = resolve_known_authors(match.artists, known)
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
    artists = split_authors(song.current.get("artists", ""))
    album = song.current.get("album", "") or None
    return str(song.current.get("title") or song.path.stem), artists, album


def process_song(
    song: LibrarySong,
    position: int | None = None,
    total: int | None = None,
) -> bool:
    """Fetch candidates, let the user pick one, and apply confirmed changes."""
    s = settings()
    title, artists, album = _search_query(song)
    progress = f"[{position}/{total}] " if position is not None and total else ""
    print(f"\n{progress}=== {song.display_name} ===\n")
    matches = search_recordings(title, artists, album)
    if not matches:
        print("No MusicBrainz matches found, skipping.")
        return False

    selected = prompt_match_selection(matches)
    if selected is None:
        print("Skipped.")
        return False
    match = matches[selected]

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
            if cover_path:
                display_image(cover_path)

    intended = _intended_profile(match, song.current)
    changes = collect_tag_changes(song.current, intended)
    if not changes and not cover_pending:
        print("No tag changes to apply.")
        return False
    to_apply = prompt_tag_changes(changes, cover_pending=cover_pending)
    try:
        if not to_apply:
            print("Skipped.")
            return False
        _apply(song, match, intended, cover_path, fields=set(to_apply))
    finally:
        if cover_path is not None:
            with suppress(OSError):
                cover_path.unlink()
    return True


def _apply(
    song: LibrarySong,
    match: MusicBrainzMatch,
    intended: dict[str, str],
    cover_path: Path | None,
    fields: set[str] | None = None,
) -> None:
    s = settings()
    if fields is None:
        fields = set(_enabled_fields())
        if cover_path is not None:
            fields.add(COVER_FIELD)
    current = song.current
    if "artists" in fields:
        artists = _intend_artists(match, current) or split_authors(
            current.get("artists", "")
        )
    else:
        artists = split_authors(current.get("artists", ""))
    ok = apply_tag_update(
        song.path,
        title=_intend_title(match, current) if "title" in fields else None,
        artists=artists,
        album=_intend_album(match, current) if "album" in fields else None,
        date=_intend_date(match, current) if "date" in fields else None,
        album_artist=_intend_album_artist(match, current)
        if "album_artist" in fields
        else None,
        track_number=_intend_track_number(match, current)
        if "track_number" in fields
        else None,
        image=cover_path if "cover" in fields else None,
    )
    if not ok:
        print(f"Warning: could not apply changes to '{song.path.name}'.")
        return
    previous_artists = split_authors(current.get("artists", ""))
    new_artists = distinct_authors(artists, previous_artists)
    append_unique_lines(s.authors_file, new_artists)
    new_title = intended.get("title") or current.get("title") or song.path.stem
    append_unique_lines(s.songs_info, [f"{new_title} - {', '.join(artists)}"])
    append_unique_lines(
        s.tagged_files_file, [song.path.relative_to(s.music_folder).as_posix()]
    )
    print(f"Updated '{song.path.name}'.")


def _cleanup_tag_cover_files() -> None:
    """Remove leftover tag-cover files, including from interrupted runs."""
    temp_folder = settings().temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob(f"{TAG_COVER_PREFIX}*"):
        with suppress(OSError):
            temp_file.unlink()


def cmd_tag(_args: object) -> None:
    init()
    print("Scanning music files...")
    scan_library()

    songs = list_library_songs()
    if not songs:
        print("No MP3 files found in the library to tag.")
        return

    enabled = _enabled_fields()
    print(f"Found {len(songs)} MP3(s) in the library.")
    if enabled:
        print(f"Fields to auto-fill: {', '.join(sorted(enabled))}")
    else:
        print("Warning: no tag fields are enabled (config 'tag_fields' is empty).")
        print("Add fields such as 'title', 'artists', 'album' to tag song metadata.")
    print("\nSongs in your library:")
    for index, song in enumerate(songs, start=1):
        print(f"{index:3}. {song.display_name}")

    selected = prompt_song_selection(len(songs))
    updated = 0
    skipped = 0
    music_folder = settings().music_folder
    tagged_files = set(read_lines(settings().tagged_files_file))
    try:
        if not selected:
            return
        for index, position in enumerate(selected, start=1):
            song = songs[position]
            if song.path.relative_to(music_folder).as_posix() in tagged_files:
                print(f"Skipping '{song.display_name}': already tagged before.")
                skipped += 1
                continue
            try:
                if process_song(song, position=index, total=len(selected)):
                    updated += 1
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                raise SystemExit(130) from None
            except Exception as exc:
                print(f"Warning: could not process '{song.display_name}': {exc!r}")
    finally:
        _cleanup_tag_cover_files()
    message = f"\nDone. Updated {updated} song(s)."
    if skipped:
        message += f" Skipped {skipped} already-tagged song(s)."
    print(message)
