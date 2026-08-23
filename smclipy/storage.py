from pathlib import Path


def read_lines(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return []


def append_unique_lines(path: Path, lines: list[str]) -> None:
    existing = set(read_lines(path))
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
    path.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
