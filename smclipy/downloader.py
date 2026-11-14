import hashlib
import re
from dataclasses import dataclass
from typing import Any, cast

import yt_dlp as yt_dlp
import yt_dlp.utils

from smclipy.config import settings
from smclipy.formats import FORMAT_TO_NATIVE_CODEC
from smclipy.helpers import normalize_author, sanitize_filename, split_authors
from smclipy.matching import MATCH_DURATION_TOLERANCE, duration_matches

YT_STEM_PREFIX = "yt-"
SC_STEM_PREFIX = "sc-"
TMP_STEM_PREFIX = "tmp-"
TEMP_STEM_PREFIXES = (YT_STEM_PREFIX, SC_STEM_PREFIX, TMP_STEM_PREFIX)


class VideoDownloadError(Exception):
    """Raised when yt-dlp fails with a client-side HTTP error (e.g. 403)."""

    def __init__(self, video_id: str, status_code: int) -> None:
        super().__init__(f"Video '{video_id}' failed with HTTP {status_code}")
        self.video_id = video_id
        self.status_code = status_code


class SourceSearchError(Exception):
    """Raised when a source provider search fails."""


class PlaylistError(Exception):
    """Raised when a playlist URL cannot be read or expanded."""


@dataclass(frozen=True)
class SourceCandidate:
    """One search result that could become a song's download source."""

    provider: str
    title: str
    uploader: str
    duration: float | None
    url: str


@dataclass(frozen=True)
class PlaylistPreview:
    """Lightweight playlist description used before choosing a range."""

    title: str
    count: int | None


SOURCE_DURATION_TOLERANCE = MATCH_DURATION_TOLERANCE


def matches_duration(candidate: SourceCandidate, song_duration: float | None) -> bool:
    """Whether a candidate's length is close enough to the library song's."""
    return duration_matches(candidate.duration, song_duration)


SOURCE_SEARCH_PREFIXES: dict[str, str] = {
    "youtube": "ytsearch",
    "soundcloud": "scsearch",
}

_BENIGN_HOST_DELIMITERS = frozenset("([{<'\"" + "`=,;:")


def _is_host_at_boundary(text: str, start: int) -> bool:
    """Whether `text[start:]` begins at a real host position.

    A host may start at the beginning of the text, after whitespace, right
    after an ``http(s)://`` scheme, or after a benign delimiter that commonly
    wraps links (parentheses, brackets, quotes, ``=``, ``,``, etc.). This
    rejects lookalike hosts nested inside another URL, e.g.
    ``https://evil.com/soundcloud.com/artist/track``, and non-HTTP schemes
    such as ``file://`` or ``shield://``.
    """
    if start == 0:
        return True
    prefix: str = text[:start]
    char: str = prefix[-1]
    if char in _BENIGN_HOST_DELIMITERS or char.isspace():
        return True
    return prefix.endswith(("https://", "http://"))


_YOUTUBE_VIDEO_ID_RE: re.Pattern[str] = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)*youtube\.com/"
    r"(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
    r"([a-zA-Z0-9_-]{11})(?![\w-])"
)
_YOUTUBE_SHORT_URL_RE: re.Pattern[str] = re.compile(
    r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})(?![\w-])"
)


def _iter_youtube_ids(text: str) -> list[str]:
    video_ids: list[str] = []
    for url_re in (_YOUTUBE_VIDEO_ID_RE, _YOUTUBE_SHORT_URL_RE):
        for match in url_re.finditer(text):
            if _is_host_at_boundary(text, match.start()):
                video_ids.append(match.group(1))
    return video_ids


def _extract_youtube_urls(text: str) -> list[tuple[int, str]]:
    """``(start offset, normalized URL)`` pairs, per-regex in text order."""
    found: list[tuple[int, str]] = []
    for url_re in (_YOUTUBE_VIDEO_ID_RE, _YOUTUBE_SHORT_URL_RE):
        for match in url_re.finditer(text):
            if _is_host_at_boundary(text, match.start()):
                found.append(
                    (
                        match.start(),
                        f"https://www.youtube.com/watch?v={match.group(1)}",
                    )
                )
    return found


_RESERVED_USER_SEGMENTS: tuple[str, ...] = (
    "search",
    "discover",
    "you",
    "upload",
    "settings",
    "messages",
    "notifications",
    "people",
    "sets",
    "tracks",
    "likes",
    "albums",
    "playlists",
    "followers",
    "following",
    "comments",
    "stream",
)
_RESERVED_TRACK_SEGMENTS: tuple[str, ...] = (
    "search",
    "discover",
    "upload",
    "settings",
    "messages",
    "notifications",
    "sets",
    "tracks",
    "likes",
    "albums",
    "playlists",
    "followers",
    "following",
    "comments",
    "stream",
)


