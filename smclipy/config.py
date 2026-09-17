import json
import os
from pathlib import Path
from typing import Any

from smclipy.formats import SUPPORTED_FORMATS
from smclipy.helpers import sanitize_filename

DEFAULT_CONFIG: dict[str, Any] = {
    "name": "smclipy",
    "path_to_music_folder": "./Music",
    "description_max_lines": 5,
    "write_album_if_same_as_title": False,
    "audio_format": "mp3",
    "tag_fields": [
        "title",
        "artists",
        "album",
        "date",
        "album_artist",
        "track_number",
        "cover",
    ],
}

COVER_FIELD = "cover"
TAG_FIELDS: frozenset[str] = frozenset(
    {"title", "artists", "album", "date", "album_artist", "track_number", COVER_FIELD}
)
CONFIG_PATH = Path(
    os.environ.get(
        "SMCLIPY_CONFIG",
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "smclipy"
        / "config.json",
    )
)


class Settings:
    """Resolved, per-run configuration."""

    def __init__(self, raw: dict[str, Any]) -> None:
        raw_path = raw.get("path_to_music_folder", ".")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = "."
            print(
                "Warning: 'path_to_music_folder' must be a non-empty string "
                "path, defaulting to '.'"
            )
        self.music_folder = Path(raw_path)
        raw_name = raw.get("name", "smclipy")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raw_name = "smclipy"
            print("Warning: 'name' must be a non-empty string, using 'smclipy'")
        script_name: str = sanitize_filename(raw_name)
        if not script_name:
            script_name = "smclipy"
            print(
                "Warning: 'name' only contains characters that are invalid in "
                "filenames, using 'smclipy'"
            )
        self.script_folder: Path = self.music_folder.joinpath(script_name)
        self.temp_folder: Path = self.script_folder.joinpath(".temp")
        # Legacy text-file tracking paths, referenced only by the one-time
        # migration into the SQLite database (smclipy/db.py).
        self.pending_ids_file: Path = self.temp_folder.joinpath("pending_ids.txt")
        self.processed_ids_file: Path = self.temp_folder.joinpath("processed_ids.txt")
        self.authors_file: Path = self.script_folder.joinpath("authors.txt")
        self.tagged_files_file: Path = self.script_folder.joinpath("tagged_files.txt")
        self.false_positives_file: Path = self.script_folder.joinpath(
            "cropping_tool_false_positives.txt"
        )
        self.db_path: Path = self.script_folder.joinpath("smclipy.db")
        self.covers_folder: Path = self.script_folder.joinpath("covers")
        raw_value = raw.get("description_max_lines", 5)
        if isinstance(raw_value, bool):
            raw_value = 5
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = 5
        self.description_max_lines: int = max(0, parsed)
        raw_flag = raw.get("write_album_if_same_as_title", False)
        self.write_album_if_same_as_title: bool = (
            raw_flag if isinstance(raw_flag, bool) else False
        )
        raw_fields = raw.get("tag_fields", list(DEFAULT_CONFIG.get("tag_fields", [])))
        if isinstance(raw_fields, list):
            fields: list[str] = [
                field for field in raw_fields if isinstance(field, str)
            ]
        else:
            fields = []
        self.tag_fields: list[str] = [field for field in fields if field in TAG_FIELDS]
        raw_format = raw.get("audio_format", "mp3")
        if not isinstance(raw_format, str) or raw_format not in SUPPORTED_FORMATS:
            print(
                f"Warning: 'audio_format' must be one of "
                f"{', '.join(SUPPORTED_FORMATS)}, using 'mp3'"
            )
            raw_format = "mp3"
        self.audio_format: str = raw_format


# Global settings singleton. This is a CLI: a single settings object lives for
# the whole process, created lazily by init()/settings() and shared across
# modules. Tests monkeypatch config._settings to isolate runs.
_settings: Settings | None = None


def _load_raw_config(create_if_missing: bool = True) -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        if not create_if_missing:
            return dict(DEFAULT_CONFIG)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        print(
            "Looks like its your first time executing the script, "
            "please modify config.json to your liking! and read the README.md file"
        )
        raise SystemExit(0)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"Error: could not read config at {CONFIG_PATH}: {exc}")
        print("Fix or delete the config file, then run smclipy again.")
        raise SystemExit(1) from exc
    if not isinstance(raw, dict):
        print(
            f"Error: config at {CONFIG_PATH} must contain a JSON object, "
            f"found {type(raw).__name__}."
        )
        print("Fix or delete the config file, then run smclipy again.")
        raise SystemExit(1)
    merged = {**DEFAULT_CONFIG, **raw}
    if merged != raw:
        CONFIG_PATH.write_text(json.dumps(merged, indent=4), encoding="utf-8")
    return merged


def setup_folders() -> None:
    s: Settings = settings()
    try:
        for folder in (s.script_folder, s.covers_folder, s.temp_folder):
            folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"Error: could not create the smclipy folder structure at "
            f"'{s.script_folder}': {exc}"
        )
        print("Check that 'path_to_music_folder' points to a writable directory.")
        raise SystemExit(1) from exc


def init() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings(_load_raw_config())
        setup_folders()
    return _settings


def settings() -> Settings:
    return _settings if _settings is not None else init()
