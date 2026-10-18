"""The ``stats`` command: library totals plus top albums and artists."""

import argparse
import json
from collections import Counter
from typing import Any

import smclipy.db as db
from smclipy.config import Settings, init
from smclipy.helpers import split_authors
from smclipy.metadata import scan_library
from smclipy.ui import msg

DEFAULT_TOP = 10


def _format_duration(seconds: float) -> str:
    total: int = int(round(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _rank(counter: Counter[str], top_n: int) -> list[tuple[str, int]]:
    """Most-crowded first, ties broken alphabetically for stable output."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))[
        :top_n
    ]


def _library_stats(top_n: int) -> dict[str, Any]:
    total_songs = 0
    total_duration = 0.0
    unknown_duration = 0
    albums: Counter[str] = Counter()
    artists: Counter[str] = Counter()
    for row in db.list_songs():
        if not row["current_path"]:
            continue
        total_songs += 1
        duration: Any = row["duration"]
        if duration is None:
            unknown_duration += 1
        else:
            total_duration += float(duration)
        album: str = (row["album"] or "").strip()
        if album:
            albums[album] += 1
        for artist in split_authors(row["artists"] or ""):
            artists[artist] += 1
    return {
        "total_songs": total_songs,
        "total_albums": len(albums),
        "total_artists": len(artists),
        "total_duration_seconds": round(total_duration),
        "total_duration_human": _format_duration(total_duration),
        "unknown_duration": unknown_duration,
        "top_albums": [
            {"album": name, "count": count} for name, count in _rank(albums, top_n)
        ],
        "top_artists": [
            {"artist": name, "count": count} for name, count in _rank(artists, top_n)
        ],
    }


def _print_ranking(title: str, entries: list[dict[str, Any]], key: str) -> None:
    print()
    print(f"{title}:")
    if not entries:
        print("  (none)")
        return
    for index, entry in enumerate(entries, start=1):
        print(f"  {index:>2}. {entry[key]} ({entry['count']})")


def cmd_stats(args: argparse.Namespace) -> None:
    s: Settings = init()
    as_json: bool = bool(getattr(args, "json", False))
    top_n: int = int(getattr(args, "top", DEFAULT_TOP))
    if top_n < 1:
        top_n = 1

    if not bool(getattr(args, "no_scan", False)):
        if not s.music_folder.is_dir():
            if as_json:
                print(
                    json.dumps(
                        {
                            "error": "music_folder_not_found",
                            "path": str(s.music_folder),
                        }
                    )
                )
            else:
                print(f"Music folder not found at '{s.music_folder}'.")
                print("Create it or fix 'path_to_music_folder' in the config.")
            raise SystemExit(1)
        msg("Scanning music files...")
        scan_library()

    stats: dict[str, Any] = _library_stats(top_n)

    if as_json:
        print(json.dumps(stats, indent=2))
        return

    print(f"Total songs: {stats['total_songs']}")
    print(f"Total albums: {stats['total_albums']}")
    print(f"Total artists: {stats['total_artists']}")
    print(f"Total playtime: {stats['total_duration_human']}")
    if stats["unknown_duration"]:
        print(f"  ({stats['unknown_duration']} songs with unknown duration)")
    _print_ranking("Top albums", stats["top_albums"], "album")
    _print_ranking("Top artists", stats["top_artists"], "artist")
