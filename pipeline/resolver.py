"""
Resolve a player's real name (from a tournament entry list) to their
Lichess and chess.com usernames.

Strategy:
  Lichess  — autocomplete search; pick the top result; confidence is
             determined by fuzzy name similarity.
  chess.com — try guessed username patterns in order; confidence is
              high if an early guess matches, low if a later one does.

Confidence levels: "high" | "low"
"""

from difflib import SequenceMatcher

import requests


_HIGH_THRESHOLD = 0.55   # similarity ratio for high confidence
_LOW_THRESHOLD  = 0.30   # below this → skip


def _similarity(a: str, b: str) -> float:
    """Case-insensitive character similarity between two name strings."""
    def norm(s: str) -> str:
        return "".join(s.lower().split()).replace(",", "").replace(".", "")
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def resolve_lichess(name: str) -> tuple[str | None, str | None]:
    """
    Search Lichess by display name. Returns (username, confidence) or (None, None).
    Imports lookup.lichess lazily so resolver is testable without network.
    """
    try:
        from lookup.lichess import search
        candidates = search(name, max_results=5)
    except Exception:
        return None, None

    if not candidates:
        return None, None

    best = candidates[0]
    sim = _similarity(name, best.get("display_name", ""))

    if sim >= _HIGH_THRESHOLD:
        return best["username"], "high"
    if sim >= _LOW_THRESHOLD:
        return best["username"], "low"
    return None, None


def resolve_chesscom(name: str) -> tuple[str | None, str | None]:
    """
    Try guessed chess.com usernames in order. Returns (username, confidence)
    or (None, None). First two guesses → high confidence, later → low.
    """
    try:
        from lookup.chesscom import guess_usernames, get_profile
    except Exception:
        return None, None

    guesses = guess_usernames(name)
    for i, username in enumerate(guesses):
        try:
            get_profile(username)
            confidence = "high" if i < 2 else "low"
            return username, confidence
        except (requests.HTTPError, requests.ConnectionError):
            continue
    return None, None
