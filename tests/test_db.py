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


def test_requeue_marks_url_failed_after_max_attempts(app_settings):
    import sqlite3

    db.reset_queue(["A"])
    db.requeue(["A"])
    db.requeue(["A"])
    db.requeue(["A"])
    assert db.get_pending_urls() == ["A"]
    assert db.get_failed_urls() == []

    db.requeue(["A"], error="HTTP Error 404")

    assert db.get_pending_urls() == []
    assert db.get_failed_urls() == ["A"]
    with sqlite3.connect(app_settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, attempts, last_error FROM queue WHERE url = 'A'"
        ).fetchone()
    assert row["status"] == db.QUEUE_FAILED
    assert row["attempts"] == 4
    assert row["last_error"] == "HTTP Error 404"


def test_requeue_honors_custom_max_download_attempts(app_settings):
    import sqlite3

    app_settings.max_download_attempts = 1
    db.reset_queue(["A"])

    db.requeue(["A"])
    assert db.get_pending_urls() == ["A"]

    db.requeue(["A"])
    assert db.get_pending_urls() == []
    assert db.get_failed_urls() == ["A"]
    with sqlite3.connect(app_settings.db_path) as conn:
        status = conn.execute("SELECT status FROM queue WHERE url = 'A'").fetchone()[0]
    assert status == db.QUEUE_FAILED


def test_reset_queue_recovers_permanently_failed_url(app_settings):
    app_settings.max_download_attempts = 1
    db.reset_queue(["A"])
    db.requeue(["A"])
    db.requeue(["A"])
    assert db.get_failed_urls() == ["A"]

    db.reset_queue(["A"])
    assert db.get_pending_urls() == ["A"]
    assert db.get_failed_urls() == []


def test_playlist_crud(app_settings):
    assert db.create_playlist("Chill")
    assert not db.create_playlist("Chill")
    assert not db.create_playlist("")

    row = db.get_playlist("Chill")
    assert row is not None and row["description"] == ""
    rows = db.list_playlists()
    assert [r["name"] for r in rows] == ["Chill"]
    assert rows[0]["song_count"] == 0

    assert db.rename_playlist("Chill", "Weekend")
    assert db.get_playlist("Chill") is None
    assert db.get_playlist("Weekend") is not None
    assert not db.rename_playlist("Weekend", "Weekend")
    db.create_playlist("Occupied")
    assert not db.rename_playlist("Weekend", "Occupied")

    assert db.delete_playlist("Weekend")
    assert db.get_playlist("Weekend") is None


def test_playlist_entries_add_ordered_and_deduped(app_settings):
    db.create_playlist("P")
    alpha = db.insert_song(current_path="a.mp3", title="Alpha")
    beta = db.insert_song(current_path="b.mp3", title="Beta")
    gamma = db.insert_song(current_path="c.mp3", title="Gamma")

    assert db.add_song_to_playlist("P", gamma)
    assert db.add_song_to_playlist("P", alpha)
    assert not db.add_song_to_playlist("P", gamma)
    assert db.add_song_to_playlist("P", beta)

    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [gamma, alpha, beta]
    assert [r["position"] for r in rows] == [1, 2, 3]
    assert rows[0]["title"] == "Gamma"

    assert not db.add_song_to_playlist("Missing", alpha)


def test_playlist_remove_renumbers(app_settings):
    db.create_playlist("P")
    first = db.insert_song(current_path="a.mp3")
    second = db.insert_song(current_path="b.mp3")
    third = db.insert_song(current_path="c.mp3")
    for song_uuid in (first, second, third):
        db.add_song_to_playlist("P", song_uuid)

    assert db.remove_song_from_playlist("P", second)
    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [first, third]
    assert [r["position"] for r in rows] == [1, 2]
    assert not db.remove_song_from_playlist("P", second)