def _reserved_segment_lookahead(segments: tuple[str, ...]) -> str:
    r"""A negative lookahead rejecting any reserved segment at a word boundary.

    The trailing ``(?![\w-])`` makes each alternative only match a *full*
    segment, so ``young-future``, ``sets-lover``, or ``streams-it`` (which
    merely start with a reserved word) stay valid usernames.
    """
    return "|".join(f"{segment}(?![\\w-])" for segment in segments)


_SOUNDCLOUD_URL_RE: re.Pattern[str] = re.compile(
    rf"(?:https?://)?(?:(?:www|m|mobile)\.)?soundcloud\.com/"
    rf"(?!{_reserved_segment_lookahead(_RESERVED_USER_SEGMENTS)})([\w.-]+)/"
    rf"(?!{_reserved_segment_lookahead(_RESERVED_TRACK_SEGMENTS)})"
    rf"([\w-]+)(?:[/?#].*)?"
)

_YOUTUBE_PLAYLIST_URL_RE: re.Pattern[str] = re.compile(
    r"(?:https?://)?(?:[\w-]+\.)*youtube\.com/"
    r"playlist\?(?:[^\s#]*?&)?list=([A-Za-z0-9_-]+)"
)

_SOUNDCLOUD_SET_URL_RE: re.Pattern[str] = re.compile(
    rf"(?:https?://)?(?:(?:www|m|mobile)\.)?soundcloud\.com/"
    rf"(?!{_reserved_segment_lookahead(_RESERVED_USER_SEGMENTS)})([\w.-]+)/"
    rf"sets/([\w-]+)(?:[/?#].*)?"
)

_PLAYLIST_ITEM_RE: re.Pattern[str] = re.compile(r"^(\d+)(?:-(\d+)(?::(\d+))?)?$")


def _extract_soundcloud_urls(text: str) -> list[tuple[int, str, str]]:
    return [
        (
            match.start(),
            match.group(1),
            match.group(2),
        )
        for match in _SOUNDCLOUD_URL_RE.finditer(text)
        if _is_host_at_boundary(text, match.start())
    ]


def extract_urls(text: str) -> list[str]:
    found: list[tuple[int, str]] = [
        *_extract_youtube_urls(text),
        *(
            (start, f"https://soundcloud.com/{user}/{track}")
            for start, user, track in _extract_soundcloud_urls(text)
        ),
    ]
    found.sort(key=lambda item: item[0])
    return list(dict.fromkeys(url for _, url in found))


def extract_playlist_urls(text: str) -> list[str]:
    """Return canonical playlist/set URLs found in ``text``, in text order.

    Only explicit YouTube ``/playlist?list=...`` and SoundCloud ``/sets/...``
    links match. A watch link that carries a ``list=`` parameter is handled by
    :func:`extract_attached_playlist_urls` instead.
    """
    found: list[tuple[int, str]] = [
        (
            match.start(),
            f"https://www.youtube.com/playlist?list={match.group(1)}",
        )
        for match in _YOUTUBE_PLAYLIST_URL_RE.finditer(text)
        if _is_host_at_boundary(text, match.start())
    ]
    found.extend(
        (
            match.start(),
            f"https://soundcloud.com/{match.group(1)}/sets/{match.group(2)}",
        )
        for match in _SOUNDCLOUD_SET_URL_RE.finditer(text)
        if _is_host_at_boundary(text, match.start())
    )
    found.sort(key=lambda item: item[0])
    return list(dict.fromkeys(url for _, url in found))


def extract_attached_playlist_urls(text: str) -> list[str]:
    """Return playlist URLs attached to video links via a ``list=`` parameter.

    Unlike :func:`extract_playlist_urls`, this matches watch/shorts/live/embed
    and ``youtu.be`` links that carry ``&list=...``. Such a link identifies a
    single video *and* a playlist, so callers can ask the user which one they
    want (the video URL itself is still returned by :func:`extract_urls`).
    """
    playlist_ids: list[str] = []
    for regex in (_YOUTUBE_VIDEO_ID_RE, _YOUTUBE_SHORT_URL_RE):
        for match in regex.finditer(text):
            if not _is_host_at_boundary(text, match.start()):
                continue
            token: str = re.split(r"\s", text[match.start() :], maxsplit=1)[0]
            list_match: re.Match[str] | None = re.search(
                r"[?&]list=([A-Za-z0-9_-]+)", token
            )
            if list_match is None:
                continue
            playlist_id: str = list_match.group(1)
            if playlist_id not in playlist_ids:
                playlist_ids.append(playlist_id)
    return [
        f"https://www.youtube.com/playlist?list={playlist_id}"
        for playlist_id in playlist_ids
    ]


