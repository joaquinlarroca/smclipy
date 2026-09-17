import json

import smclipy.db as db


def test_authors_dedupe_case_insensitive(app_settings):
    db.add_authors(["Twenty One Pilots"])
    db.add_authors(["twenty one pilots"])
    db.add_authors(["Pink Floyd", "  "])

    assert db.get_authors() == ["Twenty One Pilots", "Pink Floyd"]


def test_queue_reset_and_pending(app_settings):
    db.reset_queue(["A", "B", "C"])
    assert db.get_pending_urls() == ["A", "B", "C"]
    assert len(db.get_pending_urls()) == 3

    db.reset_queue(["A"])
    assert db.get_pending_urls() == ["A"]
    assert len(db.get_pending_urls()) == 1


def test_queue_mark_processed(app_settings):
    db.reset_queue(["A", "B"])
    db.mark_processed("A", title="Song", artists="Artist")
    assert db.get_pending_urls() == ["B"]


def test_requeue_keeps_pending_processing(app_settings):
    db.reset_queue(["A", "B"])
    db.mark_processed("A")
    db.requeue(["A", "B"])

    assert db.get_pending_urls() == ["A", "B"]


def test_requeue_increments_attempts(app_settings):
    import sqlite3

    db.reset_queue(["A"])
    db.requeue(["A"])
    db.requeue(["A"])

    with sqlite3.connect(app_settings.db_path) as conn:
        attempts = conn.execute(
            "SELECT attempts FROM queue WHERE url = 'A'"
        ).fetchone()[0]
    assert attempts == 2


def test_song_insert_get_and_update(app_settings):
    song_uuid = db.insert_song(
        current_path="Artist-Song.mp3",
        title="Song",
        artists="Artist",
        has_cover=True,
        source_url="https://example.com/x",
    )

    row = db.get_song_by_uuid(song_uuid)
    assert row is not None
    assert row["current_path"] == "Artist-Song.mp3"
    assert row["first_seen_path"] == "Artist-Song.mp3"
    assert row["has_cover"] == 1
    assert row["source_url"] == "https://example.com/x"

    db.set_song_path(song_uuid, "Renamed-Song.mp3")
    row = db.get_song_by_uuid(song_uuid)
    assert row["current_path"] == "Renamed-Song.mp3"
    assert row["first_seen_path"] == "Artist-Song.mp3"

    db.mark_song_missing(song_uuid)
    row = db.get_song_by_uuid(song_uuid)
    assert row["current_path"] is None


def test_get_song_by_path_matches_any_known_path(app_settings):
    song_uuid = db.insert_song(current_path="Current-Song.mp3", title="Song")
    db.set_song_path(song_uuid, "Moved-Song.mp3")

    first = db.get_song_by_path("Current-Song.mp3")
    current = db.get_song_by_path("Moved-Song.mp3")
    assert first and current
    assert first[0]["uuid"] == current[0]["uuid"] == song_uuid


def test_record_mb_status_tagged(app_settings):
    song_uuid = db.insert_song(current_path="Song.mp3")
    db.record_mb_status(
        song_uuid,
        db.MB_STATUS_TAGGED,
        recording_id="rec-1",
        release_group_id="rg-1",
        tagged=True,
    )

    row = db.get_song_by_uuid(song_uuid)
    assert row["musicbrainz_status"] == "tagged"
    assert row["musicbrainz_recording_id"] == "rec-1"
    assert row["musicbrainz_release_group_id"] == "rg-1"
    assert row["tagged"] == 1


def test_cover_statuses(app_settings):
    song_uuid = db.insert_song(current_path="Song.mp3")
    db.set_cover_status(song_uuid, db.COVER_STATUS_CROPPED)

    row = db.get_song_by_uuid(song_uuid)
    assert row["cover_status"] == "cropped"


def test_false_positives(app_settings):
    assert db.get_false_positives() == set()
    db.add_false_positive("Artist-Song")
    assert db.get_false_positives() == {"Artist-Song"}


def test_log_event_and_get_events(app_settings):
    song_uuid = db.insert_song(current_path="Song.mp3")
    db.log_event(song_uuid, "downloaded", {"url": "https://example.com/x"})
    db.log_event(song_uuid, "renamed", {"old_path": "a.mp3", "new_path": "b.mp3"})

    events = db.get_events(song_uuid)
    assert [e["event"] for e in events] == ["downloaded", "renamed"]
    assert json.loads(events[0]["detail"]) == {"url": "https://example.com/x"}


def test_migration_imports_legacy_text_files(app_settings):
    s = app_settings
    s.script_folder.mkdir(parents=True, exist_ok=True)
    s.temp_folder.mkdir(parents=True, exist_ok=True)
    s.authors_file.write_text("Artist A\nArtist B\n", encoding="utf-8")
    s.tagged_files_file.write_text("Artist A-Song.mp3\n", encoding="utf-8")
    s.pending_ids_file.write_text("https://example.com/pending\n", encoding="utf-8")
    s.processed_ids_file.write_text("https://example.com/processed\n", encoding="utf-8")
    s.false_positives_file.write_text("Artist A-Song\n", encoding="utf-8")

    assert db.get_authors() == ["Artist A", "Artist B"]
    assert db.get_pending_urls() == ["https://example.com/pending"]
    assert db.get_false_positives() == {"Artist A-Song"}

    songs = db.list_songs()
    assert len(songs) == 1
    assert songs[0]["current_path"] == "Artist A-Song.mp3"
    # Legacy songs were tagged by the manual download flow, never by
    # MusicBrainz, so they must not be skipped by the `tag` command.
    assert songs[0]["tagged"] == 0
    assert songs[0]["musicbrainz_status"] is None

    db.get_authors()
    assert len(db.list_songs()) == 1
    assert db.get_authors() == ["Artist A", "Artist B"]


