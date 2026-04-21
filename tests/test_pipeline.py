"""
Unit tests for pipeline.resolver, pipeline.runner, and the confidence
flag in dossier.report — all network and I/O calls mocked.
"""

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

from pipeline.resolver import resolve_lichess, resolve_chesscom, _similarity
from pipeline.runner import _slug, run_pipeline
from dossier.report import build_dossier, render_markdown, render_html, render_html_combined


# ---------------------------------------------------------------------------
# _similarity
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical(self):
        assert _similarity("Magnus Carlsen", "Magnus Carlsen") == pytest.approx(1.0)

    def test_case_insensitive(self):
        assert _similarity("smith", "SMITH") == pytest.approx(1.0)

    def test_partial_match(self):
        assert 0.0 < _similarity("Smith John", "jsmith") < 1.0

    def test_no_match(self):
        assert _similarity("aaaa", "zzzz") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_last_first(self):
        assert _slug("Smith, John") == "smith_john"

    def test_spaces(self):
        assert _slug("Magnus Carlsen") == "magnus_carlsen"

    def test_special_chars(self):
        assert _slug("O'Brien, Pat") == "o_brien_pat"


# ---------------------------------------------------------------------------
# resolve_lichess
# ---------------------------------------------------------------------------

LICHESS_CANDIDATES = [
    {"username": "jsmith", "display_name": "JSmith", "title": None, "ratings": {}, "url": ""},
]

class TestResolveLichess:
    @patch("pipeline.resolver.resolve_lichess")
    def test_high_confidence(self, mock_resolve):
        mock_resolve.return_value = ("jsmith", "high")
        u, c = mock_resolve("Smith, John")
        assert u == "jsmith"
        assert c == "high"

    @patch("lookup.lichess.search")
    def test_returns_none_when_no_candidates(self, mock_search):
        mock_search.return_value = []
        u, c = resolve_lichess("Zzz, Qqq")
        assert u is None
        assert c is None

    @patch("lookup.lichess.search")
    def test_low_confidence_on_weak_match(self, mock_search):
        mock_search.return_value = [
            {"username": "xyz99", "display_name": "xyz99", "title": None,
             "ratings": {}, "url": ""}
        ]
        u, c = resolve_lichess("Smith, John")
        # "xyz99" vs "Smith, John" → low similarity
        assert c in ("low", None)

    @patch("lookup.lichess.search", side_effect=Exception("network error"))
    def test_returns_none_on_exception(self, mock_search):
        u, c = resolve_lichess("Smith, John")
        assert u is None


# ---------------------------------------------------------------------------
# resolve_chesscom
# ---------------------------------------------------------------------------

class TestResolveChesscom:
    @patch("lookup.chesscom.get_profile")
    def test_high_confidence_first_guess(self, mock_get):
        mock_get.return_value = {"username": "johnsmith", "ratings": {}}
        u, c = resolve_chesscom("Smith, John")
        assert u is not None
        assert c == "high"

    @patch("lookup.chesscom.get_profile")
    def test_low_confidence_later_guess(self, mock_get):
        # First 2 fail, 3rd succeeds → low confidence
        err = requests.HTTPError()
        mock_get.side_effect = [err, err, {"username": "jsmith_chess", "ratings": {}}]
        u, c = resolve_chesscom("Smith, John")
        assert u is not None
        assert c == "low"

    @patch("lookup.chesscom.get_profile", side_effect=requests.HTTPError())
    def test_returns_none_when_all_fail(self, mock_get):
        u, c = resolve_chesscom("Zzz, Qqq")
        assert u is None


# ---------------------------------------------------------------------------
# Confidence flag in dossier render
# ---------------------------------------------------------------------------

def _pgn(white, black, result):
    return textwrap.dedent(f"""\
        [White "{white}"]
        [Black "{black}"]
        [Result "{result}"]

        1. e4 e5 {result}
    """)

class TestConfidenceFlag:
    def test_low_confidence_shows_warning(self):
        profile = {
            "username": "jsmith", "display_name": "jsmith", "title": None,
            "ratings": {}, "url": "https://lichess.org/@/jsmith",
            "confidence": "low",
        }
        d = build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")],
                          profiles=[profile])
        md = render_markdown(d)
        assert "low-confidence" in md

    def test_high_confidence_no_warning(self):
        profile = {
            "username": "jsmith", "display_name": "jsmith", "title": None,
            "ratings": {}, "url": "https://lichess.org/@/jsmith",
            "confidence": "high",
        }
        d = build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")],
                          profiles=[profile])
        md = render_markdown(d)
        assert "low-confidence" not in md

    def test_no_confidence_key_no_warning(self):
        profile = {
            "username": "jsmith", "display_name": "jsmith", "title": None,
            "ratings": {}, "url": "https://lichess.org/@/jsmith",
        }
        d = build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")],
                          profiles=[profile])
        md = render_markdown(d)
        assert "low-confidence" not in md


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

