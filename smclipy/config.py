import json
import os
import sys
from pathlib import Path
from typing import Any

from yt_dlp.cookies import SUPPORTED_BROWSERS

from smclipy.formats import SUPPORTED_FORMATS
from smclipy.helpers import sanitize_filename

DEFAULT_CONFIG: dict[str, Any] = {
    "name": "smclipy",
    "path_to_music_folder": "./Music",
    "description_max_lines": 5,
    "write_album_if_same_as_title": False,
    "clean_unwanted_tags": False,
    "audio_format": "mp3",
    "max_download_attempts": 3,
    "max_backups": -1,
    "cookies": "",
    "cookies_from_browser": "",
    "source_preferences": ["youtube", "soundcloud"],
    "tag_fields": [
        "title",
        "artists",
        "album",
        "date",
        "genre",
        "album_artist",
        "track_number",
        "cover",
    ],
}

SOURCE_PROVIDER_ALIASES: dict[str, str] = {
    "youtube": "youtube",
    "yt": "youtube",
    "soundcloud": "soundcloud",
    "sc": "soundcloud",
}
SOURCE_PROVIDERS: tuple[str, ...] = ("youtube", "soundcloud")
DEFAULT_SOURCE_PREFERENCES: list[str] = ["youtube", "soundcloud"]

