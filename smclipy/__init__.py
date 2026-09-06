"""smclipy - simple music cli py."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("smclipy")
except PackageNotFoundError:
    _pyproject = tomllib.loads(
        Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text()
    )
    __version__ = _pyproject["project"]["version"]