def test_playlist_move(app_settings):
    db.create_playlist("P")
    ids = [db.insert_song(current_path=f"{c}.mp3") for c in "abcd"]
    for song_uuid in ids:
        db.add_song_to_playlist("P", song_uuid)

    db.move_playlist_song("P", ids[3], 1)
    assert [r["uuid"] for r in db.get_playlist_songs("P")] == [
        ids[3],
        ids[0],
        ids[1],
        ids[2],
    ]

    db.move_playlist_song("P", ids[0], 4)
    assert [r["uuid"] for r in db.get_playlist_songs("P")] == [
        ids[3],
        ids[1],
        ids[2],
        ids[0],
    ]

    db.move_playlist_song("P", ids[0], 2)
    assert [r["uuid"] for r in db.get_playlist_songs("P")] == [
        ids[3],
        ids[0],
        ids[1],
        ids[2],
    ]

    db.move_playlist_song("P", ids[0], 2)
    assert [r["uuid"] for r in db.get_playlist_songs("P")] == [
        ids[3],
        ids[0],
        ids[1],
        ids[2],
    ]

    db.move_playlist_song("P", ids[3], 999)
    assert [r["uuid"] for r in db.get_playlist_songs("P")] == ids

    assert not db.move_playlist_song("P", "nope", 1)


def test_playlist_sort_by_title_and_reverse(app_settings):
    db.create_playlist("P")
    ids = [
        db.insert_song(current_path=f"{c}.mp3", title=title)
        for c, title in (("z", "Zeta"), ("a", "Alpha"), ("m", "Mike"))
    ]
    for song_uuid in ids:
        db.add_song_to_playlist("P", song_uuid)

    db.sort_playlist("P", key="title")
    assert [r["title"] for r in db.get_playlist_songs("P")] == [
        "Alpha",
        "Mike",
        "Zeta",
    ]

    db.sort_playlist("P", key="title", reverse=True)
    assert [r["title"] for r in db.get_playlist_songs("P")] == [
        "Zeta",
        "Mike",
        "Alpha",
    ]


def test_playlist_sort_falls_back_to_path_and_missing_last(app_settings):
    db.create_playlist("P")
    path_only = db.insert_song(current_path="zzz.mp3", title="")
    titled = db.insert_song(current_path="aaa.mp3", title="beta")
    missing = db.insert_song(current_path="gone.mp3", title="")
    db.mark_song_missing(missing)
    for song_uuid in (path_only, titled, missing):
        db.add_song_to_playlist("P", song_uuid)

    db.sort_playlist("P", key="title")
    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [titled, path_only, missing]


def test_playlist_set_contents_replaces_and_dedupes(app_settings):
    db.create_playlist("P")
    ids = [db.insert_song(current_path=f"{c}.mp3") for c in "abc"]
    db.set_playlist_songs("P", [ids[1], ids[0], ids[1], ids[2]])

    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [ids[1], ids[0], ids[2]]


def test_playlist_delete_cascades_entries(app_settings):
    db.create_playlist("P")
    song_uuid = db.insert_song(current_path="a.mp3")
    db.add_song_to_playlist("P", song_uuid)

    assert db.delete_playlist("P")
    assert db.get_playlist_songs("P") == []


def test_playlist_entries_cascade_when_song_deleted(app_settings):
    import sqlite3

    db.create_playlist("P")
    first = db.insert_song(current_path="a.mp3")
    second = db.insert_song(current_path="b.mp3")
    db.add_song_to_playlist("P", first)
    db.add_song_to_playlist("P", second)

    with sqlite3.connect(app_settings.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM songs WHERE uuid = ?", (first,))
        conn.commit()

    rows = db.get_playlist_songs("P")
    assert [r["uuid"] for r in rows] == [second]


def test_v2_database_migrates_to_v3_with_playlists(app_settings):
    import sqlite3

    if app_settings.db_path.exists():
        app_settings.db_path.unlink()
    app_settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(app_settings.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version', '2');
            CREATE TABLE songs (
        """
            + db._SONGS_COLUMNS
            + """
            );
            CREATE TABLE song_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_uuid TEXT,
                event TEXT,
                detail TEXT NOT NULL DEFAULT '{}',
                at TEXT NOT NULL
            );
            CREATE TABLE queue (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                title TEXT,
                artists TEXT,
                saved_at TEXT
            );
            CREATE TABLE authors (
                name TEXT NOT NULL,
                normalized TEXT NOT NULL,
                PRIMARY KEY (normalized)
            );
            CREATE TABLE false_positives (stem TEXT PRIMARY KEY);
            """
        )
        conn.commit()

    db.get_authors()

    with sqlite3.connect(app_settings.db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"playlists", "playlist_entries"} <= tables
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version["value"] == "3"
