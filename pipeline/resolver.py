"""
Resolve a player's real name (from a tournament entry list) to their
Lichess and chess.com usernames.

Strategy:
  Lichess   — autocomplete search returns several candidates; each is
              scored on name/handle similarity plus (when available)
              rating closeness to the entry-list rating and country,
              and the best-scoring candidate wins.
  chess.com — try every guessed username pattern that resolves to a real
              profile and keep the best-scoring one (username-pattern
              specificity stands in for name similarity, since every
              guess is mechanically derived from the name itself, unless
              the account has a genuine real name on file).

Confidence levels: "high" | "low" | None (no usable match).
Returns (username, confidence, score, reasons) — score is 0..1, reasons
is a short list of strings explaining what drove the score, meant for
display so a human can sanity-check the match.
"""

import re
from datetime import date
from difflib import SequenceMatcher

import requests


_HIGH_THRESHOLD = 0.55   # composite score for high confidence
_LOW_THRESHOLD  = 0.30   # below this → reject entirely

# A game-count figure is a confidence signal (see _game_count_score), but on
# its own it's also a hard guardrail: an account with almost no games is too
# thin a sample to trust as a "high confidence" identity match even when the
# name/rating/country signals line up perfectly — that combination is just
# as consistent with "different person, coincidentally similar profile" as
# with a real match. Below this many games, cap confidence at "low".
_MIN_GAMES_FOR_HIGH = 5

# Games above this count no longer add further confidence — an account with
# hundreds of games isn't meaningfully more "them" than one with 50.
_GAMES_FOR_FULL_SCORE = 50

# Recency is a separate signal from games_count: an account with hundreds
# of games but no activity in years is weak evidence of being *this*
# player's current account, no matter how many games it has on record.
# Active within this many days scores full marks...
_RECENCY_FULL_SCORE_DAYS = 90
# ...tapering to zero by this many days of inactivity...
_RECENCY_ZERO_SCORE_DAYS = 730
# ...and beyond this many days inactive (~5 years), it's a hard guardrail
# like _MIN_GAMES_FOR_HIGH — never "high" confidence regardless of score.
_STALE_ACCOUNT_DAYS = 5 * 365

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


def _country_score(country: str | None, fide_country: str | None = None) -> tuple[float | None, str | None]:
    """
    When we know the entrant's actual FIDE nationality (looked up from
    their USCF record — see lookup.uscf), compare the candidate's country
    directly against it: a real, specific signal, not a guess. Only when
    that's unavailable do we fall back to the old "US-preferred" heuristic
    (both supported tournament sites are US-based, so a US profile is weak
    positive evidence) — a blanket assumption that's flatly wrong for the
    many international players who compete in US-run events, and confirmed
    in practice to actively prefer a worse candidate over a better one
    purely for being US-based.
    """
    if not country:
        return None, None
    if fide_country:
        matches = country.strip().lower() == fide_country.strip().lower()
        reason = f"country {country}" + (" (matches FIDE nationality)" if matches
                                         else f" (FIDE nationality is {fide_country})")
        return (1.0 if matches else 0.0), reason
    is_preferred = country.strip().lower() in _PREFERRED_COUNTRIES
    reason = f"country {country}" + ("" if is_preferred else " (non-US)")
    return (1.0 if is_preferred else 0.0), reason


def _game_count_score(games_count: int | None) -> tuple[float | None, str | None]:
    """
    More games on record is stronger evidence the account is a real,
    active identity rather than an unrelated same-name profile a search
    or guess happened to land on. Scales linearly up to
    _GAMES_FOR_FULL_SCORE games, capped at 1.0 above that.
    """
    if games_count is None:
        return None, None
    score = min(1.0, games_count / _GAMES_FOR_FULL_SCORE)
    return score, f"{games_count} game(s) on record"


