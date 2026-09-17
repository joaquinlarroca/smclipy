import re


def sanitize_filename(filename: str) -> str:
    clean_name: str = re.sub(r'[\\/*?:"<>|]', "", filename)
    return clean_name.strip(" .")


def split_authors(string: str) -> list[str]:
    return [a.strip() for a in re.split(r"[\\\\,]+", string) if a.strip()]


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
