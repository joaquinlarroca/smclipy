import os
from collections.abc import Iterator
from enum import Enum, auto
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.mp3 import MP3, error
from PIL import Image as PILImage

from smclipy.config import Settings, settings
from smclipy.helpers import distinct_authors, sanitize_filename, split_authors
from smclipy.storage import append_unique_lines, read_lines
from smclipy.ui import prompt_overwrite


class SaveResult(Enum):
    """Outcome of moving a tagged track into the library."""

    SAVED = auto()
    SKIPPED = auto()
    FAILED = auto()


def _title_from_id3(id3: ID3) -> str:
    for frame in id3.getall("TIT2"):
        if frame.text:
            return str(frame.text[0])
    return "Unknown"


def _artists_from_id3(id3: ID3) -> list[str]:
    artists = [str(text) for frame in id3.getall("TPE1") for text in frame.text]
    return list(dict.fromkeys(artist.strip() for artist in artists if artist.strip()))


def _single_text_from_id3(id3: ID3, frame_id: str) -> str:
    for frame in id3.getall(frame_id):
        if frame.text:
            return str(frame.text[0]).strip()
    return ""


def _album_from_id3(id3: ID3) -> str:
    return _single_text_from_id3(id3, "TALB")


def _date_from_id3(id3: ID3) -> str:
    return _single_text_from_id3(id3, "TDRC")


def _genre_from_id3(id3: ID3) -> str:
    return _single_text_from_id3(id3, "TCON")


def _album_artist_from_id3(id3: ID3) -> str:
    return _single_text_from_id3(id3, "TPE2")


def _track_number_from_id3(id3: ID3) -> str:
    return _single_text_from_id3(id3, "TRCK")


def _iter_tagged_mp3s() -> Iterator[tuple[ID3, str, list[str]]]:
    for file in settings().music_folder.glob("*.mp3"):
        if not file.is_file():
            continue
        try:
            id3 = ID3(file)
        except Exception as exc:
            print(f"Warning: could not read '{file.name}', skipping: {exc}")
            continue
        yield id3, _title_from_id3(id3), _artists_from_id3(id3)


def _library_fingerprint(s: Settings) -> str:
    files = []
    for file in s.music_folder.glob("*.mp3"):
        try:
            stat = file.stat()
        except OSError:
            continue
        files.append(f"{file.name}\0{stat.st_mtime_ns}\0{stat.st_size}")
    return repr(sorted(files))


def scan_library() -> None:
    s = settings()
    fingerprint = _library_fingerprint(s)
    try:
        previous = s.scan_state_file.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        previous = None
    if previous == fingerprint:
        return
    known_authors = read_lines(s.authors_file)
    for _id3, title, artists in _iter_tagged_mp3s():
        if not artists:
            continue
        new_authors = distinct_authors(artists, known_authors)
        if new_authors:
            append_unique_lines(s.authors_file, new_authors)
            known_authors.extend(new_authors)
        append_unique_lines(s.songs_info, [f"{title} - {', '.join(artists)}"])
    s.scan_state_file.parent.mkdir(parents=True, exist_ok=True)
    s.scan_state_file.write_text(fingerprint, encoding="utf-8")


def save_all_covers() -> None:
    s = settings()
    for id3, title, artists in _iter_tagged_mp3s():
        if not artists or not any(key.startswith("APIC") for key in id3):
            continue
        save_image(sanitize_filename(f"{artists[0]}-{title}"), id3, s.covers_folder)


def get_image_from_file(file: Path, save_to_path: Path, save_as: str) -> Path | None:
    if not file.is_file():
        return None
    try:
        file_id3: ID3 = ID3(file)
    except Exception as exc:
        print(f"Warning: could not read '{file.name}', skipping cover: {exc}")
        return None
    return save_image(sanitize_filename(save_as), file_id3, save_to_path)


def _open_audio(file: Path) -> MP3 | None:
    if not file.is_file():
        return None
    try:
        audio: MP3 = MP3(file, ID3=ID3)
    except (error, OSError):
        try:
            audio = MP3(file)
        except (error, OSError):
            return None
    if audio.tags is None:
        audio.add_tags()
    return audio


def save_song_temp_to_main(
    file: Path, image: Path | None, title: str, authors: str, album: str
) -> SaveResult:
    authors_list = split_authors(authors)
    if not authors_list:
        print("Warning: no artists provided, skipping save")
        return SaveResult.FAILED
    target_name = sanitize_filename(f"{authors_list[0]}-{title}.mp3")
    if not target_name:
        print("Warning: artist/title produced an invalid filename, skipping save")
        return SaveResult.FAILED
    target = settings().music_folder.joinpath(target_name)
    if target.is_file():
        print(f"Warning: '{target.name}' already exists")
        if not prompt_overwrite():
            return SaveResult.SKIPPED
    if not _tag_file(file, title, authors_list, album, image):
        return SaveResult.FAILED
    os.replace(str(file), str(target))
    return SaveResult.SAVED