def _recency_score(last_active: str | None) -> tuple[float | None, str | None]:
    """
    A separate signal from games_count: an account with hundreds of games
    but no activity in years is weak evidence of being *this* player's
    current account, no matter how many games it has on record. Scales
    down from 1.0 (active within _RECENCY_FULL_SCORE_DAYS) to 0.0 (inactive
    for _RECENCY_ZERO_SCORE_DAYS or more).
    """
    if not last_active:
        return None, None
    try:
        last_date = date.fromisoformat(last_active)
    except ValueError:
        return None, None
    days_ago = (date.today() - last_date).days
    span = _RECENCY_ZERO_SCORE_DAYS - _RECENCY_FULL_SCORE_DAYS
    score = max(0.0, min(1.0, 1 - (days_ago - _RECENCY_FULL_SCORE_DAYS) / span))
    age = f"{days_ago}d ago" if days_ago < 365 else f"{days_ago / 365:.1f}y ago"
    return score, f"last active {last_active} ({age})"


def _composite_score(name_signal: float, name_reason: str, candidate_ratings: dict,
                     candidate_country: str | None, entry_rating: int | None,
                     fide_rating: int | None = None,
                     games_count: int | None = None,
                     entry_fide_country: str | None = None,
                     last_active: str | None = None) -> tuple[float, list[str], bool, bool]:
    """
    Combine name/handle, rating, country, game-count, and recency signals
    into one 0..1 score. Each signal is weighted, but signals with no data
    (e.g. no country on file) are simply left out rather than penalising
    the candidate. `entry_fide_country` is the tournament entrant's own
    FIDE nationality (looked up separately, e.g. via lookup.uscf) —
    distinct from `fide_rating`, which is a *candidate* profile's linked
    FIDE rating — and sharpens the country signal from a blanket
    US-preference guess into an actual identity cross-check; see
    _country_score.

    Returns (score, reasons, rating_ok, recency_ok). rating_ok is False
    only when a rating comparison was actually possible *and* it came back
    catastrophically bad (clamped to the 0.0 floor, i.e. the gap is at or
    beyond _RATING_TOLERANCE) — a strong sign this is a different person
    wearing a similar name. recency_ok is False only when the account's
    last activity is known *and* older than _STALE_ACCOUNT_DAYS. Both are
    hard caps _confidence_for uses so a high name/country/games score
    can't paper over them; missing data leaves both True — absence of
    evidence isn't evidence of a mismatch.
    """
    reasons = [name_reason]
    weighted = name_signal * 0.5
    weight = 0.5
    rating_ok = True
    recency_ok = True

    rating_s, rating_reason = _rating_score(entry_rating, candidate_ratings, fide_rating)
    if rating_s is not None:
        weighted += rating_s * 0.3
        weight += 0.3
        reasons.append(rating_reason)
        rating_ok = rating_s > 0.0

    country_s, country_reason = _country_score(candidate_country, entry_fide_country)
    if country_s is not None:
        weighted += country_s * 0.2
        weight += 0.2
        reasons.append(country_reason)

    games_s, games_reason = _game_count_score(games_count)
    if games_s is not None:
        weighted += games_s * 0.2
        weight += 0.2
        reasons.append(games_reason)

    recency_s, recency_reason = _recency_score(last_active)
    if recency_s is not None:
        weighted += recency_s * 0.15
        weight += 0.15
        reasons.append(recency_reason)
        if last_active:
            recency_ok = (date.today() - date.fromisoformat(last_active)).days <= _STALE_ACCOUNT_DAYS

    return round(weighted / weight, 2), reasons, rating_ok, recency_ok


def _score_lichess_profile(name: str, profile: dict, rating: int | None,
                           fide_country: str | None = None) -> tuple[float, list[str], bool, bool]:
    name_s = max(
        _name_score(name, profile.get("real_name") or ""),
        _name_score(name, profile.get("display_name", "")),
    )
    return _composite_score(
        name_s, f"name match {name_s:.2f}",
        profile.get("ratings", {}), profile.get("country"), rating,
        fide_rating=profile.get("fide_rating"),
        games_count=profile.get("games_count"),
        entry_fide_country=fide_country,
        last_active=profile.get("last_active"),
    )