def playlist_is_autogenerated(url: str) -> bool:
    """Whether ``url`` points at a YouTube auto-generated mix (``RD...``)."""
    match = _YOUTUBE_PLAYLIST_URL_RE.search(url)
    return bool(match and match.group(1).startswith("RD"))


def validate_playlist_items(spec: str, *, allow_unbounded: bool) -> str:
    """Validate a playlist item selection and return its normalized form.

    Accepts comma-separated numbers and ranges (``3``, ``1-50``, ``1-50:2``,
    ``1,3,5-8``) and, when ``allow_unbounded`` is true, ``all``. Auto-generated
    YouTube mixes are unbounded, so callers pass ``allow_unbounded=false`` to
    reject selections that would never end.
    """
    value = spec.strip()
    if not value:
        raise ValueError("playlist items cannot be empty")
    if value.casefold() == "all":
        if not allow_unbounded:
            raise ValueError(
                "this playlist can be infinite; use a bounded range like 1-50"
            )
        return "1:"
    for raw in value.split(","):
        segment = raw.strip()
        match = _PLAYLIST_ITEM_RE.fullmatch(segment)
        if match is None:
            raise ValueError(
                f"'{segment}' is not a valid item number or range "
                "(use e.g. 1-50, 3,5-8)"
            )
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else None
        step = int(match.group(3)) if match.group(3) is not None else None
        if start < 1:
            raise ValueError("playlist item numbers start at 1")
        if end is not None and end < start:
            raise ValueError(f"range '{segment}' ends before it starts")
        if step is not None and step < 1:
            raise ValueError("a range step must be positive")
    return ",".join(segment.strip() for segment in value.split(","))


def temp_stem(url: str) -> str:
    video_ids: list[str] = _iter_youtube_ids(url)
    if video_ids:
        return f"{YT_STEM_PREFIX}{video_ids[0]}"
    soundcloud_urls: list[tuple[int, str, str]] = _extract_soundcloud_urls(url)
    if soundcloud_urls:
        _, user, track = soundcloud_urls[0]
        return sanitize_filename(f"{SC_STEM_PREFIX}{user}-{track}")
    digest: str = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{TMP_STEM_PREFIX}{digest}"


def get_author(info: dict[str, Any]) -> list[str]:
    raw: list[str] = []

    creators = info.get("artist") or info.get("creator")
    if creators:
        raw.extend(creators if isinstance(creators, list) else [creators])
    elif isinstance(info.get("track"), list):
        raw.extend(info["track"])
    else:
        for key in ("uploader", "channel"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                raw.append(value.strip())
                break

    # A single field often holds several artists
    # ("artist1, artist2, artist1, artist3"), so split every entry on the
    # reserved separators and drop repeats (case/whitespace-insensitive),
    # keeping the first spelling.
    flat: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        flat.extend(split_authors(entry))

    deduped: list[str] = []
    seen: set[str] = set()
    for artist in flat:
        normalized: str = normalize_author(artist)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(artist)
    return deduped


def get_album(info: dict[str, Any]) -> str:
    album = info.get("album")
    return album.strip() if isinstance(album, str) else ""


def _extract_http_status(exc: BaseException) -> int | None:
    match = re.search(r"HTTP Error (\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _contains_keyboard_interrupt(exc: BaseException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, KeyboardInterrupt):
            return True
        cause = cause.__cause__
    return False


def _cookie_opts() -> dict[str, Any]:
    s = settings()
    if s.cookies:
        return {"cookiefile": s.cookies}
    if s.cookies_from_browser:
        return {"cookiesfrombrowser": (s.cookies_from_browser,)}
    return {}


def _yt_dlp_opts(stem: str) -> dict[str, Any]:
    s = settings()
    codec: str = FORMAT_TO_NATIVE_CODEC.get(s.audio_format, "mp3")
    extract_audio_opts: dict[str, Any] = {
        "key": "FFmpegExtractAudio",
        "preferredcodec": codec,
    }
    if s.audio_format == "mp3":
        extract_audio_opts["preferredquality"] = "320"
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(s.temp_folder.joinpath(f"{stem}.%(ext)s")),
        "postprocessors": [
            extract_audio_opts,
            {
                "key": "EmbedThumbnail",
            },
        ],
    }
    opts.update(_cookie_opts())
    return opts


def _search_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    opts.update(_cookie_opts())
    return opts


def _playlist_opts(items: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": False,
    }
    if items is not None:
        opts["playlist_items"] = items
    opts.update(_cookie_opts())
    return opts


def _candidate_url(provider: str, entry: dict[str, Any]) -> str | None:
    if provider == "youtube":
        video_id = entry.get("id")
        if isinstance(video_id, str) and re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
    raw = entry.get("webpage_url") or entry.get("url")
    if not isinstance(raw, str) or not raw.strip():
        return None
    urls = extract_urls(raw)
    if urls:
        return urls[0]
    if raw.startswith(("http://", "https://")):
        return raw
    return None


def _candidate_from_entry(
    provider: str, entry: dict[str, Any]
) -> SourceCandidate | None:
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    url = _candidate_url(provider, entry)
    if url is None:
        return None
    raw_uploader = (
        entry.get("uploader") or entry.get("channel") or entry.get("artist") or ""
    )
    uploader = raw_uploader.strip() if isinstance(raw_uploader, str) else ""
    raw_duration = entry.get("duration")
    duration = (
        float(raw_duration)
        if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool)
        else None
    )
    return SourceCandidate(
        provider=provider,
        title=title.strip(),
        uploader=uploader,
        duration=duration,
        url=url,
    )


