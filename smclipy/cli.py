import argparse
import sys
from contextlib import suppress

from smclipy import __version__
from smclipy.config import CONFIG_PATH, Settings, _load_raw_config, init, settings
from smclipy.downloader import (
    VideoDownloadError,
    download,
    extract_urls,
    get_album,
    get_author,
)
from smclipy.helpers import (
    get_list_from_split_str,
    get_unique_items,
    resolve_known_authors,
)
from smclipy.images import (
    crop_image_1_to_1,
    display_image,
    is_image_1_to_1,
    is_image_pillarbox,
)
from smclipy.metadata import (
    COVER_EXTENSIONS,
    change_cover,
    get_all_names,
    get_image_from_file,
    save_all_authors,
    save_all_covers,
    save_all_songs_info,
    save_song_temp_to_main,
)
from smclipy.storage import append_unique_lines, read_lines, write_lines
from smclipy.ui import (
    prompt_album,
    prompt_authors,
    prompt_crop,
    prompt_resume,
    prompt_title,
    show_video_info,
)


def collect_urls() -> list[str]:
    print("Enter '' as url to finish inputting url's")
    urls_list: list[str] = []
    while True:
        try:
            url = input("Enter your URL: ")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            sys.exit(0)
        if url == "":
            break
        extracted_urls = extract_urls(url)
        if not extracted_urls:
            print(f"Warning: no recognizable video URLs found in '{url}'")
        else:
            urls_list.extend(extracted_urls)
    return urls_list


def process_video(url: str, authors_list: list[str]) -> bool:
    s = settings()
    temp_mp3 = s.temp_folder.joinpath("temp.mp3")
    temp_png = s.temp_folder.joinpath("temp.png")

    _cleanup_temp_files(s)

    info_dictionary = download(url)
    has_cover = get_image_from_file(temp_mp3, s.temp_folder, "temp.png")

    if has_cover:
        print("\n\n")
        display_image(temp_png)

        if is_image_1_to_1(temp_png):
            print("Already 1:1")
        elif prompt_crop(default="False"):
            crop_image_1_to_1(temp_png)

    print("\n\n")
    title = prompt_title(default=str(info_dictionary.get("title", "")))
    while not title.strip():
        print("Title cannot be empty.")
        title = prompt_title(default="")
    title = title.strip()

    print("\n\n")
    album = prompt_album(default=get_album(info_dictionary))

    print("\n\n")
    show_video_info(info_dictionary)

    yt_artists = get_author(info_dictionary)
    authors_default = "\\".join(resolve_known_authors(yt_artists, authors_list))
    authors = prompt_authors(authors_list, default=authors_default)
    while not get_list_from_split_str(authors, "\\"):
        print("At least one artist is required.")
        authors = prompt_authors(authors_list, default="")

    current_authors_list = get_list_from_split_str(authors, "\\")
    for item in get_unique_items(current_authors_list, authors_list):
        authors_list.append(item)

    if save_song_temp_to_main(temp_mp3, temp_png, title, authors, album):
        append_unique_lines(s.authors_file, current_authors_list)
        append_unique_lines(
            s.songs_info, [f"{title} - {', '.join(current_authors_list)}"]
        )
        return True
    return False


def filter_processed_ids(pending_ids: list[str], processed_ids: list[str]) -> list[str]:
    processed_set = set(processed_ids)
    return [video_id for video_id in pending_ids if video_id not in processed_set]


def _collect_new_queue(s: Settings) -> tuple[list[str], list[str]]:
    pending_ids = collect_urls()
    write_lines(s.pending_ids_file, pending_ids)
    write_lines(s.processed_ids_file, [])
    return pending_ids, []


def _cleanup_temp_files(s: Settings) -> None:
    temp_folder = s.temp_folder
    if not temp_folder.is_dir():
        return
    for temp_file in temp_folder.glob("temp.*"):
        with suppress(OSError):
            temp_file.unlink()


def cmd_download(args: argparse.Namespace) -> None:
    s = init()

    print("Scanning music files...")
    save_all_authors()
    save_all_songs_info()

    print("Fetching all authors names...")
    authors_list: list[str] = get_all_names()

    pending_ids = read_lines(s.pending_ids_file)
    processed_ids = read_lines(s.processed_ids_file)
    remaining_ids = filter_processed_ids(pending_ids, processed_ids)

    if remaining_ids and prompt_resume():
        pending_ids, processed_ids = remaining_ids, []
        write_lines(s.pending_ids_file, pending_ids)
        write_lines(s.processed_ids_file, [])
    else:
        pending_ids, processed_ids = _collect_new_queue(s)

    print(f"Preparing to download {len(pending_ids)} vid/s")
    skipped_ids: list[str] = []
    try:
        for video_id in pending_ids:
            try:
                saved = process_video(video_id, authors_list)
            except VideoDownloadError as exc:
                print(f"Skipping '{video_id}': {exc}")
                skipped_ids.append(video_id)
                continue
            except KeyboardInterrupt:
                print("\nInterrupted, exiting...")
                break
            except Exception as exc:
                print(f"Skipping '{video_id}': unexpected error: {exc!r}")
                skipped_ids.append(video_id)
                continue
            if not saved:
                skipped_ids.append(video_id)
                continue
            try:
                append_unique_lines(s.processed_ids_file, [video_id])
                processed_ids.append(video_id)
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
            f"Finished with {len(skipped_ids)} skipped vid/s "
            f"(still pending for a retry): {skipped_ids}"
        )


def cmd_crop(args: argparse.Namespace) -> None:
    s = init()

    print("Scanning music files...")
    save_all_covers()

    print(f"Reading {s.false_positives_file.name}...")
    false_positives = read_lines(s.false_positives_file)

    print("Scanning images files...")
    cover_patterns = tuple(f"*{ext}" for ext in COVER_EXTENSIONS)
    cover_files = [
        file
        for pattern in cover_patterns
        for file in s.covers_folder.glob(pattern)
        if file.is_file()
        and file.stem.strip() not in false_positives
        and is_image_pillarbox(file)
    ]

    if not cover_files:
        print("No images to crop...")
    else:
        for cover in cover_files:
            filename = cover.stem
            display_image(cover)
            if prompt_crop(default="True"):
                crop_image_1_to_1(cover)
                mp3_file = s.music_folder.joinpath(f"{filename}.mp3")
                if mp3_file.is_file():
                    change_cover(cover, mp3_file)
                else:
                    print(f"{filename}: MP3 not found, skipping cover re-embed")
            else:
                false_positives.append(filename)
            print("\n\n")
    append_unique_lines(s.false_positives_file, false_positives)


def cmd_directories(args: argparse.Namespace) -> None:
    s = Settings(_load_raw_config())
    print(f"Config dir:  {CONFIG_PATH.parent.resolve()}")
    print(f"Music:       {s.music_folder.resolve()}")
    print(f"Library:     {s.script_folder.resolve()}")
    print(f"Temp:        {s.temp_folder.resolve()}")
    print(f"Covers:      {s.covers_folder.resolve()}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="smclipy",
        description="Download, tag, and organize music from YouTube and SoundCloud.",
        epilog=(
            "examples:\n"
            "  smclipy download   Batch download songs and tag them interactively\n"
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

    if args.command in ("download", "crop") and not sys.stdin.isatty():
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