def _resolve_lichess_by_autocomplete(
    name: str, rating: int | None, fide_country: str | None = None
) -> tuple[str | None, float, list[str], int | None, bool, bool]:
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
        return None, -1.0, [], None, True, True

    if not candidates:
        return None, -1.0, [], None, True, True

    ranked = sorted(candidates,
                    key=lambda c: _name_score(name, c.get("display_name", "")),
                    reverse=True)

    best_username, best_score, best_reasons = None, -1.0, []
    best_games, best_rating_ok, best_recency_ok = None, True, True
    for c in ranked[:2]:
        profile = c
        try:
            profile = get_profile(c["username"])
        except Exception:
            pass
        score, reasons, rating_ok, recency_ok = _score_lichess_profile(name, profile, rating, fide_country)
        if score > best_score:
            best_username = profile.get("username") or c["username"]
            best_score, best_reasons = score, reasons
            best_games, best_rating_ok, best_recency_ok = profile.get("games_count"), rating_ok, recency_ok

    return best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok


def _resolve_lichess_by_search(
    name: str, rating: int | None, searxng_url: str, fide_country: str | None = None
) -> tuple[str | None, float, list[str], int | None, bool, bool]:
    """
    Web-search for the player's Lichess profile — catches an account whose
    username has nothing to do with `name`, findable only via its
    real-name field (which /player/autocomplete never searches, since it
    only matches on username).
    """
    try:
        from lookup.lichess import find_usernames_via_search, get_profile
    except Exception:
        return None, -1.0, [], None, True, True

    best_username, best_score, best_reasons = None, -1.0, []
    best_games, best_rating_ok, best_recency_ok = None, True, True
    for username in find_usernames_via_search(name, searxng_url):
        try:
            profile = get_profile(username)
        except Exception:
            continue
        score, reasons, rating_ok, recency_ok = _score_lichess_profile(name, profile, rating, fide_country)
        if score > best_score:
            best_username, best_score, best_reasons = username, score, reasons
            best_games, best_rating_ok, best_recency_ok = profile.get("games_count"), rating_ok, recency_ok

    return best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok


def _confidence_for(score: float, games_count: int | None, rating_ok: bool = True,
                    recency_ok: bool = True) -> str | None:
    """
    Map a composite score to a confidence label, with three hard overrides
    that can only ever pull "high" down to "low", never the reverse:
      - a match backed by very few games (see _MIN_GAMES_FOR_HIGH)
      - a match whose rating comparison was catastrophically bad (see
        _composite_score's rating_ok)
      - a match whose account has had no activity in ~5 years (see
        _composite_score's recency_ok)
    Any one alone means "score says high, but the strongest piece of
    corroborating evidence actively argues against it" — not a case to
    present as a confident identity match.
    """
    if score >= _HIGH_THRESHOLD:
        if games_count is not None and games_count < _MIN_GAMES_FOR_HIGH:
            return "low"
        if not rating_ok:
            return "low"
        if not recency_ok:
            return "low"
        return "high"
    if score >= _LOW_THRESHOLD:
        return "low"
    return None


