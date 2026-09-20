"""SQLite-backed tracking database for smclipy.

Replaces the legacy line-per-entry text files (tagged_files.txt,
pending_ids.txt, processed_ids.txt, authors.txt, cropping tools' false
positive list) with a single persistent database keyed by a stable song
UUID stored in each audio file's tags (ID3 UFID for MP3, a custom atom or
comment for other formats), so a song can be renamed and still be tracked.

The legacy text files are imported once on first use by ``connect``.
"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smclipy.config import Settings, settings
from smclipy.helpers import normalize_author

_SONGS_COLUMNS = """    uuid TEXT PRIMARY KEY,
    source_url TEXT,
    musicbrainz_recording_id TEXT,
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
    updated_at TEXT"""

_SONGS_INDEXES = """CREATE INDEX IF NOT EXISTS idx_songs_current_path
    ON songs(current_path);
CREATE INDEX IF NOT EXISTS idx_songs_first_seen_path ON songs(first_seen_path);
CREATE INDEX IF NOT EXISTS idx_songs_source_url ON songs(source_url);
CREATE INDEX IF NOT EXISTS idx_songs_recording_id
    ON songs(musicbrainz_recording_id);"""

SCHEMA: str = f"""
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS songs (
{_SONGS_COLUMNS}
);

{_SONGS_INDEXES}

CREATE TABLE IF NOT EXISTS song_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_uuid TEXT NOT NULL REFERENCES songs(uuid) ON DELETE CASCADE,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{{}}',
    at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_song ON song_events(song_uuid);

CREATE TABLE IF NOT EXISTS queue (
    url TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    title TEXT,
    artists TEXT,
    album TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);

CREATE TABLE IF NOT EXISTS authors (
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    PRIMARY KEY (normalized)
);

CREATE TABLE IF NOT EXISTS false_positives (
    stem TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS playlists (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_entries (
    playlist_name TEXT NOT NULL REFERENCES playlists(name)
        ON DELETE CASCADE ON UPDATE CASCADE,
    song_uuid TEXT NOT NULL REFERENCES songs(uuid) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (playlist_name, song_uuid)
);

CREATE INDEX IF NOT EXISTS idx_entries_playlist_position
    ON playlist_entries(playlist_name, position);
"""

MB_STATUS_TAGGED = "tagged"
MB_STATUS_SKIPPED = "skipped"
MB_STATUS_NOT_FOUND = "not_found"

COVER_STATUS_NOT_PILLARBOX = "not_pillarbox"
COVER_STATUS_FALSE_POSITIVE = "false_positive"
COVER_STATUS_CROPPED = "cropped"

QUEUE_PENDING = "pending"
QUEUE_PROCESSED = "processed"
QUEUE_FAILED = "failed"

_LEGACY_MIGRATED_KEY = "legacy_migrated"

# Bumped when the schema changes; _migrate_schema applies incremental upgrades.
_SCHEMA_VERSION = 3


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _read_legacy_file(path: Path) -> list[str]:
    try:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (FileNotFoundError, OSError):
        return []


def _songs_recording_id_unique(conn: sqlite3.Connection) -> bool:
    """Whether songs.musicbrainz_recording_id still carries the legacy UNIQUE
    constraint from the first release of the schema."""
    for index in conn.execute("PRAGMA index_list('songs')"):
        if index["origin"] != "u":
            continue
        columns = [
            row["name"] for row in conn.execute(f"PRAGMA index_info('{index['name']}')")
        ]
        if "musicbrainz_recording_id" in columns:
            return True
    return False


def _schema_version_of(conn: sqlite3.Connection) -> int:
    """The schema version a database was created/last migrated to.

    A missing meta row means either a brand-new database (created by
    ``SCHEMA`` at the current version) or a pre-versioning database from the
    first release, which is recognizable by its legacy UNIQUE constraint on
    ``musicbrainz_recording_id``.
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is not None:
        return int(row["value"])
    if _songs_recording_id_unique(conn):
        return 1
    return _SCHEMA_VERSION


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations recorded in meta.schema_version."""
    current: int = _schema_version_of(conn)
    if current >= _SCHEMA_VERSION:
        # Fresh databases are created by SCHEMA at the current version but
        # carry no version row yet; record it so the DB is self-describing.
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            conn.commit()
        return
    if current < 2 and _songs_recording_id_unique(conn):
        # A UNIQUE constraint on musicbrainz_recording_id makes a second song
        # matched against the same MusicBrainz recording raise an
        # IntegrityError; rebuild the table with a plain index instead.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            f"""
            DROP TABLE IF EXISTS songs_new;
            CREATE TABLE songs_new (
{_SONGS_COLUMNS}
            );
            INSERT INTO songs_new SELECT * FROM songs;
            DROP TABLE songs;
            ALTER TABLE songs_new RENAME TO songs;
            {_SONGS_INDEXES}
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")
    # Playlists arrive in v3. connect() runs SCHEMA before this function, and
    # SCHEMA already creates the playlist tables, so only the version bump
    # below is needed to finish the migration.
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(_SCHEMA_VERSION),),
    )
    conn.commit()


