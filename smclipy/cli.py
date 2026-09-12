import argparse
import sys
from contextlib import suppress
from pathlib import Path

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
    change_cover,
    get_all_names,
    get_image_from_file,
    save_all_covers,
    save_song_temp_to_main,
    scan_library,
)
from smclipy.storage import append_unique_lines, read_lines, write_lines
from smclipy.tag import TAG_COVER_PREFIX, cmd_tag
from smclipy.ui import (
    prompt_album,
    prompt_authors,
    prompt_crop,
    prompt_resume,
    prompt_title,
    show_video_info,
)


def _save_queue_state(s: Settings, pending_ids: list[str]) -> None:
    write_lines(s.pending_ids_file, pending_ids)
    write_lines(s.processed_ids_file, [])


def collect_urls(s: Settings) -> list[str]:
    print("Enter '' as url to finish inputting urls")
    urls_list: list[str] = []
    while True:
        try:
            url = input("Enter your URL: ")
        except (KeyboardInterrupt, EOFError):
            print("\nInterrupted while collecting URLs...")
            _save_queue_state(s, urls_list)
            if urls_list:
                print(
                    f"Saved partial queue of {len(urls_list)} url/s for a later resume."
                )
            raise SystemExit(130) from None
        if url == "":
            break
        extracted_urls = extract_urls(url)
        if not extracted_urls:
            print(f"Warning: no recognizable video URLs found in '{url}'")
        else:
            urls_list.extend(url for url in extracted_urls if url not in urls_list)
    return urls_list


def process_video(url: str, authors_list: list[str]) -> SaveResult:
    s = settings()
    stem = temp_stem(url)
    temp_mp3 = s.temp_folder.joinpath(f"{stem}.mp3")

    _cleanup_temp_files(s)

    info_dictionary = download(url, stem)
    cover_path = get_image_from_file(temp_mp3, s.temp_folder, stem)

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
    title = prompt_title(default=default_title)
    while not title.strip():
        print("Title cannot be empty.")
        title = prompt_title(default=default_title)
    title = title.strip()

    album = prompt_album(default=get_album(info_dictionary))

    yt_artists = get_author(info_dictionary)
    authors_default = "\\".join(resolve_known_authors(yt_artists, authors_list))
    authors = prompt_authors(authors_list, default=authors_default)
    while not split_authors(authors):
        print("At least one artist is required.")
        authors = prompt_authors(authors_list, default=authors_default)

    current_authors_list = split_authors(authors)
    new_authors = distinct_authors(current_authors_list, authors_list)
    authors_list.extend(new_authors)

    result = save_song_temp_to_main(temp_mp3, cover_path, title, authors, album)
    if result is SaveResult.SAVED:
        append_unique_lines(s.authors_file, new_authors)
        append_unique_lines(
            s.songs_info, [f"{title} - {', '.join(current_authors_list)}"]
        )
    return result


def filter_processed_ids(pending_ids: list[str], processed_ids: list[str]) -> list[str]:
    processed_set = set(processed_ids)
    return [video_id for video_id in pending_ids if video_id not in processed_set]


def _collect_new_queue(s: Settings) -> list[str]:
    pending_ids = collect_urls(s)
    _save_queue_state(s, pending_ids)
    return pending_ids


_TEMP_FILE_PREFIXES = ("temp.", *TEMP_STEM_PREFIXES, TAG_COVER_PREFIX)


def _cleanup_temp_files(s: Settings) -> None:
    temp_folder = s.temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob("*"):
        name = temp_file.name
        if name.startswith(_TEMP_FILE_PREFIXES):
            with suppress(OSError):
                temp_file.unlink()