def resolve_lichess(name: str, rating: int | None = None,
                    searxng_url: str | None = None,
                    fide_country: str | None = None) -> tuple[str | None, str | None, float, list[str]]:
    """
    Resolve a Lichess account: try the autocomplete search first (cheap,
    no external dependency), then — if `searxng_url` is given and that
    didn't already land a high-confidence match — also try a web search,
    keeping whichever candidate scores best. Imports lookup.lichess lazily
    so resolver is testable without network access.

    `fide_country` is the entrant's own FIDE nationality (ISO alpha-2,
    e.g. from lookup.uscf.get_fide_country) — when given, it replaces the
    generic "US-preferred" country heuristic with an actual identity
    cross-check against each candidate's listed country.

    Returns (username, confidence, score, reasons).
    """
    name = _strip_title(name)

    best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok = (
        _resolve_lichess_by_autocomplete(name, rating, fide_country))

    if searxng_url and best_score < _HIGH_THRESHOLD:
        s_username, s_score, s_reasons, s_games, s_rating_ok, s_recency_ok = _resolve_lichess_by_search(
            name, rating, searxng_url, fide_country)
        if s_score > best_score:
            best_username, best_score, best_reasons = s_username, s_score, s_reasons
            best_games, best_rating_ok, best_recency_ok = s_games, s_rating_ok, s_recency_ok

    if best_username is None:
        return None, None, 0.0, []

    confidence = _confidence_for(best_score, best_games, best_rating_ok, best_recency_ok)
    if confidence == "low" and best_score >= _HIGH_THRESHOLD:
        if best_games is not None and best_games < _MIN_GAMES_FOR_HIGH:
            best_reasons = best_reasons + [f"capped from high: only {best_games} games on record"]
        elif not best_rating_ok:
            best_reasons = best_reasons + ["capped from high: rating far outside tolerance"]
        elif not best_recency_ok:
            best_reasons = best_reasons + ["capped from high: no recent activity"]
    if confidence is None:
        return None, None, best_score, best_reasons
    return best_username, confidence, best_score, best_reasons


def _resolve_chesscom_by_guessing(
    name: str, rating: int | None, fide_country: str | None = None
) -> tuple[str | None, float, list[str], int | None, bool, bool]:
    """
    Try every guessed chess.com username, scoring each one that resolves
    to a real profile and keeping the best-scoring candidate — rather than
    stopping at the first hit, which can lock in a weak, generic match
    (e.g. a bare "john" collides with an unrelated, unrated stranger)
    purely because it happened to come first in the guess list, even when
    a later guess would score far better on rating/country/games evidence.
    Since every guess is mechanically derived from `name`, the guess
    itself carries little information — what actually discriminates a real
    match is rating/country/game-count corroboration, plus a genuine
    real-name match when the account has one on file (chess.com profiles
    can carry a real name, unlike a Lichess autocomplete result), so those
    signals matter more here than for Lichess. Can't find personalized
    handles like "beaumontchess_fr" — see _resolve_chesscom_by_search for
    that.
    """
    try:
        from lookup.chesscom import guess_usernames, get_profile
    except Exception:
        return None, -1.0, [], None, True, True

    guesses = guess_usernames(name)
    best_username, best_score, best_reasons = None, -1.0, []
    best_games, best_rating_ok, best_recency_ok = None, True, True
    for i, username in enumerate(guesses):
        try:
            profile = get_profile(username)
        except (requests.HTTPError, requests.ConnectionError):
            continue

        # Compound guesses ("firstlast", "lastfirst") are common, deliberate
        # username conventions; single-word guesses ("first", "last" alone)
        # are generic and much more likely to collide with a stranger.
        name_s = 0.8 if i < 2 else 0.4
        name_reason = f"username pattern (guess #{i + 1} of {len(guesses)})"

        # A real name on file is direct evidence, unlike the guess itself
        # (which is mechanically derived from `name` and so proves nothing
        # about *this specific* account) — never fall back to the guessed
        # username string here, only a genuine profile.get("real_name").
        real_name = profile.get("real_name")
        if real_name:
            real_s = _name_score(name, real_name)
            if real_s > name_s:
                name_s, name_reason = real_s, f"real name match {real_s:.2f} ({real_name})"

        score, reasons, rating_ok, recency_ok = _composite_score(
            name_s, name_reason,
            profile.get("ratings", {}), profile.get("country"), rating,
            games_count=profile.get("games_count"),
            entry_fide_country=fide_country,
            last_active=profile.get("last_active"),
        )
        if score > best_score:
            best_username, best_score, best_reasons = username, score, reasons
            best_games, best_rating_ok, best_recency_ok = profile.get("games_count"), rating_ok, recency_ok

    return best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok


