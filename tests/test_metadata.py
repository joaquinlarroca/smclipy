import io

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from mutagen.mp3 import MP3
from PIL import Image

from smclipy.metadata import (
    SaveResult,
    _get_image_mime,
    _mime_to_ext,
    _open_audio,
    _set_cover,
    _tag_file,
    change_cover,
    get_image_from_file,
    save_all_covers,
    save_image,
    save_song_temp_to_main,
    scan_library,
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


def test_get_image_from_file_missing_file(tmp_path):
    assert get_image_from_file(tmp_path / "missing.mp3", tmp_path, "cover.png") is None


def test_get_image_from_file_untagged_returns_none(tmp_path):
    untagged = tmp_path / "untagged.mp3"
    untagged.write_bytes(MINIMAL_MP3)
    assert get_image_from_file(untagged, tmp_path, "cover.png") is None


def test_get_image_from_file_saves_cover(tmp_path):
    tagged = tmp_path / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=make_png_bytes())
    )
    img.save()
    saved = get_image_from_file(tagged, tmp_path, "cover.png")
    assert saved == tmp_path / "cover.png"
    assert (tmp_path / "cover.png").read_bytes() == make_png_bytes()


def test_get_image_from_file_uses_real_mime_extension(tmp_path):
    tagged = tmp_path / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=make_jpeg_bytes(),
        )
    )
    img.save()
    saved = get_image_from_file(tagged, tmp_path, "temp")
    assert saved == tmp_path / "temp.jpg"
    assert (tmp_path / "temp.jpg").read_bytes() == make_jpeg_bytes()


def test_get_image_from_file_warns_on_corrupt_file(tmp_path, capsys):
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(b"not an mp3 at all")
    assert get_image_from_file(corrupt, tmp_path, "cover.png") is None
    assert not (tmp_path / "cover.png").exists()
    assert "could not read" in capsys.readouterr().out


