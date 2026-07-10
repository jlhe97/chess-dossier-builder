"""
Resolve a player's real name (from a tournament entry list) to their
Lichess and chess.com usernames.

Strategy:
  Lichess   — autocomplete search returns several candidates; each is
              scored on name/handle similarity plus (when available)
              rating closeness to the entry-list rating and country,
              and the best-scoring candidate wins.
  chess.com — try guessed username patterns in order, stopping at the
              first one that resolves to a real profile; that single
              candidate is then scored the same way (username-pattern
              specificity stands in for name similarity, since every
              guess is mechanically derived from the name itself).

Confidence levels: "high" | "low" | None (no usable match).
Returns (username, confidence, score, reasons) — score is 0..1, reasons
is a short list of strings explaining what drove the score, meant for
display so a human can sanity-check the match.
"""

import re
from difflib import SequenceMatcher

import requests


_HIGH_THRESHOLD = 0.55   # composite score for high confidence
_LOW_THRESHOLD  = 0.30   # below this → reject entirely

# A big online-vs-OTB rating gap is still plausible (different formats,
# inflation/deflation), so this is a generous tolerance band, not a
# strict cutoff — it only nudges the score, never hard-rejects.
_RATING_TOLERANCE = 800

# Tournaments on the two supported sites (kingregistration.com,
# chessaction.com) are US-based, so a US-located profile is weak
# positive evidence and a clearly non-US one is weak negative evidence.
_PREFERRED_COUNTRIES = {"us", "usa"}

# FIDE/USCF title prefixes tournament entry lists prepend to a name,
# e.g. "GM Vachier-Lagrave, Maxime" — strip before searching/matching,
# since these never appear in PGN headers or online usernames.
_TITLE_RE = re.compile(
    r"^(?:GM|IM|FM|CM|NM|WGM|WIM|WFM|WCM|WNM)\.?\s+",
    re.IGNORECASE,
)


def _strip_title(name: str) -> str:
    """Remove a leading FIDE/USCF title from a player name, if present."""
    return _TITLE_RE.sub("", name.strip()).strip()


def _similarity(a: str, b: str) -> float:
    """Case-insensitive character similarity between two name strings."""
    def norm(s: str) -> str:
        return "".join(s.lower().split()).replace(",", "").replace(".", "")
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _name_score(name: str, candidate: str) -> float:
    """
    Similarity between an entry-list name and a Lichess display name
    (which is just their username, since Lichess has no real-name field).

    Blends raw character similarity with a surname-substring check, so a
    handle like "hikaru99" still scores well against "Nakamura, Hikaru"
    even though the two strings look quite different overall.
    """
    sim = _similarity(name, candidate)
    last = name.split(",")[0].strip() if "," in name else (name.split()[-1] if name.split() else "")
    last_norm = re.sub(r"[^a-z0-9]", "", last.lower())
    cand_norm = re.sub(r"[^a-z0-9]", "", candidate.lower())
    if len(last_norm) >= 3 and last_norm in cand_norm:
        sim = max(sim, 0.65)
    return sim


def _rating_score(entry_rating: int | None, candidate_ratings: dict,
                  fide_rating: int | None = None) -> tuple[float | None, str | None]:
    """
    Compare an entry-list (USCF/FIDE) rating against the best available
    online figure. A FIDE rating on the candidate's profile is a much
    more direct comparison than an online blitz/rapid rating (different
    time controls and inflation), so it gets a tighter tolerance band.
    """
    if not entry_rating:
        return None, None
    if fide_rating:
        delta = abs(fide_rating - entry_rating)
        score = max(0.0, 1 - delta / (_RATING_TOLERANCE / 2))
        return score, f"FIDE {fide_rating} vs {entry_rating} (Δ{delta})"
    if not candidate_ratings:
        return None, None
    online = (candidate_ratings.get("classical") or candidate_ratings.get("rapid")
             or candidate_ratings.get("blitz") or candidate_ratings.get("bullet"))
    if not online:
        return None, None
    delta = abs(online - entry_rating)
    score = max(0.0, 1 - delta / _RATING_TOLERANCE)
    return score, f"rating {online} vs {entry_rating} (Δ{delta})"


