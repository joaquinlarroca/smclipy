"""One-off recovery: restore original title/artists stripped by a junk tag run.

Some library songs were tagged with placeholder metadata ("Renamed" / "Artist" /
"Album" / "1980") by a mock-driven `tag` run (DB rows carry fake MusicBrainz ids
``r-1``/``rg-1``). Their audio and covers are intact; only the four tag fields
were overwritten. The original ``artists - title`` string survives in each file's
name, so we can rebuild the tags from the filenames.

Usage:
    uv run python scripts/restore_junk_tags.py --dry-run   # preview mapping
    uv run python scripts/restore_junk_tags.py             # apply + rescan
"""

import argparse
import sys
from pathlib import Path

import smclipy.db as db
from smclipy.config import init
from smclipy.metadata import (
    apply_tag_update,
    iter_audio_files,
    read_song_profile,
    read_song_uuid,
    scan_library,
)

_SEPARATORS: tuple[str, ...] = (" - ", " ", "", "-")


def _is_junk(profile: dict[str, str]) -> bool:
    return (
        profile.get("title") == "Renamed"
        and profile.get("artists", "").strip() == "Artist"
        and profile.get("album", "").strip() == "Album"
        and profile.get("date", "").strip() == "1980"
    )


def _split_stem(stem: str, known: list[str]) -> tuple[list[str], str] | None:
    """Recover (artists, title) from a library stem (``Artists - Title``).

    Authors were joined with varying separators across download versions, so we
    greedily consume known authors from the left (longest match first); whatever
    remains is the title. Returns None when the result is unusable.
    """
    by_len: list[str] = sorted(
        (name for name in known if name), key=lambda n: (-len(n), n)
    )
    tail: str = stem
    artists: list[str] = []
    while True:
        best: tuple[str, str] | None = None
        best_extent: int = -1
        for name in by_len:
            for sep in _SEPARATORS:
                if tail.startswith(name + sep):
                    extent: int = len(name) + len(sep)
                    if extent > best_extent:
                        best, best_extent = (name, sep), extent
        if best is None:
            break
        name, sep = best
        artists.append(name)
        tail = tail[len(name) + len(sep) :]
    title: str = tail.strip().lstrip("-").strip()
    if not artists or not title:
        return None
    return artists, title


def _restore_song(file: Path, known: list[str]) -> tuple[bool, str | None]:
    profile, _ = read_song_profile(file)
    if not _is_junk(profile):
        return False, None
    parsed = _split_stem(file.stem, known)
    if parsed is None:
        return False, f"could not parse {file.name!r}"
    artists, title = parsed
    if not apply_tag_update(
        file,
        title=title,
        artists=artists,
        album="",
        date="",
    ):
        return False, f"write failed for {file.name!r}"
    song_uuid: str | None = read_song_uuid(file)
    if song_uuid is not None:
        db.reset_mb_status(song_uuid)
        db.log_event(song_uuid, "musicbrainz", {"status": "reset"})
    return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the filename -> artists/title mapping without writing anything",
    )
    args = parser.parse_args(argv)

    s = init()
    known: list[str] = db.get_authors()
    music: Path = s.music_folder

    junk: list[tuple[Path, list[str] | None, str | None]] = []
    for file in iter_audio_files():
        profile, _ = read_song_profile(file)
        if not _is_junk(profile):
            continue
        parsed = _split_stem(file.stem, known)
        if parsed is None:
            junk.append((file, None, f"unparseable: {file.stem!r}"))
        else:
            junk.append((file, parsed, None))

    if not junk:
        print("No junk-tagged songs found.")
        return 0

    print(f"Found {len(junk)} junk-tagged song(s) in '{music}'.")
    if args.dry_run:
        print("\nResulting tags (not written):")
        for file, parsed, note in junk:
            if parsed is None:
                print(f"  SKIP  {file.name!r}")
                print(f"        {note}")
            else:
                artists, title = parsed
                print(
                    f"  {file.name!r}\n"
                    f"        artists: {', '.join(artists)}\n"
                    f"        title:   {title!r}"
                )
        return 0

    applied: int = 0
    skipped: int = 0
    for file, parsed, note in junk:
        if parsed is None:
            skipped += 1
            print(f"SKIP  {file.name!r}: {note}", file=sys.stderr)
            continue
        artists, title = parsed
        if not apply_tag_update(file, title=title, artists=artists, album="", date=""):
            skipped += 1
            print(f"FAIL  {file.name!r}: write failed", file=sys.stderr)
            continue
        song_uuid: str | None = read_song_uuid(file)
        if song_uuid is not None:
            db.reset_mb_status(song_uuid)
            db.log_event(song_uuid, "musicbrainz", {"status": "reset"})
        applied += 1

    stale: int = 0
    for row in db.list_songs():
        if row["title"] != "Renamed":
            continue
        # Files never junk-tagged on disk but the DB row still carries the
        # fake match; forget it so the tag command re-offers these too.
        if Path(music, row["current_path"] or row["first_seen_path"] or "").exists():
            db.reset_mb_status(row["uuid"])
            db.log_event(row["uuid"], "musicbrainz", {"status": "reset"})
            stale += 1

    print(
        f"\nRestored {applied} song(s), skipped {skipped}, reset {stale} stale row(s)."
    )

    if applied:
        print("Rescanning the library to sync the database...")
        summary = scan_library()
        print(
            f"Scan done: {summary.added} added, {summary.renamed} renamed, "
            f"{summary.changed} changed, {summary.missing} missing."
        )
        print(
            "Re-run 'smclipy tag --auto --all' to re-fill album/date from MusicBrainz."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
