"""Merge artist and album spellings that differ only by case or whitespace."""

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
from smclipy.tag import LibrarySong, list_library_songs
from smclipy.ui import prompt_canonical_spelling


@dataclass(frozen=True)
class VariantGroup:
    """One normalized name plus every exact spelling found for it."""

    field: str
    label: str
    key: str
    variants: tuple[tuple[str, int], ...]

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
    spellings: dict[str, int],
) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            spellings.items(),
            key=lambda item: (-item[1], _ordering_key(item[0])),
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
    """Groups of album names equal after case/whitespace normalization."""
    counts: dict[str, dict[str, int]] = {}
    for song in songs:
        album: str = (song.current.get("album") or "").strip()
        key: str = normalize_author(album)
        if not key:
            continue
        counts.setdefault(key, {}).setdefault(album, 0)
        counts[key][album] += 1
    groups: list[VariantGroup] = []
    for key, spellings in counts.items():
        if len(spellings) < 2:
            continue
        groups.append(VariantGroup("album", "album", key, _variants(spellings)))
    return groups


def sort_groups(groups: list[VariantGroup]) -> list[VariantGroup]:
    """Most impactful groups first (largest affected library)."""
    return sorted(groups, key=lambda group: (-group.song_count, group.field, group.key))


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
    album_map: dict[str, str],
) -> SongChange | None:
    """Return the changes for ``song``, or None when nothing would change."""
    current: dict[str, str] = song.current
    old_artists: list[str] = split_authors(current.get("artists", ""))
    new_artists: list[str] = (
        canonicalize_artists(old_artists, artist_map) if artist_map else old_artists
    )
    old_album: str = current.get("album", "")
    new_album: str = (
        album_map.get(normalize_author(old_album), old_album)
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

    groups: list[VariantGroup] = []
    if want_artists:
        groups.extend(find_artist_groups(songs, db.get_authors()))
    if want_albums:
        groups.extend(find_album_groups(songs))
    groups = sort_groups(groups)
    if not groups:
        print("No duplicate artist or album spellings found.")
        return

    print(f"\nFound {len(groups)} name group(s) with inconsistent spelling:")
    for group in groups:
        print(f"\n{group.label.capitalize()} ({group.song_count} song(s)):")
        for spelling, count in group.variants:
            where: str = "" if count else "  [authors list only]"
            print(f"  {spelling!r}  ({count} song(s)){where}")

    artist_map: dict[str, str] = {}
    album_map: dict[str, str] = {}
    for group in groups:
        chosen: str | None = prompt_canonical_spelling(
            f"{group.label} {group.key!r}",
            list(group.variants),
            group.variants[0][0],
        )
        if chosen is None:
            print(f"Skipped {group.label} {group.key!r}.")
            continue
        if group.field == "artists":
            artist_map[group.key] = chosen
        else:
            album_map[group.key] = chosen

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

    _print_summary(
        dry_run=dry_run,
        merged_groups=len(artist_map) + len(album_map),
        counts=counts,
    )
