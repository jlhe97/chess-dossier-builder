"""
Lichess player lookup and game retrieval.

Usage:
  python -m lookup.lichess search "Magnus Carlsen"
  python -m lookup.lichess profile thibault
  python -m lookup.lichess games thibault
  python -m lookup.lichess games thibault --max 20 --output json

API used (no auth required):
  https://lichess.org/api/player/autocomplete  — name → username candidates (username-only, no real-name search)
  https://lichess.org/api/user/{username}     — profile + ratings
  https://lichess.org/api/games/user/{username} — PGN/ndjson game stream
"""

import re
import sys
import json
import argparse
import time

import requests

_BASE = "https://lichess.org/api"
_HEADERS = {
    "User-Agent": "chess-dossier-builder/1.0",
    "Accept": "application/json",
}
_RATE_DELAY = 1.0  # seconds between requests to stay within rate limits


def _get(path: str, params: dict | None = None, accept: str = "application/json") -> requests.Response:
    headers = {**_HEADERS, "Accept": accept}
    resp = requests.get(f"{_BASE}{path}", headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp


def search(name: str, max_results: int = 5) -> list[dict]:
    """
    Search for Lichess users by display name or username.
    Returns a list of candidate profiles (id, username, title, ratings).
    """
    # The autocomplete endpoint 400s on punctuation like the comma in
    # "Last, First" — collapse to plain whitespace-separated words.
    term = " ".join(name.replace(",", " ").split())
    resp = _get("/player/autocomplete", params={"term": term, "object": "true"})
    data = resp.json()
    users = data if isinstance(data, list) else data.get("result", [])
    return [_slim_profile(u) for u in users[:max_results]]


def get_profile(username: str) -> dict:
    """Fetch full profile for a known username."""
    resp = _get(f"/user/{username}")
    return _slim_profile(resp.json())


_PROFILE_URL_RE = re.compile(r"lichess\.org/@/([A-Za-z0-9_-]+)", re.IGNORECASE)


def find_usernames_via_search(name: str, api_key: str, max_results: int = 5) -> list[str]:
    """
    Search the web for the player's Lichess profile. /player/autocomplete
    only matches on username — a player whose Lichess handle has nothing
    to do with their real name (and who filled in a real-name field) is
    invisible to it, but findable the way a human would: searching their
    name alongside "lichess.org".
    """
    from lookup.websearch import search as web_search
    results = web_search(f'"{name}" lichess.org/@', api_key, count=max_results)
    usernames: list[str] = []
    seen: set[str] = set()
    for r in results:
        m = _PROFILE_URL_RE.search(r.get("url", ""))
        if m and m.group(1).lower() not in seen:
            seen.add(m.group(1).lower())
            usernames.append(m.group(1))
    return usernames


def get_studies(username: str, max: int = 20) -> list[dict]:
    """List public studies owned by `username`. Each dict has at least 'id' and 'name'."""
    resp = _get(f"/study/by/{username}", accept="application/x-ndjson")
    studies = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    return studies[:max]


def get_study_pgn(study_id: str) -> str:
    """Export all chapters of a study as PGN (one game per chapter)."""
    resp = _get(f"/study/{study_id}.pgn", accept="application/x-chess-pgn")
    return resp.text


def get_games(username: str, max: int = 50,
              perf_types: str = "classical,rapid,blitz") -> list[dict]:
    """
    Fetch up to `max` recent games for a Lichess user.
    Returns a list of dicts with PGN and metadata.
    """
    time.sleep(_RATE_DELAY)
    resp = requests.get(
        f"{_BASE}/games/user/{username}",
        headers={**_HEADERS, "Accept": "application/x-ndjson"},
        params={"max": max, "perfType": perf_types, "clocks": "false", "evals": "false"},
        timeout=30,
        stream=True,
    )
    resp.raise_for_status()

    games = []
    for line in resp.iter_lines():
        if line:
            games.append(json.loads(line))
    return games


def games_as_pgn(username: str, max: int = 50,
                 perf_types: str = "classical,rapid,blitz") -> str:
    """Fetch games and return as a single PGN string."""
    time.sleep(_RATE_DELAY)
    resp = requests.get(
        f"{_BASE}/games/user/{username}",
        headers={**_HEADERS, "Accept": "application/x-chess-pgn"},
        params={"max": max, "perfType": perf_types, "clocks": "false", "evals": "false"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def _slim_profile(data: dict) -> dict:
    perfs = data.get("perfs", {})
    profile = data.get("profile") or {}
    return {
        "username": data.get("id") or data.get("username", ""),
        "display_name": data.get("username", ""),
        "title": data.get("title"),
        "ratings": {
            k: perfs[k]["rating"]
            for k in ("classical", "rapid", "blitz", "bullet")
            if k in perfs and "rating" in perfs[k]
        },
        "url": f"https://lichess.org/@/{data.get('id') or data.get('username', '')}",
        # Only present on a full /api/user/{username} profile, not on
        # /player/autocomplete search results — and even then only if
        # the account owner filled them in.
        "real_name": profile.get("realName"),
        "country": profile.get("flag"),
        "fide_rating": profile.get("fideRating"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Lichess player lookup.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Search users by name")
    p_search.add_argument("name")
    p_search.add_argument("--max", type=int, default=5)

    p_profile = sub.add_parser("profile", help="Fetch profile by username")
    p_profile.add_argument("username")

    p_games = sub.add_parser("games", help="Fetch recent games by username")
    p_games.add_argument("username")
    p_games.add_argument("--max", type=int, default=50)
    p_games.add_argument("--output", choices=["json", "pgn"], default="pgn")
    p_games.add_argument("--perf", default="classical,rapid,blitz")

    args = parser.parse_args()

    if args.cmd == "search":
        results = search(args.name, max_results=args.max)
        print(json.dumps(results, indent=2))

    elif args.cmd == "profile":
        print(json.dumps(get_profile(args.username), indent=2))

    elif args.cmd == "games":
        if args.output == "pgn":
            print(games_as_pgn(args.username, max=args.max, perf_types=args.perf))
        else:
            games = get_games(args.username, max=args.max, perf_types=args.perf)
            print(f"Fetched {len(games)} game(s).", file=sys.stderr)
            print(json.dumps(games, indent=2))


if __name__ == "__main__":
    main()
