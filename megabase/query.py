"""
Query a MegaDatabase SQLite index for a player's games.

Usage:
  python -m megabase.query <player_name> [--db megabase.db] [--output pgn|json] [--limit N]

Examples:
  python -m megabase.query "Kasparov, Garry"
  python -m megabase.query "Kasparov, Garry" --output json
  python -m megabase.query "Kasparov" --limit 50 --output json
"""

import re
import sys
import json
import argparse
import sqlite3

DEFAULT_DB = "megabase.db"

DEFAULT_RATING_TOLERANCE = 400

_ELO_RE = {
    "white": re.compile(r'\[WhiteElo "(\d+)"\]'),
    "black": re.compile(r'\[BlackElo "(\d+)"\]'),
}


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def _rating_mismatch(game: dict, name_tokens: set[str], rating: int,
                     tolerance: int) -> bool:
    """
    True if the matched side's Elo (as recorded in the game's own PGN
    headers) is too far from `rating` to plausibly be the same person.
    A missing Elo can't be checked, so it's never treated as a mismatch.
    """
    side = "white" if name_tokens <= _tokens(game["white"]) else "black"
    m = _ELO_RE[side].search(game["pgn"])
    if not m:
        return False
    return abs(int(m.group(1)) - rating) > tolerance


def _delimited(field: str) -> str:
    """
    SQL expression wrapping `field` in comma delimiters, with internal
    whitespace also turned into commas — turns "Vachier-Lagrave, Maxime"
    into ",vachier-lagrave,,maxime," so 'LIKE %,token,%' matches only
    whole words. A bare 'LIKE %token%' would match a short token *inside*
    an unrelated word (e.g. "an" inside "Anderson") — on an 11M-game
    database that's not a rare edge case, it's catastrophic (a short,
    common token can match millions of unrelated rows).
    """
    return f"(',' || REPLACE(LOWER({field}), ' ', ',') || ',')"


def get_player_games(name: str, db_path: str = DEFAULT_DB,
                     limit: int | None = None, rating: int | None = None,
                     rating_tolerance: int = DEFAULT_RATING_TOLERANCE) -> list[dict]:
    """
    Return all games where player name appears as White or Black.

    Matching is case-insensitive and whole-word, token-based: every word
    in `name` (split on whitespace/punctuation) must appear as a whole
    word somewhere in the White or Black field, in any order. This
    tolerates name variants a plain substring match would miss — e.g.
    "Lagrave, Maxime" still matches a PGN header of "Vachier-Lagrave,
    Maxime" (a compound surname the entry list truncated), since both
    "Lagrave" and "Maxime" are present as whole words even though the
    exact "Last, First" substring isn't.

    Even whole-word matching can over-match a common name across an 11M+
    game, multi-century, international database (many distinct real
    people share the same common first/last name combination). Passing
    `rating` (the player's current tournament rating) filters out games
    where the matched side's Elo is more than `rating_tolerance` points
    away — a cheap, local disambiguator using data already in the PGN.
    """
    tokens = _tokens(name)
    if not tokens:
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    patterns = [f"%,{t},%" for t in tokens]
    white_field = _delimited("white")
    black_field = _delimited("black")
    white_clause = " AND ".join([f"{white_field} LIKE ?"] * len(tokens))
    black_clause = " AND ".join([f"{black_field} LIKE ?"] * len(tokens))
    sql = f"""
        SELECT white, black, date, event, result, pgn
        FROM games
        WHERE ({white_clause})
           OR ({black_clause})
        ORDER BY date DESC
    """
    params: list = patterns + patterns

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    games = [dict(row) for row in rows]

    if rating is not None:
        games = [g for g in games
                if not _rating_mismatch(g, tokens, rating, rating_tolerance)]

    if limit is not None:
        games = games[:limit]

    return games


def output_pgn(games: list[dict]) -> None:
    for game in games:
        print(game["pgn"])
        print()


def output_json(games: list[dict]) -> None:
    # Exclude raw pgn from JSON summary by default for readability;
    # keep all fields since callers may need the pgn too.
    print(json.dumps(games, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Query a MegaDatabase SQLite index by player name.")
    parser.add_argument("player", help="Player name (full or partial, case-insensitive)")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--output", choices=["pgn", "json"], default="pgn",
                        help="Output format (default: pgn)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of games to return")
    args = parser.parse_args()

    games = get_player_games(args.player, db_path=args.db, limit=args.limit)
    print(f"Found {len(games)} game(s) for '{args.player}'.", file=sys.stderr)

    if not games:
        return

    if args.output == "json":
        output_json(games)
    else:
        output_pgn(games)


if __name__ == "__main__":
    main()
