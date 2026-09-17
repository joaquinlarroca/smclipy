"""Per-format audio tag adapters for the supported library formats.

Every song stores the same tag profile (title, artists, album, date, genre,
album_artist, track_number, cover art) plus an internal tracking UUID. This
module maps that common profile onto each format's native tag machinery:

- MP3  -> ID3: TIT2/TPE1/TALB/TDRC/TCON/TPE2/TRCK + APIC cover + UFID uuid
- M4A  -> MP4: (:copyright:nam/:copyright:ART/:copyright:alb/:copyright:day
  /:copyright:gen/aART/trkn) + covr cover + freeform 'smclipy-uuid'
- FLAC/OPUS/OGG -> Vorbis comments (TITLE/ARTIST/ALBUM/DATE/GENRE/
  ALBUMARTIST/TRACKNUMBER) + a PICTURE block (FLAC) or METADATA_BLOCK_PICTURE
  base64 comment (OGG) + a SMCLIPY_UUID comment
"""

import base64
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, UFID
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from PIL import Image as PILImage

UFID_OWNER = "smclipy"
MP4_UUID_ATOM = "----:com.apple.iTunes:smclipy-uuid"
VORBIS_UUID_FIELD = "SMCLIPY_UUID"

SUPPORTED_FORMATS: tuple[str, ...] = ("mp3", "m4a", "flac", "opus", "ogg")
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(f".{fmt}" for fmt in SUPPORTED_FORMATS)
FORMAT_TO_NATIVE_CODEC: dict[str, str] = {
    "mp3": "mp3",
    "m4a": "m4a",
    "flac": "flac",
    "opus": "opus",
    "ogg": "vorbis",
}

_EXTENSION_TO_FORMAT: dict[str, str] = {f".{fmt}": fmt for fmt in SUPPORTED_FORMATS}

_MP3_TEXT_KEY: dict[str, str] = {
    "title": "TIT2",
    "album": "TALB",
    "date": "TDRC",
    "genre": "TCON",
    "album_artist": "TPE2",
    "track_number": "TRCK",
}
_MP3_TEXT_CLASS: dict[str, Any] = {
    "title": TIT2,
    "album": TALB,
    "date": TDRC,
    "genre": TCON,
    "album_artist": TPE2,
    "track_number": TRCK,
}
_MP4_TEXT_FIELD: dict[str, str] = {
    "title": "\xa9nam",
    "album": "\xa9alb",
    "date": "\xa9day",
    "genre": "\xa9gen",
    "album_artist": "aART",
}
_VORBIS_TEXT_FIELD: dict[str, str] = {
    "title": "TITLE",
    "album": "ALBUM",
    "date": "DATE",
    "genre": "GENRE",
    "album_artist": "ALBUMARTIST",
    "track_number": "TRACKNUMBER",
}

_MP4_COVER_JPEG = 13
_MP4_COVER_PNG = 14


def audio_ext(audio_format: str) -> str:
    """File extension (with dot) for a configured audio format."""
    if audio_format in SUPPORTED_FORMATS:
        return f".{audio_format}"
    return ".mp3"


def format_of(file: Path) -> str | None:
    return _EXTENSION_TO_FORMAT.get(file.suffix.casefold())


def image_mime(image: Path) -> str:
    with PILImage.open(image) as img:
        return PILImage.MIME.get(img.format or "", "application/octet-stream")


def _first_text(values: Any) -> str:
    if isinstance(values, (list, tuple)):
        values = values[0] if values else ""
    if values is None:
        return ""
    return str(values).strip()


def _mp4_track_str(vals: Any) -> str:
    if not vals:
        return ""
    track, total = vals[0][0], vals[0][1]
    if not track:
        return ""
    return f"{track}/{total}" if total else str(track)


def _parse_track_number(value: str) -> tuple[int, int]:
    text: str = value.strip()
    if "/" in text:
        track, _, total = text.partition("/")
        return (
            int(track) if track.isdigit() else 0,
            int(total) if total.isdigit() else 0,
        )
    return (int(text) if text.isdigit() else 0, 0)


def _build_picture(mime: str, data: bytes) -> Picture:
    picture = Picture()
    picture.type = 3
    picture.mime = mime
    picture.data = data
    return picture


