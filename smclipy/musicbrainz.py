"""MusicBrainz metadata lookup via the musicbrainzngs client."""

import re
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import musicbrainzngs
from PIL import Image as PILImage

from smclipy import __version__

_AGENT_CONFIGURED = False

# Contact for the MusicBrainz web-service User-Agent so API requests identify
# the app (name, version, and a reachable URL), as required by MusicBrainz.
_CONTACT_URL = "https://github.com/joaquinlarroca/smclipy"

_SEARCH_LIMIT = 5

# MusicBrainz permits about 1 request/second; keep a polite minimum gap.
_MIN_REQUEST_INTERVAL = 1.0
_last_request_at = 0.0

# Entities whose genres smclipy can look up. Release groups are album-level and
# much better populated than recordings, so they are tried first.
_GENRE_INCLUDE_ENTITIES = ("release-group", "recording")
_GENRE_CACHE: dict[str, str] = {}
_genre_failure_warned = False

_SNIFFED_EXT_BY_FORMAT: dict[str, str] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}


class MusicBrainzUnavailable(Exception):
    """MusicBrainz could not be reached or returned an unusable response."""


class CoverArtUnavailable(Exception):
    """Cover art could not be fetched because of a transient failure."""


def _throttle() -> None:
    global _last_request_at
    elapsed: float = time.monotonic() - _last_request_at
    remaining: float = _MIN_REQUEST_INTERVAL - elapsed
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
    genre: str = ""


# Disambiguations and bracketed/suffixed title qualifiers mark alternate
# versions ("live", "cover"); matching the bare words would also punish real
# song titles such as "Live Forever" or "Cover Me".
_ALT_VERSION_TAG = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_ALT_VERSION_WORDS = re.compile(r"\b(?:live|cover)\b", re.IGNORECASE)


def _enable_genre_include() -> None:
    """Allow the API's ``genres`` include in the bundled musicbrainzngs.

    The client's include allow-list predates MusicBrainz's genre support, so
    the entity types smclipy queries need the include added once. Without it
    the client rejects the request before it reaches the API. The attribute is
    private, so a client upgrade that moves it must not break the import;
    ``fetch_genre`` then just reports genres as unavailable at runtime.
    """
    try:
        for entity in _GENRE_INCLUDE_ENTITIES:
            includes = musicbrainzngs.musicbrainz.VALID_INCLUDES.get(entity)
            if isinstance(includes, list) and "genres" not in includes:
                includes.append("genres")
    except Exception:
        pass


_enable_genre_include()


def _configure_agent() -> None:
    global _AGENT_CONFIGURED
    if not _AGENT_CONFIGURED:
        musicbrainzngs.set_useragent("smclipy", __version__, _CONTACT_URL)
        _AGENT_CONFIGURED = True


def _artist_credit(recording: dict[str, Any]) -> list[str]:
    artists: list[str] = []
    for credit in recording.get("artist-credit", []):
        if not isinstance(credit, dict):
            continue
        artist = credit.get("artist")
        name: str = (
            str(artist.get("name", "")).strip() if isinstance(artist, dict) else ""
        )
        if not name:
            name = str(credit.get("name", "")).strip()
        if name and name not in artists:
            artists.append(name)
    return artists


def _release_matches_album(release: dict[str, Any], wanted: str) -> bool:
    release_group: dict[str, Any] = release.get("release-group") or {}
    title: str = str(release.get("title") or release_group.get("title") or "")
    return title.strip().casefold() == wanted


def _first_official_release(
    recording: dict[str, Any], album: str | None = None
) -> dict[str, Any] | None:
    """Pick the release to read album/date/track number from.

    When the search carried an album, prefer the official release whose title
    matches it so a compilation or live album listed on the recording cannot
    supply the album, date, and track number for an unrelated release.
    """
    releases: list[dict[str, Any]] = list(recording.get("release-list", []))
    if not releases:
        return None
    wanted: str = album.strip().casefold() if album else ""

    def official(release: dict[str, Any]) -> bool:
        return str(release.get("status", "")).casefold() == "official"

    if wanted:
        for release in releases:
            if official(release) and _release_matches_album(release, wanted):
                return release
    for release in releases:
        if official(release):
            return release
    if wanted:
        for release in releases:
            if _release_matches_album(release, wanted):
                return release
    return releases[0]


