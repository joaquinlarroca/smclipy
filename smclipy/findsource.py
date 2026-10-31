"""Find and record download sources for library songs."""

import json
import re
import sys
import unicodedata
from contextlib import nullcontext, redirect_stdout
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import smclipy.db as db
from smclipy.config import init, settings
from smclipy.downloader import (
    SourceCandidate,
    SourceSearchError,
    matches_duration,
    search_source,
)
from smclipy.helpers import join_authors, split_authors
from smclipy.metadata import read_song_duration, scan_library
from smclipy.playlist_file import (
    PlaylistDocument,
    PlaylistEntrySong,
    PlaylistFileError,
    PlaylistWriteError,
    build_entry_songs,
    load_playlist_document,
    set_entry_source_url,
    write_playlist_document,
)
from smclipy.search import apply_filters
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import format_duration, prompt_song_selection, prompt_source_selection

_SKIPPABLE_SOURCE_STATUSES = frozenset(
    {
        db.SOURCE_STATUS_SKIPPED,
        db.SOURCE_STATUS_NOT_FOUND,
        db.SOURCE_STATUS_REJECTED,
    }
)


@dataclass
class SourceTarget:
    """The metadata and tracking state needed to search for one source."""

    title: str
    artists: list[str]
    display_name: str
    duration: float | None
    uuid: str | None
    source_url: str


@dataclass
class SourceSearchResult:
    """The outcome of searching and picking a candidate for one target."""

    outcome: str
    candidate: SourceCandidate | None = None
    all_failed: bool = False
    rejected_count: int = 0
    error: str | None = None


_PREVIOUS_SKIP_REASONS: dict[str, str] = {
    db.SOURCE_STATUS_SKIPPED: "you skipped it on a previous run",
    db.SOURCE_STATUS_NOT_FOUND: "no source was found on a previous run",
    db.SOURCE_STATUS_REJECTED: (
        "no result matched the artist or title on a previous run"
    ),
}


def _previous_skip_reason(status: str | None) -> str:
    return _PREVIOUS_SKIP_REASONS.get(status or "", "it was skipped on a previous run")


_DASH_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_ARTIST_SEPARATOR = re.compile(r"\s*(?:&|,|/|\+)\s*|\s+[xX]\s+")
_FEATURED_ARTIST = re.compile(
    r"(?:\bfeaturing\b|\bfeat\.?|\bft\.?|,\s*with)\s*([^()\[\]|–—-]+)",
    re.IGNORECASE,
)
_ARTIST_PREFIX_MAX_CHARS = 50
_ARTIST_PREFIX_MAX_TOKENS = 8
_TITLE_TOKEN_RATIO = 0.85


def _search_query(target: SourceTarget) -> str:
    """Build a provider search query from the target's title and artists."""
    return " ".join(part for part in (*target.artists, target.title) if part).strip()


def _target_from_library_song(
    song: LibrarySong, *, source_url: str | None = None
) -> SourceTarget:
    title: str = (song.current.get("title") or song.path.stem).strip()
    display_name: str = str(getattr(song, "display_name", "") or title)
    uuid_value: str | None = getattr(song, "uuid", None)
    duration: float | None = (
        read_song_duration(song.path) if song.path.is_file() else None
    )
    return SourceTarget(
        title=title,
        artists=split_authors(song.current.get("artists", "")),
        display_name=display_name,
        duration=duration,
        uuid=uuid_value,
        source_url=source_url or "",
    )


def _target_from_playlist_song(song: PlaylistEntrySong) -> SourceTarget:
    duration: float | None = (
        read_song_duration(song.path) if song.path.is_file() else None
    )
    return SourceTarget(
        title=song.current.get("title", "").strip() or song.path.stem,
        artists=split_authors(song.current.get("artists", "")),
        display_name=song.display_name,
        duration=duration,
        uuid=song.uuid,
        source_url=song.source_url,
    )


def _as_target(target: SourceTarget | LibrarySong) -> SourceTarget:
    if isinstance(target, SourceTarget):
        return target
    return _target_from_library_song(target)


_AUTHOR_NOISE = ("topic", "vevo", "official", "music")


