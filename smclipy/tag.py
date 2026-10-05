"""Interactive library tagging using MusicBrainz metadata."""

import json
import re
import sys
from contextlib import nullcontext, redirect_stdout, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
    snapshot_song,
)
from smclipy.musicbrainz import (
    MusicBrainzMatch,
    MusicBrainzUnavailable,
    fetch_cover_art,
    search_recordings,
)
from smclipy.ui import (
    collect_tag_changes,
    prompt_match_selection,
    prompt_song_selection,
    prompt_tag_changes,
)

TAG_COVER_PREFIX = "tag-cover-"

_MAX_AGE_UNITS: dict[str, float] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


def parse_max_age(value: str) -> float:
    """Parse a duration like ``24h``, ``30m``, ``7d`` into seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s*", str(value))
    if not match:
        raise ValueError(
            f"invalid age {value!r}: use a number plus a unit "
            "(s/m/h/d/w, e.g. '30m', '24h', '7d')"
        )
    amount, unit = float(match.group(1)), match.group(2).casefold()
    if unit not in _MAX_AGE_UNITS:
        raise ValueError(
            f"invalid age {value!r}: unknown unit {match.group(2)!r} "
            "(use s/m/h/d/w, e.g. '30m', '24h', '7d')"
        )
    seconds = amount * _MAX_AGE_UNITS[unit]
    if seconds <= 0:
        raise ValueError(f"invalid age {value!r}: must be greater than zero")
    return seconds


def _max_age_seconds(args: object) -> tuple[float | None, str | None]:
    """Return ``(seconds, raw)`` for the ``--max-age`` flag, if given."""
    raw: object = getattr(args, "max_age", None)
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        seconds = float(raw)
        if seconds <= 0:
            print("error: --max-age must be greater than zero", file=sys.stderr)
            raise SystemExit(2)
        return seconds, str(raw)
    try:
        return parse_max_age(str(raw)), str(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def _filter_by_first_seen(
    songs: list["LibrarySong"], max_age: float
) -> list["LibrarySong"]:
    """Keep songs first seen within the last ``max_age`` seconds.

    ``scan_library`` rewrites files when backfilling UUIDs, so file mtimes
    can't be trusted for recency — the database ``first_seen_at`` timestamp
    is stable across rescans and renames. Songs with no UUID or no recorded
    timestamp are kept so the filter never hides untracked songs.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age)
    seen_at_by_uuid: dict[str, str] = {}
    for row in db.list_songs():
        try:
            seen_at_by_uuid[str(row["uuid"])] = str(row["first_seen_at"] or "")
        except (KeyError, IndexError, TypeError):
            continue
    recent: list[LibrarySong] = []
    for song in songs:
        if song.uuid is None:
            recent.append(song)
            continue
        raw: str = seen_at_by_uuid.get(song.uuid, "")
        if not raw:
            recent.append(song)
            continue
        try:
            seen_at = datetime.fromisoformat(raw)
        except ValueError:
            recent.append(song)
            continue
        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=UTC)
        if seen_at >= cutoff:
            recent.append(song)
    return recent


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
    report: list[dict[str, Any]] | None = None,
) -> bool:
    """Fetch candidates, pick one, and apply confirmed changes.

    ``mode`` selects the interaction level:
    - ``"interactive"``: let the user pick a candidate and confirm the changes.
    - ``"semi"``: auto-pick the top candidate but still confirm the changes.
    - ``"auto"``: auto-pick the top candidate and apply all configured fields
      without asking.

    When ``report`` is given, an entry describing the outcome (applied /
    not_found / unavailable / skipped / skipped_not_found / already_tagged /
    already_matching / error) is appended
    for machine-readable retagging reports.
    """
    s: Settings = settings()
    title, artists, album = _search_query(song)
    progress: str = f"[{position}/{total}] " if position is not None and total else ""
    if mode == "auto":
        print(f"{progress}Auto-tagging '{song.display_name}'...")
    else:
        print(f"\n{progress}=== {song.display_name} ===\n")

    def append(outcome: str, **extra: Any) -> None:
        if report is not None:
            report.append(
                {
                    "outcome": outcome,
                    "display_name": song.display_name,
                    "title": song.current.get("title") or song.path.stem,
                    "artists": song.current.get("artists", ""),
                    "recording_id": None,
                    "release_group_id": None,
                    **extra,
                }
            )

    try:
        matches: list[MusicBrainzMatch] = search_recordings(title, artists, album)
    except MusicBrainzUnavailable:
        print(
            "MusicBrainz is unreachable, skipping without recording a result "
            "(the song will be offered again next run)."
        )
        append("unavailable")
        return False
    if not matches:
        print("No MusicBrainz matches found, skipping.")
        _record_mb_outcome(song.uuid, db.MB_STATUS_NOT_FOUND)
        append("not_found")
        return False

    if mode == "interactive":
        selected: int | None = prompt_match_selection(matches)
        if selected is None:
            print("Skipped.")
            _record_mb_outcome(song.uuid, db.MB_STATUS_SKIPPED)
            append("skipped")
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
        append(
            "already_matching",
            recording_id=match.recording_id or None,
            release_group_id=match.release_group_id,
            fields=[],
        )
        return False
    to_apply: list[str] | None = None
    if mode != "auto":
        to_apply = prompt_tag_changes(changes, cover_pending=cover_pending)
    requested_fields: set[str] = set()
    try:
        fields: set[str] | None
        if mode == "auto":
            fields = None
        elif to_apply:
            fields = set(to_apply)
        else:
            print("Skipped.")
            _record_mb_outcome(song.uuid, db.MB_STATUS_SKIPPED)
            append("skipped")
            return False
        # Mirror how ``_apply`` resolves a ``None`` field set: every enabled
        # field, plus the cover when one was fetched.
        requested_fields = set(_enabled_fields()) if fields is None else fields
        if fields is None and cover_path is not None:
            requested_fields.add(COVER_FIELD)
        if not _apply(song, match, cover_path, fields):
            append("error")
            return False
    finally:
        if cover_path is not None:
            with suppress(OSError):
                cover_path.unlink()
    # The report lists only what was really written: fields whose value changed
    # (``_apply`` skips fields the tags already match) plus the embedded cover.
    changed_fields: set[str] = {field for field, _, _ in changes}
    applied_fields: set[str] = requested_fields & changed_fields
    if cover_path is not None and COVER_FIELD in requested_fields:
        applied_fields.add(COVER_FIELD)
    append(
        "applied",
        recording_id=match.recording_id or None,
        release_group_id=match.release_group_id,
        fields=sorted(applied_fields),
    )
    return True


