import io
import os
from unittest.mock import Mock

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3
from PIL import Image

import smclipy.db as db
from smclipy.formats import Track
from smclipy.metadata import (
    SaveResult,
    ScanSummary,
    _mime_to_ext,
    _open_audio,
    _tag_file,
    apply_tag_update,
    backup_cover,
    change_cover,
    get_image_from_file,
    has_cover,
    list_snapshots,
    read_song_profile,
    read_song_uuid,
    restore_song_from_snapshot,
    save_all_covers,
    save_image,
    save_song_temp_to_main,
    scan_library,
    snapshot_song,
    write_song_uuid,
)

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Foo", artist="Artist", album=None) -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    if album is not None:
        audio["album"] = [album]
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
    assert "could not read" in capsys.readouterr().err


def test_scan_library_tracks_songs_and_authors(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")
    write_tagged_mp3(music / "two.mp3", title="Other", artist="Artist A")
    write_tagged_mp3(music / "three.mp3", title="Third", artist="Artist B")
    scan_library()
    assert db.get_authors() == ["Artist A", "Artist B"]
    songs = db.list_songs()
    assert len(songs) == 3
    assert {row["title"] for row in songs} == {"Song", "Other", "Third"}
    assert {row["artists"] for row in songs} == {"Artist A", "Artist B"}


def test_scan_library_skips_untagged_and_corrupt(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "tagged.mp3", title="Song", artist="Artist A")
    (music / "untagged.mp3").write_bytes(MINIMAL_MP3)
    (music / "garbage.mp3").write_bytes(b"not an mp3 at all")
    scan_library()
    assert db.get_authors() == ["Artist A"]
    assert len(db.list_songs()) == 1


def test_scan_library_is_idempotent(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")

    scan_library()
    scan_library()

    assert len(db.list_songs()) == 1
    assert db.get_authors() == ["Artist A"]


def test_scan_library_skips_case_variant_author_duplicates(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="PINK FLOYD")
    scan_library()
    assert db.get_authors() == ["PINK FLOYD"]

    write_tagged_mp3(music / "two.mp3", title="Other", artist="Pink Floyd")
    scan_library()

    assert db.get_authors() == ["PINK FLOYD"]
    assert len(db.list_songs()) == 2


def test_scan_library_assigns_uuid_to_untracked_files(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "untracked.mp3"
    write_tagged_mp3(file, title="Song", artist="Artist A")

    assert read_song_uuid(file) is None
    scan_library()
    assert read_song_uuid(file) is not None

    scan_library()
    assert len(db.list_songs()) == 1


def test_scan_library_tracks_rename(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "A-Song.mp3"
    write_tagged_mp3(file, title="Song", artist="Artist A")
    scan_library()

    song_uuid = read_song_uuid(file)
    assert song_uuid is not None

    os.rename(file, music / "B-Song.mp3")
    scan_library()

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["current_path"] == "B-Song.mp3"
    assert {e["event"] for e in db.get_events(song_uuid)} == {"appeared", "renamed"}


def test_scan_library_marks_missing_songs(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "A-Song.mp3"
    write_tagged_mp3(file, title="Song", artist="Artist A")
    scan_library()

    song_uuid = read_song_uuid(file)
    assert song_uuid is not None

    file.unlink()
    scan_library()

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["current_path"] is None
    assert {e["event"] for e in db.get_events(song_uuid)} == {"appeared", "missing"}


def test_scan_library_backfills_row_uuid_into_writable_file(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "Legacy-Song.mp3"
    write_tagged_mp3(file, title="Song", artist="Artist A")

    db.insert_song(current_path="Legacy-Song.mp3", title="Song", artists="Artist A")
    legacy_uuid = db.get_song_by_path("Legacy-Song.mp3")[0]["uuid"]

    scan_library()

    assert read_song_uuid(file) == legacy_uuid
    assert len(db.list_songs()) == 1


def test_scan_library_new_file_at_renamed_path_gets_new_uuid(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    original = music / "A-Song.mp3"
    write_tagged_mp3(original, title="Song", artist="Artist A")
    scan_library()

    song_uuid = read_song_uuid(original)
    assert song_uuid is not None

    os.rename(original, music / "B-Song.mp3")
    replacement = music / "A-Song.mp3"
    write_tagged_mp3(replacement, title="Different", artist="Artist B")
    scan_library()

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["current_path"] == "B-Song.mp3"
    assert read_song_uuid(replacement) != song_uuid
    assert len(db.list_songs()) == 2


def test_scan_library_reports_counts(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    first = music / "A-Song.mp3"
    write_tagged_mp3(first, title="Song", artist="Artist A")
    second = music / "Other-Song.mp3"
    write_tagged_mp3(second, title="Other", artist="Artist B")
    write_tagged_mp3(music / "Changed-Tag.mp3", title="Old", artist="Artist A")

    summary = scan_library()
    assert summary == ScanSummary(added=3, renamed=0, missing=0, changed=0)

    os.rename(first, music / "Renamed.mp3")
    changed_tags = EasyID3(str(music / "Changed-Tag.mp3"))
    changed_tags["title"] = ["New"]
    changed_tags.save()
    summary = scan_library()
    assert summary == ScanSummary(added=0, renamed=1, missing=0, changed=1)

    (music / "Renamed.mp3").unlink()
    summary = scan_library()
    assert summary == ScanSummary(added=0, renamed=0, missing=1, changed=0)

    summary = scan_library()
    assert summary == ScanSummary(added=0, renamed=0, missing=0, changed=0)


def test_scan_library_syncs_full_profile_tags(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    file = music / "A-Song.mp3"
    write_tagged_mp3(file, title="Song", artist="Artist A", album="Album")
    scan_library()
    song_uuid = read_song_uuid(file)
    assert song_uuid is not None
    assert db.get_song_by_uuid(song_uuid)["album"] == "Album"
    assert db.get_song_by_uuid(song_uuid)["date"] in (None, "")

    retagged = EasyID3(str(file))
    retagged["album"] = ["New Album"]
    retagged["date"] = ["2001"]
    retagged.save()

    summary = scan_library()
    assert summary == ScanSummary(added=0, renamed=0, missing=0, changed=1)

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["album"] == "New Album"
    assert row["date"] == "2001"
    assert row["title"] == "Song"

    summary = scan_library()
    assert summary == ScanSummary(added=0, renamed=0, missing=0, changed=0)


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


def test_tag_file_writes_tags_cover_and_tracking_uuid(tmp_path):
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
    assert read_song_uuid(mp3_file) is not None


def test_tag_file_preserves_existing_tracking_uuid(tmp_path):
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Song"]
    audio["artist"] = ["Artist"]
    audio.save(mp3_file)

    song_uuid = write_song_uuid(mp3_file)
    assert song_uuid is not None

    assert _tag_file(mp3_file, "New", ["Artist"], "Album", None) is True
    assert read_song_uuid(mp3_file) == song_uuid


def test_tag_file_clears_stale_frames(app_settings, tmp_path):
    app_settings.clean_unwanted_tags = True
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Old"]
    audio["artist"] = ["Old Artist"]
    audio["album"] = ["Old Album"]
    audio["date"] = ["1999"]
    audio["genre"] = ["Rock"]
    audio["albumartist"] = ["Old Album Artist"]
    audio["tracknumber"] = ["7"]
    audio.save(mp3_file)

    assert _tag_file(mp3_file, "New", ["Artist"], "Album", None) is True

    tags = ID3(mp3_file)
    assert str(tags["TIT2"]) == "New"
    assert str(tags["TPE1"]) == "Artist"
    assert str(tags["TALB"]) == "Album"
    assert "TDRC" not in tags
    assert "TCON" not in tags
    assert "TPE2" not in tags
    assert "TRCK" not in tags


def test_tag_file_preserves_extra_frames_by_default(tmp_path):
    mp3_file = tmp_path / "song.mp3"
    mp3_file.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = ["Old"]
    audio["artist"] = ["Old Artist"]
    audio["album"] = ["Old Album"]
    audio["date"] = ["1999"]
    audio["genre"] = ["Rock"]
    audio["albumartist"] = ["Old Album Artist"]
    audio["tracknumber"] = ["7"]
    audio.save(mp3_file)

    assert _tag_file(mp3_file, "New", ["Artist"], "Album", None) is True

    tags = ID3(mp3_file)
    assert str(tags["TIT2"]) == "New"
    assert str(tags["TPE1"]) == "Artist"
    assert str(tags["TALB"]) == "Album"
    assert str(tags["TDRC"]) == "1999"
    assert str(tags["TCON"]) == "Rock"
    assert str(tags["TPE2"]) == "Old Album Artist"
    assert str(tags["TRCK"]) == "7"


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
    assert "unrecognized cover mime" in capsys.readouterr().err


def test_save_song_temp_to_main_tags_then_moves(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result, target = save_song_temp_to_main(
        source, image, "Song", "Artist A", "Great Album"
    )

    assert result is SaveResult.SAVED
    assert target == app_settings.music_folder / "Artist A-Song.mp3"
    assert not source.exists()
    assert target.is_file()
    tags = EasyID3(target)
    assert tags["title"] == ["Song"]
    assert tags["artist"] == ["Artist A"]
    assert tags["album"] == ["Great Album"]
    assert ID3(target).getall("APIC")
    assert read_song_uuid(target) is not None


def test_save_song_temp_to_main_splits_comma_artists(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result, _ = save_song_temp_to_main(
        source, image, "Song", "Author 1, Author 2", "Al"
    )

    assert result is SaveResult.SAVED
    assert not source.exists()
    target = app_settings.music_folder / "Author 1-Song.mp3"
    assert target.is_file()
    tags = EasyID3(target)
    assert tags["artist"] == ["Author 1", "Author 2"]


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

    assert "already exists" in capsys.readouterr().err


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

    result, _ = save_song_temp_to_main(source, image, "Song", "Artist A", "Album")

    assert result is SaveResult.SKIPPED
    assert target.read_bytes() == b"original"
    assert source.exists()


def test_save_song_temp_to_main_overwrite_false_skips_without_prompt(
    app_settings, tmp_path, monkeypatch
):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist A-Song.mp3"
    target.write_bytes(b"original")

    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    monkeypatch.setattr(
        "smclipy.metadata.prompt_overwrite",
        Mock(side_effect=AssertionError("headless runs must not prompt")),
    )

    result, _ = save_song_temp_to_main(
        source, image, "Song", "Artist A", "Album", overwrite=False
    )

    assert result is SaveResult.SKIPPED
    assert target.read_bytes() == b"original"
    assert source.exists()


def test_save_song_temp_to_main_overwrite_true_replaces_without_prompt(
    app_settings, tmp_path, monkeypatch
):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist A-Song.mp3"
    target.write_bytes(b"original")

    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    monkeypatch.setattr(
        "smclipy.metadata.prompt_overwrite",
        Mock(side_effect=AssertionError("headless runs must not prompt")),
    )

    result, _ = save_song_temp_to_main(
        source, image, "Song", "Artist A", "Album", overwrite=True
    )

    assert result is SaveResult.SAVED
    assert target.is_file()
    assert not source.exists()


def test_scan_library_removes_orphan_temp_files(app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Foo.mp3")
    orphan = music / ".Foo.smclipy-tmp.mp3"
    orphan.write_bytes(MINIMAL_MP3)

    scan_library()

    assert not orphan.exists()
    assert (music / "Foo.mp3").exists()
    assert "leftover temp file(s)" in capsys.readouterr().err


def test_save_song_temp_to_main_empty_authors_skips(app_settings, tmp_path, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result, _ = save_song_temp_to_main(source, image, "Song", "   ", "Album")

    assert result is SaveResult.FAILED
    assert source.exists()
    assert "no artists" in capsys.readouterr().err


def test_save_song_temp_to_main_missing_file_skips(app_settings, tmp_path, capsys):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())

    result, _ = save_song_temp_to_main(
        tmp_path / "missing.mp3", image, "Song", "A", "Al"
    )

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


def test_read_song_profile_returns_tags(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    tagged = app_settings.music_folder / "A-Song.mp3"
    write_tagged_mp3(tagged, title="My Song", artist="Artist", album="My Album")

    profile, has_cover_flag = read_song_profile(tagged)

    assert profile["title"] == "My Song"
    assert profile["artists"] == "Artist"
    assert profile["album"] == "My Album"
    assert profile["date"] == ""
    assert profile["genre"] == ""
    assert profile["album_artist"] == ""
    assert profile["track_number"] == ""
    assert has_cover_flag is False


def test_read_song_profile_missing_file_warns(app_settings, tmp_path, capsys):
    profile, has_cover_flag = read_song_profile(tmp_path / "missing.mp3")
    assert profile == {}
    assert has_cover_flag is False
    assert "could not read" in capsys.readouterr().err


def test_has_cover_true_and_false(app_settings, tmp_path):
    tagged = tmp_path / "tagged.mp3"
    write_tagged_mp3(tagged, title="Song", artist="Artist")
    assert has_cover(tagged) is False

    img = ID3(tagged)
    img.add(
        APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=make_png_bytes())
    )
    img.save()
    assert has_cover(tagged) is True


def test_apply_tag_update_partial(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "A-Song.mp3"
    write_tagged_mp3(target, title="Old", artist="Artist", album="Album")

    ok = apply_tag_update(
        target,
        title="New",
        date="1990",
        genre="Rock",
        album_artist="Band",
        track_number="3/12",
    )

    assert ok is True
    id3 = ID3(target)
    assert str(id3["TIT2"]) == "New"
    assert str(id3["TALB"]) == "Album"
    assert str(id3["TDRC"]) == "1990"
    assert str(id3["TCON"]) == "Rock"
    assert str(id3["TPE2"]) == "Band"
    assert str(id3["TRCK"]) == "3/12"


def test_apply_tag_update_none_fields_leave_existing(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "A-Song.mp3"
    write_tagged_mp3(target, title="Old", artist="Artist", album="Album")

    ok = apply_tag_update(target, title="New")

    assert ok is True
    id3 = ID3(target)
    assert str(id3["TIT2"]) == "New"
    assert str(id3["TPE1"]) == "Artist"
    assert str(id3["TALB"]) == "Album"


def test_apply_tag_update_garbage_returns_false(app_settings, tmp_path, capsys):
    bad = app_settings.music_folder / "bad.mp3"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not an mp3 at all")
    assert apply_tag_update(bad, title="Song") is False


def test_apply_tag_update_leaves_no_temp_file(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(MINIMAL_MP3)

    assert apply_tag_update(path, title="New") is True

    assert read_song_profile(path)[0]["title"] == "New"
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]


def test_apply_tag_update_failure_keeps_original(tmp_path, monkeypatch, capsys):
    from smclipy.formats import Track

    path = tmp_path / "song.mp3"
    write_tagged_mp3(path, title="Original")
    original = path.read_bytes()

    def boom(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Track, "save", boom)
    ok = apply_tag_update(path, title="New")

    assert ok is False
    assert path.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]
    assert "could not save" in capsys.readouterr().err


def test_write_song_uuid_leaves_no_temp_file(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(MINIMAL_MP3)

    song_uuid = write_song_uuid(path)

    assert song_uuid is not None
    assert read_song_uuid(path) == song_uuid
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]


def test_scan_library_includes_subfolders_and_skips_state(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    sub = music / "Coldplay"
    sub.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")
    write_tagged_mp3(sub / "nested.mp3", title="Nested", artist="Artist B")
    app_settings.script_folder.mkdir(parents=True, exist_ok=True)
    (app_settings.script_folder / "state.mp3").write_bytes(MINIMAL_MP3)
    (app_settings.script_folder / ".temp").mkdir()
    (app_settings.script_folder / ".temp" / "yt-abc.mp3").write_bytes(MINIMAL_MP3)

    scan_library()

    songs = db.list_songs()
    assert {row["title"] for row in songs} == {"Song", "Nested"}
    assert {row["current_path"] for row in songs} == {
        "Coldplay/nested.mp3",
        "one.mp3",
    }


def test_scan_library_ignores_hidden_directories(app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "one.mp3", title="Song", artist="Artist A")
    hidden = music / ".archive"
    hidden.mkdir()
    write_tagged_mp3(hidden / "old.mp3", title="Old", artist="Artist Z")

    scan_library()

    assert [row["title"] for row in db.list_songs()] == ["Song"]


def test_save_song_temp_to_main_reuses_existing_uuid(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.music_folder / "Artist A-Song.mp3"
    write_tagged_mp3(target, title="Old", artist="Artist A")
    existing_uuid = write_song_uuid(target)
    assert existing_uuid is not None

    source = tmp_path / "temp.mp3"
    source.write_bytes(MINIMAL_MP3)

    result, saved = save_song_temp_to_main(
        source,
        None,
        "Song",
        "Artist A",
        "Album",
        overwrite=True,
        song_uuid=existing_uuid,
    )

    assert result is SaveResult.SAVED
    assert saved == target
    assert target.is_file()
    assert read_song_uuid(target) == existing_uuid


def test_snapshot_and_restore_roundtrip(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    path = app_settings.music_folder / "A-Song.mp3"
    write_tagged_mp3(path, title="Song", artist="Artist A", album="LP")
    scan_library()
    song_uuid = read_song_uuid(path)
    assert song_uuid is not None

    snap = snapshot_song(path, song_uuid, "tag")
    assert snap is not None
    assert snap.is_file()

    assert apply_tag_update(path, title="Wrong", artists=["Wrong Artist"]) is True

    snapshots = list_snapshots(song_uuid)
    assert len(snapshots) == 1
    assert snapshots[0].title == "Song"
    assert snapshots[0].artists == "Artist A"

    assert restore_song_from_snapshot(path, snapshots[0]) is True

    profile, _ = read_song_profile(path)
    assert profile["title"] == "Song"
    assert profile["artists"] == "Artist A"
    assert profile["album"] == "LP"
    assert read_song_uuid(path) == song_uuid


def test_snapshot_keeps_cover_art(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    path = app_settings.music_folder / "A-Song.mp3"
    write_tagged_mp3(path, title="Song", artist="Artist A")
    song_uuid = write_song_uuid(path)
    assert song_uuid is not None
    image = tmp_path / "cover.png"
    image.write_bytes(make_png_bytes())
    change_cover(image, path)
    assert has_cover(path) is True

    snapshot_song(path, song_uuid, "tag")

    snapshots = list_snapshots(song_uuid)
    assert len(snapshots) == 1
    assert snapshots[0].cover is not None
    assert snapshots[0].cover.read_bytes() == make_png_bytes()


def test_backup_cover_copies_into_backups(app_settings, tmp_path):
    cover = tmp_path / "A-Song.png"
    cover.write_bytes(make_png_bytes())

    assert backup_cover(cover) is True

    backed = app_settings.backups_folder / "covers" / "A-Song.png"
    assert backed.is_file()
    assert backed.read_bytes() == make_png_bytes()


def test_save_image_relabel_backs_up_previous(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    covered = tmp_path / "covered.mp3"
    write_tagged_mp3(covered, title="Song", artist="Artist A")
    img = ID3(covered)
    img.add(
        APIC(
            encoding=3,
            mime="image/png",
            type=3,
            desc="Cover",
            data=make_png_bytes(),
        )
    )
    img.save()
    track = Track.open(covered)
    assert track is not None

    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    old = app_settings.covers_folder / "A-Song.webp"
    old.write_bytes(make_png_bytes())

    saved = save_image("A-Song.webp", track, app_settings.covers_folder)

    assert saved == app_settings.covers_folder / "A-Song.png"
    assert not old.exists()
    assert (app_settings.backups_folder / "covers" / "A-Song.webp").is_file()


def test_save_image_changed_cover_backs_up_previous(app_settings, tmp_path):
    app_settings.music_folder.mkdir(parents=True, exist_ok=True)
    covered = tmp_path / "covered.mp3"
    write_tagged_mp3(covered, title="Song", artist="Artist A")
    img = ID3(covered)
    img.add(
        APIC(
            encoding=3,
            mime="image/png",
            type=3,
            desc="Cover",
            data=make_png_bytes(),
        )
    )
    img.save()
    track = Track.open(covered)
    assert track is not None

    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    target = app_settings.covers_folder / "A-Song.png"
    target.write_bytes(b"old-bytes")

    saved = save_image("A-Song.png", track, app_settings.covers_folder)

    assert saved == target
    assert target.read_bytes() == make_png_bytes()
    assert (app_settings.backups_folder / "covers" / "A-Song.png").is_file()
    backed = app_settings.backups_folder / "covers" / "A-Song.png"
    assert backed.read_bytes() == b"old-bytes"