def _country_score(country: str | None) -> tuple[float | None, str | None]:
    if not country:
        return None, None
    is_preferred = country.strip().lower() in _PREFERRED_COUNTRIES
    reason = f"country {country}" + ("" if is_preferred else " (non-US)")
    return (1.0 if is_preferred else 0.0), reason


def _composite_score(name_signal: float, name_reason: str, candidate_ratings: dict,
                     candidate_country: str | None, entry_rating: int | None,
                     fide_rating: int | None = None) -> tuple[float, list[str]]:
    """
    Combine name/handle, rating, and country signals into one 0..1 score.
    Each signal is weighted, but signals with no data (e.g. no country
    on file) are simply left out rather than penalising the candidate.
    """
    reasons = [name_reason]
    weighted = name_signal * 0.5
    weight = 0.5

    rating_s, rating_reason = _rating_score(entry_rating, candidate_ratings, fide_rating)
    if rating_s is not None:
        weighted += rating_s * 0.3
        weight += 0.3
        reasons.append(rating_reason)

    country_s, country_reason = _country_score(candidate_country)
    if country_s is not None:
        weighted += country_s * 0.2
        weight += 0.2
        reasons.append(country_reason)

    return round(weighted / weight, 2), reasons


def _score_lichess_profile(name: str, profile: dict, rating: int | None) -> tuple[float, list[str]]:
    name_s = max(
        _name_score(name, profile.get("real_name") or ""),
        _name_score(name, profile.get("display_name", "")),
    )
    return _composite_score(
        name_s, f"name match {name_s:.2f}",
        profile.get("ratings", {}), profile.get("country"), rating,
        fide_rating=profile.get("fide_rating"),
    )


def _resolve_lichess_by_autocomplete(name: str, rating: int | None) -> tuple[str | None, float, list[str]]:
    """
    Score every candidate the autocomplete endpoint returns (a username-only
    search — see module docstring) and pick the best. A cheap name-only
    first pass ranks all candidates, then the full profile (rating, country
    flag, real name, linked FIDE rating) is fetched for just the top 2 to
    score properly, bounding request volume. Can't find an account whose
    username *and* real-name field are both unrelated to `name` — see
    _resolve_lichess_by_search for that.
    """
    try:
        from lookup.lichess import search, get_profile
        candidates = search(name, max_results=5)
    except Exception:
        return None, -1.0, []

    if not candidates:
        return None, -1.0, []

    ranked = sorted(candidates,
                    key=lambda c: _name_score(name, c.get("display_name", "")),
                    reverse=True)

    best_username, best_score, best_reasons = None, -1.0, []
    for c in ranked[:2]:
        profile = c
        try:
            profile = get_profile(c["username"])
        except Exception:
            pass
        score, reasons = _score_lichess_profile(name, profile, rating)
        if score > best_score:
            best_username = profile.get("username") or c["username"]
            best_score, best_reasons = score, reasons

    return best_username, best_score, best_reasons


def _resolve_lichess_by_search(name: str, rating: int | None,
                               search_api_key: str) -> tuple[str | None, float, list[str]]:
    """
    Web-search for the player's Lichess profile — catches an account whose
    username has nothing to do with `name`, findable only via its
    real-name field (which /player/autocomplete never searches, since it
    only matches on username).
    """
    try:
        from lookup.lichess import find_usernames_via_search, get_profile
    except Exception:
        return None, -1.0, []

    best_username, best_score, best_reasons = None, -1.0, []
    for username in find_usernames_via_search(name, search_api_key):
        try:
            profile = get_profile(username)
        except Exception:
            continue
        score, reasons = _score_lichess_profile(name, profile, rating)
        if score > best_score:
            best_username, best_score, best_reasons = username, score, reasons

    return best_username, best_score, best_reasons


