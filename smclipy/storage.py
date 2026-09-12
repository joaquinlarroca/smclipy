import os
from pathlib import Path

_seen_cache: dict[Path, set[str]] = {}


def read_lines(path: Path) -> list[str]:
    try:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return []


def _cached_lines(path: Path) -> set[str]:
    known = _seen_cache.get(path)
    if known is None:
        known = set(read_lines(path))
        _seen_cache[path] = known
    return known


def append_unique_lines(path: Path, lines: list[str]) -> None:
    existing = _cached_lines(path)
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in existing:
            new_lines.append(stripped)
            existing.add(stripped)
    if not new_lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode="a", encoding="utf-8") as file:
        for line in new_lines:
            file.write(f"{line}\n")


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [line.strip() for line in lines if line.strip()]
    content = "\n".join(cleaned) + ("\n" if cleaned else "")
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)
    _seen_cache[path] = set(cleaned)
