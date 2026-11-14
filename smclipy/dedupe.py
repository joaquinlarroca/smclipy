"""Merge artist/album spellings that differ by case, spacing, or a bracket suffix."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import smclipy.db as db
from smclipy.config import Settings, init
from smclipy.helpers import (
    join_authors,
    normalize_author,
    resolve_known_authors,
    sanitize_filename,
    split_authors,
)
from smclipy.metadata import (
    COVER_EXTENSIONS,
    apply_tag_update,
    library_target_path,
    rename_cover_stem,
    rename_song_file,
    same_file,
    scan_library,
    snapshot_song,
)
from smclipy.search import field_values, filter_by_field
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import (
    prompt_canonical_spelling,
    prompt_confirm,
    prompt_multi_selection,
    prompt_search_value,
)

_ALBUM_TRAILING_QUALIFIER = re.compile(r"\s*[\(\[]([^()\[\]]*)[\)\]]\s*$")
_ALBUM_PROTECTED_QUALIFIERS = frozenset(
    {
        "acoustic",
        "act",
        "book",
        "chapter",
        "demo",
        "disc",
        "disk",
        "instrumental",
        "karaoke",
        "live",
        "mix",
        "part",
        "pt",
        "remix",
        "score",
        "session",
        "sessions",
        "side",
        "soundtrack",
        "unplugged",
        "vol",
        "volume",
        "cd",
    }
)
_ALBUM_NUMBERED_QUALIFIER = re.compile(r"(?:cd|disc|disk|vol|volume|part|pt|side)\d+")


def _album_qualifier_is_protected(content: str) -> bool:
    """Whether a bracketed suffix describes a distinct album/edition."""
    for token in re.findall(r"[^\W_]+", content.casefold()):
        if (
            token in _ALBUM_PROTECTED_QUALIFIERS
            or token.isdigit()
            or _ALBUM_NUMBERED_QUALIFIER.fullmatch(token)
        ):
            return True
    return False


def _album_base(album: str) -> str:
    """Strip trailing ``(...)``/``[...]`` qualifiers from an album name."""
    text: str = album.strip()
    while True:
        match: re.Match[str] | None = _ALBUM_TRAILING_QUALIFIER.search(text)
        if match is None:
            return text
        if _album_qualifier_is_protected(match.group(1)):
            return text
        text = text[: match.start()].rstrip()


def _album_key(album: str) -> str:
    return normalize_author(_album_base(album))


def _album_scope(current: dict[str, str]) -> str:
    """Identify the album's owner: album artist, else the first song artist."""
    album_artist: str = (current.get("album_artist") or "").strip()
    if album_artist:
        return normalize_author(album_artist)
    artists: list[str] = split_authors(current.get("artists", ""))
    return normalize_author(artists[0]) if artists else ""


@dataclass(frozen=True)
class VariantGroup:
    """One normalized name plus every exact spelling found for it."""

    field: str
    label: str
    key: str
    variants: tuple[tuple[str, int], ...]
    scope: str = ""

    @property
    def song_count(self) -> int:
        return sum(count for _, count in self.variants)


@dataclass(frozen=True)
class SongChange:
    """The tag, filename, and cover changes planned for one song."""

    song: LibrarySong
    old_artists: tuple[str, ...]
    new_artists: tuple[str, ...]
    old_album: str
    new_album: str
    old_album_artist: str
    new_album_artist: str
    target: Path | None
    cover_old: str | None
    cover_new: str | None


@dataclass
class MergeCounts:
    songs: int = 0
    renamed: int = 0
    covers: int = 0
    skipped_renames: int = 0
    failed: int = 0


def _ordering_key(spelling: str) -> tuple[int, int, str, str]:
    """Prefer spellings made of real words over letter-by-letter spacing.

    For example this ranks "Artist" before "a r t i s t" and
    "Abbey Road" before "AbbeyRoad" when the song counts tie.
    """
    words: list[str] = spelling.split()
    multi_char: int = sum(1 for word in words if len(word) > 1)
    return (-multi_char, len(words), spelling.casefold(), spelling)


def _variants(
    spellings: dict[str, int], *, key: str | None = None
) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            spellings.items(),
            key=lambda item: (
                0 if key is not None and normalize_author(item[0]) == key else 1,
                -item[1],
                _ordering_key(item[0]),
            ),
        )
    )