def connect() -> sqlite3.Connection:
    """Open the database, creating the schema and running the one-time
    import of legacy text tracking files."""

    s: Settings = settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(s.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    _migrate_legacy(conn, s)
    return conn


def _rw(
    conn: sqlite3.Connection | None,
) -> tuple[sqlite3.Connection, bool]:
    """Return the connection to use and whether this call owns it (and must
    commit/close it). An owned connection is closed on the way out."""
    if conn is not None:
        return conn, False
    return connect(), True


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block of database work on one connection with a single commit."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_legacy(conn: sqlite3.Connection, s: Settings) -> None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_LEGACY_MIGRATED_KEY,)
    ).fetchone()
    if row is not None:
        return
    for url, status in (
        (record, QUEUE_PENDING) for record in _read_legacy_file(s.pending_ids_file)
    ):
        now: str = _now()
        conn.execute(
            "INSERT OR REPLACE INTO queue (url, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (url, status, now, now),
        )
    for url in _read_legacy_file(s.processed_ids_file):
        now = _now()
        conn.execute(
            "INSERT OR REPLACE INTO queue (url, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (url, QUEUE_PROCESSED, now, now),
        )
    for name in _read_legacy_file(s.authors_file):
        conn.execute(
            "INSERT OR IGNORE INTO authors (name, normalized) VALUES (?, ?)",
            (name, normalize_author(name)),
        )
    for stem in _read_legacy_file(s.false_positives_file):
        conn.execute(
            "INSERT OR REPLACE INTO false_positives (stem) VALUES (?)", (stem,)
        )
    for rel in _read_legacy_file(s.tagged_files_file):
        song_uuid: str = _new_uuid()
        now = _now()
        # Legacy rows were tagged by the manual download flow, never by
        # MusicBrainz: leave musicbrainz_status NULL and tagged=0 so the new
        # `tag` command offers them for MusicBrainz metadata like any other
        # untagged song instead of skipping the whole library.
        conn.execute(
            "INSERT OR IGNORE INTO songs (uuid, current_path, first_seen_path,"
            " first_seen_at, last_seen_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (song_uuid, rel, rel, now, now, now),
        )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, '1')",
        (_LEGACY_MIGRATED_KEY,),
    )
    conn.commit()


def _event(
    conn: sqlite3.Connection, song_uuid: str, event: str, detail: dict[str, Any] | None
) -> None:
    conn.execute(
        "INSERT INTO song_events (song_uuid, event, detail, at) VALUES (?, ?, ?, ?)",
        (song_uuid, event, json.dumps(detail or {}), _now()),
    )


