"""smclipy - simple music cli py."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

try:
    __version__: str = version("smclipy")
except PackageNotFoundError:
    _pyproject: dict[str, Any] = tomllib.loads(
        Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text()
    )
    __version__ = _pyproject["project"]["version"]
