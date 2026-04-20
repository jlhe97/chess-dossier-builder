"""
chess.com player lookup and game retrieval.

chess.com has no public search endpoint — a username must be known.
Use `guess_usernames(name)` to generate common patterns to try.

Usage:
  python -m lookup.chesscom profile Magnus_Carlsen
  python -m lookup.chesscom games Magnus_Carlsen
  python -m lookup.chesscom games Magnus_Carlsen --months 3 --output json

API used (no auth required):
  https://api.chess.com/pub/player/{username}               — profile
  https://api.chess.com/pub/player/{username}/stats         — ratings
  https://api.chess.com/pub/player/{username}/games/{y}/{m} — monthly archives
"""

import sys
import json
import argparse
import calendar
from datetime import date, timedelta

import requests

_BASE = "https://api.chess.com/pub"
_HEADERS = {
    "User-Agent": "chess-dossier-builder/1.0",
}


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp


def guess_usernames(name: str) -> list[str]:
    """
    Generate plausible chess.com usernames from a 'Last, First' or 'First Last' name.
    Returns candidates ordered by likelihood — caller should try each with get_profile().
    """
    name = name.strip()
    if "," in name:
        last, _, first = name.partition(",")
        last, first = last.strip(), first.strip()
    else:
        parts = name.split()
        first, last = parts[0], parts[-1]

    f, l = first.lower(), last.lower()
    return [
        f"{f}{l}", f"{l}{f}", f"{f}_{l}", f"{l}_{f}",
        f"{f[0]}{l}", f"{l}{f[0]}", f"{f}.{l}",
        f"{f}", f"{l}",
    ]


def get_profile(username: str) -> dict:
    """Fetch profile and ratings for a chess.com username. Raises on 404."""
    profile = _get(f"{_BASE}/player/{username}").json()
    try:
        stats = _get(f"{_BASE}/player/{username}/stats").json()
    except requests.HTTPError:
        stats = {}

    return _slim_profile(username, profile, stats)


def find_profile(name: str) -> dict | None:
    """
    Try guessed usernames until one resolves. Returns first match or None.
    """
    for username in guess_usernames(name):
        try:
            return get_profile(username)
        except requests.HTTPError:
            continue
    return None


def get_games(username: str, months: int = 3) -> list[dict]:
    """
    Fetch games from the last `months` monthly archives.
    Returns a flat list of game dicts (chess.com native format).
    """
    games = []
    for year, month in _recent_months(months):
        try:
            data = _get(f"{_BASE}/player/{username}/games/{year}/{month:02d}").json()
            games.extend(data.get("games", []))
        except requests.HTTPError:
            continue
    return games


def games_as_pgn(username: str, months: int = 3) -> str:
    """Fetch games from recent archives and return as a single PGN string."""
    pgns = []
    for year, month in _recent_months(months):
        try:
            data = _get(f"{_BASE}/player/{username}/games/{year}/{month:02d}/pgn").text
            pgns.append(data)
        except requests.HTTPError:
            continue
    return "\n\n".join(pgns)


def _recent_months(n: int) -> list[tuple[int, int]]:
    months = []
    d = date.today().replace(day=1)
    for _ in range(n):
        months.append((d.year, d.month))
        d = (d - timedelta(days=1)).replace(day=1)
    return months


def _slim_profile(username: str, profile: dict, stats: dict) -> dict:
    ratings = {}
    for key, label in (
        ("chess_classical", "classical"),
        ("chess_rapid", "rapid"),
        ("chess_blitz", "blitz"),
        ("chess_bullet", "bullet"),
    ):
        if key in stats and "last" in stats[key]:
            ratings[label] = stats[key]["last"]["rating"]

    return {
        "username": username,
        "display_name": profile.get("name") or profile.get("username", username),
        "title": profile.get("title"),
        "ratings": ratings,
        "url": profile.get("url", f"https://www.chess.com/member/{username}"),
        "country": profile.get("country", "").split("/")[-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="chess.com player lookup.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_profile = sub.add_parser("profile", help="Fetch profile by username")
    p_profile.add_argument("username")

    p_find = sub.add_parser("find", help="Guess username from a player name")
    p_find.add_argument("name", help="Player name e.g. 'Carlsen, Magnus'")

    p_games = sub.add_parser("games", help="Fetch recent games by username")
    p_games.add_argument("username")
    p_games.add_argument("--months", type=int, default=3)
    p_games.add_argument("--output", choices=["json", "pgn"], default="pgn")

    args = parser.parse_args()

    if args.cmd == "profile":
        print(json.dumps(get_profile(args.username), indent=2))

    elif args.cmd == "find":
        result = find_profile(args.name)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print(f"No chess.com profile found for '{args.name}'.", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "games":
        if args.output == "pgn":
            print(games_as_pgn(args.username, months=args.months))
        else:
            games = get_games(args.username, months=args.months)
            print(f"Fetched {len(games)} game(s).", file=sys.stderr)
            print(json.dumps(games, indent=2))


if __name__ == "__main__":
    main()