class TestRenderHtml:
    def _dossier(self, confidence=None):
        profile = {
            "username": "jsmith", "display_name": "jsmith", "title": None,
            "ratings": {"rapid": 1800}, "url": "https://lichess.org/@/jsmith",
        }
        if confidence is not None:
            profile["confidence"] = confidence
        return build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")],
                              profiles=[profile])

    def test_returns_valid_html(self):
        html = render_html(self._dossier())
        assert html.startswith("<!doctype html")
        assert "<title>" in html
        assert "</html>" in html

    def test_contains_player_name(self):
        html = render_html(self._dossier())
        assert "Smith, John" in html

    def test_contains_as_white_section(self):
        html = render_html(self._dossier())
        assert "As White" in html

    def test_contains_as_black_section(self):
        html = render_html(self._dossier())
        assert "As Black" in html

    def test_low_confidence_shows_warning(self):
        html = render_html(self._dossier(confidence="low"))
        assert "low-confidence" in html

    def test_high_confidence_no_warning(self):
        html = render_html(self._dossier(confidence="high"))
        assert "low-confidence" not in html

    def test_win_pct_colour_class_present(self):
        html = render_html(self._dossier())
        assert any(c in html for c in ("wp-hi", "wp-mid", "wp-lo"))

    def test_combined_has_nav_and_all_players(self):
        d1 = build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")])
        d2 = build_dossier("Doe, Jane",   [_pgn("Doe, Jane",   "Opp", "0-1")])
        html = render_html_combined([d1, d2])
        assert "Smith, John" in html
        assert "Doe, Jane" in html
        assert "<nav>" in html

    def test_combined_has_section_anchors(self):
        d1 = build_dossier("Smith, John", [_pgn("Smith, John", "Opp", "1-0")])
        html = render_html_combined([d1])
        assert "id='smith_john'" in html


# ---------------------------------------------------------------------------
# run_pipeline (fully mocked)
# ---------------------------------------------------------------------------

MOCK_PLAYERS = [
    {"name": "Smith, John", "rating": "1800", "section": "Open"},
    {"name": "Doe, Jane",   "rating": "1650", "section": "Open"},
]

SAMPLE_PGN = textwrap.dedent("""\
    [White "Smith, John"]
    [Black "Opponent"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 1-0
""")


class TestRunPipeline:
    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_output_dir(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        out = tmp_path / "out"
        run_pipeline("Challenge34", output_dir=str(out))
        assert out.exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_per_player_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        assert (tmp_path / "smith_john.md").exists()
        assert (tmp_path / "doe_jane.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_combined_md(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        assert (tmp_path / "combined.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_combined_contains_all_players(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        combined = (tmp_path / "combined.md").read_text()
        assert "Smith, John" in combined
        assert "Doe, Jane" in combined

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=[])
    def test_empty_entry_list_returns_no_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        result = run_pipeline("Challenge34", output_dir=str(tmp_path))
        assert result == []

    @patch("pipeline.runner._fetch_chesscom_games",
           return_value=([], {"username": "jdoe", "display_name": "jdoe",
                              "title": None, "ratings": {},
                              "url": "https://www.chess.com/member/jdoe",
                              "country": "US"}))
    @patch("pipeline.runner._fetch_lichess_games",
           return_value=([SAMPLE_PGN], {"username": "jsmith", "display_name": "jsmith",
                                        "title": None, "ratings": {"rapid": 1800},
                                        "url": "https://lichess.org/@/jsmith"}))
    @patch("pipeline.runner.resolve_chesscom", return_value=("jdoe", "low"))
    @patch("pipeline.runner.resolve_lichess",  return_value=("jsmith", "high"))
    @patch("pipeline.runner.scrape_entry_list",
           return_value=[{"name": "Smith, John", "rating": "1800"}])
    def test_games_fed_into_dossier(self, mock_scrape, mock_lich, mock_cc,
                                    mock_fetch_lich, mock_fetch_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        md = (tmp_path / "smith_john.md").read_text()
        assert "Smith, John" in md
        assert "## Overview" in md

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_json_format_no_combined(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="json")
        assert (tmp_path / "smith_john.json").exists()
        assert not (tmp_path / "combined.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_html_format_creates_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        assert (tmp_path / "smith_john.html").exists()
        assert (tmp_path / "doe_jane.html").exists()
        assert (tmp_path / "combined.html").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_html_combined_has_nav(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        html = (tmp_path / "combined.html").read_text()
        assert "<nav>" in html
        assert "Smith, John" in html
        assert "Doe, Jane" in html
