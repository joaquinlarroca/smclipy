import re


def sanitize_filename(filename: str) -> str:
    clean_name = re.sub(r'[\\/*?:"<>|]', "", filename)
    return clean_name.strip(" .")


def get_list_from_split_str(string: str, pattern: str) -> list[str]:
    return [a.strip() for a in re.split(re.escape(pattern) + "+", string) if a.strip()]


def get_unique_items(source_items: list[str], target_list: list[str]) -> list[str]:
    return [item for item in source_items if item not in target_list]


def normalize_author(name: str) -> str:
    return "".join(name.split()).casefold()


def resolve_known_authors(artists: list[str], known_authors: list[str]) -> list[str]:
    known_by_normalized = {
        normalize_author(known): known
        for known in known_authors
        if normalize_author(known)
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for artist in artists:
        normalized = normalize_author(artist)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(known_by_normalized.get(normalized, artist))
    return resolved
