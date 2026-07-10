"""
Opening repertoire analysis.

Given a list of PGN strings and a player name, produces ranked opening
lines for each colour with frequency and W/D/L breakdown.

Usage:
  python -m analysis.openings games.pgn "Smith, John"
  python -m analysis.openings games.pgn "Smith, John" --depth 8 --top 10
"""

import io
import re
import sys
import json
import argparse
from collections import defaultdict

import chess.pgn

from pgnutil import split_pgn_games


def _parse_game(pgn_text: str) -> chess.pgn.Game | None:
    try:
        return chess.pgn.read_game(io.StringIO(pgn_text))
    except Exception:
        return None


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def _name_matches(player: str, header_name: str) -> bool:
    """
    True if every word in `player` appears somewhere in `header_name`.

    Token-based rather than plain substring so a truncated/compound
    surname in the source name (e.g. tournament entry "Lagrave, Maxime"
    vs. PGN header "Vachier-Lagrave, Maxime") still matches.
    """
    player_tokens = _tokens(player)
    if not player_tokens:
        return False
    return player_tokens <= _tokens(header_name)


def _game_url(headers) -> str | None:
    """
    Best-effort link to view a game. Checks, in order: a custom GameURL
    header (used for locally-generated views of games with no public
    page, e.g. from the megabase), then the standard Link/Site tags
    lichess and chess.com PGN exports populate with the game's URL.
    """
    gu = headers.get("GameURL", "")
    if gu:
        return gu
    for key in ("Link", "Site"):
        val = headers.get(key, "")
        if val.startswith("http"):
            return val
    return None


def _opening_line(game: chess.pgn.Game, depth: int) -> str:
    """Return the first `depth` half-moves as a SAN string, e.g. '1. e4 e5 2. Nf3'."""
    board = game.board()
    parts = []
    node = game
    half = 0
    while node.variations and half < depth:
        node = node.variations[0]
        move_num = board.fullmove_number
        is_white = board.turn == chess.WHITE
        san = board.san(node.move)
        if is_white:
            parts.append(f"{move_num}. {san}")
        else:
            parts.append(san)
        board.push(node.move)
        half += 1
    return " ".join(parts)


def _result_for_player(game: chess.pgn.Game, player: str) -> str:
    """Return 'win', 'draw', or 'loss' from the given player's perspective."""
    headers = game.headers
    result = headers.get("Result", "*")
    white = headers.get("White", "")
    black = headers.get("Black", "")

    is_white = _name_matches(player, white)
    is_black = _name_matches(player, black)

    if result == "1-0":
        if is_white:   return "win"
        if is_black:   return "loss"
    elif result == "0-1":
        if is_black:   return "win"
        if is_white:   return "loss"
    elif result in ("1/2-1/2", "½-½"):
        return "draw"
    return "unknown"


_MAX_GAMES_PER_LINE = 20


def _tally(records: dict) -> dict:
    """Sort opening records by count and compute win_pct."""
    rows = []
    for line, r in records.items():
        total = r["wins"] + r["draws"] + r["losses"]
        games = sorted(r["games"], key=lambda g: g.get("date") or "", reverse=True)
        rows.append({
            "line": line,
            "count": total,
            "wins": r["wins"],
            "draws": r["draws"],
            "losses": r["losses"],
            "win_pct": round(100 * r["wins"] / total, 1) if total else 0.0,
            "games": games[:_MAX_GAMES_PER_LINE],
        })
    return sorted(rows, key=lambda x: x["count"], reverse=True)


def analyse_openings(pgn_strings: list[str], player: str,
                     depth: int = 6, top: int = 0) -> dict:
    """
    Analyse opening repertoire for `player` across the given PGN games.

    Returns:
      {
        "as_white": [{"line": "1. e4", "count": 30, "wins": 15, ...}, ...],
        "as_black": [...],
      }
    Sorted by frequency. Pass top > 0 to limit to N lines per colour.
    """
    white_lines: dict[str, dict] = defaultdict(lambda: {"wins": 0, "draws": 0, "losses": 0, "games": []})
    black_lines: dict[str, dict] = defaultdict(lambda: {"wins": 0, "draws": 0, "losses": 0, "games": []})

    for pgn_text in pgn_strings:
        game = _parse_game(pgn_text)
        if game is None:
            continue

        headers = game.headers
        white = headers.get("White", "")
        black = headers.get("Black", "")

        is_white = _name_matches(player, white)
        is_black = _name_matches(player, black)
        if not is_white and not is_black:
            continue

        line = _opening_line(game, depth)
        result = _result_for_player(game, player)
        if result == "unknown":
            continue

        key = {"win": "wins", "draw": "draws", "loss": "losses"}[result]
        bucket = white_lines if is_white else black_lines
        bucket[line][key] += 1
        bucket[line]["games"].append({
            "url": _game_url(headers),
            "date": headers.get("Date"),
            "opponent": black if is_white else white,
            "result": result,
        })

    as_white = _tally(white_lines)
    as_black = _tally(black_lines)

    if top:
        as_white = as_white[:top]
        as_black = as_black[:top]

    return {"as_white": as_white, "as_black": as_black}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse opening repertoire from a PGN file.")
    parser.add_argument("pgn_file", help="PGN file containing the player's games")
    parser.add_argument("player", help="Player name to analyse")
    parser.add_argument("--depth", type=int, default=6,
                        help="Half-moves to use as opening key (default: 6)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N openings per colour (default: 10, 0 = all)")
    args = parser.parse_args()

    with open(args.pgn_file, encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    raw_games = split_pgn_games(content)
    print(f"Parsing {len(raw_games)} game(s)…", file=sys.stderr)

    result = analyse_openings(raw_games, args.player, depth=args.depth, top=args.top)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