def _clean_author(name: str) -> str:
    text: str = unicodedata.normalize("NFKD", name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[\W_]+", "", text.casefold())
    changed: bool = True
    while changed:
        changed = False
        for noise in _AUTHOR_NOISE:
            if text.endswith(noise) and len(text) > len(noise):
                text = text[: -len(noise)]
                changed = True
    return text


def _name_score(target_name: str, other_name: str) -> int:
    """Compare two artist names: 2 exact, 1 similar, 0 unrelated."""
    target: str = _clean_author(target_name)
    other: str = _clean_author(other_name)
    if not target or not other:
        return 0
    if target == other:
        return 2
    if len(target) >= 3 and len(other) >= 3 and (target in other or other in target):
        return 1
    if (
        len(target) >= 4
        and len(other) >= 4
        and SequenceMatcher(None, target, other).ratio() >= 0.8
    ):
        return 1
    return 0


def _clean_title(text: str) -> list[str]:
    """Case/accent-insensitive title tokens, keeping non-Latin scripts."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.findall(r"[^\W_]+", text.casefold())


def _title_token_present(token: str, candidates: list[str]) -> bool:
    for other in candidates:
        if token == other:
            return True
        if (
            len(token) >= 4
            and len(other) >= 4
            and SequenceMatcher(None, token, other).ratio() >= _TITLE_TOKEN_RATIO
        ):
            return True
    return False


def _title_score(song_title: str, candidate_title: str) -> int:
    """Compare titles: 2 exact phrase, 1 all words present, 0 unrelated."""
    target: list[str] = _clean_title(song_title)
    candidate: list[str] = _clean_title(candidate_title)
    if not target or not candidate:
        return 0
    span: int = len(target)
    if any(
        candidate[index : index + span] == target
        for index in range(len(candidate) - span + 1)
    ):
        return 2
    if all(_title_token_present(token, candidate) for token in target):
        return 1
    if (
        SequenceMatcher(None, " ".join(target), " ".join(candidate)).ratio()
        >= _TITLE_TOKEN_RATIO
    ):
        return 1
    return 0


def _looks_like_artist_prefix(prefix: str) -> bool:
    """Whether a ``Artist - Title`` prefix is name-like rather than a sentence."""
    text: str = prefix.strip()
    if not text or len(text) > _ARTIST_PREFIX_MAX_CHARS:
        return False
    if any(char in text for char in ":?!"):
        return False
    tokens: list[str] = _clean_title(text)
    return 0 < len(tokens) <= _ARTIST_PREFIX_MAX_TOKENS


def _split_artist_names(text: str) -> list[str]:
    return [part.strip() for part in _ARTIST_SEPARATOR.split(text) if part.strip()]


def _title_artist_names(title: str) -> list[str]:
    """Artist names implied by ``Artist - Title feat. Artist2`` style titles."""
    names: list[str] = []
    parts: list[str] = _DASH_SEPARATOR.split(title, maxsplit=1)
    if len(parts) == 2:
        prefix: str = parts[0].strip()
        if _looks_like_artist_prefix(prefix):
            names.extend(_split_artist_names(prefix))
    for match in _FEATURED_ARTIST.finditer(title):
        names.extend(_split_artist_names(match.group(1)))
    return names


def _artist_score(
    target: SourceTarget | LibrarySong, candidate: SourceCandidate
) -> int:
    """Best artist match across the target's artists and all candidate names.

    Artist names come from the uploader, an ``Artist - Title`` prefix, and
    ``feat.``/``ft.``/``featuring``/``with`` credits in the candidate title.
    """
    source: SourceTarget = _as_target(target)
    candidate_names: list[str] = _title_artist_names(candidate.title)
    if candidate.uploader:
        candidate_names.insert(0, candidate.uploader)
    best: int = 0
    for artist in source.artists:
        for name in candidate_names:
            best = max(best, _name_score(artist, name))
            if best == 2:
                return 2
    return best


def _is_plausible(
    target: SourceTarget | LibrarySong, candidate: SourceCandidate
) -> bool:
    """Whether a candidate names the target's artist or repeats its title."""
    source: SourceTarget = _as_target(target)
    if _artist_score(source, candidate) > 0:
        return True
    if _title_score(source.title, candidate.title) > 0:
        return True
    if _clean_title(source.title):
        return False
    return not any(_clean_title(artist) for artist in source.artists)


def _rank_candidates(
    target: SourceTarget | LibrarySong,
    candidates: list[SourceCandidate],
    song_duration: float | None,
) -> list[SourceCandidate]:
    """Order candidates by duration match, artist similarity, provider.

    Duration wins over artist similarity (a length match is the strongest
    signal that a result is the same recording), provider preference breaks
    ties, and the provider's own relevance order is the final tie-breaker.
    """
    source: SourceTarget = _as_target(target)
    provider_rank: dict[str, int] = {
        provider: index for index, provider in enumerate(settings().source_preferences)
    }
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            not matches_duration(item[1], song_duration),
            -_artist_score(source, item[1]),
            provider_rank.get(item[1].provider, len(provider_rank)),
            item[0],
        ),
    )
    return [candidate for _index, candidate in ranked]


