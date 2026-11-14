import argparse
from pathlib import Path
from types import SimpleNamespace

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3

import smclipy.db as db
from smclipy import dedupe

MINIMAL_MP3 = (bytes.fromhex("FFFB9064") + bytes(413)) * 2


def write_tagged_mp3(path, title="Song", artist="Artist", album="Album") -> None:
    path.write_bytes(MINIMAL_MP3)
    audio = EasyID3()
    audio["title"] = [title]
    audio["artist"] = [artist]
    audio["album"] = [album]
    audio.save(path)


def fake_song(
    title: str,
    artists: str,
    album: str = "",
    stem: str = "song",
    album_artist: str = "",
):
    return SimpleNamespace(
        path=Path(f"{stem}.mp3"),
        current={
            "title": title,
            "artists": artists,
            "album": album,
            "album_artist": album_artist,
        },
    )


def test_find_artist_groups_bundles_case_and_spacing_variants():
    songs = [
        fake_song("One", "Artist"),
        fake_song("Two", "artist"),
        fake_song("Three", "a r t i s t"),
        fake_song("Four", "Other"),
    ]
    groups = dedupe.find_artist_groups(songs, [])
    assert len(groups) == 1
    group = groups[0]
    assert group.field == "artists"
    assert group.key == "artist"
    assert [spelling for spelling, _ in group.variants] == [
        "Artist",
        "artist",
        "a r t i s t",
    ]
    assert group.song_count == 3


def test_find_artist_groups_includes_authors_table_only_variant():
    songs = [fake_song("One", "artist")]
    groups = dedupe.find_artist_groups(songs, ["a r t i s t"])
    assert len(groups) == 1
    assert set(groups[0].variants) == {("artist", 1), ("a r t i s t", 0)}


def test_find_artist_groups_counts_a_song_once_per_normalized_name():
    songs = [
        fake_song("One", "Artist, artist"),
        fake_song("Two", "a r t i s t"),
    ]
    groups = dedupe.find_artist_groups(songs, [])
    assert len(groups) == 1
    assert groups[0].song_count == 2
    assert not dedupe.find_artist_groups([fake_song("One", "Artist, artist")], [])


def test_find_album_groups_skips_empty_and_matches_spacing():
    songs = [
        fake_song("One", "Artist", "Album"),
        fake_song("Two", "Artist", "a l b u m"),
        fake_song("Three", "Artist", ""),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].field == "album"
    assert {spelling for spelling, _ in groups[0].variants} == {"Album", "a l b u m"}