def find_artist_groups(
    songs: list[LibrarySong], known_authors: list[str]
) -> list[VariantGroup]:
    """Groups of artist names equal after case/whitespace normalization."""
    counts: dict[str, dict[str, int]] = {}
    for song in songs:
        counted: set[str] = set()
        for raw_name in split_authors(song.current.get("artists", "")):
            name: str = raw_name.strip()
            if not name:
                continue
            key: str = normalize_author(name)
            if not key or key in counted:
                # A song may carry two spellings of the same name (for
                # example "Artist" and "artist"); count it once so the
                # displayed group total stays the number of affected songs.
                continue
            counted.add(key)
            counts.setdefault(key, {}).setdefault(name, 0)
            counts[key][name] += 1
    for known in known_authors:
        known_name: str = known.strip()
        key = normalize_author(known_name)
        if not key:
            continue
        # Stored authors count as a candidate spelling even when no current
        # song uses them (they still drive the download autocomplete).
        counts.setdefault(key, {}).setdefault(known_name, 0)
    groups: list[VariantGroup] = []
    for key, spellings in counts.items():
        if len(spellings) < 2:
            continue
        groups.append(VariantGroup("artists", "artist", key, _variants(spellings)))
    return groups


def find_album_groups(songs: list[LibrarySong]) -> list[VariantGroup]:
    """Groups of album names equal after normalization of case and spacing.

    Trailing bracketed qualifiers such as ``(test)`` are ignored, while
    edition markers such as ``(Part 1)``, ``(Disc 2)``, or ``(Live)`` keep
    albums distinct. Albums are scoped by their album artist, falling back to
    the song's first artist, so same-named albums by different artists do not
    merge.
    """
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for song in songs:
        album: str = (song.current.get("album") or "").strip()
        key: str = _album_key(album)
        if not key:
            continue
        group_key: tuple[str, str] = (key, _album_scope(song.current))
        counts.setdefault(group_key, {}).setdefault(album, 0)
        counts[group_key][album] += 1
    groups: list[VariantGroup] = []
    for (key, scope), spellings in counts.items():
        if len(spellings) < 2:
            continue
        groups.append(
            VariantGroup(
                "album",
                "album",
                key,
                _variants(spellings, key=key),
                scope,
            )
        )
    return groups


def sort_groups(groups: list[VariantGroup]) -> list[VariantGroup]:
    """Most impactful groups first (largest affected library)."""
    return sorted(
        groups,
        key=lambda group: (-group.song_count, group.field, group.key, group.scope),
    )


def _skip_key(group: VariantGroup) -> tuple[str, str, str]:
    """The database key identifying a group across runs."""
    return (group.field, group.key, group.scope)


def _skip_fingerprint(group: VariantGroup) -> frozenset[str]:
    """The spellings a skipped group contained, used to detect new variants."""
    return frozenset(spelling for spelling, _ in group.variants)


def canonicalize_artists(artists: list[str], mapping: dict[str, str]) -> list[str]:
    """Apply ``mapping`` to a song's artists and drop duplicate spellings."""
    if not any(normalize_author(artist) in mapping for artist in artists):
        return artists
    mapped: list[str] = [
        mapping.get(normalize_author(artist), artist) for artist in artists
    ]
    return resolve_known_authors(mapped, [])


def canonicalize_album_artist(value: str, mapping: dict[str, str]) -> str:
    """Apply the artist mapping to a (possibly multi-artist) album artist."""
    artists: list[str] = split_authors(value)
    if not artists or not any(normalize_author(a) in mapping for a in artists):
        return value
    return join_authors(canonicalize_artists(artists, mapping))