def cmd_download(_args: argparse.Namespace) -> None:
    s = init()

    print("Scanning music files...")
    scan_library()

    print("Fetching all authors names...")
    authors_list: list[str] = get_all_names()

    pending_ids = read_lines(s.pending_ids_file)
    remaining_ids = filter_processed_ids(pending_ids, read_lines(s.processed_ids_file))

    if remaining_ids and prompt_resume():
        pending_ids = remaining_ids
        _save_queue_state(s, pending_ids)
    else:
        pending_ids = _collect_new_queue(s)

    print(f"Preparing to download {len(pending_ids)} vid/s")
    skipped_ids: list[str] = []
    interrupted = False
    try:
        total = len(pending_ids)
        for index, video_id in enumerate(pending_ids, start=1):
            print(f"\n[{index}/{total}] {video_id}")
            try:
                result = process_video(video_id, authors_list)
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
                append_unique_lines(s.processed_ids_file, [video_id])
            except OSError as exc:
                print(f"Warning: could not record '{video_id}' as processed: {exc!r}")
                skipped_ids.append(video_id)
    finally:
        _cleanup_temp_files(s)

    if skipped_ids:
        try:
            append_unique_lines(s.pending_ids_file, skipped_ids)
        except OSError as exc:
            print(f"Warning: could not persist skipped videos for retry: {exc!r}")
        print(
            f"{'Interrupted' if interrupted else 'Finished'} with "
            f"{len(skipped_ids)} skipped vid/s "
            f"(still pending for a retry): {skipped_ids}"
        )

    if interrupted:
        raise SystemExit(130) from None


def cmd_crop(_args: argparse.Namespace) -> None:
    s = init()

    print("Scanning music files...")
    save_all_covers()

    print(f"Reading {s.false_positives_file.name}...")
    false_positives = read_lines(s.false_positives_file)

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
            except Exception as exc:
                print(f"Warning: could not analyze '{file.name}', skipping: {exc!r}")

    if not cover_files:
        print("No images to crop...")
    else:
        for cover in cover_files:
            filename = cover.stem
            try:
                display_image(cover)
                if prompt_crop(default=True):
                    crop_image_1_to_1(cover)
                    mp3_file = s.music_folder.joinpath(f"{filename}.mp3")
                    if mp3_file.is_file():
                        change_cover(cover, mp3_file)
                    else:
                        print(f"{filename}: MP3 not found, skipping cover re-embed")
                else:
                    false_positives.append(filename)
                    append_unique_lines(s.false_positives_file, [filename])
            except Exception as exc:
                print(f"Warning: could not process '{filename}': {exc!r}")
            print("\n\n")


def cmd_directories(args: argparse.Namespace) -> None:
    config_exists = CONFIG_PATH.exists()
    s = Settings(_load_raw_config(create_if_missing=False))
    if not config_exists:
        print(f"Using default configuration (no config file at {CONFIG_PATH}).")
        print("Run `smclipy download` to generate one.")
        print()
    fields = [
        ("Config dir:", str(CONFIG_PATH.parent.resolve())),
        ("Music:", str(s.music_folder.resolve())),
        ("Library:", str(s.script_folder.resolve())),
        ("Temp:", str(s.temp_folder.resolve())),
        ("Covers:", str(s.covers_folder.resolve())),
        ("Authors:", str(s.authors_file.resolve())),
        ("Songs info:", str(s.songs_info.resolve())),
    ]
    for label, value in fields:
        print(f"{label:<13}{value}")


_TTY_REQUIRED_COMMANDS = frozenset({"download", "crop", "tag"})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="smclipy",
        description="Download, tag, and organize music from YouTube and SoundCloud.",
        epilog=(
            "examples:\n"
            "  smclipy download   Batch download songs and tag them interactively\n"
            "  smclipy tag        Retag library songs using MusicBrainz metadata\n"
            "  smclipy crop       Crop pillarboxed cover images and re-embed them\n"
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
    subparsers = parser.add_subparsers(dest="command", metavar="command")

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
            "interactively, and re-embed each result into its matching MP3."
        ),
    )

    subparsers.add_parser(
        "tag",
        help="Retag library songs using MusicBrainz metadata.",
        description=(
            "List every song in the library, fetch matching metadata from "
            "MusicBrainz, and interactively review and apply title, artist, album, "
            "release date, track number, album artist, and cover art changes."
        ),
    )

    parser.add_argument(
        "-d",
        "--directories",
        action="store_true",
        help="Print the directories smclipy uses and exit.",
    )

    args = parser.parse_args(argv)

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
