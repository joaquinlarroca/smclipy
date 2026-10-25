import re

# Leave room for extensions and suffixes so the final name stays under the
# usual 255-byte filesystem limit (UTF-8 names can take more than one byte
# per character).
_MAX_FILENAME_BYTES = 200


def sanitize_filename(filename: str) -> str:
    clean_name: str = re.sub(r'[\\/*?:"<>|]', "", filename).strip(" .")
    encoded: bytes = clean_name.encode("utf-8")
    if len(encoded) <= _MAX_FILENAME_BYTES:
        return clean_name
    # Truncate by characters so a multi-byte codepoint is never split.
    truncated: list[str] = []
    used: int = 0
    for char in clean_name:
        size: int = len(char.encode("utf-8"))
        if used + size > _MAX_FILENAME_BYTES:
            break
        truncated.append(char)
        used += size
    return "".join(truncated).strip(" .")


def split_authors(string: str) -> list[str]:
    """Split a joined artist string on commas or (legacy) backslashes.

    A separator can be escaped with a backslash: ``\\,`` is a literal comma
    (so "Earth, Wind & Fire" survives :func:`join_authors`) and ``\\\\`` is a
    literal backslash. Any other backslash is still a separator, because older
    versions joined artists with ``\\`` and that data must keep splitting.
    """
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(string):
        char: str = string[index]
        if char == "\\":
            following: str = string[index + 1] if index + 1 < len(string) else ""
            if following in ("\\", ","):
                current.append(following)
                index += 2
                continue
            parts.append("".join(current))
            current = []
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def join_authors(authors: list[str], separator: str = ", ") -> str:
    """Join artists so :func:`split_authors` can recover the exact names."""
    escaped: list[str] = [
        author.replace("\\", "\\\\").replace(",", "\\,") for author in authors
    ]
    return separator.join(escaped)


def display_authors(authors: str) -> str:
    """Render a stored artist string for humans (without escape characters)."""
    return ", ".join(split_authors(authors))


def normalize_author(name: str) -> str:
    return "".join(name.split()).casefold()


def resolve_known_authors(artists: list[str], known_authors: list[str]) -> list[str]:
    known_by_normalized: dict[str, str] = {
        normalize_author(known): known
        for known in known_authors
        if normalize_author(known)
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for artist in artists:
        normalized: str = normalize_author(artist)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(known_by_normalized.get(normalized, artist))
    return resolved


def distinct_authors(authors: list[str], known_authors: list[str]) -> list[str]:
    """Return `authors` entries not already in `known_authors`, ignoring
    case and whitespace so a differently-spelled duplicate isn't kept."""
    known_by_normalized: set[str] = {
        normalize_author(author) for author in known_authors if normalize_author(author)
    }
    distinct: list[str] = []
    seen: set[str] = set()
    for author in authors:
        normalized: str = normalize_author(author)
        if not normalized or normalized in known_by_normalized or normalized in seen:
            continue
        seen.add(normalized)
        distinct.append(author)
    return distinct