def plan_song_change(
    song: LibrarySong,
    artist_map: dict[str, str],
    album_map: dict[tuple[str, str], str],
) -> SongChange | None:
    """Return the changes for ``song``, or None when nothing would change."""
    current: dict[str, str] = song.current
    old_artists: list[str] = split_authors(current.get("artists", ""))
    new_artists: list[str] = (
        canonicalize_artists(old_artists, artist_map) if artist_map else old_artists
    )
    old_album: str = current.get("album", "")
    new_album: str = (
        album_map.get((_album_key(old_album), _album_scope(current)), old_album)
        if old_album and album_map
        else old_album
    )
    old_album_artist: str = current.get("album_artist", "")
    new_album_artist: str = (
        canonicalize_album_artist(old_album_artist, artist_map)
        if artist_map and old_album_artist
        else old_album_artist
    )
    if (
        new_artists == old_artists
        and new_album == old_album
        and new_album_artist == old_album_artist
    ):
        return None

    target: Path | None = None
    cover_old: str | None = None
    cover_new: str | None = None
    title: str = (current.get("title") or "").strip()
    first_changed: bool = bool(new_artists) and (
        not old_artists or new_artists[0] != old_artists[0]
    )
    if first_changed and title:
        library_target: Path | None = library_target_path(song.path, title, new_artists)
        if library_target is not None:
            target = song.path.with_name(library_target.name)
            if target == song.path:
                target = None
            else:
                cover_old = sanitize_filename(f"{old_artists[0]}-{title}")
                cover_new = sanitize_filename(f"{new_artists[0]}-{title}")
                if cover_old == cover_new:
                    cover_old = cover_new = None
    return SongChange(
        song=song,
        old_artists=tuple(old_artists),
        new_artists=tuple(new_artists),
        old_album=old_album,
        new_album=new_album,
        old_album_artist=old_album_artist,
        new_album_artist=new_album_artist,
        target=target,
        cover_old=cover_old,
        cover_new=cover_new,
    )


def _cover_files(stem: str, s: Settings) -> list[str]:
    return [
        ext
        for ext in COVER_EXTENSIONS
        if s.covers_folder.joinpath(f"{stem}{ext}").is_file()
    ]


def _describe(change: SongChange, s: Settings) -> list[str]:
    lines: list[str] = []
    if change.new_artists != change.old_artists:
        before: str = ", ".join(change.old_artists) or "(empty)"
        after: str = ", ".join(change.new_artists) or "(empty)"
        lines.append(f"artists: {before} -> {after}")
    if change.new_album != change.old_album:
        lines.append(f"album: {change.old_album or '(empty)'} -> {change.new_album}")
    if change.new_album_artist != change.old_album_artist:
        before_aa: str = change.old_album_artist or "(empty)"
        lines.append(f"album artist: {before_aa} -> {change.new_album_artist}")
    if change.target is not None:
        if change.target.exists() and not same_file(change.song.path, change.target):
            lines.append(
                f"rename skipped: '{change.target.name}' already exists "
                f"(keeping '{change.song.path.name}')"
            )
        else:
            lines.append(f"rename: '{change.song.path.name}' -> '{change.target.name}'")
    if change.cover_old and change.cover_new and _cover_files(change.cover_old, s):
        if _cover_files(change.cover_new, s):
            lines.append(f"cover rename skipped: '{change.cover_new}.*' already exists")
        else:
            lines.append(f"cover: '{change.cover_old}.*' -> '{change.cover_new}.*'")
    return lines


def _event_detail(change: SongChange) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    if change.new_artists != change.old_artists:
        detail["artists"] = {
            "before": ", ".join(change.old_artists),
            "after": ", ".join(change.new_artists),
        }
    if change.new_album != change.old_album:
        detail["album"] = {"before": change.old_album, "after": change.new_album}
    if change.new_album_artist != change.old_album_artist:
        detail["album_artist"] = {
            "before": change.old_album_artist,
            "after": change.new_album_artist,
        }
    return detail


def _would_rename_cover(change: SongChange, s: Settings) -> bool:
    if not change.cover_old or not change.cover_new:
        return False
    return bool(_cover_files(change.cover_old, s)) and not _cover_files(
        change.cover_new, s
    )


