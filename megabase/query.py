"""
Query a MegaDatabase SQLite index for a player's games.

Usage:
  python -m megabase.query <player_name> [--db megabase.db] [--output pgn|json] [--limit N]

Examples:
  python -m megabase.query "Kasparov, Garry"
  python -m megabase.query "Kasparov, Garry" --output json
  python -m megabase.query "Kasparov" --limit 50 --output json
"""

import sys
import json
import argparse
import sqlite3

DEFAULT_DB = "megabase.db"


def get_player_games(name: str, db_path: str = DEFAULT_DB,
                     limit: int | None = None) -> list[dict]:
    """
    Return all games where player name appears as White or Black.
    Matching is case-insensitive and supports partial names.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    pattern = f"%{name}%"
    sql = """
        SELECT white, black, date, event, result, pgn
        FROM games
        WHERE white LIKE ? COLLATE NOCASE
           OR black LIKE ? COLLATE NOCASE
        ORDER BY date DESC
    """
    params: tuple = (pattern, pattern)
    if limit is not None:
        sql += " LIMIT ?"
        params = (pattern, pattern, limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [dict(row) for row in rows]


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