def _has_official_release(recording: dict[str, Any]) -> bool:
    return any(
        str(release.get("status", "")).casefold() == "official"
        for release in recording.get("release-list", [])
    )


def _best_track_number(release: dict[str, Any], recording_title: str) -> str:
    """The track number of ``recording_title`` inside ``release``.

    Only an exact track-title match wins; anything else returns "" so a
    guessed number for a different song is never written into the tags.
    """
    expected: str = recording_title.casefold().strip()
    for medium in release.get("medium-list", []):
        for track in medium.get("track-list", []):
            number: str = str(track.get("number", "")).strip()
            if not number:
                continue
            if str(track.get("title", "")).strip().casefold() == expected:
                return number
    return ""


def _release_details(
    release: dict[str, Any] | None, recording_title: str
) -> tuple[str, str, str, str, str | None]:
    if release is None:
        return "", "", "", "", None
    album: str = str(release.get("title", "")).strip()
    date: str = str(release.get("date", "")).strip()
    album_artist: str = str(release.get("artist-credit-phrase", "")).strip()
    release_group = release.get("release-group", {})
    release_group_id: str | None = str(release_group.get("id") or "") or None
    track_number: str = _best_track_number(release, recording_title)
    return album, date, album_artist, track_number, release_group_id


def _match_from_recording(
    recording: dict[str, Any], album: str | None = None
) -> MusicBrainzMatch:
    title: str = str(recording.get("title", "")).strip()
    artists: list[str] = _artist_credit(recording)
    release: dict[str, Any] | None = _first_official_release(recording, album)
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
    raw_score: Any = recording.get("ext:score", "") or ""
    try:
        score = int(str(raw_score))
    except (TypeError, ValueError):
        score = 0
    official: Literal[0, 1] = 1 if _has_official_release(recording) else 0
    score_part: int = score if official else max(0, score - 20)
    title: str = str(recording.get("title", ""))
    disambiguation: str = str(recording.get("disambiguation", ""))
    alternate_version: bool = bool(_ALT_VERSION_WORDS.search(disambiguation)) or any(
        _ALT_VERSION_WORDS.search(qualifier)
        for qualifier in _ALT_VERSION_TAG.findall(title)
    )
    if alternate_version:
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
    cleaned_title: str = _clean_query_value(title)
    if not cleaned_title:
        return []
    query_parts: list[str] = [f'"{cleaned_title}"']
    for artist in artists:
        clean: str = _clean_query_value(artist)
        if clean:
            query_parts.append(f' AND artist:"{clean}"')
    if album:
        clean_album: str = _clean_query_value(album)
        if clean_album:
            query_parts.append(f' AND release:"{clean_album}"')
    try:
        _throttle()
        result = musicbrainzngs.search_recordings(
            query="".join(query_parts), limit=limit
        )
    except (musicbrainzngs.WebServiceError, OSError, ValueError):
        raise MusicBrainzUnavailable(
            "The MusicBrainz web service could not be reached."
        ) from None
    recordings = result.get("recording-list", [])
    recordings.sort(key=_rank_recording, reverse=True)
    return [_match_from_recording(recording, album) for recording in recordings]


def _cache_key(release_group_id: str | None, recording_id: str | None) -> str:
    if release_group_id:
        return f"rg:{release_group_id}"
    if recording_id:
        return f"rec:{recording_id}"
    return ""