COVER_FIELD = "cover"
TAG_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "artists",
        "album",
        "date",
        "genre",
        "album_artist",
        "track_number",
        COVER_FIELD,
    }
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
                "path, defaulting to '.'",
                file=sys.stderr,
            )
        self.music_folder = Path(raw_path).expanduser()
        raw_name = raw.get("name", "smclipy")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raw_name = "smclipy"
            print(
                "Warning: 'name' must be a non-empty string, using 'smclipy'",
                file=sys.stderr,
            )
        script_name: str = sanitize_filename(raw_name)
        if not script_name:
            script_name = "smclipy"
            print(
                "Warning: 'name' only contains characters that are invalid in "
                "filenames, using 'smclipy'",
                file=sys.stderr,
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
        self.backups_folder: Path = self.script_folder.joinpath("backups")
        raw_value = raw.get("description_max_lines", 5)
        if isinstance(raw_value, bool):
            raw_value = 5
            print(
                "Warning: 'description_max_lines' must be a non-negative "
                "integer, using 5",
                file=sys.stderr,
            )
        try:
            if isinstance(raw_value, float) and raw_value != int(raw_value):
                raise ValueError
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = 5
            print(
                "Warning: 'description_max_lines' must be a non-negative "
                "integer, using 5",
                file=sys.stderr,
            )
        if parsed < 0:
            parsed = 0
            print(
                "Warning: 'description_max_lines' must be a non-negative "
                "integer, using 0",
                file=sys.stderr,
            )
        self.description_max_lines: int = parsed
        raw_flag = raw.get("write_album_if_same_as_title", False)
        self.write_album_if_same_as_title: bool = (
            raw_flag if isinstance(raw_flag, bool) else False
        )
        raw_clean = raw.get("clean_unwanted_tags", False)
        self.clean_unwanted_tags: bool = (
            raw_clean if isinstance(raw_clean, bool) else False
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
                f"{', '.join(SUPPORTED_FORMATS)}, using 'mp3'",
                file=sys.stderr,
            )
            raw_format = "mp3"
        self.audio_format: str = raw_format
        raw_attempts = raw.get("max_download_attempts", 3)
        if isinstance(raw_attempts, bool):
            raw_attempts = 3
            print(
                "Warning: 'max_download_attempts' must be a positive integer, using 3",
                file=sys.stderr,
            )
        try:
            parsed_attempts = int(raw_attempts)
        except (TypeError, ValueError):
            parsed_attempts = 3
            print(
                "Warning: 'max_download_attempts' must be a positive integer, using 3",
                file=sys.stderr,
            )
        if parsed_attempts < 1:
            parsed_attempts = 3
            print(
                "Warning: 'max_download_attempts' must be a positive integer, using 3",
                file=sys.stderr,
            )
        self.max_download_attempts: int = parsed_attempts
        raw_backups = raw.get("max_backups", -1)
        if isinstance(raw_backups, bool):
            raw_backups = -1
            print(
                "Warning: 'max_backups' must be -1 (unlimited) or a positive "
                "integer, using -1",
                file=sys.stderr,
            )
        try:
            if isinstance(raw_backups, float) and raw_backups != int(raw_backups):
                raise ValueError
            parsed_backups = int(raw_backups)
        except (TypeError, ValueError):
            parsed_backups = -1
            print(
                "Warning: 'max_backups' must be -1 (unlimited) or a positive "
                "integer, using -1",
                file=sys.stderr,
            )
        if parsed_backups == 0 or parsed_backups < -1:
            parsed_backups = -1
            print(
                "Warning: 'max_backups' must be -1 (unlimited) or a positive "
                "integer, using -1",
                file=sys.stderr,
            )
        self.max_backups: int = parsed_backups
        raw_cookies = raw.get("cookies", "")
        if not isinstance(raw_cookies, str):
            raw_cookies = ""
            print(
                "Warning: 'cookies' must be a non-empty string path, ignoring it",
                file=sys.stderr,
            )
        elif raw_cookies and not raw_cookies.strip():
            raw_cookies = ""
            print("Warning: 'cookies' is blank, ignoring it", file=sys.stderr)
        cookies_value: str = raw_cookies.strip()
        if cookies_value:
            cookies_path = Path(cookies_value).expanduser()
            if not cookies_path.is_file() or not os.access(cookies_path, os.R_OK):
                print(
                    f"Warning: 'cookies' file does not exist or is not readable: "
                    f"'{cookies_path}', ignoring it",
                    file=sys.stderr,
                )
                cookies_value = ""
            else:
                cookies_value = str(cookies_path)
        self.cookies: str = cookies_value
        raw_browser = raw.get("cookies_from_browser", "")
        if not isinstance(raw_browser, str):
            raw_browser = ""
            print(
                "Warning: 'cookies_from_browser' must be a non-empty string, "
                "ignoring it",
                file=sys.stderr,
            )
        elif raw_browser and not raw_browser.strip():
            raw_browser = ""
            print(
                "Warning: 'cookies_from_browser' is blank, ignoring it",
                file=sys.stderr,
            )
        browser_value: str = raw_browser.strip()
        if browser_value and browser_value.split(":", 1)[0] not in SUPPORTED_BROWSERS:
            print(
                f"Warning: 'cookies_from_browser' '{browser_value}' is not "
                f"supported by yt-dlp, ignoring it",
                file=sys.stderr,
            )
            browser_value = ""
        if cookies_value and browser_value:
            browser_value = ""
            print(
                "Warning: both 'cookies' and 'cookies_from_browser' are set; "
                "using the cookies file and ignoring the browser.",
                file=sys.stderr,
            )
        self.cookies_from_browser: str = browser_value
        raw_sources = raw.get("source_preferences", DEFAULT_SOURCE_PREFERENCES)
        if not isinstance(raw_sources, list):
            print(
                "Warning: 'source_preferences' must be a list of providers "
                f"({', '.join(SOURCE_PROVIDERS)}), using the default",
                file=sys.stderr,
            )
            raw_sources = list(DEFAULT_SOURCE_PREFERENCES)
        preferences: list[str] = []
        for entry in raw_sources:
            if not isinstance(entry, str):
                print(
                    "Warning: 'source_preferences' entries must be strings, "
                    f"ignoring {entry!r}",
                    file=sys.stderr,
                )
                continue
            provider = SOURCE_PROVIDER_ALIASES.get(entry.strip().casefold())
            if provider is None:
                print(
                    f"Warning: unknown source provider {entry!r}, ignoring it "
                    f"(use {', '.join(SOURCE_PROVIDERS)})",
                    file=sys.stderr,
                )
                continue
            if provider not in preferences:
                preferences.append(provider)
        if not preferences:
            print(
                "Warning: 'source_preferences' has no valid providers, "
                f"using the default ({', '.join(DEFAULT_SOURCE_PREFERENCES)})",
                file=sys.stderr,
            )
            preferences = list(DEFAULT_SOURCE_PREFERENCES)
        self.source_preferences: list[str] = preferences


# Global settings singleton. This is a CLI: a single settings object lives for
# the whole process, created lazily by init()/settings() and shared across
# modules. Tests monkeypatch config._settings to isolate runs.
_settings: Settings | None = None


def _load_raw_config(
    create_if_missing: bool = True, *, write_defaults: bool = True
) -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        if not create_if_missing:
            return dict(DEFAULT_CONFIG)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        print(
            "Looks like its your first time executing the script, "
            "please modify config.json to your liking! and read the README.md file",
            file=sys.stderr,
        )
        raise SystemExit(1)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"Error: could not read config at {CONFIG_PATH}: {exc}", file=sys.stderr)
        print("Fix or delete the config file, then run smclipy again.", file=sys.stderr)
        raise SystemExit(1) from exc
    if not isinstance(raw, dict):
        print(
            f"Error: config at {CONFIG_PATH} must contain a JSON object, "
            f"found {type(raw).__name__}.",
            file=sys.stderr,
        )
        print("Fix or delete the config file, then run smclipy again.", file=sys.stderr)
        raise SystemExit(1)
    merged = {**DEFAULT_CONFIG, **raw}
    if merged != raw and write_defaults:
        # Filling in new default keys is a convenience, not a requirement:
        # a read-only config must not turn every command into a crash.
        try:
            CONFIG_PATH.write_text(json.dumps(merged, indent=4), encoding="utf-8")
        except OSError as exc:
            print(
                f"Warning: could not update {CONFIG_PATH} with the missing "
                f"default keys ({exc}); using them for this run only.",
                file=sys.stderr,
            )
    return merged


def setup_folders() -> None:
    s: Settings = settings()
    try:
        for folder in (
            s.script_folder,
            s.covers_folder,
            s.temp_folder,
            s.backups_folder,
        ):
            folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"Error: could not create the smclipy folder structure at "
            f"'{s.script_folder}': {exc}",
            file=sys.stderr,
        )
        print(
            "Check that 'path_to_music_folder' points to a writable directory.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def init() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings(_load_raw_config())
        setup_folders()
    return _settings


def settings() -> Settings:
    return _settings if _settings is not None else init()