def _apply_change(
    change: SongChange,
    s: Settings,
    *,
    dry_run: bool,
    counts: MergeCounts,
) -> None:
    song: LibrarySong = change.song
    print(f"\n{song.path.name}")
    for line in _describe(change, s):
        print(f"  {line}")

    if song.uuid is None:
        print(
            "  Warning: song has no tracking UUID, skipping (run `smclipy update`).",
            file=sys.stderr,
        )
        counts.failed += 1
        return

    if dry_run:
        if change.target is not None:
            if change.target.exists() and not same_file(song.path, change.target):
                counts.skipped_renames += 1
                return
            counts.renamed += 1
        counts.songs += 1
        if _would_rename_cover(change, s) and change.cover_old:
            counts.covers += len(_cover_files(change.cover_old, s))
        return

    if not snapshot_song(song.path, song.uuid, "dedupe"):
        print("  Warning: could not record a backup, skipping.", file=sys.stderr)
        counts.failed += 1
        return

    new_path: Path = song.path
    renamed: bool = False
    if change.target is not None:
        if not rename_song_file(song.path, change.target):
            print(
                "  Warning: could not rename the file; leaving this song "
                "unchanged (resolve the name collision and rerun).",
                file=sys.stderr,
            )
            counts.skipped_renames += 1
            counts.failed += 1
            return
        new_path = change.target
        renamed = True

    artists_changed: bool = change.new_artists != change.old_artists
    album_changed: bool = change.new_album != change.old_album
    album_artist_changed: bool = change.new_album_artist != change.old_album_artist
    if not apply_tag_update(
        new_path,
        artists=list(change.new_artists) if artists_changed else None,
        album=change.new_album if album_changed else None,
        album_artist=change.new_album_artist if album_artist_changed else None,
    ):
        if renamed:
            # Keep the database path in sync with the file on disk: undo the
            # rename rather than leaving a moved file pointing at the old row.
            if rename_song_file(new_path, song.path):
                print(
                    "  Reverted the filename change after the tag write failed.",
                    file=sys.stderr,
                )
            else:
                print(
                    "  Warning: could not restore the original filename; "
                    "run `smclipy update` to resync the database.",
                    file=sys.stderr,
                )
        print("  Warning: could not write tags, skipping.", file=sys.stderr)
        counts.failed += 1
        return

    counts.songs += 1
    if renamed:
        counts.renamed += 1
    if change.cover_old and change.cover_new:
        moved: int = rename_cover_stem(
            s.covers_folder, change.cover_old, change.cover_new
        )
        counts.covers += moved
        leftover: bool = any(
            s.covers_folder.joinpath(f"{change.cover_old}{ext}").is_file()
            for ext in COVER_EXTENSIONS
        )
        if leftover:
            # A destination collision kept some images under the old stem;
            # both stems need the false-positive bookkeeping then.
            if moved:
                db.add_false_positive(change.cover_new)
        else:
            db.rename_false_positive(change.cover_old, change.cover_new)

    old_rel: str = song.path.relative_to(s.music_folder).as_posix()
    new_rel: str = new_path.relative_to(s.music_folder).as_posix()
    with db.transaction() as conn:
        db.update_song_metadata(
            song.uuid,
            artists=join_authors(list(change.new_artists)),
            album=change.new_album,
            album_artist=change.new_album_artist,
            conn=conn,
        )
        if new_rel != old_rel:
            db.set_song_path(song.uuid, new_rel, conn=conn)
            db.log_event(
                song.uuid,
                "renamed",
                {"old_path": old_rel, "new_path": new_rel},
                conn=conn,
            )
        db.log_event(song.uuid, "normalized", _event_detail(change), conn=conn)
    print("  Done.")


def _print_summary(*, dry_run: bool, merged_groups: int, counts: MergeCounts) -> None:
    if dry_run:
        print(
            f"\nDry run complete: {merged_groups} group(s) selected, "
            f"{counts.songs} song(s) would be updated, "
            f"{counts.renamed} file(s) and {counts.covers} cover(s) would be "
            f"renamed, {counts.skipped_renames} name collision(s)."
        )
        return
    print(
        f"\nDedupe complete: {merged_groups} group(s) merged, "
        f"{counts.songs} song(s) updated, {counts.renamed} file(s) renamed, "
        f"{counts.covers} cover(s) renamed, {counts.skipped_renames} filename "
        f"collision(s), {counts.failed} failure(s)."
    )


def _known_author_names(songs: list[LibrarySong]) -> list[str]:
    """Authors from the library plus the download autocomplete list."""
    names: list[str] = []
    seen: set[str] = set()
    for name in [*field_values(songs, "artists"), *db.get_authors()]:
        cleaned: str = name.strip()
        key: str = normalize_author(cleaned)
        if not cleaned or not key or key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    names.sort(key=str.casefold)
    return names


def _album_candidates(selected: list[LibrarySong]) -> list[tuple[str, int]]:
    """Distinct non-empty album spellings among ``selected``, most used first."""
    counts: dict[str, int] = {}
    for song in selected:
        album: str = (song.current.get("album") or "").strip()
        if album:
            counts[album] = counts.get(album, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))