def _join_genres(genres: list[dict[str, Any]]) -> str:
    """Join genre names as ``"most voted; ...; least voted"``.

    MusicBrainz returns genres alphabetically; listeners expect the tag to
    start with the defining genre, so rank by vote count first. Names are
    deduplicated case-insensitively and empty entries are ignored.
    """
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for genre in genres:
        name: str = str(genre.get("name", "")).strip()
        key: str = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        try:
            count: int = int(genre.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        ranked.append((count, name))
    ranked.sort(key=lambda item: (-item[0], item[1].casefold()))
    return "; ".join(name for _, name in ranked)


def _warn_genre_failure(exc: Exception) -> None:
    global _genre_failure_warned
    if _genre_failure_warned:
        return
    _genre_failure_warned = True
    print(
        f"Warning: could not fetch genres from MusicBrainz ({exc}); "
        "the song will be retried on the next run.",
        file=sys.stderr,
    )


def fetch_genre(release_group_id: str | None, recording_id: str | None) -> str | None:
    """Return the match's genres joined by ``"; "``.

    Returns "" when MusicBrainz has no genres for the entity, and None when
    the request itself failed (so callers can leave the song untagged and
    retry it later instead of caching a bogus empty result).

    Release-group genres are album-level and preferred; recording genres are
    only consulted when the release group has none. Successful results are
    cached per release group so tagging a whole album costs a single request.
    """
    _configure_agent()
    key: str = _cache_key(release_group_id, recording_id)
    if key and key in _GENRE_CACHE:
        return _GENRE_CACHE[key]
    genres = ""
    try:
        if release_group_id:
            _throttle()
            release_group = musicbrainzngs.get_release_group_by_id(
                release_group_id, includes=["genres"]
            )
            genres = _join_genres(
                release_group.get("release-group", {}).get("genres", [])
            )
        if not genres and recording_id:
            _throttle()
            recording = musicbrainzngs.get_recording_by_id(
                recording_id, includes=["genres"]
            )
            genres = _join_genres(recording.get("recording", {}).get("genres", []))
    except (musicbrainzngs.MusicBrainzError, OSError, ValueError) as exc:
        _warn_genre_failure(exc)
        return None
    if key:
        _GENRE_CACHE[key] = genres
    return genres


def _sniff_cover_extension(data: bytes) -> str | None:
    """Detect the cover's image format, or None if ``data`` isn't an image."""
    try:
        with PILImage.open(BytesIO(data)) as image:
            format_name: str | None = image.format
    except Exception:
        return None
    if format_name is None:
        return None
    return _SNIFFED_EXT_BY_FORMAT.get(format_name, "")


def fetch_cover_art(release_group_id: str, dest: Path) -> Path | None:
    """Download the release-group front cover and return its path.

    The returned file path has an extension matching the actual image
    format (PNG/WebP/GIF may be served despite a ``.jpg`` request).
    Returns None when the release genuinely has no cover, and raises
    :class:`CoverArtUnavailable` for transient failures (network, rate limit,
    invalid bytes) so callers can retry the song later instead of burying it
    as permanently coverless. Nothing is written to disk on failure.
    """
    _configure_agent()
    _throttle()
    try:
        data = musicbrainzngs.get_release_group_image_front(release_group_id)
    except musicbrainzngs.ResponseError as exc:
        cause: Any = getattr(exc, "cause", None)
        if getattr(cause, "code", None) == 404:
            return None
        raise CoverArtUnavailable(
            f"Cover Art Archive returned an error for {release_group_id}."
        ) from None
    except (musicbrainzngs.MusicBrainzError, OSError, ValueError) as exc:
        raise CoverArtUnavailable(
            f"Cover Art Archive could not be reached ({exc})."
        ) from None
    if not data:
        return None
    extension: str | None = _sniff_cover_extension(data)
    if extension is None:
        raise CoverArtUnavailable(
            f"Cover art for release group {release_group_id} is not a valid image."
        )
    actual_dest: Path = dest.with_suffix(extension) if extension else dest
    actual_dest.parent.mkdir(parents=True, exist_ok=True)
    actual_dest.write_bytes(data)
    return actual_dest