def _tag_file(
    target: Path, title: str, authors: list[str], album: str, image: Path | None
) -> bool:
    audio = _open_audio(target)
    if audio is None:
        print(f"Warning: could not read '{target.name}', skipping save")
        return False
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"'{target.name}' has no ID3 tag stream")
    for key in list(tags.keys()):
        parent_id = key[:4]
        if parent_id in ("TIT2", "TPE1", "TALB", "APIC"):
            continue
        tags.delall(parent_id)
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=authors))
    tags.add(TALB(encoding=3, text=album))
    _set_cover(audio, image)
    audio.save()
    return True


def read_song_profile(file: Path) -> tuple[dict[str, str], bool]:
    """Read an MP3's tag profile and whether it carries embedded cover art."""
    try:
        id3 = ID3(file)
    except Exception as exc:
        print(f"Warning: could not read '{file.name}', skipping: {exc}")
        return {}, False
    return {
        "title": _title_from_id3(id3),
        "artists": ", ".join(_artists_from_id3(id3)),
        "album": _album_from_id3(id3),
        "date": _date_from_id3(id3),
        "genre": _genre_from_id3(id3),
        "album_artist": _album_artist_from_id3(id3),
        "track_number": _track_number_from_id3(id3),
    }, any(key.startswith("APIC") for key in id3)


def read_song_tags(file: Path) -> dict[str, str]:
    """Read the full tag profile of an MP3 into a field -> value mapping."""
    return read_song_profile(file)[0]


def has_cover(file: Path) -> bool:
    try:
        id3 = ID3(file)
    except Exception:
        return False
    return any(key.startswith("APIC") for key in id3)


def apply_tag_update(
    file: Path,
    *,
    title: str | None = None,
    artists: list[str] | None = None,
    album: str | None = None,
    date: str | None = None,
    genre: str | None = None,
    album_artist: str | None = None,
    track_number: str | None = None,
    image: Path | None = None,
) -> bool:
    """Apply a partial tag update, only overwriting fields that are not None."""
    audio = _open_audio(file)
    if audio is None:
        print(f"Warning: could not read '{file.name}', skipping update")
        return False
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"'{file.name}' has no ID3 tag stream")
    if title is not None:
        tags.add(TIT2(encoding=3, text=title))
    if artists is not None:
        tags.add(TPE1(encoding=3, text=artists))
    if album is not None:
        tags.add(TALB(encoding=3, text=album))
    if date is not None:
        tags.add(TDRC(encoding=3, text=date))
    if genre is not None:
        tags.add(TCON(encoding=3, text=genre))
    if album_artist is not None:
        tags.add(TPE2(encoding=3, text=album_artist))
    if track_number is not None:
        tags.add(TRCK(encoding=3, text=track_number))
    if image is not None:
        _set_cover(audio, image)
    audio.save()
    return True


def _get_image_mime(image: Path) -> str:
    with PILImage.open(image) as img:
        return PILImage.MIME.get(img.format or "", "application/octet-stream")


def _set_cover(audio: MP3, image: Path | None) -> None:
    if audio.tags is None:
        audio.add_tags()
    if image is None or not image.is_file():
        return
    tags = audio.tags
    if tags is None:
        raise RuntimeError("MP3 has no ID3 tag stream to embed cover into")
    tags.delall("APIC")
    tags.add(
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

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _mime_to_ext(mime: str) -> str:
    return _MIME_TO_EXT.get(mime, ".png")


def get_all_names() -> list[str]:
    return read_lines(settings().authors_file)


def _without_cover_extension(name: str) -> str:
    lowered = name.casefold()
    for extension in COVER_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def save_image(name: str, song_file: ID3, path: Path) -> Path | None:
    stem = _without_cover_extension(name)
    apic_key = next((key for key in song_file if key.startswith("APIC")), None)
    if not apic_key:
        print(f"Couldn't find '{stem}' cover")
        return None
    artwork = song_file[apic_key]
    ext = _mime_to_ext(artwork.mime)
    known_mime = artwork.mime in _MIME_TO_EXT
    if not known_mime:
        print(
            f"Warning: unrecognized cover mime '{artwork.mime}' for '{stem}', "
            "assuming PNG"
        )
    target = path.joinpath(f"{stem}{ext}")
    existing = next(
        (
            path.joinpath(f"{stem}{extension}")
            for extension in COVER_EXTENSIONS
            if path.joinpath(f"{stem}{extension}").is_file()
        ),
        None,
    )
    if existing is not None:
        if existing == target:
            if existing.read_bytes() == artwork.data:
                return target
            target.write_bytes(artwork.data)
            print(f"Updated '{target.name}'")
            return target
        if not known_mime:
            existing.write_bytes(artwork.data)
            print(f"Updated '{existing.name}'")
            return existing
        target.write_bytes(artwork.data)
        existing.unlink()
        print(f"Fixed '{existing.name}' -> '{target.name}'")
        return target
    print(f"Saving '{target.name}'")
    target.write_bytes(artwork.data)
    return target