def _album_song_change(song: LibrarySong, new_album: str) -> SongChange | None:
    """A change that only rewrites the album tag (no rename or cover move)."""
    current: dict[str, str] = song.current
    old_album: str = current.get("album", "")
    if old_album == new_album:
        return None
    artists: tuple[str, ...] = tuple(split_authors(current.get("artists", "")))
    album_artist: str = current.get("album_artist", "")
    return SongChange(
        song=song,
        old_artists=artists,
        new_artists=artists,
        old_album=old_album,
        new_album=new_album,
        old_album_artist=album_artist,
        new_album_artist=album_artist,
        target=None,
        cover_old=None,
        cover_new=None,
    )


def _group_albums_by_author(
    songs: list[LibrarySong], s: Settings, *, dry_run: bool
) -> tuple[int, MergeCounts]:
    """Interactively rewrite one author's selected songs to a shared album."""
    authors: list[str] = _known_author_names(songs)
    if not authors:
        print("No authors found in the library.")
        return 0, MergeCounts()

    groups: int = 0
    counts: MergeCounts = MergeCounts()
    while True:
        author: str = prompt_search_value("Author", authors).strip()
        if not author:
            break
        matches: list[LibrarySong] = filter_by_field(songs, "artists", author)
        if not matches:
            print(f"No songs found for author '{author}'.")
            continue

        values: list[tuple[str, str]] = [
            (
                str(index),
                f"{song.current.get('title') or song.path.stem}  "
                f"[{song.current.get('album') or 'no album'}]",
            )
            for index, song in enumerate(matches)
        ]
        selected_keys: list[str] | None = prompt_multi_selection(
            values,
            title=f"Songs by {author}",
            message="Space toggles a song, Enter applies the selection:",
        )
        if not selected_keys:
            print("No songs selected; skipping this author.")
            if not prompt_confirm("Group another album?", default=False):
                break
            continue

        selected: list[LibrarySong] = [matches[int(key)] for key in selected_keys]
        candidates: list[tuple[str, int]] = _album_candidates(selected)
        default: str = candidates[0][0] if candidates else ""
        album: str = prompt_search_value(
            "Album", [spelling for spelling, _ in candidates], default=default
        ).strip()
        if not album:
            print("No album name chosen; skipping this group.")
        else:
            changes: list[SongChange] = []
            for song in selected:
                change: SongChange | None = _album_song_change(song, album)
                if change is not None:
                    changes.append(change)
            if not changes:
                print(f"All selected songs already use the album '{album}'.")
            else:
                if dry_run:
                    print("\nDry run: no files or database rows will be changed.")
                else:
                    print("\nApplying album changes...")
                for change in changes:
                    try:
                        _apply_change(change, s, dry_run=dry_run, counts=counts)
                    except KeyboardInterrupt:
                        print("\nInterrupted, exiting...")
                        raise SystemExit(130) from None
                    except Exception as exc:
                        counts.failed += 1
                        print(
                            f"  Warning: could not process "
                            f"'{change.song.path.name}': {exc!r}",
                            file=sys.stderr,
                        )
                groups += 1

        if not prompt_confirm("Group another album?", default=False):
            break
    return groups, counts


