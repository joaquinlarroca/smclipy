import json
import os
from pathlib import Path

from smclipy.helpers import sanitize_filename

DEFAULT_CONFIG = {
    "name": "smclipy",
    "path_to_music_folder": "./Music",
    "description_max_lines": 5,
}
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

    def __init__(self, raw: dict, base_dir: Path = Path(".")) -> None:
        self.raw = raw
        self.music_folder = Path(str(raw.get("path_to_music_folder", ".")))
        self.temp_folder = base_dir.joinpath(".temp")
        self.pending_ids_file = self.temp_folder.joinpath("pending_ids.txt")
        self.processed_ids_file = self.temp_folder.joinpath("processed_ids.txt")
        self.script_folder = self.music_folder.joinpath(
            sanitize_filename(str(raw.get("name", "smclipy")))
        )
        self.authors_file = self.script_folder.joinpath("authors.txt")
        self.songs_info = self.script_folder.joinpath("songs_info.txt")
        self.false_positives_file = self.script_folder.joinpath(
            "cropping_tool_false_positives.txt"
        )
        self.covers_folder = self.script_folder.joinpath("covers")
        self.description_max_lines = int(raw.get("description_max_lines", 5))


# Global settings singleton. This is a CLI: a single settings object lives for
# the whole process, created lazily by init()/settings() and shared across
# modules. Tests monkeypatch config._settings to isolate runs.
_settings: Settings | None = None


def _load_raw_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        print(
            "Looks like its your first time executing the script, "
            "please modify config.json to your liking! and read the README.md file"
        )
        raise SystemExit(0)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def setup_folders() -> None:
    s = settings()
    for folder in (s.script_folder, s.covers_folder, s.temp_folder):
        folder.mkdir(parents=True, exist_ok=True)


def init() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings(_load_raw_config())
        setup_folders()
    return _settings


def settings() -> Settings:
    return _settings if _settings is not None else init()