def resolve_lichess(name: str, rating: int | None = None,
                    search_api_key: str | None = None) -> tuple[str | None, str | None, float, list[str]]:
    """
    Resolve a Lichess account: try the autocomplete search first (cheap,
    no external dependency), then — if `search_api_key` is given and that
    didn't already land a high-confidence match — also try a web search,
    keeping whichever candidate scores best. Imports lookup.lichess lazily
    so resolver is testable without network access.

    Returns (username, confidence, score, reasons).
    """
    name = _strip_title(name)

    best_username, best_score, best_reasons = _resolve_lichess_by_autocomplete(name, rating)

    if search_api_key and best_score < _HIGH_THRESHOLD:
        s_username, s_score, s_reasons = _resolve_lichess_by_search(name, rating, search_api_key)
        if s_score > best_score:
            best_username, best_score, best_reasons = s_username, s_score, s_reasons

    if best_username is None:
        return None, None, 0.0, []
    if best_score >= _HIGH_THRESHOLD:
        return best_username, "high", best_score, best_reasons
    if best_score >= _LOW_THRESHOLD:
        return best_username, "low", best_score, best_reasons
    return None, None, best_score, best_reasons


def _resolve_chesscom_by_guessing(name: str, rating: int | None) -> tuple[str | None, float, list[str]]:
    """
    Try guessed chess.com usernames in order, stopping at the first one
    that resolves to a real profile. Since every guess is mechanically
    derived from `name`, the guess itself carries little information —
    what actually discriminates a real match is rating/country
    corroboration, so those signals matter more here than for Lichess.
    Can't find personalized handles like "beaumontchess_fr" — see
    _resolve_chesscom_by_search for that.
    """
    try:
        from lookup.chesscom import guess_usernames, get_profile
    except Exception:
        return None, -1.0, []

    guesses = guess_usernames(name)
    for i, username in enumerate(guesses):
        try:
            profile = get_profile(username)
        except (requests.HTTPError, requests.ConnectionError):
            continue

        # Compound guesses ("firstlast", "lastfirst") are common, deliberate
        # username conventions; single-word guesses ("first", "last" alone)
        # are generic and much more likely to collide with a stranger.
        name_s = 0.8 if i < 2 else 0.4
        score, reasons = _composite_score(
            name_s, f"username pattern (guess #{i + 1} of {len(guesses)})",
            profile.get("ratings", {}), profile.get("country"), rating,
        )
        return username, score, reasons

    return None, -1.0, []


def _resolve_chesscom_by_search(name: str, rating: int | None,
                                search_api_key: str) -> tuple[str | None, float, list[str]]:
    """
    Web-search for the player's chess.com profile and score every
    candidate found on real name/rating/country — catches personalized
    handles no mechanical guess could produce. Requires a Brave Search
    API key (the same one used for Lichess broadcast discovery).
    """
    try:
        from lookup.chesscom import find_usernames_via_search, get_profile
    except Exception:
        return None, -1.0, []

    best_username, best_score, best_reasons = None, -1.0, []
    for username in find_usernames_via_search(name, search_api_key):
        try:
            profile = get_profile(username)
        except (requests.HTTPError, requests.ConnectionError):
            continue

        name_s = _name_score(name, profile.get("display_name", username))
        score, reasons = _composite_score(
            name_s, f"web search match: name {name_s:.2f}",
            profile.get("ratings", {}), profile.get("country"), rating,
        )
        if score > best_score:
            best_username, best_score, best_reasons = username, score, reasons

    return best_username, best_score, best_reasons


def resolve_chesscom(name: str, rating: int | None = None,
                     search_api_key: str | None = None) -> tuple[str | None, str | None, float, list[str]]:
    """
    Resolve a chess.com account: try mechanical username guesses first
    (cheap, no external dependency), then — if `search_api_key` is given
    and the guess didn't already land a high-confidence match — also try
    a web search, keeping whichever candidate scores best.

    Returns (username, confidence, score, reasons).
    """
    name = _strip_title(name)

    best_username, best_score, best_reasons = _resolve_chesscom_by_guessing(name, rating)

    if search_api_key and best_score < _HIGH_THRESHOLD:
        s_username, s_score, s_reasons = _resolve_chesscom_by_search(name, rating, search_api_key)
        if s_score > best_score:
            best_username, best_score, best_reasons = s_username, s_score, s_reasons

    if best_username is None:
        return None, None, 0.0, []
    if best_score >= _HIGH_THRESHOLD:
        return best_username, "high", best_score, best_reasons
    if best_score >= _LOW_THRESHOLD:
        return best_username, "low", best_score, best_reasons
    return None, None, best_score, best_reasons
