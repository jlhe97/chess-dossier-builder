"""
Shared PGN text utilities used across pipeline stages.
"""

import io

import chess.pgn


def split_pgn_games(text: str) -> list[str]:
    """
    Split a multi-game PGN text blob into a list of individual game PGN
    strings, re-serialised through python-chess so each is complete and
    self-contained.

    Deliberately not a '\\n(?=\\[)' regex split — that splits between
    every header line, not just between games, since a normal PGN header
    block has no blank lines between tags. It silently produces one
    fragment per header line plus a movetext fragment missing most of
    its headers (including White/Black), which then never attributes to
    any player.
    """
    games = []
    buf = io.StringIO(text)
    while True:
        try:
            game = chess.pgn.read_game(buf)
        except Exception:
            break
        if game is None:
            break
        out = io.StringIO()
        game.accept(chess.pgn.FileExporter(out))
        games.append(out.getvalue().strip())
    return games
