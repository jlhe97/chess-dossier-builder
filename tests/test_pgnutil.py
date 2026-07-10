"""
Unit tests for pgnutil.split_pgn_games.
"""

import textwrap

from pgnutil import split_pgn_games


MULTI_HEADER_TWO_GAMES = textwrap.dedent("""\
    [Event "Live Chess"]
    [Site "Chess.com"]
    [Date "2026.07.09"]
    [White "mr_gustavo"]
    [Black "MagnusCarlsen"]
    [Result "0-1"]
    [ECO "B00"]

    1. e4 e5 0-1

    [Event "Live Chess"]
    [Site "Chess.com"]
    [Date "2026.07.09"]
    [White "MagnusCarlsen"]
    [Black "someone"]
    [Result "1-0"]
    [ECO "C00"]

    1. d4 d5 1-0
""")


class TestSplitPgnGames:
    def test_splits_into_correct_number_of_games(self):
        games = split_pgn_games(MULTI_HEADER_TWO_GAMES)
        assert len(games) == 2

    def test_preserves_all_headers_per_game(self):
        # A naive '\n(?=\[)' regex split breaks this: it splits between
        # every header LINE, not between games, so White/Black end up in
        # their own orphaned fragments instead of staying with the game.
        games = split_pgn_games(MULTI_HEADER_TWO_GAMES)
        assert 'White "mr_gustavo"' in games[0]
        assert 'Black "MagnusCarlsen"' in games[0]
        assert 'White "MagnusCarlsen"' in games[1]
        assert 'Black "someone"' in games[1]

    def test_empty_string_returns_no_games(self):
        assert split_pgn_games("") == []

    def test_garbage_input_has_no_identifiable_player(self):
        # python-chess tolerantly parses plain text as an empty game with
        # placeholder headers ("?") rather than raising — harmless, since
        # it can never match a real player name downstream.
        games = split_pgn_games("not a pgn at all")
        assert all('White "?"' in g for g in games)

    def test_single_game_no_trailing_blank_line(self):
        pgn = '[White "A"]\n[Black "B"]\n[Result "1-0"]\n\n1. e4 1-0'
        games = split_pgn_games(pgn)
        assert len(games) == 1
        assert 'White "A"' in games[0]