def list_songs(conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    c, owner = _rw(conn)
    try:
        return c.execute(
            "SELECT * FROM songs"
            " ORDER BY current_path IS NULL, title COLLATE NOCASE, current_path"
        ).fetchall()
    finally:
        if owner:
            c.close()


def get_song_by_uuid(
    song_uuid: str, conn: sqlite3.Connection | None = None
) -> sqlite3.Row | None:
    c, owner = _rw(conn)
    try:
        return c.execute(  # type: ignore[no-any-return]
            "SELECT * FROM songs WHERE uuid = ?", (song_uuid,)
        ).fetchone()
    finally:
        if owner:
            c.close()


def get_song_by_path(
    rel_path: str, conn: sqlite3.Connection | None = None
) -> list[sqlite3.Row]:
    c, owner = _rw(conn)
    try:
        return c.execute(
            "SELECT * FROM songs WHERE current_path = ? OR first_seen_path = ?",
            (rel_path, rel_path),
        ).fetchall()
    finally:
        if owner:
            c.close()


def insert_song(
    *,
    song_uuid: str | None = None,
    current_path: str,
    title: str = "",
    artists: str = "",
    album: str = "",
    date: str = "",
    genre: str = "",
    album_artist: str = "",
    track_number: str = "",
    has_cover: bool = False,
    source_url: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    if song_uuid is None:
        song_uuid = _new_uuid()
    now: str = _now()
    c, owner = _rw(conn)
    try:
        c.execute(
            "INSERT INTO songs (uuid, source_url, current_path, first_seen_path,"
            " title, artists, album, date, genre, album_artist, track_number,"
            " has_cover, first_seen_at, last_seen_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                song_uuid,
                source_url,
                current_path,
                current_path,
                title,
                artists,
                album,
                date,
                genre,
                album_artist,
                track_number,
                int(has_cover),
                now,
                now,
                now,
            ),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()
    return song_uuid


def set_song_path(
    song_uuid: str, new_path: str, conn: sqlite3.Connection | None = None
) -> None:
    now: str = _now()
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE songs SET current_path = ?, first_seen_path ="
            " COALESCE(first_seen_path, ?), last_seen_at = ?, updated_at = ?"
            " WHERE uuid = ?",
            (new_path, new_path, now, now, song_uuid),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def touch_song(song_uuid: str, conn: sqlite3.Connection | None = None) -> None:
    now: str = _now()
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE songs SET last_seen_at = ?, updated_at = ? WHERE uuid = ?",
            (now, now, song_uuid),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def update_song_metadata(
    song_uuid: str,
    *,
    title: str | None = None,
    artists: str | None = None,
    album: str | None = None,
    date: str | None = None,
    genre: str | None = None,
    album_artist: str | None = None,
    track_number: str | None = None,
    has_cover: bool | None = None,
    source_url: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    assignments: list[str] = []
    values: list[object] = []
    if title is not None:
        assignments.append("title = ?")
        values.append(title)
    if artists is not None:
        assignments.append("artists = ?")
        values.append(artists)
    if album is not None:
        assignments.append("album = ?")
        values.append(album)
    if date is not None:
        assignments.append("date = ?")
        values.append(date)
    if genre is not None:
        assignments.append("genre = ?")
        values.append(genre)
    if album_artist is not None:
        assignments.append("album_artist = ?")
        values.append(album_artist)
    if track_number is not None:
        assignments.append("track_number = ?")
        values.append(track_number)
    if has_cover is not None:
        assignments.append("has_cover = ?")
        values.append(int(has_cover))
    if source_url is not None:
        assignments.append("source_url = ?")
        values.append(source_url)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(_now())
    values.append(song_uuid)
    c, owner = _rw(conn)
    try:
        c.execute(f"UPDATE songs SET {', '.join(assignments)} WHERE uuid = ?", values)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def mark_song_missing(song_uuid: str, conn: sqlite3.Connection | None = None) -> None:
    now: str = _now()
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE songs SET current_path = NULL, last_seen_at = ?, updated_at = ?"
            " WHERE uuid = ?",
            (now, now, song_uuid),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def record_mb_status(
    song_uuid: str,
    status: str,
    *,
    recording_id: str | None = None,
    release_group_id: str | None = None,
    tagged: bool = False,
    conn: sqlite3.Connection | None = None,
) -> None:
    assignments: list[str] = ["musicbrainz_status = ?"]
    values: list[object] = [status]
    if recording_id is not None:
        assignments.append("musicbrainz_recording_id = ?")
        values.append(recording_id)
    if release_group_id is not None:
        assignments.append("musicbrainz_release_group_id = ?")
        values.append(release_group_id)
    if tagged:
        assignments.append("tagged = 1")
    assignments.append("updated_at = ?")
    values.append(_now())
    values.append(song_uuid)
    c, owner = _rw(conn)
    try:
        c.execute(f"UPDATE songs SET {', '.join(assignments)} WHERE uuid = ?", values)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def set_cover_status(
    song_uuid: str, status: str, conn: sqlite3.Connection | None = None
) -> None:
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE songs SET cover_status = ?, updated_at = ? WHERE uuid = ?",
            (status, _now(), song_uuid),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def reset_mb_status(song_uuid: str, conn: sqlite3.Connection | None = None) -> None:
    """Forget a song's MusicBrainz match so the `tag` command offers it again."""
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE songs SET musicbrainz_status = NULL,"
            " musicbrainz_recording_id = NULL, musicbrainz_release_group_id = NULL,"
            " tagged = 0, updated_at = ? WHERE uuid = ?",
            (_now(), song_uuid),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def log_event(
    song_uuid: str,
    event: str,
    detail: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    c, owner = _rw(conn)
    try:
        _event(c, song_uuid, event, detail)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def get_events(
    song_uuid: str, conn: sqlite3.Connection | None = None
) -> list[sqlite3.Row]:
    c, owner = _rw(conn)
    try:
        return c.execute(
            "SELECT * FROM song_events WHERE song_uuid = ? ORDER BY at, id",
            (song_uuid,),
        ).fetchall()
    finally:
        if owner:
            c.close()


def reset_queue(urls: list[str]) -> None:
    """Make `urls` the pending queue without losing history.

    Processed rows are kept as history, existing rows keep their attempt
    counts, and only pending rows absent from `urls` are removed. New URLs
    are inserted as pending.
    """
    now: str = _now()
    with transaction() as conn:
        existing: set[str] = {
            row["url"] for row in conn.execute("SELECT url FROM queue").fetchall()
        }
        if urls:
            placeholders: str = ",".join("?" for _ in urls)
            conn.execute(
                f"DELETE FROM queue WHERE status = ? AND url NOT IN ({placeholders})",
                (QUEUE_PENDING, *urls),
            )
        else:
            conn.execute("DELETE FROM queue WHERE status = ?", (QUEUE_PENDING,))
        for url in urls:
            if url in existing:
                conn.execute(
                    "UPDATE queue SET status = ?, updated_at = ? WHERE url = ?",
                    (QUEUE_PENDING, now, url),
                )
            else:
                conn.execute(
                    "INSERT INTO queue (url, status, attempts, created_at, updated_at)"
                    " VALUES (?, ?, 0, ?, ?)",
                    (url, QUEUE_PENDING, now, now),
                )


def get_pending_urls(
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    c, owner = _rw(conn)
    try:
        return [
            row["url"]
            for row in c.execute(
                "SELECT url FROM queue WHERE status = ? ORDER BY rowid",
                (QUEUE_PENDING,),
            ).fetchall()
        ]
    finally:
        if owner:
            c.close()


def mark_processed(
    url: str,
    *,
    title: str | None = None,
    artists: str | None = None,
    album: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    c, owner = _rw(conn)
    try:
        c.execute(
            "UPDATE queue SET status = ?, title = COALESCE(?, title),"
            " artists = COALESCE(?, artists), album = COALESCE(?, album),"
            " updated_at = ? WHERE url = ?",
            (QUEUE_PROCESSED, title, artists, album, _now(), url),
        )
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def requeue(urls: list[str], error: str | None = None) -> None:
    """Re-pend URLs in place, incrementing their attempt count.

    Once a URL's attempts exceed ``max_download_attempts`` it is not re-queued:
    it is permanently marked ``failed`` so a dead link stops being re-offered
    on every resume. Uses UPDATE (not INSERT OR REPLACE) so rowids,
    timestamps, and any recorded title/artists/album metadata survive.
    """
    now: str = _now()
    max_attempts: int = settings().max_download_attempts
    with transaction() as conn:
        for url in urls:
            existing = conn.execute(
                "SELECT attempts, created_at FROM queue WHERE url = ?", (url,)
            ).fetchone()
            attempts: int = 1 if existing is None else int(existing["attempts"]) + 1
            status: str = QUEUE_PENDING if attempts <= max_attempts else QUEUE_FAILED
            if existing is None:
                conn.execute(
                    "INSERT INTO queue (url, status, attempts, last_error,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (url, status, attempts, error, now, now),
                )
            else:
                conn.execute(
                    "UPDATE queue SET status = ?, attempts = ?, last_error = ?,"
                    " updated_at = ? WHERE url = ?",
                    (status, attempts, error, now, url),
                )


def get_failed_urls(conn: sqlite3.Connection | None = None) -> list[str]:
    """URLs permanently dropped after exceeding the download retry limit."""
    c, owner = _rw(conn)
    try:
        return [
            row["url"]
            for row in c.execute(
                "SELECT url FROM queue WHERE status = ? ORDER BY rowid",
                (QUEUE_FAILED,),
            ).fetchall()
        ]
    finally:
        if owner:
            c.close()


def get_authors(conn: sqlite3.Connection | None = None) -> list[str]:
    c, owner = _rw(conn)
    try:
        return [
            row["name"] for row in c.execute("SELECT name FROM authors ORDER BY rowid")
        ]
    finally:
        if owner:
            c.close()


def add_authors(names: list[str], conn: sqlite3.Connection | None = None) -> list[str]:
    added: list[str] = []
    c, owner = _rw(conn)
    try:
        for name in names:
            normalized: str = normalize_author(name)
            if not normalized:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO authors (name, normalized) VALUES (?, ?)",
                (name, normalized),
            )
            if cur.rowcount:
                added.append(name)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()
    return added


def get_false_positives(
    conn: sqlite3.Connection | None = None,
) -> set[str]:
    c, owner = _rw(conn)
    try:
        return {row["stem"] for row in c.execute("SELECT stem FROM false_positives")}
    finally:
        if owner:
            c.close()


def add_false_positive(stem: str, conn: sqlite3.Connection | None = None) -> None:
    c, owner = _rw(conn)
    try:
        c.execute("INSERT OR REPLACE INTO false_positives (stem) VALUES (?)", (stem,))
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def _touch_playlist(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE playlists SET updated_at = ? WHERE name = ?", (_now(), name))


def _renumber_uuids(conn: sqlite3.Connection, name: str, song_uuids: list[str]) -> None:
    """Rewrite positions 1..n preserving each entry's added_at timestamp."""
    for position, song_uuid in enumerate(song_uuids, start=1):
        conn.execute(
            "UPDATE playlist_entries SET position = ?"
            " WHERE playlist_name = ? AND song_uuid = ?",
            (position, name, song_uuid),
        )


def list_playlists(conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    c, owner = _rw(conn)
    try:
        return c.execute(
            "SELECT p.*, COUNT(e.song_uuid) AS song_count"
            " FROM playlists p LEFT JOIN playlist_entries e"
            " ON e.playlist_name = p.name GROUP BY p.name"
            " ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
    finally:
        if owner:
            c.close()


def get_playlist(
    name: str, conn: sqlite3.Connection | None = None
) -> sqlite3.Row | None:
    c, owner = _rw(conn)
    try:
        return c.execute(  # type: ignore[no-any-return]
            "SELECT * FROM playlists WHERE name = ?", (name,)
        ).fetchone()
    finally:
        if owner:
            c.close()


def create_playlist(
    name: str, description: str = "", conn: sqlite3.Connection | None = None
) -> bool:
    if not name:
        return False
    now: str = _now()
    c, owner = _rw(conn)
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO playlists (name, description, created_at,"
            " updated_at) VALUES (?, ?, ?, ?)",
            (name, description, now, now),
        )
        if owner:
            c.commit()
        return bool(cur.rowcount)
    finally:
        if owner:
            c.close()


def rename_playlist(
    old_name: str, new_name: str, conn: sqlite3.Connection | None = None
) -> bool:
    if not old_name or not new_name or old_name == new_name:
        return False
    if get_playlist(new_name, conn=conn) is not None:
        return False
    c, owner = _rw(conn)
    try:
        cur = c.execute(
            "UPDATE playlists SET name = ?, updated_at = ? WHERE name = ?",
            (new_name, _now(), old_name),
        )
        if owner:
            c.commit()
        return bool(cur.rowcount)
    finally:
        if owner:
            c.close()


def delete_playlist(name: str, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a playlist; its entries are removed by the ON DELETE CASCADE."""
    c, owner = _rw(conn)
    try:
        cur = c.execute("DELETE FROM playlists WHERE name = ?", (name,))
        if owner:
            c.commit()
        return bool(cur.rowcount)
    finally:
        if owner:
            c.close()


def add_song_to_playlist(
    name: str, song_uuid: str, conn: sqlite3.Connection | None = None
) -> bool:
    """Append ``song_uuid`` to the playlist; False when already present."""
    c, owner = _rw(conn)
    try:
        row = c.execute("SELECT 1 FROM playlists WHERE name = ?", (name,)).fetchone()
        if row is None:
            return False
        existing = c.execute(
            "SELECT 1 FROM playlist_entries WHERE playlist_name = ? AND song_uuid = ?",
            (name, song_uuid),
        ).fetchone()
        if existing is not None:
            return False
        row = c.execute(
            "SELECT COALESCE(MAX(position), 0) FROM playlist_entries"
            " WHERE playlist_name = ?",
            (name,),
        ).fetchone()
        c.execute(
            "INSERT INTO playlist_entries (playlist_name, song_uuid, position,"
            " added_at) VALUES (?, ?, ?, ?)",
            (name, song_uuid, int(row[0]) + 1, _now()),
        )
        _touch_playlist(c, name)
        if owner:
            c.commit()
        return True
    finally:
        if owner:
            c.close()


def remove_song_from_playlist(
    name: str, song_uuid: str, conn: sqlite3.Connection | None = None
) -> bool:
    """Remove ``song_uuid`` and renumber the remaining positions 1..n."""
    c, owner = _rw(conn)
    try:
        cur = c.execute(
            "DELETE FROM playlist_entries WHERE playlist_name = ? AND song_uuid = ?",
            (name, song_uuid),
        )
        if cur.rowcount:
            remaining = [
                row["song_uuid"]
                for row in c.execute(
                    "SELECT song_uuid FROM playlist_entries WHERE playlist_name = ?"
                    " ORDER BY position",
                    (name,),
                )
            ]
            _renumber_uuids(c, name, remaining)
            _touch_playlist(c, name)
        if owner:
            c.commit()
        return bool(cur.rowcount)
    finally:
        if owner:
            c.close()


def get_playlist_songs(
    name: str, conn: sqlite3.Connection | None = None
) -> list[sqlite3.Row]:
    c, owner = _rw(conn)
    try:
        return c.execute(
            "SELECT e.position, e.added_at, s.*"
            " FROM playlist_entries e JOIN songs s ON s.uuid = e.song_uuid"
            " WHERE e.playlist_name = ? ORDER BY e.position",
            (name,),
        ).fetchall()
    finally:
        if owner:
            c.close()


def move_playlist_song(
    name: str,
    song_uuid: str,
    target_position: int,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Move ``song_uuid`` so it lands at 1-based ``target_position``."""
    c, owner = _rw(conn)
    try:
        rows = c.execute(
            "SELECT song_uuid FROM playlist_entries WHERE playlist_name = ?"
            " ORDER BY position",
            (name,),
        ).fetchall()
        uuids: list[str] = [row["song_uuid"] for row in rows]
        if song_uuid not in uuids or len(uuids) < 2:
            return False
        target_position = max(1, min(target_position, len(uuids)))
        uuids.remove(song_uuid)
        uuids.insert(target_position - 1, song_uuid)
        _renumber_uuids(c, name, uuids)
        _touch_playlist(c, name)
        if owner:
            c.commit()
        return True
    finally:
        if owner:
            c.close()


def sort_playlist(
    name: str,
    key: str = "title",
    reverse: bool = False,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Rewrites playlist order by a song column, case-insensitively.

    Empty title/artists/album values fall back to the current path, and
    missing songs keep their slot but sort last. ``key`` may be ``title``,
    ``artists``, ``album``, ``path``, or ``added`` (time added).
    """
    if key not in {"title", "artists", "album", "path", "added"}:
        raise ValueError(f"Unknown sort key {key!r}")
    if key == "added":
        sort_expr: str = "e.added_at"
    elif key == "path":
        sort_expr = "s.current_path"
    else:
        sort_expr = f"COALESCE(NULLIF(s.{key}, ''), s.current_path)"
    order: str = "DESC" if reverse else "ASC"
    c, owner = _rw(conn)
    try:
        rows = c.execute(
            "SELECT e.song_uuid FROM playlist_entries e"
            " JOIN songs s ON s.uuid = e.song_uuid"
            " WHERE e.playlist_name = ?"
            " ORDER BY s.current_path IS NULL ASC,"
            f" lower({sort_expr}) {order}, e.position",
            (name,),
        ).fetchall()
        _renumber_uuids(c, name, [row["song_uuid"] for row in rows])
        _touch_playlist(c, name)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()


def set_playlist_songs(
    name: str, song_uuids: list[str], conn: sqlite3.Connection | None = None
) -> None:
    """Replace a playlist's contents in the given order (skips duplicates)."""
    unique: list[str] = list(dict.fromkeys(song_uuids))
    c, owner = _rw(conn)
    try:
        if (
            c.execute("SELECT 1 FROM playlists WHERE name = ?", (name,)).fetchone()
            is None
        ):
            return
        c.execute("DELETE FROM playlist_entries WHERE playlist_name = ?", (name,))
        now: str = _now()
        for position, song_uuid in enumerate(unique, start=1):
            c.execute(
                "INSERT INTO playlist_entries (playlist_name, song_uuid, position,"
                " added_at) VALUES (?, ?, ?, ?)",
                (name, song_uuid, position, now),
            )
        _touch_playlist(c, name)
        if owner:
            c.commit()
    finally:
        if owner:
            c.close()