def _find_candidates(
    query: str, *, all_providers: bool = False
) -> tuple[list[SourceCandidate], bool, bool]:
    """Search providers in configured order and return usable candidates.

    By default the first provider that returns results wins, with duplicate
    URLs removed. With ``all_providers`` every configured provider is queried
    and the combined, URL-deduplicated list is returned so the caller can rank
    across sources.
    Returns the candidates, whether any provider failed, and whether every
    configured provider failed. A provider that returns no results is not an
    error; the next provider is tried. ``unavailable`` means the search was
    incomplete, so retrying the song later is worthwhile even when another
    provider answered with an empty result.
    """
    failed = 0
    answered = 0
    collected: list[SourceCandidate] = []
    seen_urls: set[str] = set()
    for provider in settings().source_preferences:
        try:
            candidates: list[SourceCandidate] = search_source(provider, query)
        except SourceSearchError as exc:
            print(f"Warning: {exc}", file=sys.stderr)
            failed += 1
            continue
        answered += 1
        unique: list[SourceCandidate] = []
        for candidate in candidates:
            key: str = candidate.url.strip()
            if key in seen_urls:
                continue
            seen_urls.add(key)
            unique.append(candidate)
        if unique and not all_providers:
            return unique, failed > 0, False
        collected.extend(unique)
    if collected:
        return collected, failed > 0, False
    unavailable = failed > 0
    all_failed = unavailable and answered == 0
    return [], unavailable, all_failed


def _print_song_header(
    song: SourceTarget,
    *,
    position: int | None,
    total: int | None,
    mode: str,
    duration: float | None,
) -> None:
    """Announce the song being processed, with its length when available."""
    progress: str = f"[{position}/{total}] " if position is not None and total else ""
    duration_hint: str = (
        f" ({format_duration(duration)})"
        if duration is not None and duration > 0
        else ""
    )
    if mode == "interactive":
        print(f"\n{progress}=== {song.display_name}{duration_hint} ===")
    else:
        print(f"{progress}=== {song.display_name}{duration_hint} ===")


