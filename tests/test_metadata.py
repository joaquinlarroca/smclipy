import io

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from mutagen.mp3 import MP3
from PIL import Image

from smclipy.metadata import (
    _get_image_mime,
    _set_cover,
    get_artists,
    get_image_from_file,
    get_title,
    save_all_authors,
    save_all_covers,
    save_all_songs_info,
    save_artist,
    save_image,
    save_song_temp_to_main,
    save_songs_info,
)
from smclipy.storage import read_lines

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Foo", artist="Artist") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio.save(path)


def make_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return buf.getvalue()


def make_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "JPEG")
    return buf.getvalue()


def test_get_title():
    assert get_title({"title": ["Foo"]}) == "Foo"


def test_get_title_unknown():
    assert get_title({}) == "Unknown"


def test_get_artists():
    assert get_artists({"artist": ["A", "B"]}) == ["A", "B"]


def test_get_artists_unknown():
    assert get_artists({}) == ["Unknown"]


def test_save_artist_appends_unique(app_settings):
    save_artist({"artist": ["Artist A", "Artist B"]})
    save_artist({"artist": ["Artist A", "Artist C"]})
    assert read_lines(app_settings.authors_file) == ["Artist A", "Artist B", "Artist C"]


def test_save_songs_info_formats_line(app_settings):
    save_songs_info({"artist": ["A", "B"], "title": ["Song"]})
    assert read_lines(app_settings.songs_info) == ["Song - A, B"]


def test_save_image_writes_artwork(app_settings, tmp_path):
    class FakeArt:
        data = b"image-bytes"

    song_file = {"APIC:FrontCover": FakeArt()}
    saved = save_image("artist-title.png", song_file, tmp_path)
    assert saved is True
    assert (tmp_path / "artist-title.png").read_bytes() == b"image-bytes"


def test_save_image_skips_existing(app_settings, tmp_path):
    class FakeArt:
        data = b"image-bytes"

    song_file = {"APIC:FrontCover": FakeArt()}
    target = tmp_path / "artist-title.png"
    target.write_bytes(b"old")
    assert save_image("artist-title.png", song_file, tmp_path) is False
    assert target.read_bytes() == b"old"


def test_save_image_missing_cover(app_settings, tmp_path):
    assert save_image("artist-title.png", {}, tmp_path) is False


def test_save_all_authors_skips_untagged(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "tagged.mp3", title="Song", artist="Artist A")
    (music / "untagged.mp3").write_bytes(MINIMAL_MP3)
    (music / "garbage.mp3").write_bytes(b"not an mp3 at all")
    save_all_authors()
    assert read_lines(app_settings.authors_file) == ["Artist A"]


def test_save_all_songs_info_skips_untagged(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "tagged.mp3", title="Song", artist="Artist A")
    (music / "untagged.mp3").write_bytes(MINIMAL_MP3)
    (music / "garbage.mp3").write_bytes(b"not an mp3 at all")
    save_all_songs_info()
    assert read_lines(app_settings.songs_info) == ["Song - Artist A"]


def test_save_all_covers_skips_untagged_and_saves_tagged(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    tagged = music / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=make_png_bytes())
    )
    img.save()
    (music / "untagged.mp3").write_bytes(MINIMAL_MP3)
    save_all_covers()
    cover = app_settings.covers_folder / "Artist A-Song.png"
    assert cover.is_file()
    assert cover.read_bytes() == make_png_bytes()


def test_get_image_from_file_missing_file(app_settings, tmp_path):
    assert get_image_from_file(tmp_path / "missing.mp3", tmp_path, "cover.png") is False


def test_get_image_from_file_untagged_returns_false(app_settings, tmp_path):
    untagged = tmp_path / "untagged.mp3"
    untagged.write_bytes(MINIMAL_MP3)
    assert get_image_from_file(untagged, tmp_path, "cover.png") is False


def test_get_image_from_file_saves_cover(app_settings, tmp_path):
    tagged = tmp_path / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=make_png_bytes())
    )
    img.save()
    assert get_image_from_file(tagged, tmp_path, "cover.png") is True
    assert (tmp_path / "cover.png").read_bytes() == make_png_bytes()


def test_get_image_mime_detects_format(tmp_path):
    png = tmp_path / "img.png"
    png.write_bytes(make_png_bytes())
    jpg = tmp_path / "img.jpg"
    jpg.write_bytes(make_jpeg_bytes())
    assert _get_image_mime(png) == "image/png"
    assert _get_image_mime(jpg) == "image/jpeg"


def test_set_cover_uses_detected_mime(tmp_path):
    mp3_file = tmp_path / "cover.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    jpg = tmp_path / "cover.jpg"
    jpg.write_bytes(make_jpeg_bytes())
    audio = MP3(mp3_file, ID3=ID3)
    _set_cover(audio, jpg)
    audio.save()
    apic = ID3(mp3_file).getall("APIC")
    assert len(apic) == 1
    assert apic[0].mime == "image/jpeg"
    assert apic[0].data == make_jpeg_bytes()


def test_set_cover_missing_image_does_not_add_apic(tmp_path):
    mp3_file = tmp_path / "cover.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    audio = MP3(mp3_file, ID3=ID3)
    _set_cover(audio, tmp_path / "missing.png")
    assert not audio.tags.getall("APIC")


def test_save_song_temp_to_main_tags_then_moves(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    save_song_temp_to_main(source, image, "Song", "Artist A", "Great Album")

    assert not source.exists()
    target = app_settings.music_folder / "Artist A-Song.mp3"
    assert target.is_file()
    tags = EasyID3(target)
    assert tags["title"] == ["Song"]
    assert tags["artist"] == ["Artist A"]
    assert tags["album"] == ["Great Album"]
    assert ID3(target).getall("APIC")


def test_save_song_temp_to_main_warns_on_collision(
    app_settings, tmp_path, capsys, monkeypatch
):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    monkeypatch.setattr("smclipy.metadata.prompt_overwrite", lambda: True)

    save_song_temp_to_main(source, image, "Song", "Artist A", "Album")
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    save_song_temp_to_main(source, image, "Song", "Artist A", "Album")

    assert "already exists" in capsys.readouterr().out


def test_save_song_temp_to_main_cancel_keeps_existing(
    app_settings, tmp_path, monkeypatch
):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist A-Song.mp3"
    target.write_bytes(b"original")

    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    monkeypatch.setattr("smclipy.metadata.prompt_overwrite", lambda: False)

    saved = save_song_temp_to_main(source, image, "Song", "Artist A", "Album")

    assert saved is False
    assert target.read_bytes() == b"original"
    assert source.exists()
