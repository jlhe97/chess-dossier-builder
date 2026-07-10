"""
Find Lichess broadcast (live relay) games for a player by name, via a
general web search — Lichess has no API to search broadcasts by player
name, only by broadcast/tournament title (see lookup.websearch for why).

Usage:
  python -m lookup.broadcasts "Nakamura, Hikaru" --api-key BSA...

API used (no auth required, once a round is found):
  https://lichess.org/api/broadcast/round/{roundId}.pgn
"""

import re
import sys
import json
import argparse

import requests

from pgnutil import split_pgn_games
from lookup.websearch import search as web_search

_ROUND_ID_RE = re.compile(r"/broadcast/[^/]+/[^/]+/([A-Za-z0-9]{8})")
_ROUND_PGN_URL = "https://lichess.org/api/broadcast/round/{round_id}.pgn"


def _extract_round_id(url: str) -> str | None:
    """
    Pull the broadcast round ID out of a lichess.org/broadcast/... URL.
    Handles both a plain round URL and a deep link to a specific game
    within it (an extra ID segment after the round ID) —
    e.g. .../tim-just-winter-open-xlii/round-1/9PtLvze6/beOPcXRZ → 9PtLvze6
    """
    m = _ROUND_ID_RE.search(url)
    return m.group(1) if m else None


def find_broadcast_round_ids(player_name: str, api_key: str, max_results: int = 5) -> list[str]:
    """Web-search for the player's name alongside Lichess broadcasts, return round IDs found."""
    results = web_search(f'"{player_name}" lichess.org/broadcast', api_key, count=max_results)
    round_ids: list[str] = []
    for r in results:
        rid = _extract_round_id(r.get("url", ""))
        if rid and rid not in round_ids:
            round_ids.append(rid)
    return round_ids


def get_round_pgn(round_id: str) -> str:
    """Fetch the full PGN (all games) for a broadcast round."""
    resp = requests.get(_ROUND_PGN_URL.format(round_id=round_id), timeout=20)
    resp.raise_for_status()
    return resp.text


def find_games(player_name: str, api_key: str, max_results: int = 5) -> list[str]:
    """
    Search for broadcasts mentioning `player_name`, fetch matching rounds,
    and return every game in them as a PGN string. Games not actually
    involving the player are included too — downstream analysis already
    filters by name, so this stays a simple, honest "here's what we
    found" source rather than duplicating that filtering logic.
    """
    pgns: list[str] = []
    for round_id in find_broadcast_round_ids(player_name, api_key, max_results=max_results):
        try:
            text = get_round_pgn(round_id)
        except requests.HTTPError:
            continue
        pgns += split_pgn_games(text)
    return pgns


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find Lichess broadcast games for a player via web search."
    )
    parser.add_argument("player", help="Player name, e.g. 'Carlsen, Magnus'")
    parser.add_argument("--api-key", required=True, help="Brave Search API key")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--output", choices=["pgn", "json"], default="pgn")
    args = parser.parse_args()

    games = find_games(args.player, args.api_key, max_results=args.max_results)
    print(f"Found {len(games)} game(s).", file=sys.stderr)

    if args.output == "json":
        print(json.dumps(games, indent=2, ensure_ascii=False))
    else:
        for g in games:
            print(g)
            print()


if __name__ == "__main__":
    main()