def _append_report(
    report: list[dict[str, Any]] | None,
    target: SourceTarget,
    result: SourceSearchResult,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    if report is None:
        return
    candidate: SourceCandidate | None = result.candidate
    entry: dict[str, Any] = {
        "outcome": result.outcome,
        "display_name": target.display_name,
        "title": target.title,
        "artists": join_authors(target.artists),
        "provider": candidate.provider if candidate is not None else None,
        "url": (
            candidate.url
            if candidate is not None
            else target.source_url
            if result.outcome == "already_has_source"
            else None
        ),
    }
    if result.outcome == "unavailable":
        entry["all_providers_failed"] = result.all_failed
    if result.rejected_count:
        entry["rejected_count"] = result.rejected_count
    if result.error is not None:
        entry["error"] = result.error
    if extra:
        entry.update(extra)
    report.append(entry)


def _record_result_status(uuid: str | None, result: SourceSearchResult) -> None:
    if uuid is None:
        return
    statuses: dict[str, str] = {
        "unavailable": db.SOURCE_STATUS_UNAVAILABLE,
        "not_found": db.SOURCE_STATUS_NOT_FOUND,
        "rejected": db.SOURCE_STATUS_REJECTED,
        "skipped": db.SOURCE_STATUS_SKIPPED,
        "unchanged": db.SOURCE_STATUS_SAVED,
    }
    status: str | None = statuses.get(result.outcome)
    if status is not None:
        db.record_source_status(uuid, status)


def _search_target(
    target: SourceTarget,
    *,
    mode: str,
    force: bool = False,
    all_providers: bool = False,
) -> SourceSearchResult:
    """Search, rank, and pick a candidate for ``target`` without saving it."""
    if target.source_url and not force:
        print("Skipped: already has a source URL (use --force to search again).")
        return SourceSearchResult("already_has_source")

    if mode == "interactive":
        print()

    query: str = _search_query(target)
    print(f"Searching for: {query}")
    candidates, unavailable, all_failed = _find_candidates(
        query, all_providers=all_providers
    )
    if not candidates:
        if unavailable:
            if all_failed:
                print(
                    "Skipped: every configured source provider failed "
                    "(run the command again to retry)."
                )
            else:
                print(
                    "Skipped: no source candidates found and at least one "
                    "provider failed (run the command again to retry)."
                )
            return SourceSearchResult("unavailable", all_failed=all_failed)
        print("Skipped: no source candidates found.")
        return SourceSearchResult("not_found")

    ranked = _rank_candidates(target, candidates, target.duration)
    matches: list[bool] = [_is_plausible(target, candidate) for candidate in ranked]
    if mode == "auto":
        candidates = [
            candidate
            for candidate, matched in zip(ranked, matches, strict=True)
            if matched
        ]
        if not candidates:
            if unavailable:
                print(
                    "Skipped: no candidate matched the artist or title and at "
                    "least one provider failed (run the command again to retry)."
                )
                return SourceSearchResult(
                    "unavailable",
                    all_failed=all_failed,
                    rejected_count=len(ranked),
                )
            print("Skipped: no candidate matched the artist or title.")
            return SourceSearchResult("rejected", rejected_count=len(ranked))
        selected: int | None = 0
    else:
        ordered = sorted(
            zip(ranked, matches, strict=True), key=lambda item: not item[1]
        )
        candidates = [candidate for candidate, _matched in ordered]
        matches = [matched for _candidate, matched in ordered]
        selected = prompt_source_selection(
            candidates, song_duration=target.duration, matches=matches
        )
    if selected is None:
        print("Skipped: no source selected.")
        return SourceSearchResult("skipped")
    candidate: SourceCandidate = candidates[selected]
    if target.source_url and candidate.url.strip() == target.source_url.strip():
        return SourceSearchResult("unchanged", candidate=candidate)
    return SourceSearchResult("saved", candidate=candidate)


def process_song(
    song: LibrarySong,
    *,
    mode: str = "interactive",
    force: bool = False,
    has_source: bool = False,
    existing_source: str | None = None,
    position: int | None = None,
    total: int | None = None,
    report: list[dict[str, Any]] | None = None,
    all_providers: bool = False,
) -> bool:
    """Search for and save a source URL for ``song``; return whether it saved.

    ``mode`` is ``"auto"`` (rank the results and take the best without
    prompting) or ``"interactive"`` (show the ranked candidates and let the
    user pick or skip). Candidates are ordered by matching duration, then
    artist similarity, then provider preference, and only candidates that
    mention the song's artist or title are considered plausible. Auto mode
    drops implausible candidates and skips the song when none remain;
    interactive mode keeps them visible but marks them and defaults to Skip
    when nothing matches. Songs that already have a recorded source are
    skipped unless ``force`` is set; ``existing_source`` is that URL (used for
    reporting and to avoid rewriting an unchanged source). With
    ``all_providers`` every configured provider is searched instead of
    stopping at the first one that returns results.
    """
    target: SourceTarget = _target_from_library_song(song, source_url=existing_source)
    _print_song_header(
        target,
        position=position,
        total=total,
        mode=mode,
        duration=target.duration,
    )

    if has_source and not force:
        print("Skipped: already has a source URL (use --force to search again).")
        _append_report(
            report,
            target,
            SourceSearchResult("already_has_source"),
        )
        return False
    if song.uuid is None:
        print("Skipped: no tracking UUID (cannot record a source).")
        _append_report(
            report,
            target,
            SourceSearchResult("error", error="missing song UUID"),
        )
        return False

    result: SourceSearchResult = _search_target(
        target, mode=mode, force=force, all_providers=all_providers
    )
    if result.outcome == "saved":
        candidate: SourceCandidate = result.candidate  # type: ignore[assignment]
        with db.transaction() as conn:
            db.update_song_metadata(song.uuid, source_url=candidate.url, conn=conn)
            db.record_source_status(song.uuid, db.SOURCE_STATUS_SAVED, conn=conn)
            db.log_event(
                song.uuid,
                "source",
                {
                    "provider": candidate.provider,
                    "url": candidate.url,
                    "tool": "findsource",
                },
                conn=conn,
            )
        print(f"Saved source for '{song.path.name}': {candidate.url}")
    elif result.outcome == "unchanged":
        candidate = result.candidate  # type: ignore[assignment]
        db.record_source_status(song.uuid, db.SOURCE_STATUS_SAVED)
        print(f"Source already recorded for '{song.path.name}': {candidate.url}")
    else:
        _record_result_status(song.uuid, result)
    _append_report(report, target, result)
    return result.outcome == "saved"


def _record_error_status(song: LibrarySong) -> None:
    """Keep a crashed search retryable unless the song already has a source."""
    if song.uuid is None:
        return
    try:
        row = db.get_song_by_uuid(song.uuid)
        if row is not None and str(row["source_url"] or "").strip():
            return
        db.record_source_status(song.uuid, db.SOURCE_STATUS_ERROR)
    except Exception as exc:
        print(
            f"Warning: could not record the failed source search: {exc!r}",
            file=sys.stderr,
        )


def _run_findsource(
    args: object, mode: str, report: list[dict[str, Any]] | None
) -> tuple[int, int]:
    """Run the source-search workflow; returns ``(found, skipped)``.

    ``skipped`` counts songs that already had a source URL, plus songs
    previously skipped, not found, or rejected when ``--skip-skipped`` is set.
    While ``report`` is collecting, human-facing output goes to stderr so the
    caller can emit a machine-readable JSON report on stdout.
    """
    with redirect_stdout(sys.stderr) if report is not None else nullcontext():
        if mode == "auto":
            print(
                "Auto mode: the best ranked search result will be saved for "
                "each selected song with no further prompts."
            )
        init()
        print("Scanning music files...")
        scan_library()
        songs: list[LibrarySong] = list_library_songs()
        if not songs:
            print("No audio files found in the library to search.")
            return 0, 0
        songs, filtered = apply_filters(songs, args)
        if not songs:
            return 0, 0
        if filtered:
            print(f"Found {len(songs)} matching audio file(s).")
        else:
            print(f"Found {len(songs)} audio file(s) in the library.")
        print("\nMatching songs:" if filtered else "\nSongs in your library:")
        for index, song in enumerate(songs, start=1):
            print(f"{index:3}. {song.display_name}")

        select_all: bool = bool(getattr(args, "all", False))
        if select_all:
            selected: list[int] = list(range(len(songs)))
        else:
            selected = prompt_song_selection(len(songs))

        sourced_urls: dict[str, str] = {}
        source_statuses: dict[str, str] = {}
        for row in db.list_songs():
            url = str(row["source_url"] or "").strip()
            if url:
                sourced_urls[str(row["uuid"])] = url
            source_statuses[str(row["uuid"])] = str(row["source_status"] or "")
        force: bool = bool(getattr(args, "force", False))
        all_providers: bool = bool(getattr(args, "all_providers", False))
        skip_skipped: bool = bool(getattr(args, "skip_skipped", False))
        found = 0
        skipped = 0
        for index, position in enumerate(selected, start=1):
            song = songs[position]
            existing_source: str | None = (
                sourced_urls.get(song.uuid) if song.uuid is not None else None
            )
            has_source: bool = existing_source is not None
            previous_status: str | None = (
                source_statuses.get(song.uuid) if song.uuid is not None else None
            )
            if (
                skip_skipped
                and not has_source
                and previous_status in _SKIPPABLE_SOURCE_STATUSES
            ):
                target = _target_from_library_song(song)
                _print_song_header(
                    target,
                    position=index,
                    total=len(selected),
                    mode=mode,
                    duration=target.duration,
                )
                print(
                    f"Skipped: {_previous_skip_reason(previous_status)} "
                    "(run without --skip-skipped to retry)."
                )
                skipped += 1
                if report is not None:
                    report.append(
                        {
                            "outcome": "already_skipped",
                            "display_name": song.display_name,
                            "title": song.current.get("title") or song.path.stem,
                            "artists": song.current.get("artists", ""),
                            "provider": None,
                            "url": None,
                            "source_status": previous_status,
                        }
                    )
                continue
            try:
                if process_song(
                    song,
                    mode=mode,
                    force=force,
                    has_source=has_source,
                    existing_source=existing_source,
                    position=index,
                    total=len(selected),
                    report=report,
                    all_providers=all_providers,
                ):
                    found += 1
                elif has_source and not force:
                    skipped += 1
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                raise SystemExit(130) from None
            except Exception as exc:
                print(f"Warning: could not process '{song.display_name}': {exc!r}")
                _record_error_status(song)
                if report is not None:
                    report.append(
                        {
                            "outcome": "error",
                            "display_name": song.display_name,
                            "title": song.current.get("title") or song.path.stem,
                            "artists": song.current.get("artists", ""),
                            "provider": None,
                            "url": None,
                            "error": repr(exc),
                        }
                    )
    return found, skipped


def _write_playlist(document: PlaylistDocument) -> None:
    try:
        write_playlist_document(document)
    except PlaylistWriteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _playlist_db_source(
    entry: PlaylistEntrySong, url: str, provider: str | None = None
) -> None:
    """Record a playlist entry's URL in the DB when it is downloaded/tracked."""
    if not entry.downloaded or entry.uuid is None:
        return
    detail: dict[str, Any] = {
        "url": url,
        "tool": "findsource",
        "origin": "playlist",
    }
    if provider is not None:
        detail["provider"] = provider
    try:
        with db.transaction() as conn:
            db.update_song_metadata(entry.uuid, source_url=url, conn=conn)
            db.record_source_status(entry.uuid, db.SOURCE_STATUS_SAVED, conn=conn)
            db.log_event(entry.uuid, "source", detail, conn=conn)
    except Exception as exc:
        print(
            f"Warning: saved the URL in the playlist but could not update the "
            f"tracking database for '{entry.display_name}': {exc!r}",
            file=sys.stderr,
        )


def _sync_playlist_entries(
    document: PlaylistDocument, entries: list[PlaylistEntrySong]
) -> None:
    """Reconcile URLs between playlist entries and downloaded DB rows."""
    changed: bool = False
    for entry in entries:
        if not entry.downloaded or entry.uuid is None or entry.row is None:
            continue
        db_url: str = str(entry.row["source_url"] or "").strip()
        if not entry.source_url and db_url:
            if set_entry_source_url(entry.entry, db_url):
                changed = True
            entry.source_url = db_url
            entry.current["source_url"] = db_url
        elif entry.source_url and not db_url:
            _playlist_db_source(entry, entry.source_url)
    if changed:
        _write_playlist(document)


def _record_playlist_error(entry: PlaylistEntrySong) -> None:
    if not entry.downloaded or entry.uuid is None:
        return
    try:
        row = db.get_song_by_uuid(entry.uuid)
        if row is not None and str(row["source_url"] or "").strip():
            return
        db.record_source_status(entry.uuid, db.SOURCE_STATUS_ERROR)
    except Exception as exc:
        print(
            f"Warning: could not record the failed source search: {exc!r}",
            file=sys.stderr,
        )


def _run_findsource_file(
    args: object, mode: str, report: list[dict[str, Any]] | None
) -> tuple[int, int]:
    """Search source URLs for the entries of a v2 playlist JSON file."""
    with redirect_stdout(sys.stderr) if report is not None else nullcontext():
        if mode == "auto":
            print(
                "Auto mode: the best ranked search result will be saved for "
                "each selected playlist entry with no further prompts."
            )
        init()
        path: Path = Path(str(getattr(args, "file", None))).expanduser()
        try:
            document = load_playlist_document(path)
        except PlaylistFileError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print("Scanning music files...")
        scan_library()
        try:
            songs: list[PlaylistEntrySong] = build_entry_songs(document)
        except PlaylistFileError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        if not songs:
            print(f"No entries found in '{path.name}'.")
            return 0, 0

        _sync_playlist_entries(document, songs)
        songs, filtered = apply_filters(songs, args)
        if not songs:
            return 0, 0

        if filtered:
            print(f"Found {len(songs)} matching playlist entr(y/ies).")
        else:
            print(f"Found {len(songs)} playlist entr(y/ies).")
        label: str = document.name or path.stem
        print(f"\nPlaylist '{label}' entries:")
        for index, song in enumerate(songs, start=1):
            markers: list[str] = []
            if song.source_url:
                markers.append("has source")
            if not song.downloaded:
                markers.append("not in library")
            suffix: str = f" [{' / '.join(markers)}]" if markers else ""
            print(f"{index:3}. {song.display_name}{suffix}")

        select_all: bool = bool(getattr(args, "all", False))
        selected: list[int] = (
            list(range(len(songs))) if select_all else prompt_song_selection(len(songs))
        )
        if not selected:
            return 0, 0

        force: bool = bool(getattr(args, "force", False))
        all_providers: bool = bool(getattr(args, "all_providers", False))
        skip_skipped: bool = bool(getattr(args, "skip_skipped", False))
        found = 0
        skipped = 0
        for position, index in enumerate(selected, start=1):
            entry: PlaylistEntrySong = songs[index]
            target: SourceTarget = _target_from_playlist_song(entry)
            previous_status: str = (
                str(entry.row["source_status"] or "") if entry.row is not None else ""
            )
            extra: dict[str, Any] = {"entry": entry.index}
            if (
                skip_skipped
                and not target.source_url
                and previous_status in _SKIPPABLE_SOURCE_STATUSES
            ):
                _print_song_header(
                    target,
                    position=position,
                    total=len(selected),
                    mode=mode,
                    duration=target.duration,
                )
                print(
                    f"Skipped: {_previous_skip_reason(previous_status)} "
                    "(run without --skip-skipped to retry)."
                )
                skipped += 1
                if report is not None:
                    report.append(
                        {
                            "outcome": "already_skipped",
                            "display_name": target.display_name,
                            "title": target.title,
                            "artists": join_authors(target.artists),
                            "provider": None,
                            "url": None,
                            "source_status": previous_status,
                            **extra,
                        }
                    )
                continue
            _print_song_header(
                target,
                position=position,
                total=len(selected),
                mode=mode,
                duration=target.duration,
            )
            if target.source_url and not force:
                print(
                    "Skipped: already has a source URL (use --force to search again)."
                )
                _append_report(
                    report,
                    target,
                    SourceSearchResult("already_has_source"),
                    extra=extra,
                )
                skipped += 1
                continue
            try:
                result: SourceSearchResult = _search_target(
                    target, mode=mode, force=force, all_providers=all_providers
                )
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                raise SystemExit(130) from None
            except Exception as exc:
                print(f"Warning: could not process '{entry.display_name}': {exc!r}")
                _record_playlist_error(entry)
                _append_report(
                    report,
                    target,
                    SourceSearchResult("error", error=repr(exc)),
                    extra=extra,
                )
                continue

            if result.outcome == "saved":
                candidate: SourceCandidate = result.candidate  # type: ignore[assignment]
                if set_entry_source_url(entry.entry, candidate.url):
                    _write_playlist(document)
                entry.source_url = candidate.url
                entry.current["source_url"] = candidate.url
                _playlist_db_source(entry, candidate.url, candidate.provider)
                print(f"Saved source for '{entry.display_name}': {candidate.url}")
                found += 1
            elif result.outcome == "unchanged":
                candidate = result.candidate  # type: ignore[assignment]
                _playlist_db_source(entry, candidate.url, candidate.provider)
                print(
                    f"Source already recorded for '{entry.display_name}': "
                    f"{candidate.url}"
                )
            elif entry.downloaded and entry.uuid is not None:
                _record_result_status(entry.uuid, result)
            _append_report(report, target, result, extra=extra)
    return found, skipped


def cmd_findsource(args: object) -> None:
    as_json: bool = bool(getattr(args, "json", False))
    auto: bool = bool(getattr(args, "auto", False))
    mode: str = "auto" if auto else "interactive"

    report: list[dict[str, Any]] | None = [] if as_json else None
    found: int
    skipped: int
    if getattr(args, "file", None):
        found, skipped = _run_findsource_file(args, mode, report)
    else:
        found, skipped = _run_findsource(args, mode, report)

    if as_json:
        print(
            json.dumps(
                {
                    "found": found,
                    "skipped": skipped,
                    "songs": report,
                },
                indent=2,
            )
        )
        if any(entry.get("outcome") == "error" for entry in report or []):
            raise SystemExit(1)
    else:
        message: str = f"\nDone. Saved sources for {found} song(s)."
        if skipped:
            if bool(getattr(args, "skip_skipped", False)):
                message += (
                    f" Skipped {skipped} song(s) "
                    "(already sourced, skipped, no match found, or rejected "
                    "before)."
                )
            else:
                message += f" Skipped {skipped} already-sourced song(s)."
        print(message)