def test_scan_library_adds_authors_and_songs(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")
    write_tagged_mp3(music / "two.mp3", title="Other", artist="Artist A")
    write_tagged_mp3(music / "three.mp3", title="Third", artist="Artist B")
    scan_library()
    assert sorted(read_lines(app_settings.authors_file)) == ["Artist A", "Artist B"]
    assert sorted(read_lines(app_settings.songs_info)) == [
        "Other - Artist A",
        "Song - Artist A",
        "Third - Artist B",
    ]


def test_scan_library_skips_untagged_and_corrupt(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "tagged.mp3", title="Song", artist="Artist A")
    (music / "untagged.mp3").write_bytes(MINIMAL_MP3)
    (music / "garbage.mp3").write_bytes(b"not an mp3 at all")
    scan_library()
    assert read_lines(app_settings.authors_file) == ["Artist A"]
    assert read_lines(app_settings.songs_info) == ["Song - Artist A"]


def test_scan_library_skips_when_library_unchanged(app_settings, monkeypatch):
    import smclipy.metadata as metadata

    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")

    calls: list[int] = []
    real = metadata._iter_tagged_mp3s

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(metadata, "_iter_tagged_mp3s", counting)

    scan_library()
    scan_library()

    assert len(calls) == 1
    assert read_lines(app_settings.authors_file) == ["Artist A"]


def test_scan_library_rescans_when_library_changed(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")
    scan_library()

    write_tagged_mp3(music / "two.mp3", title="Other", artist="Artist B")
    scan_library()

    assert sorted(read_lines(app_settings.authors_file)) == ["Artist A", "Artist B"]


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


def test_save_all_covers_uses_real_format_extension(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    tagged = music / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=make_jpeg_bytes(),
        )
    )
    img.save()
    save_all_covers()
    assert (app_settings.covers_folder / "Artist A-Song.jpg").is_file()
    assert not (app_settings.covers_folder / "Artist A-Song.png").exists()


def test_save_image_writes_artwork(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/png"

    saved = save_image("artist-title.png", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "artist-title.png"
    assert (tmp_path / "artist-title.png").read_bytes() == b"image-bytes"


def test_save_image_updates_existing_cover_when_changed(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/png"

    target = tmp_path / "artist-title.png"
    target.write_bytes(b"old")
    saved = save_image("artist-title.png", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "artist-title.png"
    assert target.read_bytes() == b"image-bytes"


def test_save_image_missing_cover(tmp_path):
    assert save_image("artist-title.png", {}, tmp_path) is None


def test_save_image_uses_actual_mime_extension(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/jpeg"

    saved = save_image("cover", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "cover.jpg"


def test_save_image_relabels_mislabeled_existing_cover(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/png"

    (tmp_path / "artist-title.jpg").write_bytes(b"old")
    saved = save_image("artist-title.png", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "artist-title.png"
    assert not (tmp_path / "artist-title.jpg").exists()
    assert (tmp_path / "artist-title.png").read_bytes() == b"image-bytes"


def test_save_image_leaves_matching_existing_cover(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/png"

    (tmp_path / "artist-title.png").write_bytes(b"image-bytes")
    saved = save_image("artist-title.png", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "artist-title.png"
    assert (tmp_path / "artist-title.png").read_bytes() == b"image-bytes"


def test_save_image_preserves_dots_in_stem(tmp_path):
    class FakeArt:
        data = b"image-bytes"
        mime = "image/png"

    saved = save_image("Artist A-Song.One", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == tmp_path / "Artist A-Song.One.png"
    assert (tmp_path / "Artist A-Song.One.png").read_bytes() == b"image-bytes"


def test_save_all_covers_preserves_dots_in_filename(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    tagged = music / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song.One", artist="Artist A")
    img = ID3(tagged)
    img.add(
        APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=make_png_bytes())
    )
    img.save()
    save_all_covers()
    assert (app_settings.covers_folder / "Artist A-Song.One.png").is_file()


def test_tag_file_writes_tags_and_cover(tmp_path):
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    assert _tag_file(mp3_file, "Song", ["Artist A", "Artist B"], "Album", image) is True

    tags = EasyID3(mp3_file)
    assert tags["title"] == ["Song"]
    assert tags["artist"] == ["Artist A", "Artist B"]
    assert tags["album"] == ["Album"]
    assert ID3(mp3_file).getall("APIC")


def test_change_cover_replaces_existing_apic(tmp_path):
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    first = tmp_path / "first.png"
    first.write_bytes(make_png_bytes())
    _tag_file(mp3_file, "Song", ["Artist"], "", first)

    second = tmp_path / "second.jpg"
    second.write_bytes(make_jpeg_bytes())
    change_cover(second, mp3_file)

    apics = ID3(mp3_file).getall("APIC")
    assert len(apics) == 1
    assert apics[0].mime == "image/jpeg"
    assert apics[0].data == make_jpeg_bytes()


def test_save_image_keeps_existing_cover_on_unknown_mime(tmp_path, capsys):
    class FakeArt:
        data = b"new-bytes"
        mime = "application/octet-stream"

    existing = tmp_path / "artist-title.jpg"
    existing.write_bytes(b"old")
    saved = save_image("artist-title.png", {"APIC:FrontCover": FakeArt()}, tmp_path)
    assert saved == existing
    assert existing.read_bytes() == b"new-bytes"
    assert not (tmp_path / "artist-title.png").exists()
    assert "unrecognized cover mime" in capsys.readouterr().out


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


def test_set_cover_none_image_does_not_add_apic(tmp_path):
    mp3_file = tmp_path / "cover.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    audio = MP3(mp3_file, ID3=ID3)
    _set_cover(audio, None)
    assert not audio.tags.getall("APIC")


def test_save_song_temp_to_main_tags_then_moves(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result = save_song_temp_to_main(source, image, "Song", "Artist A", "Great Album")

    assert result is SaveResult.SAVED
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


def test_save_song_temp_to_main_declined_overwrite_is_skipped(
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

    result = save_song_temp_to_main(source, image, "Song", "Artist A", "Album")

    assert result is SaveResult.SKIPPED
    assert target.read_bytes() == b"original"
    assert source.exists()


def test_save_song_temp_to_main_empty_authors_skips(app_settings, tmp_path, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result = save_song_temp_to_main(source, image, "Song", "   ", "Album")

    assert result is SaveResult.FAILED
    assert source.exists()
    assert "no artists" in capsys.readouterr().out


def test_save_song_temp_to_main_missing_file_skips(app_settings, tmp_path, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result = save_song_temp_to_main(tmp_path / "missing.mp3", image, "Song", "A", "Al")

    assert result is SaveResult.FAILED
    assert not (app_settings.music_folder / "A-Song.mp3").exists()


def test_open_audio_missing_returns_none(tmp_path):
    assert _open_audio(tmp_path / "missing.mp3") is None


def test_open_audio_garbage_returns_none(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not an mp3 at all")
    assert _open_audio(bad) is None


def test_tag_file_garbage_returns_false(tmp_path, capsys):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not an mp3 at all")
    assert _tag_file(bad, "Song", ["Artist"], "Album", tmp_path / "nope.png") is False


def test_mime_to_ext_mapping():
    assert _mime_to_ext("image/jpeg") == ".jpg"
    assert _mime_to_ext("image/png") == ".png"
    assert _mime_to_ext("image/webp") == ".webp"
    assert _mime_to_ext("image/gif") == ".gif"
    assert _mime_to_ext("application/octet-stream") == ".png"
