"""
Unit tests for dossier.report — no network, no files.
"""

import json
import textwrap
import pytest

from dossier.report import build_dossier, render_markdown, render_json


# ---------------------------------------------------------------------------
# PGN fixtures (reuse same pattern as test_analysis)
# ---------------------------------------------------------------------------

def _pgn(white, black, result, moves="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6"):
    return textwrap.dedent(f"""\
        [White "{white}"]
        [Black "{black}"]
        [Result "{result}"]

        {moves} {result}
    """)


PLAYER = "Smith, John"

GAMES = [
    _pgn(PLAYER, "Opponent A", "1-0"),
    _pgn(PLAYER, "Opponent B", "1-0"),
    _pgn(PLAYER, "Opponent C", "0-1"),
    _pgn(PLAYER, "Opponent D", "1/2-1/2"),
    _pgn("Opponent E", PLAYER, "0-1",
         "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6"),
    _pgn("Opponent F", PLAYER, "1-0",
         "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7"),
]

PROFILE_LICHESS = {
    "username": "jsmith",
    "display_name": "jsmith",
    "title": None,
    "ratings": {"rapid": 1750, "blitz": 1700},
    "url": "https://lichess.org/@/jsmith",
}

PROFILE_CHESSCOM = {
    "username": "JohnSmith99",
    "display_name": "John Smith",
    "title": None,
    "ratings": {"rapid": 1720},
    "url": "https://www.chess.com/member/JohnSmith99",
    "country": "US",
}


# ---------------------------------------------------------------------------
# build_dossier
# ---------------------------------------------------------------------------

class TestBuildDossier:
    def setup_method(self):
        self.dossier = build_dossier(PLAYER, GAMES, profiles=[PROFILE_LICHESS])

    def test_player_field(self):
        assert self.dossier["player"] == PLAYER

    def test_profiles_included(self):
        assert len(self.dossier["profiles"]) == 1
        assert self.dossier["profiles"][0]["username"] == "jsmith"

    def test_stats_present(self):
        assert "stats" in self.dossier
        assert self.dossier["stats"]["total"] == len(GAMES)

    def test_openings_present(self):
        assert "openings" in self.dossier
        assert "as_white" in self.dossier["openings"]
        assert "as_black" in self.dossier["openings"]

    def test_generated_date_present(self):
        assert "generated" in self.dossier
        assert len(self.dossier["generated"]) == 10  # YYYY-MM-DD

    def test_no_profiles_defaults_to_empty(self):
        d = build_dossier(PLAYER, GAMES)
        assert d["profiles"] == []

    def test_correct_white_game_count(self):
        assert self.dossier["stats"]["as_white"]["count"] == 4

    def test_correct_black_game_count(self):
        assert self.dossier["stats"]["as_black"]["count"] == 2


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def setup_method(self):
        self.dossier = build_dossier(PLAYER, GAMES, profiles=[PROFILE_LICHESS,
                                                               PROFILE_CHESSCOM])
        self.md = render_markdown(self.dossier)

    def test_contains_player_name(self):
        assert PLAYER in self.md

    def test_contains_overview_heading(self):
        assert "## Overview" in self.md

    def test_contains_as_white_heading(self):
        assert "## As White" in self.md

    def test_contains_as_black_heading(self):
        assert "## As Black" in self.md

    def test_contains_profiles_heading(self):
        assert "## Online Profiles" in self.md

    def test_lichess_profile_linked(self):
        assert "lichess.org" in self.md

    def test_chesscom_profile_linked(self):
        assert "chess.com" in self.md

    def test_contains_opening_table(self):
        assert "| Opening |" in self.md

    def test_contains_total_games(self):
        assert str(len(GAMES)) in self.md

    def test_contains_generated_date(self):
        assert self.dossier["generated"] in self.md

    def test_vs_e4_section_present(self):
        assert "vs 1. e4" in self.md

    def test_vs_d4_section_present(self):
        assert "vs 1. d4" in self.md

    def test_no_profiles_omits_profiles_section(self):
        d = build_dossier(PLAYER, GAMES)
        md = render_markdown(d)
        assert "## Online Profiles" not in md


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------

class TestRenderJson:
    def test_valid_json(self):
        d = build_dossier(PLAYER, GAMES)
        parsed = json.loads(render_json(d))
        assert parsed["player"] == PLAYER

    def test_contains_all_keys(self):
        d = build_dossier(PLAYER, GAMES)
        parsed = json.loads(render_json(d))
        for key in ("player", "profiles", "stats", "openings", "generated"):
            assert key in parsed

    def test_stats_totals_correct(self):
        d = build_dossier(PLAYER, GAMES)
        parsed = json.loads(render_json(d))
        assert parsed["stats"]["total"] == len(GAMES)