def test_migration_empty_legacy_files(app_settings):
    db.get_authors()
    assert db.list_songs() == []
    assert db.get_pending_urls() == []
    assert db.get_authors() == []


def test_same_recording_id_allowed_for_two_songs(app_settings):
    first = db.insert_song(current_path="a.mp3")
    second = db.insert_song(current_path="b.mp3")

    db.record_mb_status(first, db.MB_STATUS_TAGGED, recording_id="rec-1", tagged=True)
    db.record_mb_status(second, db.MB_STATUS_TAGGED, recording_id="rec-1", tagged=True)

    row = db.get_song_by_uuid(second)
    assert row is not None
    assert row["musicbrainz_recording_id"] == "rec-1"


def test_migration_drops_unique_recording_constraint(app_settings):
    import sqlite3

    app_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(app_settings.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE songs (
                uuid TEXT PRIMARY KEY,
                source_url TEXT,
                musicbrainz_recording_id TEXT UNIQUE,
                musicbrainz_release_group_id TEXT,
                musicbrainz_status TEXT,
                cover_status TEXT,
                current_path TEXT,
                first_seen_path TEXT,
                title TEXT,
                artists TEXT,
                album TEXT,
                date TEXT,
                genre TEXT,
                album_artist TEXT,
                track_number TEXT,
                has_cover INTEGER NOT NULL DEFAULT 0,
                tagged INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                updated_at TEXT
            );
            INSERT INTO songs (uuid, current_path, musicbrainz_recording_id, tagged)
            VALUES ('legacy-1', 'old.mp3', 'rec-1', 1);
            """
        )

    db.list_songs()

    with sqlite3.connect(app_settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT uuid, current_path, musicbrainz_recording_id, tagged FROM songs"
        ).fetchone()
        assert row["uuid"] == "legacy-1"
        assert row["current_path"] == "old.mp3"
        assert row["musicbrainz_recording_id"] == "rec-1"
        unique_indexes = [
            idx["name"]
            for idx in conn.execute("PRAGMA index_list('songs')")
            if idx["origin"] == "u"
        ]
        assert all(
            "musicbrainz_recording_id" not in str(name) for name in unique_indexes
        )

    second = db.insert_song(current_path="new.mp3")
    db.record_mb_status(second, db.MB_STATUS_TAGGED, recording_id="rec-1", tagged=True)
    assert db.get_song_by_uuid(second)["musicbrainz_recording_id"] == "rec-1"


def test_fresh_database_is_at_current_schema_version(app_settings):
    import sqlite3

    # A brand-new database has no meta.version row and no legacy UNIQUE
    # recording constraint; it must be treated as already at the current
    # schema version so future migrations don't re-run against it.
    app_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(app_settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        assert db._schema_version_of(conn) == db._SCHEMA_VERSION

    db.list_songs()
    with sqlite3.connect(app_settings.db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    assert int(row[0]) == db._SCHEMA_VERSION


def test_pre_versioning_database_is_detected_as_v1(app_settings):
    import sqlite3

    app_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(app_settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE songs (
                uuid TEXT PRIMARY KEY,
                musicbrainz_recording_id TEXT UNIQUE,
                current_path TEXT
            );
            """
        )
        assert db._schema_version_of(conn) == 1


def test_reset_queue_preserves_processed_history(app_settings):
    db.reset_queue(["A", "B"])
    db.mark_processed("A", title="Song", artists="Artist")
    db.reset_queue(["B", "C"])

    assert db.get_pending_urls() == ["B", "C"]
    import sqlite3

    with sqlite3.connect(app_settings.db_path) as conn:
        status = conn.execute("SELECT status FROM queue WHERE url = 'A'").fetchone()[0]
    assert status == "processed"


def test_reset_queue_preserves_attempts(app_settings):
    import sqlite3

    db.reset_queue(["A"])
    db.requeue(["A"])
    db.reset_queue(["A"])

    with sqlite3.connect(app_settings.db_path) as conn:
        attempts = conn.execute(
            "SELECT attempts FROM queue WHERE url = 'A'"
        ).fetchone()[0]
    assert attempts == 1


def test_requeue_preserves_processed_metadata(app_settings):
    import sqlite3

    db.reset_queue(["A"])
    db.mark_processed("A", title="Song", artists="Artist")
    db.requeue(["A"])

    with sqlite3.connect(app_settings.db_path) as conn:
        row = conn.execute(
            "SELECT title, artists, attempts FROM queue WHERE url = 'A'"
        ).fetchone()
    assert row == ("Song", "Artist", 1)
