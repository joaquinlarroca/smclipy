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


def _open_audio(file: Path) -> MP3 | None:
    if not file.is_file():
        return None
    try:
        audio: MP3 = MP3(file, ID3=ID3)
    except (error, ID3NoHeaderError, OSError):
        try:
            audio = MP3(file)
        except (error, OSError):
            return None
    if audio.tags is None:
        audio.add_tags()
    return audio


def save_song_temp_to_main(
    file: Path, image: Path, title: str, authors: str, album: str
) -> bool:
    authors_list = get_list_from_split_str(authors, "\\")
    if not authors_list:
        print("Warning: no artists provided, skipping save")
        return False
    target_name = sanitize_filename(f"{authors_list[0]}-{title}.mp3")
    if not target_name:
        print("Warning: artist/title produced an invalid filename, skipping save")
        return False
    target = settings().music_folder.joinpath(target_name)
    if target.is_file():
        print(f"Warning: '{target.name}' already exists")
        if not prompt_overwrite():
            return False
    if not _tag_file(file, title, authors_list, album, image):
        return False
    os.replace(str(file), str(target))
    return True


def _tag_file(
    target: Path, title: str, authors: list[str], album: str, image: Path
) -> bool:
    audio = _open_audio(target)
    if audio is None:
        print(f"Warning: could not read '{target.name}', skipping save")
        return False
    assert audio.tags is not None
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=authors))
    audio.tags.add(TALB(encoding=3, text=album))
    _set_cover(audio, image)
    audio.save()
    return True


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
    audio = _open_audio(file)
    if audio is None:
        print(f"Warning: could not read '{file.name}', skipping cover re-embed")
        return
    _set_cover(audio, image)
    audio.save()


COVER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _mime_to_ext(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".png")


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
        apic_key = next((key for key in file_id3 if key.startswith("APIC")), None)
        ext = _mime_to_ext(file_id3[apic_key].mime) if apic_key else ".png"
        save_image(
            sanitize_filename(f"{artist}-{title}") + ext,
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
    stem = Path(name_as).stem
    apic_key = next((key for key in song_file if key.startswith("APIC")), None)
    if not apic_key:
        print(f"Couldn't find '{stem}' cover")
        return False
    artwork = song_file[apic_key].data
    target = path.joinpath(name_as)
    existing = next(
        (
            path.joinpath(f"{stem}{ext}")
            for ext in COVER_EXTENSIONS
            if path.joinpath(f"{stem}{ext}").is_file()
        ),
        None,
    )
    if existing is not None:
        if existing == target:
            if existing.read_bytes() == artwork:
                return False
            target.write_bytes(artwork)
            print(f"Updated '{target.name}'")
            return True
        target.write_bytes(artwork)
        existing.unlink()
        print(f"Fixed '{existing.name}' -> '{target.name}'")
        return True
    print(f"Saving '{name_as}'")
    target.write_bytes(artwork)
    return True


def get_all_names() -> list[str]:
    return read_lines(settings().authors_file)