def _resolve_chesscom_by_search(
    name: str, rating: int | None, searxng_url: str, fide_country: str | None = None
) -> tuple[str | None, float, list[str], int | None, bool, bool]:
    """
    Web-search for the player's chess.com profile and score every
    candidate found on real name/rating/country — catches personalized
    handles no mechanical guess could produce. Requires a running SearXNG
    instance (the same one used for Lichess broadcast discovery).
    """
    try:
        from lookup.chesscom import find_usernames_via_search, get_profile
    except Exception:
        return None, -1.0, [], None, True, True

    best_username, best_score, best_reasons = None, -1.0, []
    best_games, best_rating_ok, best_recency_ok = None, True, True
    for username in find_usernames_via_search(name, searxng_url):
        try:
            profile = get_profile(username)
        except (requests.HTTPError, requests.ConnectionError):
            continue

        name_s = max(
            _name_score(name, profile.get("real_name") or ""),
            _name_score(name, profile.get("display_name", username)),
        )
        score, reasons, rating_ok, recency_ok = _composite_score(
            name_s, f"web search match: name {name_s:.2f}",
            profile.get("ratings", {}), profile.get("country"), rating,
            games_count=profile.get("games_count"),
            entry_fide_country=fide_country,
            last_active=profile.get("last_active"),
        )
        if score > best_score:
            best_username, best_score, best_reasons = username, score, reasons
            best_games, best_rating_ok, best_recency_ok = profile.get("games_count"), rating_ok, recency_ok

    return best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok


def resolve_chesscom(name: str, rating: int | None = None,
                     searxng_url: str | None = None,
                     fide_country: str | None = None) -> tuple[str | None, str | None, float, list[str]]:
    """
    Resolve a chess.com account: try mechanical username guesses first
    (cheap, no external dependency), then — if `searxng_url` is given
    and the guess didn't already land a high-confidence match — also try
    a web search, keeping whichever candidate scores best.

    `fide_country` is the entrant's own FIDE nationality (ISO alpha-2,
    e.g. from lookup.uscf.get_fide_country) — when given, it replaces the
    generic "US-preferred" country heuristic with an actual identity
    cross-check against each candidate's listed country.

    Returns (username, confidence, score, reasons).
    """
    name = _strip_title(name)

    best_username, best_score, best_reasons, best_games, best_rating_ok, best_recency_ok = (
        _resolve_chesscom_by_guessing(name, rating, fide_country))

    if searxng_url and best_score < _HIGH_THRESHOLD:
        s_username, s_score, s_reasons, s_games, s_rating_ok, s_recency_ok = _resolve_chesscom_by_search(
            name, rating, searxng_url, fide_country)
        if s_score > best_score:
            best_username, best_score, best_reasons = s_username, s_score, s_reasons
            best_games, best_rating_ok, best_recency_ok = s_games, s_rating_ok, s_recency_ok

    if best_username is None:
        return None, None, 0.0, []

    confidence = _confidence_for(best_score, best_games, best_rating_ok, best_recency_ok)
    if confidence == "low" and best_score >= _HIGH_THRESHOLD:
        if best_games is not None and best_games < _MIN_GAMES_FOR_HIGH:
            best_reasons = best_reasons + [f"capped from high: only {best_games} games on record"]
        elif not best_rating_ok:
            best_reasons = best_reasons + ["capped from high: rating far outside tolerance"]
        elif not best_recency_ok:
            best_reasons = best_reasons + ["capped from high: no recent activity"]
    if confidence is None:
        return None, None, best_score, best_reasons
    return best_username, confidence, best_score, best_reasons
