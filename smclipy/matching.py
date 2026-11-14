"""Shared fuzzy matching for source candidates and MusicBrainz recordings."""

import re
import unicodedata
from difflib import SequenceMatcher

MATCH_DURATION_TOLERANCE = 2.0
_TITLE_TOKEN_RATIO = 0.85
_AUTHOR_NOISE = ("topic", "vevo", "official", "music")


def duration_rank(candidate_seconds: float | None, target_seconds: float | None) -> int:
    """Rank a candidate length: 0 match, 1 unknown, 2 mismatch."""
    if (
        target_seconds is None
        or target_seconds <= 0
        or candidate_seconds is None
        or candidate_seconds <= 0
    ):
        return 1
    if abs(candidate_seconds - target_seconds) <= MATCH_DURATION_TOLERANCE:
        return 0
    return 2


def duration_matches(
    candidate_seconds: float | None, target_seconds: float | None
) -> bool:
    """Whether the candidate length is within tolerance of the target."""
    return duration_rank(candidate_seconds, target_seconds) == 0


def clean_author(name: str) -> str:
    text: str = unicodedata.normalize("NFKD", name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[\W_]+", "", text.casefold())
    changed: bool = True
    while changed:
        changed = False
        for noise in _AUTHOR_NOISE:
            if text.endswith(noise) and len(text) > len(noise):
                text = text[: -len(noise)]
                changed = True
    return text


def name_score(target_name: str, other_name: str) -> int:
    """Compare two artist names: 2 exact, 1 similar, 0 unrelated."""
    target: str = clean_author(target_name)
    other: str = clean_author(other_name)
    if not target or not other:
        return 0
    if target == other:
        return 2
    if len(target) >= 3 and len(other) >= 3 and (target in other or other in target):
        return 1
    if (
        len(target) >= 4
        and len(other) >= 4
        and SequenceMatcher(None, target, other).ratio() >= 0.8
    ):
        return 1
    return 0


def clean_title(text: str) -> list[str]:
    """Case/accent-insensitive title tokens, keeping non-Latin scripts."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.findall(r"[^\W_]+", text.casefold())


def title_token_present(token: str, candidates: list[str]) -> bool:
    for other in candidates:
        if token == other:
            return True
        if (
            len(token) >= 4
            and len(other) >= 4
            and SequenceMatcher(None, token, other).ratio() >= _TITLE_TOKEN_RATIO
        ):
            return True
    return False


def title_score(song_title: str, candidate_title: str) -> int:
    """Compare titles: 2 exact phrase, 1 all words present, 0 unrelated."""
    target: list[str] = clean_title(song_title)
    candidate: list[str] = clean_title(candidate_title)
    if not target or not candidate:
        return 0
    span: int = len(target)
    if any(
        candidate[index : index + span] == target
        for index in range(len(candidate) - span + 1)
    ):
        return 2
    if all(title_token_present(token, candidate) for token in target):
        return 1
    if (
        SequenceMatcher(None, " ".join(target), " ".join(candidate)).ratio()
        >= _TITLE_TOKEN_RATIO
    ):
        return 1
    return 0
