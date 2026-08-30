import os
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, ID3NoHeaderError
from mutagen.mp3 import MP3, error
from PIL import Image as PILImage

from smclipy.config import settings
from smclipy.helpers import get_list_from_split_str, sanitize_filename
from smclipy.storage import append_unique_lines, read_lines
from smclipy.ui import prompt_overwrite


def get_title(file: EasyID3) -> str:
    title_data = file.get("title")
    return (title_data if title_data else ["Unknown"])[0]


def get_artists(file: EasyID3) -> list[str]:
    authors_data = file.get("artist")
    return authors_data if authors_data else ["Unknown"]


def get_image_from_file(file: Path, save_to_path: Path, save_as: str) -> bool:
    if not file.is_file():
        return False
    try:
        file_id3: ID3 = ID3(file)
    except ID3NoHeaderError:
        return False
    return save_image(sanitize_filename(save_as), file_id3, save_to_path)


def save_song_temp_to_main(
    file: Path, image: Path, title: str, authors: str, album: str
) -> bool:
    authors_list = get_list_from_split_str(authors, "\\")
    first_author = authors_list[0]
    target = settings().music_folder.joinpath(
        sanitize_filename(f"{first_author}-{title}.mp3")
    )
    if target.is_file():
        print(f"Warning: '{target.name}' already exists")
        if not prompt_overwrite():
            return False
    _tag_file(file, title, authors_list, album, image)
    os.replace(str(file), str(target))
    return True


def _tag_file(
    target: Path, title: str, authors: list[str], album: str, image: Path
) -> None:
    try:
        audio: MP3 = MP3(target, ID3=ID3)
    except error:
        audio = MP3(target)
        audio.add_tags()

    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=authors))
    audio.tags.add(TALB(encoding=3, text=album))
    _set_cover(audio, image)
    audio.save()


def _get_image_mime(image: Path) -> str:
    with PILImage.open(image) as img:
        return PILImage.MIME.get(img.format or "", "application/octet-stream")


def _set_cover(audio: MP3, image: Path) -> None:
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    if not image.is_file():
        return
    audio.tags.delall("APIC")
    audio.tags.add(
        APIC(
            encoding=3,
            mime=_get_image_mime(image),
            type=3,
            desc="Cover",
            data=image.read_bytes(),
        )
    )


def change_cover(image: Path, file: Path) -> None:
    try:
        audio: MP3 = MP3(file, ID3=ID3)
    except (error, ID3NoHeaderError):
        audio = MP3(file)
        audio.add_tags()
    _set_cover(audio, image)
    audio.save()


def save_all_covers() -> None:
    for file in settings().music_folder.glob("*.mp3"):
        if not file.is_file():
            continue
        try:
            file_easyid3 = EasyID3(file)
            file_id3 = ID3(file)
        except ID3NoHeaderError:
            continue
        title = get_title(file_easyid3)
        artist = get_artists(file_easyid3)[0]
        save_image(
            sanitize_filename(f"{artist}-{title}.png"),
            file_id3,
            settings().covers_folder,
        )


def save_all_authors() -> None:
    for file in settings().music_folder.glob("*.mp3"):
        if not file.is_file():
            continue
        try:
            save_artist(EasyID3(file))
        except ID3NoHeaderError:
            continue


def save_all_songs_info() -> None:
    for file in settings().music_folder.glob("*.mp3"):
        if not file.is_file():
            continue
        try:
            save_songs_info(EasyID3(file))
        except ID3NoHeaderError:
            continue


def save_songs_info(song_file: EasyID3) -> None:
    authors = get_artists(song_file)
    authors_string = ", ".join(a.strip() for a in authors)
    title = get_title(song_file)
    append_unique_lines(settings().songs_info, [f"{title} - {authors_string}"])


def save_artist(song_file: EasyID3) -> None:
    append_unique_lines(settings().authors_file, get_artists(song_file))


def save_image(name_as: str, song_file: ID3, path: Path) -> bool:
    cur_path = path.joinpath(name_as)
    if cur_path.is_file():
        return False
    apic_key = next((key for key in song_file if key.startswith("APIC")), None)
    if not apic_key:
        print(f"Couldn't find '{name_as.replace('.png', '')}' cover")
        return False
    artwork = song_file[apic_key].data
    print(f"Saving '{name_as}'")
    cur_path.write_bytes(artwork)
    return True


def get_all_names() -> list[str]:
    return read_lines(settings().authors_file)
