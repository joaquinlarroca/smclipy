import argparse
import json
import sys
from contextlib import suppress
from pathlib import Path

import smclipy.db as db
from smclipy import __version__
from smclipy.config import CONFIG_PATH, Settings, _load_raw_config, init, settings
from smclipy.downloader import (
    TEMP_STEM_PREFIXES,
    VideoDownloadError,
    download,
    extract_urls,
    get_album,
    get_author,
    temp_stem,
)
from smclipy.formats import SUPPORTED_EXTENSIONS, audio_ext
from smclipy.helpers import (
    distinct_authors,
    resolve_known_authors,
    sanitize_filename,
    split_authors,
)
from smclipy.images import (
    crop_image_1_to_1,
    display_image,
    is_image_1_to_1,
    is_image_pillarbox,
)
from smclipy.metadata import (
    COVER_EXTENSIONS,
    SaveResult,
    ScanSummary,
    backup_cover,
    change_cover,
    get_image_from_file,
    has_cover,
    library_target_path,
    read_song_uuid,
    save_all_covers,
    save_song_temp_to_main,
    scan_library,
    snapshot_song,
)
from smclipy.modify import cmd_modify
from smclipy.playlists import cmd_playlist
from smclipy.restore import cmd_restore
from smclipy.tag import TAG_COVER_PREFIX, cmd_tag
from smclipy.ui import (
    msg,
    prompt_album,
    prompt_authors,
    prompt_crop,
    prompt_resume,
    prompt_title,
    show_video_info,
)


def collect_urls() -> list[str]:
    print("Enter '' as url to finish inputting urls")
    prior_pending: list[str] = db.get_pending_urls()
    urls_list: list[str] = []
    while True:
        try:
            url: str = input("Enter your URL: ")
        except (KeyboardInterrupt, EOFError):
            print("\nInterrupted while collecting URLs...")
            saved: list[str] = list(dict.fromkeys(prior_pending + urls_list))
            db.reset_queue(saved)
            if urls_list:
                print(
                    f"Saved partial queue of {len(urls_list)} url/s for a later resume."
                )
            raise SystemExit(130) from None
        if url == "":
            break
        extracted_urls: list[str] = extract_urls(url)
        if not extracted_urls:
            print(f"Warning: no recognizable video URLs found in '{url}'")
        else:
            urls_list.extend(url for url in extracted_urls if url not in urls_list)
    return urls_list