def test_find_album_groups_ignores_trailing_bracketed_qualifiers():
    songs = [
        fake_song("One", "Artist", "Album"),
        fake_song("Two", "Artist", "Album (test)"),
        fake_song("Three", "Artist", "Album [test]"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].key == "album"
    assert [spelling for spelling, _ in groups[0].variants][0] == "Album"
    assert {spelling for spelling, _ in groups[0].variants} == {
        "Album",
        "Album (test)",
        "Album [test]",
    }


def test_find_album_groups_prefers_bare_spelling_over_more_qualified_songs():
    songs = [
        fake_song("One", "Artist", "Album (test)"),
        fake_song("Two", "Artist", "Album (test)"),
        fake_song("Three", "Artist", "Album (test)"),
        fake_song("Four", "Artist", "Album"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert groups[0].variants[0] == ("Album", 1)


def test_find_album_groups_keeps_edition_suffixes_distinct():
    songs = [fake_song("One", "Artist", "Album")]
    for suffix in ("Part 1", "Part 2", "Disc 2", "Volume 3", "Live", "Remix"):
        songs.append(fake_song(suffix, "Artist", f"Album ({suffix})"))
    assert dedupe.find_album_groups(songs) == []


def test_find_album_groups_strips_multiple_trailing_qualifiers():
    songs = [
        fake_song("One", "Artist", "Album"),
        fake_song("Two", "Artist", "Album (test) (copy)"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].key == "album"
    assert {spelling for spelling, _ in groups[0].variants} == {
        "Album",
        "Album (test) (copy)",
    }


def test_find_album_groups_keeps_albums_by_different_first_artists_apart():
    songs = [
        fake_song("One", "Artist A", "Album (test)"),
        fake_song("Two", "Artist B", "Album"),
    ]
    assert dedupe.find_album_groups(songs) == []


def test_find_album_groups_scopes_by_first_artist():
    songs = [
        fake_song("One", "Artist, Guest A", "Album (test)"),
        fake_song("Two", "Artist, Guest B", "Album"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].scope == "artist"
    assert {spelling for spelling, _ in groups[0].variants} == {
        "Album",
        "Album (test)",
    }


def test_find_album_groups_matches_album_artist_with_fallback_first_artist():
    songs = [
        fake_song("One", "Artist", "Album (test)", album_artist="Artist"),
        fake_song("Two", "Artist", "Album"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].scope == "artist"


def test_find_album_groups_prefers_album_artist_over_first_artist():
    songs = [
        fake_song("One", "Artist A", "Album (test)", album_artist="Shared"),
        fake_song("Two", "Artist B", "Album", album_artist="Shared"),
    ]
    groups = dedupe.find_album_groups(songs)
    assert len(groups) == 1
    assert groups[0].scope == "shared"


def test_no_groups_when_spellings_are_distinct():
    songs = [fake_song("One", "Artist", "Album"), fake_song("Two", "Other", "Other")]
    assert dedupe.find_artist_groups(songs, []) == []
    assert dedupe.find_album_groups(songs) == []


def test_canonicalize_artists_maps_and_drops_duplicates():
    assert dedupe.canonicalize_artists(
        ["artist", "Guest", "ARTIST"], {"artist": "Artist"}
    ) == ["Artist", "Guest"]
    assert dedupe.canonicalize_artists(["A", "B"], {}) == ["A", "B"]


def test_plan_song_change_does_not_rename_when_only_later_artist_changes(
    app_settings,
):
    song = SimpleNamespace(
        path=app_settings.music_folder / "Artist-Song.mp3",
        current={
            "title": "Song",
            "artists": "Artist, guest",
            "album": "album",
            "album_artist": "guest",
        },
    )
    change = dedupe.plan_song_change(
        song, {"guest": "Guest"}, {("album", "guest"): "Album"}
    )
    assert change is not None
    assert change.new_artists == ("Artist", "Guest")
    assert change.new_album == "Album"
    assert change.new_album_artist == "Guest"
    assert change.target is None
    assert change.cover_old is None

    assert dedupe.plan_song_change(song, {}, {}) is None


def test_plan_song_change_maps_qualified_album(app_settings):
    song = SimpleNamespace(
        path=app_settings.music_folder / "Artist-Song.mp3",
        current={
            "title": "Song",
            "artists": "Artist",
            "album": "Album (test)",
            "album_artist": "",
        },
    )
    change = dedupe.plan_song_change(song, {}, {("album", "artist"): "Album"})
    assert change is not None
    assert change.new_album == "Album"
    assert change.target is None


def test_plan_song_change_skips_album_for_a_different_artist(app_settings):
    song = SimpleNamespace(
        path=app_settings.music_folder / "Artist-B-Song.mp3",
        current={
            "title": "Song",
            "artists": "Artist B",
            "album": "Album (test)",
            "album_artist": "",
        },
    )
    change = dedupe.plan_song_change(song, {}, {("album", "artist a"): "Album"})
    assert change is None


def test_plan_song_change_uses_album_artist_scope(app_settings):
    song = SimpleNamespace(
        path=app_settings.music_folder / "Artist-B-Song.mp3",
        current={
            "title": "Song",
            "artists": "Artist B",
            "album": "Album (test)",
            "album_artist": "Shared",
        },
    )
    change = dedupe.plan_song_change(song, {}, {("album", "shared"): "Album"})
    assert change is not None
    assert change.new_album == "Album"


def test_plan_song_change_renames_with_canonical_first_artist(app_settings):
    song = SimpleNamespace(
        path=app_settings.music_folder / "artist-Song.mp3",
        current={
            "title": "Song",
            "artists": "artist, Guest",
            "album": "Album",
            "album_artist": "",
        },
    )
    change = dedupe.plan_song_change(song, {"artist": "Artist"}, {})
    assert change is not None
    assert change.target == app_settings.music_folder / "Artist-Song.mp3"
    assert change.cover_old == "artist-Song"
    assert change.cover_new == "Artist-Song"


def test_rename_false_positive_moves_entry(app_settings):
    db.add_false_positive("old-stem")
    db.rename_false_positive("old-stem", "new-stem")
    assert db.get_false_positives() == {"new-stem"}


def test_cmd_dedupe_merges_artists_files_covers_and_db(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    write_tagged_mp3(music / "weird-Three.mp3", title="Three", artist="a r t i s t")
    (app_settings.covers_folder / "artist-Two.jpg").write_bytes(b"jpeg")
    db.add_authors(["a r t i s t"])

    monkeypatch.setattr(
        dedupe,
        "prompt_canonical_spelling",
        lambda label, variants, default: "Artist",
    )

    dedupe.cmd_dedupe(argparse.Namespace())

    assert sorted(path.name for path in music.glob("*.mp3")) == [
        "Artist-One.mp3",
        "Artist-Three.mp3",
        "Artist-Two.mp3",
    ]
    for name in ("Artist-One.mp3", "Artist-Two.mp3", "Artist-Three.mp3"):
        id3 = ID3(music / name)
        assert id3["TPE1"].text == ["Artist"]

    assert (app_settings.covers_folder / "Artist-Two.jpg").is_file()
    assert not (app_settings.covers_folder / "artist-Two.jpg").exists()

    row = db.get_song_by_path("Artist-Three.mp3")[0]
    assert row["artists"] == "Artist"
    events = {event["event"] for event in db.get_events(row["uuid"])}
    assert "normalized" in events
    assert "renamed" in events
    assert db.get_authors() == ["Artist"]
    out = capsys.readouterr().out
    assert "Dedupe complete" in out


def test_cmd_dedupe_keeps_renames_inside_subfolders(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    album_dir = music / "Albums" / "One"
    album_dir.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(album_dir / "artist-One.mp3", title="One", artist="artist")
    write_tagged_mp3(music / "Artist-Two.mp3", title="Two", artist="Artist")
    monkeypatch.setattr(
        dedupe,
        "prompt_canonical_spelling",
        lambda label, variants, default: "Artist",
    )

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert (album_dir / "Artist-One.mp3").is_file()
    assert not (music / "Artist-One.mp3").exists()
    assert ID3(album_dir / "Artist-One.mp3")["TPE1"].text == ["Artist"]


def test_describe_omits_cover_line_without_a_cover_file(app_settings):
    song = SimpleNamespace(
        path=app_settings.music_folder / "artist-Song.mp3",
        current={"title": "Song", "artists": "artist", "album": ""},
    )
    change = dedupe.plan_song_change(song, {"artist": "Artist"}, {})
    assert change is not None
    app_settings.covers_folder.mkdir(parents=True, exist_ok=True)

    lines = dedupe._describe(change, app_settings)

    assert not any("cover" in line for line in lines)


def test_cmd_dedupe_albums_dry_run_writes_nothing(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="album"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Album"
    )
    monkeypatch.setattr(
        dedupe,
        "prompt_canonical_spelling",
        lambda label, variants, default: "Album",
    )

    dedupe.cmd_dedupe(argparse.Namespace(albums=True, dry_run=True))

    assert str(ID3(music / "Artist-One.mp3")["TALB"]) == "album"
    assert str(ID3(music / "Artist-Two.mp3")["TALB"]) == "Album"
    assert db.get_song_by_path("Artist-One.mp3")[0]["album"] == "album"
    assert "Dry run complete" in capsys.readouterr().out

    dedupe.cmd_dedupe(argparse.Namespace(albums=True))

    assert str(ID3(music / "Artist-One.mp3")["TALB"]) == "Album"
    assert str(ID3(music / "Artist-Two.mp3")["TALB"]) == "Album"
    assert db.get_song_by_path("Artist-One.mp3")[0]["album"] == "Album"
    # Album merges never touch filenames.
    assert (music / "Artist-One.mp3").is_file()
    assert (music / "Artist-Two.mp3").is_file()


def test_cmd_dedupe_merges_trailing_album_qualifier(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Album"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3",
        title="Two",
        artist="Artist",
        album="Album (test)",
    )
    defaults: list[str] = []

    def fake_prompt(label, variants, default):
        defaults.append(default)
        return "Album"

    monkeypatch.setattr(dedupe, "prompt_canonical_spelling", fake_prompt)

    dedupe.cmd_dedupe(argparse.Namespace(albums=True, dry_run=True))

    assert defaults == ["Album"]
    assert str(ID3(music / "Artist-Two.mp3")["TALB"]) == "Album (test)"
    capsys.readouterr()

    dedupe.cmd_dedupe(argparse.Namespace(albums=True))

    assert str(ID3(music / "Artist-One.mp3")["TALB"]) == "Album"
    assert str(ID3(music / "Artist-Two.mp3")["TALB"]) == "Album"


def test_cmd_dedupe_keeps_song_unchanged_when_target_exists(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-One.mp3", title="One", artist="artist")
    monkeypatch.setattr(
        dedupe,
        "prompt_canonical_spelling",
        lambda label, variants, default: "Artist",
    )

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert (music / "artist-One.mp3").is_file()
    assert (music / "Artist-One.mp3").is_file()
    # The rename is attempted before tags are written, so a collision leaves
    # the song untouched and the next run can retry it.
    assert ID3(music / "artist-One.mp3")["TPE1"].text == ["artist"]
    row = db.get_song_by_path("artist-One.mp3")[0]
    assert row["artists"] == "artist"
    out = capsys.readouterr().out
    assert "1 filename collision(s)" in out
    assert "1 failure(s)" in out

    (music / "Artist-One.mp3").unlink()
    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert (music / "Artist-One.mp3").is_file()
    assert not (music / "artist-One.mp3").exists()
    assert ID3(music / "Artist-One.mp3")["TPE1"].text == ["Artist"]
    assert db.get_song_by_path("Artist-One.mp3")[0]["artists"] == "Artist"


def test_cmd_dedupe_reverts_rename_when_tag_write_fails(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "artist-One.mp3", title="One", artist="artist")
    db.add_authors(["Artist"])
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: "Artist"
    )
    monkeypatch.setattr(dedupe, "apply_tag_update", lambda *a, **k: False)

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert (music / "artist-One.mp3").is_file()
    assert not (music / "Artist-One.mp3").exists()
    assert ID3(music / "artist-One.mp3")["TPE1"].text == ["artist"]
    row = db.get_song_by_path("artist-One.mp3")
    assert row is not None
    assert row[0]["artists"] == "artist"
    out = capsys.readouterr().out
    assert "0 file(s) renamed" in out
    assert "1 failure(s)" in out


def test_cmd_dedupe_keeps_false_positive_for_leftover_cover(monkeypatch, app_settings):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    covers = app_settings.covers_folder
    covers.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "artist-One.mp3", title="One", artist="artist")
    db.add_authors(["Artist"])
    (covers / "artist-One.jpg").write_bytes(b"old")
    (covers / "artist-One.png").write_bytes(b"old-png")
    (covers / "Artist-One.png").write_bytes(b"new-png")
    db.add_false_positive("artist-One")
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: "Artist"
    )

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert (covers / "Artist-One.jpg").is_file()
    assert (covers / "artist-One.png").is_file()
    assert (covers / "Artist-One.png").read_bytes() == b"new-png"
    assert db.get_false_positives() == {"artist-One", "Artist-One"}


def test_cmd_dedupe_skipped_group_writes_nothing(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: None
    )

    dedupe.cmd_dedupe(argparse.Namespace())

    assert ID3(music / "artist-Two.mp3")["TPE1"].text == ["artist"]
    assert "Nothing to merge." in capsys.readouterr().out


def test_cmd_dedupe_remembers_skipped_group(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    prompts: list[str] = []

    def skip(label, variants, default):
        prompts.append(label)
        return None

    monkeypatch.setattr(dedupe, "prompt_canonical_spelling", skip)

    dedupe.cmd_dedupe(argparse.Namespace())
    assert len(prompts) == 1
    assert "remembered" in capsys.readouterr().out
    assert db.get_dedupe_skips() == {
        ("artists", "artist", ""): frozenset({"Artist", "artist"})
    }

    prompts.clear()
    dedupe.cmd_dedupe(argparse.Namespace())

    assert prompts == []
    out = capsys.readouterr().out
    assert "All 1 name group(s) were skipped on previous runs." in out
    assert "--reset-skipped" in out


def test_cmd_dedupe_reset_skipped_reoffers_groups(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: None
    )
    dedupe.cmd_dedupe(argparse.Namespace())
    assert db.get_dedupe_skips()
    capsys.readouterr()

    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: "Artist"
    )
    dedupe.cmd_dedupe(argparse.Namespace(reset_skipped=True))

    assert db.get_dedupe_skips() == {}
    assert ID3(music / "Artist-Two.mp3")["TPE1"].text == ["Artist"]
    out = capsys.readouterr().out
    assert "Forgot 1 previously skipped group(s)." in out


def test_cmd_dedupe_reoffers_skipped_group_for_new_variant(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    prompts: list[str] = []
    monkeypatch.setattr(
        dedupe,
        "prompt_canonical_spelling",
        lambda label, variants, default: prompts.append(label) or None,
    )
    dedupe.cmd_dedupe(argparse.Namespace())
    assert len(prompts) == 1
    capsys.readouterr()

    write_tagged_mp3(music / "weird-Three.mp3", title="Three", artist="a r t i s t")
    dedupe.cmd_dedupe(argparse.Namespace())

    assert len(prompts) == 2
    out = capsys.readouterr().out
    assert "Found 1 name group(s) with inconsistent spelling:" in out
    assert "previous runs" not in out
    assert db.get_dedupe_skips() == {
        ("artists", "artist", ""): frozenset({"Artist", "artist", "a r t i s t"})
    }


def test_cmd_dedupe_dry_run_does_not_remember_skip(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: None
    )

    dedupe.cmd_dedupe(argparse.Namespace(dry_run=True))

    assert db.get_dedupe_skips() == {}
    assert "not remembered" in capsys.readouterr().out


def test_cmd_dedupe_merge_removes_remembered_skip(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "Artist-One.mp3", title="One", artist="Artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    db.record_dedupe_skip(
        "artists", "artist", "", ["Artist", "artist", "stale spelling"]
    )
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: "Artist"
    )

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    assert db.get_dedupe_skips() == {}
    assert ID3(music / "Artist-Two.mp3")["TPE1"].text == ["Artist"]


def test_cmd_dedupe_keeps_going_when_one_song_fails(monkeypatch, app_settings, capsys):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(music / "artist-One.mp3", title="One", artist="artist")
    write_tagged_mp3(music / "artist-Two.mp3", title="Two", artist="artist")
    db.add_authors(["Artist"])
    monkeypatch.setattr(
        dedupe, "prompt_canonical_spelling", lambda label, variants, default: "Artist"
    )
    real_apply = dedupe._apply_change
    failed: list[str] = []

    def flaky(change, s, *, dry_run, counts):
        # One unreadable file must not abort the whole merge run.
        if not failed:
            failed.append(change.song.path.name)
            raise OSError("unreadable file")
        real_apply(change, s, dry_run=dry_run, counts=counts)

    monkeypatch.setattr(dedupe, "_apply_change", flaky)

    dedupe.cmd_dedupe(argparse.Namespace(artists=True))

    captured = capsys.readouterr()
    assert failed == ["artist-One.mp3"]
    # The other song was still merged, and the failed one was left alone.
    assert (music / "Artist-Two.mp3").is_file()
    assert ID3(music / "Artist-Two.mp3")["TPE1"].text == ["Artist"]
    row = db.get_song_by_path("artist-One.mp3")
    assert row is not None and row[0]["artists"] == "artist"
    assert db.get_authors() == ["Artist"]
    assert "could not process 'artist-One.mp3'" in captured.err
    assert "1 failure(s)" in captured.out
    assert "Dedupe complete" in captured.out


def _patch_group_albums(monkeypatch, answers, selected_prefixes, confirm=False):
    answer_iter = iter(answers)
    monkeypatch.setattr(
        dedupe,
        "prompt_search_value",
        lambda label, values, default="": next(answer_iter),
    )

    def fake_multi(values, *, title, message):
        return [
            key
            for key, label in values
            if any(label.startswith(prefix) for prefix in selected_prefixes)
        ]

    monkeypatch.setattr(dedupe, "prompt_multi_selection", fake_multi)
    monkeypatch.setattr(
        dedupe, "prompt_confirm", lambda message, default=False: confirm
    )


def test_cmd_dedupe_group_albums_rewrites_selected_songs(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Alpha"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Beta"
    )
    write_tagged_mp3(
        music / "Artist-Three.mp3", title="Three", artist="Artist", album="Gamma"
    )
    _patch_group_albums(monkeypatch, ["Artist", "Album"], ["One  ", "Two  "])

    dedupe.cmd_dedupe(argparse.Namespace(group_albums=True))

    assert ID3(music / "Artist-One.mp3")["TALB"].text == ["Album"]
    assert ID3(music / "Artist-Two.mp3")["TALB"].text == ["Album"]
    assert ID3(music / "Artist-Three.mp3")["TALB"].text == ["Gamma"]
    assert db.get_song_by_path("Artist-One.mp3")[0]["album"] == "Album"
    assert db.get_song_by_path("Artist-Two.mp3")[0]["album"] == "Album"
    assert db.get_song_by_path("Artist-Three.mp3")[0]["album"] == "Gamma"
    out = capsys.readouterr().out
    assert "Dedupe complete" in out
    assert "2 song(s) updated" in out


def test_cmd_dedupe_group_albums_dry_run_writes_nothing(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Alpha"
    )
    write_tagged_mp3(
        music / "Artist-Two.mp3", title="Two", artist="Artist", album="Beta"
    )
    _patch_group_albums(monkeypatch, ["Artist", "Album"], ["One  ", "Two  "])

    dedupe.cmd_dedupe(argparse.Namespace(group_albums=True, dry_run=True))

    assert ID3(music / "Artist-One.mp3")["TALB"].text == ["Alpha"]
    assert ID3(music / "Artist-Two.mp3")["TALB"].text == ["Beta"]
    assert db.get_song_by_path("Artist-One.mp3")[0]["album"] == "Alpha"
    assert db.get_song_by_path("Artist-Two.mp3")[0]["album"] == "Beta"
    out = capsys.readouterr().out
    assert "Dry run complete" in out
    assert "2 song(s) would be updated" in out


def test_cmd_dedupe_group_albums_blank_author_does_nothing(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Alpha"
    )
    _patch_group_albums(monkeypatch, [""], [])

    dedupe.cmd_dedupe(argparse.Namespace(group_albums=True))

    assert ID3(music / "Artist-One.mp3")["TALB"].text == ["Alpha"]
    assert "Nothing to merge." in capsys.readouterr().out


def test_cmd_dedupe_group_albums_empty_selection_skips(
    monkeypatch, app_settings, capsys
):
    music = app_settings.music_folder
    music.mkdir(parents=True, exist_ok=True)
    write_tagged_mp3(
        music / "Artist-One.mp3", title="One", artist="Artist", album="Alpha"
    )
    _patch_group_albums(monkeypatch, ["Artist"], [])

    dedupe.cmd_dedupe(argparse.Namespace(group_albums=True))

    assert ID3(music / "Artist-One.mp3")["TALB"].text == ["Alpha"]
    assert "No songs selected" in capsys.readouterr().out