def cmd_dedupe(args: Any) -> None:
    """Find inconsistently spelled artist/album names and merge them."""
    s: Settings = init()
    dry_run: bool = bool(getattr(args, "dry_run", False))
    want_artists: bool = bool(getattr(args, "artists", False))
    want_albums: bool = bool(getattr(args, "albums", False))
    if not want_artists and not want_albums:
        want_artists = True
        want_albums = True

    print("Scanning music files...")
    scan_library()
    songs: list[LibrarySong] = list_library_songs()
    if not songs:
        print("No audio files found in the library.")
        return

    if bool(getattr(args, "group_albums", False)):
        try:
            merged_groups, group_counts = _group_albums_by_author(
                songs, s, dry_run=dry_run
            )
        except KeyboardInterrupt:
            print("\nInterrupted, exiting...")
            raise SystemExit(130) from None
        if not merged_groups:
            print("Nothing to merge.")
            return
        _print_summary(
            dry_run=dry_run, merged_groups=merged_groups, counts=group_counts
        )
        return

    groups: list[VariantGroup] = []
    if want_artists:
        groups.extend(find_artist_groups(songs, db.get_authors()))
    if want_albums:
        groups.extend(find_album_groups(songs))
    groups = sort_groups(groups)
    if not groups:
        print("No duplicate artist or album spellings found.")
        return

    reset_skipped: bool = bool(getattr(args, "reset_skipped", False))
    skips: dict[tuple[str, str, str], frozenset[str]]
    if reset_skipped:
        if dry_run:
            print(
                "Dry run: --reset-skipped would forget remembered skips; "
                "run without --dry-run to clear them."
            )
            skips = {}
        else:
            cleared: int = db.clear_dedupe_skips()
            print(f"Forgot {cleared} previously skipped group(s).")
            skips = {}
    else:
        skips = db.get_dedupe_skips()

    visible: list[VariantGroup] = []
    hidden_count: int = 0
    for group in groups:
        if skips.get(_skip_key(group)) == _skip_fingerprint(group):
            hidden_count += 1
        else:
            visible.append(group)
    groups = visible
    if not groups:
        print(
            f"\nAll {hidden_count} name group(s) were skipped on previous runs. "
            "Run with --reset-skipped to review them."
        )
        return

    print(f"\nFound {len(groups)} name group(s) with inconsistent spelling:")
    for group in groups:
        heading: str = group.label.capitalize()
        if group.field == "album" and group.scope:
            heading += f" by {group.scope!r}"
        print(f"\n{heading} ({group.song_count} song(s)):")
        for spelling, count in group.variants:
            where: str = "" if count else "  [authors list only]"
            print(f"  {spelling!r}  ({count} song(s)){where}")
    if hidden_count:
        print(
            f"\n{hidden_count} previously skipped group(s) hidden "
            "(use --reset-skipped to review)."
        )

    artist_map: dict[str, str] = {}
    album_map: dict[tuple[str, str], str] = {}
    for group in groups:
        label: str = f"{group.label} {group.key!r}"
        if group.field == "album" and group.scope:
            label += f" by {group.scope!r}"
        chosen: str | None = prompt_canonical_spelling(
            label,
            list(group.variants),
            group.variants[0][0],
        )
        if chosen is None:
            if dry_run:
                print(f"Skipped {label} (dry run: not remembered).")
            else:
                db.record_dedupe_skip(
                    group.field,
                    group.key,
                    group.scope,
                    [spelling for spelling, _ in group.variants],
                )
                print(
                    f"Skipped {label} (remembered; use --reset-skipped to "
                    "review again)."
                )
            continue
        if group.field == "artists":
            artist_map[group.key] = chosen
        else:
            album_map[(group.key, group.scope)] = chosen

    if not artist_map and not album_map:
        print("Nothing to merge.")
        return

    changes: list[SongChange] = []
    for song in songs:
        change: SongChange | None = plan_song_change(song, artist_map, album_map)
        if change is not None:
            changes.append(change)

    counts: MergeCounts = MergeCounts()
    if dry_run:
        print("\nDry run: no files or database rows will be changed.")
    else:
        print("\nApplying changes...")
    for change in changes:
        # One bad file (unreadable, unwritable, unexpected mutagen error) must
        # not abort the run and leave the library half-merged.
        try:
            _apply_change(change, s, dry_run=dry_run, counts=counts)
        except KeyboardInterrupt:
            print("\nInterrupted, exiting...")
            raise SystemExit(130) from None
        except Exception as exc:
            counts.failed += 1
            print(
                f"  Warning: could not process '{change.song.path.name}': {exc!r}",
                file=sys.stderr,
            )

    if not dry_run:
        with db.transaction() as conn:
            for key, canonical in artist_map.items():
                db.rename_author(key, canonical, conn=conn)
            for group in groups:
                if group.field == "artists":
                    merged: bool = group.key in artist_map
                else:
                    merged = (group.key, group.scope) in album_map
                if merged:
                    # A remembered skip is obsolete once the group is merged.
                    db.remove_dedupe_skip(
                        group.field, group.key, group.scope, conn=conn
                    )

    _print_summary(
        dry_run=dry_run,
        merged_groups=len(artist_map) + len(album_map),
        counts=counts,
    )
