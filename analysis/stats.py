"""
Broad player tendency statistics.

Usage:
  python -m analysis.stats games.pgn "Smith, John"
"""

import io
import sys
import json
import argparse
import re

import chess.pgn

from analysis.openings import _parse_game, _result_for_player, analyse_openings


def _game_length(game: chess.pgn.Game) -> int:
    """Return the number of half-moves played."""
    return sum(1 for _ in game.mainline_moves())


def analyse_stats(pgn_strings: list[str], player: str) -> dict:
    """
    Compute broad tendencies for `player` across the given PGN games.

    Returns:
      {
        "total": 50,
        "as_white": {"count": 25, "wins": 12, "draws": 8, "losses": 5, "win_pct": 48.0},
        "as_black": {"count": 25, "wins": 10, "draws":  9, "losses": 6, "win_pct": 40.0},
        "overall":  {"wins": 22, "draws": 17, "losses": 11, "win_pct": 44.0},
        "avg_length": 35.2,
        "vs_e4": [...],   # top opening lines as Black vs 1. e4
        "vs_d4": [...],   # top opening lines as Black vs 1. d4
      }
    """
    player_l = player.lower()

    buckets = {
        "white": {"wins": 0, "draws": 0, "losses": 0},
        "black": {"wins": 0, "draws": 0, "losses": 0},
    }
    lengths: list[int] = []
    valid_pgns: list[str] = []

    for pgn_text in pgn_strings:
        game = _parse_game(pgn_text)
        if game is None:
            continue

        headers = game.headers
        white = headers.get("White", "").lower()
        black = headers.get("Black", "").lower()

        is_white = player_l in white
        is_black = player_l in black
        if not is_white and not is_black:
            continue

        result = _result_for_player(game, player)
        if result == "unknown":
            continue

        key = {"win": "wins", "draw": "draws", "loss": "losses"}[result]
        colour = "white" if is_white else "black"
        buckets[colour][key] += 1
        lengths.append(_game_length(game))
        valid_pgns.append(pgn_text)

    def _pct(wins, total):
        return round(100 * wins / total, 1) if total else 0.0

    w = buckets["white"]
    b = buckets["black"]
    white_total = w["wins"] + w["draws"] + w["losses"]
    black_total = b["wins"] + b["draws"] + b["losses"]
    total = white_total + black_total
    overall_wins = w["wins"] + b["wins"]
    overall_draws = w["draws"] + b["draws"]
    overall_losses = w["losses"] + b["losses"]

    # Black repertoire vs 1.e4 and 1.d4
    e4_games = [p for p in valid_pgns if _first_white_move(p) == "e4"]
    d4_games = [p for p in valid_pgns if _first_white_move(p) == "d4"]

    vs_e4 = analyse_openings(e4_games, player, depth=8, top=5)["as_black"]
    vs_d4 = analyse_openings(d4_games, player, depth=8, top=5)["as_black"]

    return {
        "total": total,
        "as_white": {
            "count": white_total,
            "wins": w["wins"], "draws": w["draws"], "losses": w["losses"],
            "win_pct": _pct(w["wins"], white_total),
        },
        "as_black": {
            "count": black_total,
            "wins": b["wins"], "draws": b["draws"], "losses": b["losses"],
            "win_pct": _pct(b["wins"], black_total),
        },
        "overall": {
            "wins": overall_wins, "draws": overall_draws, "losses": overall_losses,
            "win_pct": _pct(overall_wins, total),
        },
        "avg_length": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "vs_e4": vs_e4,
        "vs_d4": vs_d4,
    }


def _first_white_move(pgn_text: str) -> str | None:
    """Return the UCI destination square of White's first move (e.g. 'e4')."""
    game = _parse_game(pgn_text)
    if game is None:
        return None
    for move in game.mainline_moves():
        return chess.square_name(move.to_square)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute player tendency stats from a PGN file.")
    parser.add_argument("pgn_file", help="PGN file containing the player's games")
    parser.add_argument("player", help="Player name to analyse")
    args = parser.parse_args()

    with open(args.pgn_file, encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    raw_games = re.split(r"\n(?=\[)", content.strip())
    print(f"Parsing {len(raw_games)} game(s)…", file=sys.stderr)

    result = analyse_stats(raw_games, args.player)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