def _collect_batch(batch_file: Path) -> list[str]:
    """Read one URL (or a blob of text) per line from a file, non-interactively."""
    try:
        text: str = batch_file.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(
            f"Error: could not read batch file '{batch_file}': {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    urls: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        urls.extend(url for url in extract_urls(line) if url not in urls)
    print(f"Read {len(urls)} URL(s) from '{batch_file.name}'.")
    db.reset_queue(urls)
    return urls


def process_video(
    url: str, authors_list: list[str], *, no_prompt: bool = False
) -> SaveResult:
    s: Settings = settings()
    stem: str = temp_stem(url)
    temp_track: Path = s.temp_folder.joinpath(f"{stem}{audio_ext(s.audio_format)}")

    _cleanup_temp_files(s)

    info_dictionary = download(url, stem)
    cover_path = get_image_from_file(temp_track, s.temp_folder, stem)

    if cover_path and not no_prompt:
        print("\n\n")
        display_image(cover_path)

        if is_image_1_to_1(cover_path):
            print("Already 1:1")
        elif prompt_crop(default=False):
            crop_image_1_to_1(cover_path)

    print("\n\n")

    show_video_info(info_dictionary)

    print("\n")

    title_value = info_dictionary.get("title")
    default_title = title_value.strip() if isinstance(title_value, str) else ""
    title: str
    album: str
    artists: list[str]
    authors: str
    if no_prompt:
        title = default_title or stem
        if not default_title:
            print(f"Warning: no title in video info, using '{stem}'.")
        album = get_album(info_dictionary)
        artists = get_author(info_dictionary)
        authors = "\\".join(resolve_known_authors(artists, authors_list))
    else:
        title = prompt_title(default=default_title)
        while not title.strip():
            print("Title cannot be empty.")
            title = prompt_title(default=default_title)
        title = title.strip()

        album = prompt_album(default=get_album(info_dictionary))

        artists = get_author(info_dictionary)
        authors_default: str = "\\".join(resolve_known_authors(artists, authors_list))
        authors = prompt_authors(authors_list, default=authors_default)
        while not split_authors(authors):
            print("At least one artist is required.")
            authors = prompt_authors(authors_list, default=authors_default)

    current_authors_list: list[str] = split_authors(authors)
    new_authors: list[str] = distinct_authors(current_authors_list, authors_list)
    authors_list.extend(new_authors)

    # If the target already exists and is going to be overwritten, keep the
    # tracked song's UUID so the existing database row is updated in place
    # instead of orphaned and replaced with a phantom duplicate.
    previous_uuid: str | None = None
    planned: Path | None = library_target_path(temp_track, title, current_authors_list)
    if planned is not None and planned.is_file():
        previous_uuid = read_song_uuid(planned)

    result: SaveResult
    target: Path | None
    result, target = save_song_temp_to_main(
        temp_track,
        cover_path,
        title,
        authors,
        album,
        overwrite=False if no_prompt else None,
        song_uuid=previous_uuid,
    )
    if result is SaveResult.SAVED and target is not None:
        db.add_authors(new_authors)
        song_uuid: str | None = read_song_uuid(target)
        if song_uuid is not None:
            rel_path: str = target.relative_to(s.music_folder).as_posix()
            existing = db.get_song_by_uuid(song_uuid)
            if existing is None:
                db.insert_song(
                    song_uuid=song_uuid,
                    current_path=rel_path,
                    title=title,
                    artists=", ".join(current_authors_list),
                    album=album,
                    has_cover=has_cover(target),
                    source_url=url,
                )
            else:
                db.update_song_metadata(
                    song_uuid,
                    title=title,
                    artists=", ".join(current_authors_list),
                    album=album,
                    has_cover=has_cover(target),
                    source_url=url,
                )
            db.log_event(song_uuid, "downloaded", {"url": url, "path": rel_path})
    return result


def _collect_new_queue() -> list[str]:
    pending_ids: list[str] = collect_urls()
    db.reset_queue(pending_ids)
    return pending_ids


_TEMP_FILE_PREFIXES = ("temp.", *TEMP_STEM_PREFIXES, TAG_COVER_PREFIX)


def _cleanup_temp_files(s: Settings) -> None:
    temp_folder: Path = s.temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob("*"):
        name: str = temp_file.name
        if name.startswith(_TEMP_FILE_PREFIXES):
            with suppress(OSError):
                temp_file.unlink()


def cmd_download(args: argparse.Namespace) -> None:
    s: Settings = init()
    no_prompt: bool = bool(getattr(args, "no_prompt", False))
    batch_raw: str | None = getattr(args, "batch", None)

    print("Scanning music files...")
    scan_library()

    print("Fetching all authors names...")
    authors_list: list[str] = db.get_authors()

    pending_ids: list[str] = db.get_pending_urls()
    failed_ids: list[str] = db.get_failed_urls()
    if failed_ids:
        print(
            f"Notice: {len(failed_ids)} URL/s reached the maximum number of "
            f"download attempts and were permanently dropped: {failed_ids}"
        )

    if batch_raw is not None:
        pending_ids = _collect_batch(Path(batch_raw))
    elif pending_ids and prompt_resume():
        db.reset_queue(pending_ids)
    else:
        pending_ids = _collect_new_queue()

    print(f"Preparing to download {len(pending_ids)} vid/s")
    skipped_ids: list[str] = []
    interrupted = False
    try:
        total: int = len(pending_ids)
        for index, video_id in enumerate(pending_ids, start=1):
            print(f"\n[{index}/{total}] {video_id}")
            try:
                result: SaveResult = process_video(
                    video_id, authors_list, no_prompt=no_prompt
                )
            except VideoDownloadError as exc:
                print(f"Skipping '{video_id}': {exc}")
                skipped_ids.append(video_id)
                continue
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                interrupted = True
                break
            except Exception as exc:
                print(f"Skipping '{video_id}': unexpected error: {exc!r}")
                skipped_ids.append(video_id)
                continue
            if result is SaveResult.FAILED:
                skipped_ids.append(video_id)
                continue
            try:
                db.mark_processed(video_id)
            except Exception as exc:
                print(f"Warning: could not record '{video_id}' as processed: {exc!r}")
                skipped_ids.append(video_id)
    finally:
        _cleanup_temp_files(s)

    if skipped_ids:
        try:
            db.requeue(skipped_ids)
        except Exception as exc:
            print(f"Warning: could not persist skipped videos for retry: {exc!r}")
        print(
            f"{'Interrupted' if interrupted else 'Finished'} with "
            f"{len(skipped_ids)} skipped vid/s "
            f"(still pending for a retry): {skipped_ids}"
        )

    if interrupted:
        raise SystemExit(130) from None


def _audio_path_variants(filename: str) -> list[str]:
    return [f"{filename}{ext}" for ext in SUPPORTED_EXTENSIONS]


def _cover_to_song_uuids() -> dict[str, list[str]]:
    """Map every cover image stem to the UUIDs of the songs it may belong to.

    A cover is saved as ``artist-title`` derived from the song's tags, so a
    song is matched both by re-deriving that stem from the recorded
    title/artists (which survive a rename) and by stripping the audio
    extension from the recorded paths (current and first-seen). Building the
    map once lets a whole ``crop`` run resolve every cover with a single
    database read.
    """
    mapping: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    def add(stem: str, uuid: str) -> None:
        key: str = stem.casefold()
        if uuid not in seen.setdefault(key, set()):
            seen[key].add(uuid)
            mapping.setdefault(key, []).append(uuid)

    for row in db.list_songs():
        uuid: str = str(row["uuid"])
        title: str = (row["title"] or "").strip()
        artists: list[str] = split_authors(row["artists"] or "")
        if title and artists:
            add(sanitize_filename(f"{artists[0]}-{title}"), uuid)
        for rel_path in (row["current_path"], row["first_seen_path"]):
            if not rel_path:
                continue
            for ext in SUPPORTED_EXTENSIONS:
                if rel_path.casefold().endswith(ext):
                    add(rel_path[: -len(ext)], uuid)
                    break
    return mapping


def _song_uuids_for_cover(
    filename: str, index: dict[str, list[str]] | None = None
) -> list[str]:
    """UUIDs of songs that the cover image stem ``filename`` belongs to.

    ``index`` is a precomputed ``_cover_to_song_uuids()`` map; when omitted it
    is built on demand, which is convenient for one-off lookups but wasteful
    when many covers are resolved in a loop.
    """
    lookup: dict[str, list[str]] = (
        index if index is not None else _cover_to_song_uuids()
    )
    return list(lookup.get(filename.casefold(), []))


def _record_cover_status(
    filename: str, status: str, *, index: dict[str, list[str]] | None = None
) -> None:
    for song_uuid in _song_uuids_for_cover(filename, index=index):
        db.set_cover_status(song_uuid, status)
        db.log_event(song_uuid, "cover", {"status": status, "path": filename})


def _crop_target_tracks(
    s: Settings, filename: str, *, index: dict[str, list[str]] | None = None
) -> list[Path]:
    """Tracks that should receive the cropped cover ``filename``.

    The cover name is derived from the song's tags, which no longer match the
    on-disk name once a tracked file has been renamed, so resolve through the
    database: both by path (remembers current and first-seen paths) and by
    re-deriving the cover stem from the recorded title/artists.
    """
    candidates: list[Path] = [
        s.music_folder.joinpath(rel_path) for rel_path in _audio_path_variants(filename)
    ]
    for song_uuid in _song_uuids_for_cover(filename, index=index):
        row = db.get_song_by_uuid(song_uuid)
        if row is not None and row["current_path"]:
            candidates.append(s.music_folder.joinpath(row["current_path"]))
    targets: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in targets:
            targets.append(candidate)
    return targets


def _cover_overlaps_many_songs(filename: str, *, index: dict[str, list[str]]) -> bool:
    """Whether a cover image stem maps to more than one *distinct* song.

    A cover is derived from the song's first artist and title, so two
    different songs could share that stem. Re-embedding a cropped cover into
    every match would then overwrite the wrong song's art; deduplicated
    copies of the same song (same title and artists) are still safe to
    re-embed.
    """
    seen: set[tuple[str, str]] = set()
    for song_uuid in _song_uuids_for_cover(filename, index=index):
        row = db.get_song_by_uuid(song_uuid)
        if row is None:
            continue
        seen.add(((row["title"] or "").casefold(), (row["artists"] or "").casefold()))
    return len(seen) > 1


def cmd_crop(_args: argparse.Namespace) -> None:
    s: Settings = init()

    print("Scanning music files...")
    scan_library()
    save_all_covers()

    print("Reading false positives...")
    false_positives: set[str] = db.get_false_positives()

    cover_uuids: dict[str, list[str]] = _cover_to_song_uuids()

    print("Scanning images files...")
    cover_patterns = tuple(f"*{ext}" for ext in COVER_EXTENSIONS)
    cover_files: list[Path] = []
    for pattern in cover_patterns:
        for file in s.covers_folder.glob(pattern):
            if not file.is_file() or file.stem.strip() in false_positives:
                continue
            try:
                if is_image_pillarbox(file):
                    cover_files.append(file)
                else:
                    _record_cover_status(
                        file.stem, db.COVER_STATUS_NOT_PILLARBOX, index=cover_uuids
                    )
            except Exception as exc:
                print(f"Warning: could not analyze '{file.name}', skipping: {exc!r}")

    if not cover_files:
        print("No images to crop...")
    else:
        for cover in cover_files:
            filename: str = cover.stem
            try:
                display_image(cover)
                if prompt_crop(default=True):
                    backup_cover(cover)
                    crop_image_1_to_1(cover)
                    targets: list[Path] = _crop_target_tracks(
                        s, filename, index=cover_uuids
                    )
                    if _cover_overlaps_many_songs(filename, index=cover_uuids):
                        print(
                            f"{filename}: cover matches multiple different songs, "
                            "skipping re-embed"
                        )
                    elif targets:
                        for track_file in targets:
                            snapshot_song(
                                track_file, read_song_uuid(track_file), "crop"
                            )
                            change_cover(cover, track_file)
                    else:
                        print(f"{filename}: track not found, skipping cover re-embed")
                    _record_cover_status(
                        filename, db.COVER_STATUS_CROPPED, index=cover_uuids
                    )
                else:
                    false_positives.add(filename)
                    db.add_false_positive(filename)
                    _record_cover_status(
                        filename, db.COVER_STATUS_FALSE_POSITIVE, index=cover_uuids
                    )
            except Exception as exc:
                print(f"Warning: could not process '{filename}': {exc!r}")
            print("\n\n")


def cmd_directories(args: argparse.Namespace) -> None:
    config_exists: bool = CONFIG_PATH.exists()
    s = Settings(_load_raw_config(create_if_missing=False))
    if not config_exists:
        print(f"Using default configuration (no config file at {CONFIG_PATH}).")
        print("Run `smclipy download` to generate one.")
        print()
    fields: list[tuple[str, str]] = [
        ("Config dir:", str(CONFIG_PATH.parent.resolve())),
        ("Music:", str(s.music_folder.resolve())),
        ("Library:", str(s.script_folder.resolve())),
        ("Temp:", str(s.temp_folder.resolve())),
        ("Covers:", str(s.covers_folder.resolve())),
        ("Database:", str(s.db_path.resolve())),
        ("Backups:", str(s.backups_folder.resolve())),
    ]
    for label, value in fields:
        print(f"{label:<13}{value}")


def cmd_update(args: argparse.Namespace) -> None:
    s: Settings = init()
    as_json: bool = bool(getattr(args, "json", False))

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
    summary: ScanSummary = scan_library(collect_details=as_json)

    if as_json:
        print(
            json.dumps(
                {
                    "added": summary.added,
                    "renamed": summary.renamed,
                    "missing": summary.missing,
                    "changed": summary.changed,
                    "songs": [
                        {
                            "status": change.status,
                            "uuid": change.uuid,
                            "path": change.path,
                            "old_path": change.old_path,
                        }
                        for change in summary.changes
                    ],
                },
                indent=2,
            )
        )
        return

    parts: list[str] = []
    for count, label in (
        (summary.added, "added"),
        (summary.renamed, "renamed"),
        (summary.missing, "missing"),
        (summary.changed, "refreshed"),
    ):
        if count:
            parts.append(f"{count} {label}")
    if parts:
        print("Updated: " + ", ".join(parts) + ".")
    else:
        print("Already up to date.")


_TTY_REQUIRED_COMMANDS: frozenset[str] = frozenset(
    {"download", "crop", "tag", "modify", "restore"}
)
_TTY_REQUIRED_PLAYLIST_ACTIONS: frozenset[str] = frozenset({"add", "remove", "move"})


def _requires_tty(args: argparse.Namespace) -> bool:
    """Whether a command needs an interactive terminal for this invocation."""
    if sys.stdin.isatty():
        return False
    if args.command == "download":
        return not (getattr(args, "batch", None) and getattr(args, "no_prompt", False))
    if args.command == "tag":
        return not (
            bool(getattr(args, "auto", False)) and bool(getattr(args, "all", False))
        )
    if args.command in _TTY_REQUIRED_COMMANDS:
        return True
    return (
        args.command == "playlist"
        and getattr(args, "playlist_action", None) in _TTY_REQUIRED_PLAYLIST_ACTIONS
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="smclipy",
        description="Download, tag, and organize music from YouTube and SoundCloud.",
        epilog=(
            "examples:\n"
            "  smclipy download   Batch download songs and tag them interactively\n"
            "  smclipy download --batch urls.txt --no-prompt  Headless batch download\n"
            "  smclipy tag        Retag library songs using MusicBrainz metadata\n"
            "  smclipy tag --auto Full-auto retag: top match applied to each song\n"
            "  smclipy tag --semi Auto-pick the top match, confirm each change\n"
            "  smclipy tag --auto --all  Headless retag of every untagged song\n"
            "  smclipy tag --auto --all --json  Same, with a JSON report on stdout\n"
            "  smclipy modify     Manually edit the tags of one or more songs\n"
            "  smclipy restore    Roll back a song to an earlier tag backup\n"
            "  smclipy crop       Crop pillarboxed cover images and re-embed them\n"
            "  smclipy update     Rescan the library and sync the tracking database\n"
            "  smclipy update --json  Machine-readable scan report on stdout\n"
            "  smclipy playlist create Chill  Create an empty playlist\n"
            "  smclipy playlist add Chill     Interactively add library songs to it\n"
            "  smclipy playlist export Chill -f m3u  Export it as an .m3u file\n"
            "  smclipy playlist import mix.m3u       Import a playlist from a file\n"
            "  smclipy playlist show Chill --json    Print its songs as JSON\n"
            "  smclipy -d         Print the directories smclipy uses\n"
            "  smclipy -v         Print the version and exit\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print the version and exit.",
    )
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser] = (
        parser.add_subparsers(dest="command", metavar="command")
    )

    download_parser = subparsers.add_parser(
        "download",
        help="Batch download songs from YouTube or SoundCloud and tag them.",
        description=(
            "Batch download songs from YouTube or SoundCloud, then interactively "
            "tag each one with a title, artist, and album before saving it."
        ),
    )
    download_parser.add_argument(
        "-b",
        "--batch",
        metavar="FILE",
        help=(
            "Read URLs from FILE (one per line) instead of prompting, so download "
            "can run without an interactive terminal."
        ),
    )
    download_parser.add_argument(
        "--no-prompt",
        action="store_true",
        help=(
            "Accept the default title, album, and artists for each download "
            "without asking (useful with --batch). A file that already exists "
            "with the same artist-title name is kept and skipped without "
            "prompting."
        ),
    )

    subparsers.add_parser(
        "crop",
        help="Crop pillarboxed cover images and re-embed them.",
        description=(
            "Scan saved cover images for pillarboxing, crop them to a 1:1 ratio "
            "interactively, and re-embed each result into its matching audio file."
        ),
    )

    tag_parser: argparse.ArgumentParser = subparsers.add_parser(
        "tag",
        help="Retag library songs using MusicBrainz metadata.",
        description=(
            "List every song in the library, fetch matching metadata from "
            "MusicBrainz, and interactively review and apply title, artist, album, "
            "release date, track number, album artist, and cover art changes."
        ),
        epilog=(
            "Metadata is provided by MusicBrainz (core data is CC0, supplementary "
            "data and docs are CC BY-NC-SA 3.0; https://musicbrainz.org ), and "
            "cover art is served by the Cover Art Archive (coverartarchive.org)."
        ),
    )
    tag_mode: argparse._MutuallyExclusiveGroup = (
        tag_parser.add_mutually_exclusive_group()
    )
    tag_mode.add_argument(
        "-a",
        "--auto",
        action="store_true",
        help=(
            "Fully automatic: pick the top match for each selected song and apply "
            "the configured tag_fields without any further prompts (only the "
            "initial range selection)."
        ),
    )
    tag_mode.add_argument(
        "-s",
        "--semi",
        action="store_true",
        help=(
            "Semi-automatic: pick the top match for each selected song but confirm "
            "each change set (checkbox dialog) before applying."
        ),
    )
    tag_parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Tag every untagged song in the library instead of asking for a range. "
            "Combine with --auto to run without an interactive terminal."
        ),
    )
    tag_parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help=(
            "Print a machine-readable JSON report to stdout while human messages "
            "go to stderr. Requires --auto --all (headless retag)."
        ),
    )

    modify_parser: argparse.ArgumentParser = subparsers.add_parser(
        "modify",
        help="Manually edit the tags of one or more library songs.",
        description=(
            "Pick songs from your library (numbers or ranges) and edit their tags "
            "by hand: title, artists, album, release date, genre, album artist, "
            "track number, and cover art. One set of values is applied to every "
            "selected song, so a range bulk-edits an album and a single number "
            "edits just that song. A blank answer keeps the field, typing /clear "
            "empties it."
        ),
    )
    modify_parser.add_argument(
        "--reset-mb",
        action="store_true",
        help=(
            "Forget the MusicBrainz match of every modified song so the `tag` "
            "command offers it again."
        ),
    )

    subparsers.add_parser(
        "restore",
        help="Undo tag changes from recorded backups.",
        description=(
            "Pick songs from your library and roll back to an earlier backup. "
            "A backup of each song's tags (and cover art) is recorded "
            "automatically before every `tag`, `modify`, and `crop` change, so a "
            "mistaken MusicBrainz match or manual edit can be undone here. The "
            "restore itself is also backed up, so undoing an undo is possible."
        ),
    )

    playlist_parser: argparse.ArgumentParser = subparsers.add_parser(
        "playlist",
        help="Create and manage library playlists, and import/export them.",
        description=(
            "Create, rename, delete, list, and show playlists of your tracked "
            "library songs. Playlists live in the tracking database and reference "
            "songs by UUID, so renames and relocations never break them. Songs can "
            "be added, removed, moved, or sorted by metadata, and playlists can be "
            "exported to .m3u or .json and imported back."
        ),
    )
    playlist_sub: argparse._SubParsersAction[argparse.ArgumentParser] = (
        playlist_parser.add_subparsers(
            dest="playlist_action", metavar="action", required=True
        )
    )

    playlist_create = playlist_sub.add_parser(
        "create",
        help="Create a new playlist.",
        description="Create a new, empty playlist.",
    )
    playlist_create.add_argument("name", help="Name of the playlist.")
    playlist_create.add_argument(
        "--description", default="", help="Optional description of the playlist."
    )

    playlist_rename = playlist_sub.add_parser(
        "rename",
        help="Rename a playlist.",
        description="Rename an existing playlist.",
    )
    playlist_rename.add_argument("name", help="Current name of the playlist.")
    playlist_rename.add_argument("new_name", help="New name for the playlist.")

    playlist_delete = playlist_sub.add_parser(
        "delete",
        help="Delete a playlist and its entries.",
        description="Delete a playlist and remove all of its entries.",
    )
    playlist_delete.add_argument("name", help="Name of the playlist to delete.")

    playlist_list = playlist_sub.add_parser(
        "list",
        help="List all playlists and their song counts.",
        description="List every playlist with its song count.",
    )
    playlist_list.add_argument(
        "-j", "--json", action="store_true", help="Print the list as JSON."
    )

    playlist_show = playlist_sub.add_parser(
        "show",
        help="Show the songs in a playlist.",
        description="List the songs in a playlist in order.",
    )
    playlist_show.add_argument("name", help="Name of the playlist to show.")
    playlist_show.add_argument(
        "-j", "--json", action="store_true", help="Print the songs as JSON."
    )

    playlist_add = playlist_sub.add_parser(
        "add",
        help="Interactively add library songs to a playlist.",
        description="Pick songs from your library to append to a playlist.",
    )
    playlist_add.add_argument("name", help="Name of the playlist to add to.")

    playlist_remove = playlist_sub.add_parser(
        "remove",
        help="Interactively remove songs from a playlist.",
        description="Pick songs from a playlist to remove.",
    )
    playlist_remove.add_argument("name", help="Name of the playlist to remove from.")

    playlist_move = playlist_sub.add_parser(
        "move",
        help="Interactively move a song to a new position.",
        description="Pick a song in a playlist and give it a new position.",
    )
    playlist_move.add_argument("name", help="Name of the playlist to reorder.")

    playlist_sort = playlist_sub.add_parser(
        "sort",
        help="Sort a playlist's songs by metadata.",
        description=(
            "Reorder a playlist's songs by title, artists, album, path, or the "
            "time they were added."
        ),
    )
    playlist_sort.add_argument("name", help="Name of the playlist to sort.")
    playlist_sort.add_argument(
        "--key",
        choices=("title", "artists", "album", "path", "added"),
        default="title",
        help="What to sort by (default: title).",
    )
    playlist_sort.add_argument(
        "--reverse", action="store_true", help="Sort in descending order."
    )

    playlist_export = playlist_sub.add_parser(
        "export",
        help="Export a playlist to .m3u or .json.",
        description=(
            "Export a playlist to an .m3u or .json file. Paths are written "
            "relative to the exported file unless --absolute is given."
        ),
    )
    playlist_export.add_argument("name", help="Name of the playlist to export.")
    playlist_export.add_argument(
        "-f",
        "--format",
        choices=("m3u", "json"),
        help="Export format (default: inferred from --output, else m3u).",
    )
    playlist_export.add_argument(
        "-o",
        "--output",
        help="Where to write the file (default: <name>.<ext> in the current dir).",
    )
    playlist_export.add_argument(
        "--absolute", action="store_true", help="Write absolute paths."
    )

    playlist_import = playlist_sub.add_parser(
        "import",
        help="Import a playlist from .m3u or .json.",
        description=(
            "Import an .m3u/.m3u8 or smclipy .json playlist. Entries are matched "
            "to your tracked library songs by path (relative to the file, your "
            "music folder, or absolute) or UUID; unmatched entries are skipped and "
            "reported."
        ),
    )
    playlist_import.add_argument("file", help="Path to the .m3u/.m3u8/.json file.")
    playlist_import.add_argument(
        "--name", help="Playlist name (default: the file's name or the JSON name)."
    )
    playlist_import.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite the playlist if it already exists.",
    )

    update_parser = subparsers.add_parser(
        "update",
        help="Rescan the library and sync the tracking database.",
        description=(
            "Reconcile the tracking database with the current state of the music "
            "folder: register new songs, record renames, and mark files that "
            "vanished as missing. Does not require an interactive terminal, so it "
            "can be run from scripts or cron."
        ),
    )
    update_parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Print a machine-readable JSON report to stdout.",
    )

    parser.add_argument(
        "-d",
        "--directories",
        action="store_true",
        help="Print the directories smclipy uses and exit.",
    )

    args: argparse.Namespace = parser.parse_args(argv)

    if args.command is None:
        if args.directories:
            cmd_directories(args)
        else:
            parser.error("the following arguments are required: command")
        return

    if (
        args.command == "tag"
        and getattr(args, "json", False)
        and not (
            bool(getattr(args, "auto", False)) and bool(getattr(args, "all", False))
        )
    ):
        parser.error("--json requires --auto and --all (run a headless retag)")

    if _requires_tty(args):
        print(
            "smclipy requires an interactive terminal. "
            "Piped or non-TTY input is not supported.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if args.command == "download":
        cmd_download(args)
    elif args.command == "crop":
        cmd_crop(args)
    elif args.command == "tag":
        cmd_tag(args)
    elif args.command == "modify":
        cmd_modify(args)
    elif args.command == "restore":
        cmd_restore(args)
    elif args.command == "playlist":
        cmd_playlist(args)
    elif args.command == "update":
        cmd_update(args)
