"""MusicBrainz metadata lookup via the musicbrainzngs client."""

import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import musicbrainzngs
from PIL import Image as PILImage

from smclipy import __version__

_AGENT_CONFIGURED = False

_SEARCH_LIMIT = 5

# MusicBrainz permits about 1 request/second; keep a polite minimum gap.
_MIN_REQUEST_INTERVAL = 1.0
_last_request_at = 0.0

_SNIFFED_EXT_BY_FORMAT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    remaining = _MIN_REQUEST_INTERVAL - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _last_request_at = time.monotonic()


@dataclass(frozen=True)
class MusicBrainzMatch:
    """A single recording candidate returned by MusicBrainz."""

    title: str
    artists: list[str]
    album: str
    date: str
    album_artist: str
    track_number: str
    recording_id: str
    release_group_id: str | None


def _configure_agent() -> None:
    global _AGENT_CONFIGURED
    if not _AGENT_CONFIGURED:
        musicbrainzngs.set_useragent("smclipy", __version__)
        _AGENT_CONFIGURED = True


def _artist_credit(recording: dict[str, Any]) -> list[str]:
    artists: list[str] = []
    for credit in recording.get("artist-credit", []):
        if not isinstance(credit, dict):
            continue
        artist = credit.get("artist")
        name = str(artist.get("name", "")).strip() if isinstance(artist, dict) else ""
        if not name:
            name = str(credit.get("name", "")).strip()
        if name and name not in artists:
            artists.append(name)
    return artists


def _first_official_release(recording: dict[str, Any]) -> dict[str, Any] | None:
    releases = list(recording.get("release-list", []))
    for release in releases:
        if str(release.get("status", "")).casefold() == "official":
            official: dict[str, Any] = release
            return official
    if not releases:
        return None
    first: dict[str, Any] = releases[0]
    return first


def _has_official_release(recording: dict[str, Any]) -> bool:
    return any(
        str(release.get("status", "")).casefold() == "official"
        for release in recording.get("release-list", [])
    )


def _best_track_number(release: dict[str, Any], recording_title: str) -> str:
    fallback = ""
    expected = recording_title.casefold().strip()
    for medium in release.get("medium-list", []):
        for track in medium.get("track-list", []):
            number = str(track.get("number", "")).strip()
            if not number:
                continue
            if str(track.get("title", "")).strip().casefold() == expected:
                return number
            if not fallback:
                fallback = number
    return fallback


def _release_details(
    release: dict[str, Any] | None, recording_title: str
) -> tuple[str, str, str, str, str | None]:
    if release is None:
        return "", "", "", "", None
    album = str(release.get("title", "")).strip()
    date = str(release.get("date", "")).strip()
    album_artist = str(release.get("artist-credit-phrase", "")).strip()
    release_group = release.get("release-group", {})
    release_group_id = str(release_group.get("id") or "") or None
    track_number = _best_track_number(release, recording_title)
    return album, date, album_artist, track_number, release_group_id


def _match_from_recording(recording: dict[str, Any]) -> MusicBrainzMatch:
    title = str(recording.get("title", "")).strip()
    artists = _artist_credit(recording)
    release = _first_official_release(recording)
    album, date, album_artist, track_number, release_group_id = _release_details(
        release, title
    )
    return MusicBrainzMatch(
        title=title,
        artists=artists,
        album=album,
        date=date,
        album_artist=album_artist,
        track_number=track_number,
        recording_id=str(recording.get("id", "")),
        release_group_id=release_group_id,
    )


def _rank_recording(recording: dict[str, Any]) -> tuple[int, int, int]:
    """Rank an official studio recording above covers and live tracks."""
    raw_score = recording.get("ext:score", "") or ""
    try:
        score = int(str(raw_score))
    except (TypeError, ValueError):
        score = 0
    official = 1 if _has_official_release(recording) else 0
    score_part = score if official else max(0, score - 20)
    haystack = " ".join(
        {
            str(recording.get("title", "")),
            str(recording.get("disambiguation", "")),
        }
    ).casefold()
    if "cover" in haystack or "live" in haystack:
        score_part -= 20
    return (score_part, official, score)


def _clean_query_value(value: str) -> str:
    """Strip double quotes, which act as query syntax in MusicBrainz searches."""
    return value.replace('"', "").strip()


def search_recordings(
    title: str, artists: list[str], album: str | None = None, limit: int = _SEARCH_LIMIT
) -> list[MusicBrainzMatch]:
    """Search MusicBrainz for a recording and return ranked candidates."""
    _configure_agent()
    cleaned_title = _clean_query_value(title)
    if not cleaned_title:
        return []
    query_parts = [f'"{cleaned_title}"']
    for artist in artists:
        clean = _clean_query_value(artist)
        if clean:
            query_parts.append(f' AND artist:"{clean}"')
    if album:
        clean_album = _clean_query_value(album)
        if clean_album:
            query_parts.append(f' AND release:"{clean_album}"')
    try:
        _throttle()
        result = musicbrainzngs.search_recordings(
            query="".join(query_parts), limit=limit
        )
    except (musicbrainzngs.WebServiceError, OSError, ValueError):
        return []
    recordings = result.get("recording-list", [])
    recordings.sort(key=_rank_recording, reverse=True)
    return [_match_from_recording(recording) for recording in recordings]


def _sniff_cover_extension(data: bytes) -> str | None:
    """Detect the cover's image format, or None if ``data`` isn't an image."""
    try:
        with PILImage.open(BytesIO(data)) as image:
            format_name = image.format
    except Exception:
        return None
    if format_name is None:
        return None
    return _SNIFFED_EXT_BY_FORMAT.get(format_name, "")


def fetch_cover_art(release_group_id: str, dest: Path) -> Path | None:
    """Download the release-group front cover and return its path.

    The returned file path has an extension matching the actual image
    format (PNG/WebP/GIF may be served despite a ``.jpg`` request).
    Returns None if the release serves no cover or the bytes are not a
    valid image, without writing anything to disk.
    """
    _configure_agent()
    _throttle()
    try:
        data = musicbrainzngs.get_release_group_image_front(release_group_id)
    except (musicbrainzngs.WebServiceError, OSError, ValueError):
        return None
    if not data:
        return None
    extension = _sniff_cover_extension(data)
    if extension is None:
        print(
            f"Warning: cover art for release group {release_group_id} "
            "is not a valid image, skipping."
        )
        return None
    actual_dest = dest.with_suffix(extension) if extension else dest
    actual_dest.parent.mkdir(parents=True, exist_ok=True)
    actual_dest.write_bytes(data)
    return actual_dest