def _apply(
    song: LibrarySong,
    match: MusicBrainzMatch,
    cover_path: Path | None,
    fields: set[str] | None = None,
) -> bool:
    """Apply ``fields`` to ``song`` from ``match``; returns whether it applied."""
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
    # Record the pre-change profile so a mistaken match can be undone with
    # the `restore` command.
    snapshot_song(song.path, song.uuid, "tag")
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
        return False
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
    return True


def _cleanup_tag_cover_files() -> None:
    """Remove leftover tag-cover files, including from interrupted runs."""
    temp_folder: Path = settings().temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob(f"{TAG_COVER_PREFIX}*"):
        with suppress(OSError):
            temp_file.unlink()


def _run_tag(
    args: object, mode: str, report: list[dict[str, Any]] | None
) -> tuple[int, int]:
    """Run the retag workflow; returns ``(updated, skipped)``.

    ``skipped`` counts songs skipped because they were already tagged or,
    with ``--skip-not-found``, because a previous lookup found no match.
    While ``report`` is collecting, every human-facing message is sent to
    stderr so the caller can emit a machine-readable JSON report on stdout.
    """
    with redirect_stdout(sys.stderr) if report is not None else nullcontext():
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
            return 0, 0
        max_age, max_age_raw = _max_age_seconds(args)
        if max_age is not None:
            total_before: int = len(songs)
            songs = _filter_by_first_seen(songs, max_age)
            print(
                f"Filtered to {len(songs)} of {total_before} song(s) "
                f"added within the last {max_age_raw}."
            )
            if not songs:
                print(f"No songs added within the last {max_age_raw}.")
                return 0, 0
        enabled: frozenset[str] = _enabled_fields()
        print(f"Found {len(songs)} audio file(s) in the library.")
        if enabled:
            print(f"Fields to auto-fill: {', '.join(sorted(enabled))}")
        else:
            print("Warning: no tag fields are enabled (config 'tag_fields' is empty).")
            print(
                "Add fields such as 'title', 'artists', 'album' to tag song metadata."
            )
        print("\nSongs in your library:")
        for index, song in enumerate(songs, start=1):
            print(f"{index:3}. {song.display_name}")

        select_all: bool = bool(getattr(args, "all", False))
        if select_all:
            selected: list[int] = list(range(len(songs)))
        else:
            selected = prompt_song_selection(len(songs))
        tagged_by_uuid: dict[str, bool] = {}
        mb_status_by_uuid: dict[str, str] = {}
        for row in db.list_songs():
            uuid = str(row["uuid"])
            tagged_by_uuid[uuid] = bool(row["tagged"])
            mb_status_by_uuid[uuid] = str(row["musicbrainz_status"] or "")
        skip_not_found: bool = bool(getattr(args, "skip_not_found", False))
        updated = 0
        skipped = 0
        try:
            for index, position in enumerate(selected, start=1):
                song = songs[position]
                if song.uuid is not None and tagged_by_uuid.get(song.uuid):
                    print(f"Skipping '{song.display_name}': already tagged before.")
                    skipped += 1
                    if report is not None:
                        report.append(
                            {
                                "outcome": "already_tagged",
                                "display_name": song.display_name,
                                "title": song.current.get("title") or song.path.stem,
                                "artists": song.current.get("artists", ""),
                                "recording_id": None,
                                "release_group_id": None,
                            }
                        )
                    continue
                if (
                    skip_not_found
                    and song.uuid is not None
                    and mb_status_by_uuid.get(song.uuid) == db.MB_STATUS_NOT_FOUND
                ):
                    print(
                        f"Skipping '{song.display_name}': no MusicBrainz match "
                        "found before (run without --skip-not-found to retry)."
                    )
                    skipped += 1
                    if report is not None:
                        report.append(
                            {
                                "outcome": "skipped_not_found",
                                "display_name": song.display_name,
                                "title": song.current.get("title") or song.path.stem,
                                "artists": song.current.get("artists", ""),
                                "recording_id": None,
                                "release_group_id": None,
                            }
                        )
                    continue
                try:
                    if process_song(
                        song,
                        position=index,
                        total=len(selected),
                        mode=mode,
                        report=report,
                    ):
                        updated += 1
                except KeyboardInterrupt:
                    print("\nInterrupted, exiting...")
                    raise SystemExit(130) from None
                except Exception as exc:
                    print(f"Warning: could not process '{song.display_name}': {exc!r}")
                    if report is not None:
                        report.append(
                            {
                                "outcome": "error",
                                "display_name": song.display_name,
                                "title": song.current.get("title") or song.path.stem,
                                "artists": song.current.get("artists", ""),
                                "recording_id": None,
                                "release_group_id": None,
                                "error": repr(exc),
                            }
                        )
        finally:
            _cleanup_tag_cover_files()
    return updated, skipped


def cmd_tag(args: object) -> None:
    auto: bool = bool(getattr(args, "auto", False))
    semi: bool = bool(getattr(args, "semi", False))
    as_json: bool = bool(getattr(args, "json", False))
    mode: str = "auto" if auto else "semi" if semi else "interactive"

    report: list[dict[str, Any]] | None = [] if as_json else None
    updated: int
    skipped: int
    updated, skipped = _run_tag(args, mode, report)

    if as_json:
        print(
            json.dumps(
                {
                    "updated": updated,
                    "skipped": skipped,
                    "songs": report,
                },
                indent=2,
            )
        )
    else:
        message: str = f"\nDone. Updated {updated} song(s)."
        if skipped:
            if bool(getattr(args, "skip_not_found", False)):
                message += (
                    f" Skipped {skipped} song(s) "
                    "(already tagged or no match found before)."
                )
            else:
                message += f" Skipped {skipped} already-tagged song(s)."
        print(message)
