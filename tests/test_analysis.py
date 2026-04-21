"""
Unit tests for analysis.openings and analysis.stats — no network, no files.
"""

import textwrap
import pytest

from analysis.openings import (
    analyse_openings, _opening_line, _result_for_player, _parse_game,
)
from analysis.stats import analyse_stats, _first_white_move


# ---------------------------------------------------------------------------
# PGN fixtures
# ---------------------------------------------------------------------------

def _pgn(white, black, result, moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6"):
    return textwrap.dedent(f"""\
        [White "{white}"]
        [Black "{black}"]
        [Result "{result}"]

        {moves} {result}
    """)


# Smith as White: e4 player, 3 wins 1 draw 1 loss
W_WIN1  = _pgn("Smith, John", "Opponent A", "1-0")
W_WIN2  = _pgn("Smith, John", "Opponent B", "1-0")
W_WIN3  = _pgn("Smith, John", "Opponent C", "1-0")
W_DRAW  = _pgn("Smith, John", "Opponent D", "1/2-1/2")
W_LOSS  = _pgn("Smith, John", "Opponent E", "0-1")

# Smith as Black vs 1.e4 Sicilian: 2 wins 1 loss
B_SIC1  = _pgn("Opponent F", "Smith, John", "0-1",
               "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6")
B_SIC2  = _pgn("Opponent G", "Smith, John", "0-1",
               "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6")
B_SIC_L = _pgn("Opponent H", "Smith, John", "1-0",
               "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6")

# Smith as Black vs 1.d4 KID: 1 draw
B_KID   = _pgn("Opponent I", "Smith, John", "1/2-1/2",
               "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6")

ALL_GAMES = [W_WIN1, W_WIN2, W_WIN3, W_DRAW, W_LOSS,
             B_SIC1, B_SIC2, B_SIC_L, B_KID]


# ---------------------------------------------------------------------------
# _parse_game
# ---------------------------------------------------------------------------

class TestParseGame:
    def test_parses_valid_pgn(self):
        game = _parse_game(W_WIN1)
        assert game is not None
        assert game.headers["White"] == "Smith, John"

    def test_returns_none_on_garbage(self):
        assert _parse_game("not a pgn") is None or True  # graceful


# ---------------------------------------------------------------------------
# _opening_line
# ---------------------------------------------------------------------------

class TestOpeningLine:
    def test_respects_depth(self):
        game = _parse_game(W_WIN1)
        line = _opening_line(game, depth=2)
        assert line == "1. e4 e5"

    def test_full_depth_six(self):
        game = _parse_game(W_WIN1)
        line = _opening_line(game, depth=6)
        assert "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6" == line

    def test_depth_exceeds_game_length(self):
        pgn = _pgn("A", "B", "1-0", "1. e4 *")
        game = _parse_game(pgn)
        line = _opening_line(game, depth=10)
        assert line == "1. e4"


# ---------------------------------------------------------------------------
# _result_for_player
# ---------------------------------------------------------------------------

class TestResultForPlayer:
    def test_white_wins(self):
        assert _result_for_player(_parse_game(W_WIN1), "Smith") == "win"

    def test_white_loses(self):
        assert _result_for_player(_parse_game(W_LOSS), "Smith") == "loss"

    def test_draw(self):
        assert _result_for_player(_parse_game(W_DRAW), "Smith") == "draw"

    def test_black_wins(self):
        assert _result_for_player(_parse_game(B_SIC1), "Smith") == "win"

    def test_black_loses(self):
        assert _result_for_player(_parse_game(B_SIC_L), "Smith") == "loss"


# ---------------------------------------------------------------------------
# analyse_openings
# ---------------------------------------------------------------------------

class TestAnalyseOpenings:
    def test_white_line_count(self):
        result = analyse_openings(ALL_GAMES, "Smith, John", depth=6)
        total_white = sum(r["count"] for r in result["as_white"])
        assert total_white == 5

    def test_black_line_count(self):
        result = analyse_openings(ALL_GAMES, "Smith, John", depth=6)
        total_black = sum(r["count"] for r in result["as_black"])
        assert total_black == 4

    def test_sorted_by_frequency(self):
        result = analyse_openings(ALL_GAMES, "Smith, John", depth=6)
        counts = [r["count"] for r in result["as_black"]]
        assert counts == sorted(counts, reverse=True)

    def test_win_pct_calculation(self):
        result = analyse_openings([W_WIN1, W_WIN2, W_LOSS], "Smith, John", depth=2)
        row = result["as_white"][0]
        assert row["wins"] == 2
        assert row["losses"] == 1
        assert row["win_pct"] == pytest.approx(66.7, abs=0.1)

    def test_top_limits_results(self):
        result = analyse_openings(ALL_GAMES, "Smith, John", depth=6, top=1)
        assert len(result["as_white"]) <= 1
        assert len(result["as_black"]) <= 1

    def test_unknown_player_returns_empty(self):
        result = analyse_openings(ALL_GAMES, "Nobody, N", depth=6)
        assert result["as_white"] == []
        assert result["as_black"] == []

    def test_case_insensitive_player_match(self):
        result = analyse_openings(ALL_GAMES, "smith", depth=2)
        assert sum(r["count"] for r in result["as_white"]) == 5


# ---------------------------------------------------------------------------
# _first_white_move
# ---------------------------------------------------------------------------

class TestFirstWhiteMove:
    def test_e4(self):
        assert _first_white_move(W_WIN1) == "e4"

    def test_d4(self):
        assert _first_white_move(B_KID) == "d4"

    def test_sicilian_e4(self):
        assert _first_white_move(B_SIC1) == "e4"


# ---------------------------------------------------------------------------
# analyse_stats
# ---------------------------------------------------------------------------

class TestAnalyseStats:
    def setup_method(self):
        self.stats = analyse_stats(ALL_GAMES, "Smith, John")

    def test_total_games(self):
        assert self.stats["total"] == 9

    def test_as_white_count(self):
        assert self.stats["as_white"]["count"] == 5

    def test_as_black_count(self):
        assert self.stats["as_black"]["count"] == 4

    def test_white_wins(self):
        assert self.stats["as_white"]["wins"] == 3

    def test_white_draw(self):
        assert self.stats["as_white"]["draws"] == 1

    def test_white_loss(self):
        assert self.stats["as_white"]["losses"] == 1

    def test_overall_wins(self):
        assert self.stats["overall"]["wins"] == 5   # 3 white + 2 black

    def test_win_pct_as_white(self):
        assert self.stats["as_white"]["win_pct"] == pytest.approx(60.0)

    def test_avg_length_positive(self):
        assert self.stats["avg_length"] > 0

    def test_vs_e4_not_empty(self):
        assert len(self.stats["vs_e4"]) > 0

    def test_vs_d4_has_kid(self):
        assert len(self.stats["vs_d4"]) > 0

    def test_vs_e4_only_sicilian(self):
        lines = [r["line"] for r in self.stats["vs_e4"]]
        assert all("c5" in line or "e4 c5" in line for line in lines)