def _playlist_provider(url: str) -> str:
    return "soundcloud" if _SOUNDCLOUD_SET_URL_RE.search(url) else "youtube"


def fetch_playlist_preview(url: str) -> PlaylistPreview:
    """Read a playlist's title (and total count when available) cheaply."""
    try:
        with yt_dlp.YoutubeDL(_playlist_opts("1")) as ydl:
            info = cast(dict[str, Any], ydl.extract_info(url, download=False))
    except (yt_dlp.utils.YoutubeDLError, OSError) as exc:
        if _contains_keyboard_interrupt(exc):
            raise KeyboardInterrupt from exc
        raise PlaylistError(f"could not read playlist '{url}': {exc}") from exc
    title_raw = info.get("title") if isinstance(info, dict) else None
    title = (
        title_raw.strip() if isinstance(title_raw, str) and title_raw.strip() else url
    )
    count_raw = info.get("playlist_count") if isinstance(info, dict) else None
    count = (
        int(count_raw)
        if isinstance(count_raw, int) and not isinstance(count_raw, bool)
        else None
    )
    return PlaylistPreview(title=title, count=count)


def expand_playlist(url: str, items: str) -> list[str]:
    """Expand a playlist/set URL into individual video URLs.

    ``items`` uses yt-dlp's item syntax (``1-50``, ``1,3,5-8``, ``1:``) and is
    required so an auto-generated YouTube mix can never be fetched forever.
    """
    provider = _playlist_provider(url)
    try:
        with yt_dlp.YoutubeDL(_playlist_opts(items)) as ydl:
            info = cast(dict[str, Any], ydl.extract_info(url, download=False))
    except (yt_dlp.utils.YoutubeDLError, OSError) as exc:
        if _contains_keyboard_interrupt(exc):
            raise KeyboardInterrupt from exc
        raise PlaylistError(f"could not read playlist '{url}': {exc}") from exc
    entries = info.get("entries") if isinstance(info, dict) else None
    urls: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        candidate = _candidate_url(provider, entry)
        if candidate is not None:
            urls.append(candidate)
    return list(dict.fromkeys(urls))


def search_source(provider: str, query: str, limit: int = 5) -> list[SourceCandidate]:
    """Search ``provider`` for ``query`` and return flat result candidates.

    Uses yt-dlp's ``ytsearch``/``scsearch`` pseudo-URLs with flattened results
    so no media is resolved or downloaded. Raises :class:`SourceSearchError`
    for provider/transport failures; an empty list means no usable results.
    """
    prefix = SOURCE_SEARCH_PREFIXES.get(provider)
    if prefix is None:
        raise SourceSearchError(f"Unsupported source provider: '{provider}'")
    if limit < 1 or not query.strip():
        return []
    search_url = f"{prefix}{limit}:{query.strip()}"
    try:
        with yt_dlp.YoutubeDL(_search_opts()) as ydl:
            info = cast(dict[str, Any], ydl.extract_info(search_url, download=False))
    except (yt_dlp.utils.YoutubeDLError, OSError) as exc:
        if _contains_keyboard_interrupt(exc):
            raise KeyboardInterrupt from exc
        raise SourceSearchError(f"{provider} search failed: {exc}") from exc
    entries = info.get("entries") if isinstance(info, dict) else None
    candidates: list[SourceCandidate] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        candidate = _candidate_from_entry(provider, entry)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def download(url: str, stem: str = "temp") -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(_yt_dlp_opts(stem)) as ydl:
            return cast(dict[str, Any], ydl.extract_info(url, download=True))
    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError) as exc:
        if _contains_keyboard_interrupt(exc):
            raise KeyboardInterrupt from exc
        status_code = _extract_http_status(exc)
        if status_code is not None and 400 <= status_code < 500:
            raise VideoDownloadError(url, status_code) from exc
        raise
