import argparse
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
    change_cover,
    get_image_from_file,
    has_cover,
    read_song_uuid,
    save_all_covers,
    save_song_temp_to_main,
    scan_library,
)
from smclipy.tag import TAG_COVER_PREFIX, cmd_tag
from smclipy.ui import (
    prompt_album,
    prompt_authors,
    prompt_crop,
    prompt_resume,
    prompt_title,
    show_video_info,
)


def collect_urls() -> list[str]:
    print("Enter '' as url to finish inputting urls")
    urls_list: list[str] = []
    while True:
        try:
            url: str = input("Enter your URL: ")
        except (KeyboardInterrupt, EOFError):
            print("\nInterrupted while collecting URLs...")
            db.reset_queue(urls_list)
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


def process_video(url: str, authors_list: list[str]) -> SaveResult:
    s: Settings = settings()
    stem: str = temp_stem(url)
    temp_track: Path = s.temp_folder.joinpath(f"{stem}{audio_ext(s.audio_format)}")

    _cleanup_temp_files(s)

    info_dictionary = download(url, stem)
    cover_path = get_image_from_file(temp_track, s.temp_folder, stem)

    if cover_path:
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
    title: str = prompt_title(default=default_title)
    while not title.strip():
        print("Title cannot be empty.")
        title = prompt_title(default=default_title)
    title = title.strip()

    album: str = prompt_album(default=get_album(info_dictionary))

    artists: list[str] = get_author(info_dictionary)
    authors_default: str = "\\".join(resolve_known_authors(artists, authors_list))
    authors: str = prompt_authors(authors_list, default=authors_default)
    while not split_authors(authors):
        print("At least one artist is required.")
        authors = prompt_authors(authors_list, default=authors_default)

    current_authors_list: list[str] = split_authors(authors)
    new_authors: list[str] = distinct_authors(current_authors_list, authors_list)
    authors_list.extend(new_authors)

    result: SaveResult
    target: Path | None
    result, target = save_song_temp_to_main(
        temp_track, cover_path, title, authors, album
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


def cmd_download(_args: argparse.Namespace) -> None:
    s: Settings = init()

    print("Scanning music files...")
    scan_library()

    print("Fetching all authors names...")
    authors_list: list[str] = db.get_authors()

    pending_ids: list[str] = db.get_pending_urls()

    if pending_ids and prompt_resume():
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
                result: SaveResult = process_video(video_id, authors_list)
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


def _record_cover_status(filename: str, status: str) -> None:
    for rel_path in _audio_path_variants(filename):
        for row in db.get_song_by_path(rel_path):
            db.set_cover_status(row["uuid"], status)
            db.log_event(row["uuid"], "cover", {"status": status, "path": filename})


def _crop_target_tracks(s: Settings, filename: str) -> list[Path]:
    """Tracks that should receive the cropped cover ``filename``.

    The cover name is derived from the song's tags, which no longer match the
    on-disk name once a tracked file has been renamed, so resolve through the
    database (which remembers both the current and first-seen paths).
    """
    candidates: list[Path] = [
        s.music_folder.joinpath(rel_path) for rel_path in _audio_path_variants(filename)
    ]
    for rel_path in _audio_path_variants(filename):
        for row in db.get_song_by_path(rel_path):
            if row["current_path"]:
                candidates.append(s.music_folder.joinpath(row["current_path"]))
    targets: list[Path] = []
    for candidate in candidates:
        if candidate.is_file() and candidate not in targets:
            targets.append(candidate)
    return targets


def cmd_crop(_args: argparse.Namespace) -> None:
    s: Settings = init()

    print("Scanning music files...")
    scan_library()
    save_all_covers()

    print("Reading false positives...")
    false_positives: set[str] = db.get_false_positives()

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
                    _record_cover_status(file.stem, db.COVER_STATUS_NOT_PILLARBOX)
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
                    crop_image_1_to_1(cover)
                    targets: list[Path] = _crop_target_tracks(s, filename)
                    if targets:
                        for track_file in targets:
                            change_cover(cover, track_file)
                    else:
                        print(f"{filename}: track not found, skipping cover re-embed")
                    _record_cover_status(filename, db.COVER_STATUS_CROPPED)
                else:
                    false_positives.add(filename)
                    db.add_false_positive(filename)
                    _record_cover_status(filename, db.COVER_STATUS_FALSE_POSITIVE)
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
    ]
    for label, value in fields:
        print(f"{label:<13}{value}")


def cmd_update(_args: argparse.Namespace) -> None:
    s: Settings = init()

    if not s.music_folder.is_dir():
        print(f"Music folder not found at '{s.music_folder}'.")
        print("Create it or fix 'path_to_music_folder' in the config.")
        return

    print("Scanning music files...")
    summary: ScanSummary = scan_library()

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


_TTY_REQUIRED_COMMANDS: frozenset[str] = frozenset({"download", "crop", "tag"})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="smclipy",
        description="Download, tag, and organize music from YouTube and SoundCloud.",
        epilog=(
            "examples:\n"
            "  smclipy download   Batch download songs and tag them interactively\n"
            "  smclipy tag        Retag library songs using MusicBrainz metadata\n"
            "  smclipy tag --auto Full-auto retag: top match applied to each song\n"
            "  smclipy tag --semi Auto-pick the top match, confirm each change\n"
            "  smclipy crop       Crop pillarboxed cover images and re-embed them\n"
            "  smclipy update     Rescan the library and sync the tracking database\n"
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

    subparsers.add_parser(
        "download",
        help="Batch download songs from YouTube or SoundCloud and tag them.",
        description=(
            "Batch download songs from YouTube or SoundCloud, then interactively "
            "tag each one with a title, artist, and album before saving it."
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

    subparsers.add_parser(
        "update",
        help="Rescan the library and sync the tracking database.",
        description=(
            "Reconcile the tracking database with the current state of the music "
            "folder: register new songs, record renames, and mark files that "
            "vanished as missing. Does not require an interactive terminal, so it "
            "can be run from scripts or cron."
        ),
    )

    parser.add_argument(
        "-d",
        "--directories",
        action="store_true",
        help="Print the directories smclipy uses and exit.",
    )

    args: argparse.Namespace = parser.parse_args(argv)

    if args.directories:
        cmd_directories(args)
        return

    if args.command is None:
        parser.error("the following arguments are required: command")

    if args.command in _TTY_REQUIRED_COMMANDS and not sys.stdin.isatty():
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
    elif args.command == "update":
        cmd_update(args)