def _sniff_image_mime(data: bytes) -> str:
    """Best-effort MIME for raw image bytes, defaulting to image/png."""
    try:
        with PILImage.open(BytesIO(data)) as image:
            return PILImage.MIME.get(image.format or "", "image/png")
    except Exception:
        return "image/png"


def _cover_atom(mime: str, data: bytes) -> Any:
    if mime == "image/jpeg":
        return MP4Cover(data, _MP4_COVER_JPEG)
    if mime == "image/png":
        return MP4Cover(data, _MP4_COVER_PNG)
    # MP4 covers officially support only JPEG/PNG; transcode anything else
    # (e.g. WebP/GIF from extracted art) to PNG rather than tagging the atom
    # with the wrong raster format.
    with PILImage.open(BytesIO(data)) as image:
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return MP4Cover(buffer.getvalue(), _MP4_COVER_PNG)


class Track:
    """A format-generic handle to an audio file's tags."""

    def __init__(self, audio_format: str, audio: Any, name: str) -> None:
        self.audio_format = audio_format
        self.name = name
        self._audio = audio

    @property
    def tags(self) -> Any:
        return self._audio.tags

    @classmethod
    def open(cls, file: Path) -> "Track | None":
        audio_format: str | None = format_of(file)
        if audio_format is None or not file.is_file():
            return None
        try:
            if audio_format == "mp3":
                audio: Any = MP3(file, ID3=ID3)
            elif audio_format == "m4a":
                audio = MP4(file)
            elif audio_format == "flac":
                audio = FLAC(file)
            elif audio_format == "opus":
                audio = OggOpus(file)
            else:
                audio = OggVorbis(file)
        except Exception:
            return None
        if audio.tags is None:
            audio.add_tags()
        return cls(audio_format, audio, file.name)

    def save(self) -> None:
        self._audio.save()

    def read_profile(self) -> tuple[dict[str, str], bool]:
        profile: dict[str, str] = {}
        for field in (
            "title",
            "album",
            "date",
            "genre",
            "album_artist",
            "track_number",
        ):
            profile[field] = self.read_text(field)
        profile["title"] = profile["title"] or "Unknown"
        profile["artists"] = ", ".join(self.read_artists())
        return profile, self.has_cover()

    def read_text(self, field: str) -> str:
        if self.audio_format == "mp3":
            return self._read_id3_text(self.tags, _MP3_TEXT_KEY[field])
        if self.audio_format == "m4a":
            if field == "track_number":
                return _mp4_track_str(self.tags.get("trkn"))
            return _first_text(self.tags.get(_MP4_TEXT_FIELD[field]))
        return _first_text(self.tags.get(_VORBIS_TEXT_FIELD[field]))

    def read_artists(self) -> list[str]:
        if self.audio_format == "mp3":
            raw: list[Any] = [
                str(text) for frame in self.tags.getall("TPE1") for text in frame.text
            ]
        elif self.audio_format == "m4a":
            raw = list(self.tags.get("\xa9ART", []))
        else:
            raw = list(self.tags.get("ARTIST", []))
        return list(dict.fromkeys(str(a).strip() for a in raw if str(a).strip()))

    def set_field(self, field: str, value: str) -> None:
        if self.audio_format == "mp3":
            self.tags.add(_MP3_TEXT_CLASS[field](encoding=3, text=value))
        elif self.audio_format == "m4a":
            if field == "track_number":
                self.tags["trkn"] = [_parse_track_number(value)]
            else:
                self.tags[_MP4_TEXT_FIELD[field]] = [value]
        else:
            self.tags[_VORBIS_TEXT_FIELD[field]] = [value]

    def set_artists(self, artists: list[str]) -> None:
        if self.audio_format == "mp3":
            self.tags.add(TPE1(encoding=3, text=artists))
        elif self.audio_format == "m4a":
            self.tags["\xa9ART"] = artists
        else:
            self.tags["ARTIST"] = artists

    def read_uuid(self) -> str | None:
        if self.audio_format == "mp3":
            for frame in self.tags.getall("UFID"):
                if getattr(frame, "owner", None) == UFID_OWNER and frame.data:
                    try:
                        return str(uuid.UUID(bytes=frame.data))
                    except (ValueError, AttributeError):
                        return None
            return None
        if self.audio_format == "m4a":
            for frame in self.tags.get(MP4_UUID_ATOM, []):
                try:
                    return str(uuid.UUID(bytes(frame).decode("utf-8").strip()))
                except (ValueError, AttributeError, TypeError, UnicodeDecodeError):
                    return None
            return None
        value: str = _first_text(self.tags.get(VORBIS_UUID_FIELD))
        if not value:
            return None
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError):
            return None

    def write_uuid(self, song_uuid: str) -> None:
        if self.audio_format == "mp3":
            for key in list(self.tags.keys()):
                if (
                    key.startswith("UFID")
                    and getattr(self.tags[key], "owner", None) == UFID_OWNER
                ):
                    del self.tags[key]
            self.tags.add(UFID(owner=UFID_OWNER, data=uuid.UUID(song_uuid).bytes))
        elif self.audio_format == "m4a":
            if MP4_UUID_ATOM in self.tags:
                del self.tags[MP4_UUID_ATOM]
            self.tags[MP4_UUID_ATOM] = [
                MP4FreeForm(data=song_uuid.encode("utf-8"), dataformat=1)
            ]
        else:
            self.tags[VORBIS_UUID_FIELD] = [song_uuid]

    def strip_unknown_tags(self) -> None:
        ".mp3-only: drop every ID3 frame except the ones smclipy owns."
        if self.audio_format != "mp3":
            return
        for key in list(self.tags.keys()):
            parent_id: str = key[:4]
            if parent_id in ("TIT2", "TPE1", "TALB", "APIC", "UFID"):
                continue
            self.tags.delall(parent_id)

    def has_cover(self) -> bool:
        if self.audio_format == "mp3":
            return any(key.startswith("APIC") for key in self.tags)
        if self.audio_format == "m4a":
            return bool(self.tags.get("covr"))
        if self.audio_format == "flac":
            return bool(self._audio.pictures)
        return bool(self.tags.get("METADATA_BLOCK_PICTURE"))

    def read_cover(self) -> tuple[str, bytes] | None:
        if self.audio_format == "mp3":
            for key in self.tags:
                if key.startswith("APIC"):
                    frame = self.tags[key]
                    return frame.mime, frame.data
            return None
        if self.audio_format == "m4a":
            covers: list[Any] = self.tags.get("covr", [])
            if not covers:
                return None
            data = bytes(covers[0])
            # The declared atom type (JPEG/PNG enum) is often wrong in the wild
            # (e.g. PNG bytes flagged as JPEG); trust the actual image bytes.
            return _sniff_image_mime(data), data
        if self.audio_format == "flac":
            pictures: list[Picture] = self._audio.pictures
            if not pictures:
                return None
            return pictures[0].mime, pictures[0].data
        picture_data = self.tags.get("METADATA_BLOCK_PICTURE", [])
        if not picture_data:
            return None
        try:
            picture: Picture = Picture(base64.b64decode(picture_data[0]))
        except Exception:
            return None
        return picture.mime, picture.data

    def set_cover(self, image: Path | None) -> None:
        if image is None or not image.is_file():
            return
        mime: str = image_mime(image)
        data: bytes = image.read_bytes()
        self._clear_cover()
        if self.audio_format == "mp3":
            self.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        elif self.audio_format == "m4a":
            self.tags["covr"] = [_cover_atom(mime, data)]
        elif self.audio_format == "flac":
            self._audio.add_picture(_build_picture(mime, data))
        else:
            self.tags["METADATA_BLOCK_PICTURE"] = [
                base64.b64encode(_build_picture(mime, data).write()).decode("ascii")
            ]

    def _clear_cover(self) -> None:
        if self.audio_format == "mp3":
            self.tags.delall("APIC")
        elif self.audio_format == "m4a":
            if "covr" in self.tags:
                del self.tags["covr"]
        elif self.audio_format == "flac":
            self._audio.clear_pictures()
        else:
            if "METADATA_BLOCK_PICTURE" in self.tags:
                del self.tags["METADATA_BLOCK_PICTURE"]

    @staticmethod
    def _read_id3_text(tags: Any, key: str) -> str:
        for frame in tags.getall(key):
            if frame.text:
                return str(frame.text[0]).strip()
        return ""


def open_for_tagging(file: Path) -> Track | None:
    """Open a file for library scanning.

    MP3 files without an ID3 header are treated as unreadable, mirroring the
    legacy behavior where the scan read tags with ``ID3(file)`` directly and
    skipped every headerless MP3.
    """
    if format_of(file) == "mp3":
        try:
            ID3(file)
        except Exception:
            return None
    return Track.open(file)
