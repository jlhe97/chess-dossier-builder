"""
Unit tests for lookup.lichess and lookup.chesscom — all HTTP calls mocked.
"""

import json
from unittest.mock import patch, MagicMock
from datetime import date

import pytest

from lookup.lichess import search, get_profile, get_games, games_as_pgn, _slim_profile
from lookup.chesscom import (
    get_profile as cc_get_profile, get_games as cc_get_games,
    games_as_pgn as cc_games_as_pgn, guess_usernames, find_profile,
    _recent_months, _slim_profile as cc_slim_profile,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LICHESS_USER = {
    "id": "gmkasparov",
    "username": "GMKasparov",
    "title": "GM",
    "perfs": {
        "classical": {"rating": 2800, "games": 10},
        "rapid":     {"rating": 2750, "games": 20},
        "blitz":     {"rating": 2700, "games": 50},
    },
}

LICHESS_AUTOCOMPLETE = [
    {"id": "gmkasparov", "username": "GMKasparov", "title": "GM", "perfs": {}},
    {"id": "kasparov1",  "username": "kasparov1",  "title": None, "perfs": {}},
]

LICHESS_GAME_NDJSON = (
    '{"id":"abc1","white":{"user":{"name":"GMKasparov"}},'
    '"black":{"user":{"name":"opponent"}},"winner":"white"}\n'
    '{"id":"abc2","white":{"user":{"name":"opponent"}},'
    '"black":{"user":{"name":"GMKasparov"}},"winner":"black"}\n'
)

CHESSCOM_PROFILE = {
    "username": "MagnusCarlsen",
    "name": "Magnus Carlsen",
    "title": "GM",
    "url": "https://www.chess.com/member/MagnusCarlsen",
    "country": "https://api.chess.com/pub/country/NO",
}

CHESSCOM_STATS = {
    "chess_rapid":     {"last": {"rating": 2850}},
    "chess_blitz":     {"last": {"rating": 2830}},
    "chess_bullet":    {"last": {"rating": 2820}},
    "chess_classical": {"last": {"rating": 2875}},
}

CHESSCOM_GAMES = {
    "games": [
        {"white": {"username": "MagnusCarlsen"}, "black": {"username": "opponent"},
         "pgn": "[White \"MagnusCarlsen\"]\n\n1. e4 *"},
        {"white": {"username": "opponent"}, "black": {"username": "MagnusCarlsen"},
         "pgn": "[Black \"MagnusCarlsen\"]\n\n1. d4 *"},
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data=None, text_data=None, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data or {}
    mock.text = text_data or ""
    mock.raise_for_status.return_value = None
    mock.iter_lines.return_value = [
        line.encode() for line in (text_data or "").splitlines() if line
    ]
    return mock


# ---------------------------------------------------------------------------
# Lichess tests
# ---------------------------------------------------------------------------

class TestLichessSlimProfile:
    def test_extracts_ratings(self):
        p = _slim_profile(LICHESS_USER)
        assert p["ratings"]["classical"] == 2800
        assert p["ratings"]["rapid"] == 2750
        assert p["ratings"]["blitz"] == 2700

    def test_extracts_username(self):
        p = _slim_profile(LICHESS_USER)
        assert p["username"] == "gmkasparov"
        assert p["display_name"] == "GMKasparov"

    def test_extracts_title(self):
        assert _slim_profile(LICHESS_USER)["title"] == "GM"

    def test_url_format(self):
        assert _slim_profile(LICHESS_USER)["url"] == "https://lichess.org/@/gmkasparov"

    def test_missing_perfs_omitted(self):
        data = {**LICHESS_USER, "perfs": {}}
        assert _slim_profile(data)["ratings"] == {}


class TestLichessSearch:
    @patch("lookup.lichess.requests.get")
    def test_returns_candidates(self, mock_get):
        mock_get.return_value = _mock_response(json_data=LICHESS_AUTOCOMPLETE)
        results = search("kasparov")
        assert len(results) == 2
        assert results[0]["username"] == "gmkasparov"

    @patch("lookup.lichess.requests.get")
    def test_respects_max_results(self, mock_get):
        mock_get.return_value = _mock_response(json_data=LICHESS_AUTOCOMPLETE)
        results = search("kasparov", max_results=1)
        assert len(results) == 1

    @patch("lookup.lichess.requests.get")
    def test_handles_result_wrapper(self, mock_get):
        mock_get.return_value = _mock_response(
            json_data={"result": LICHESS_AUTOCOMPLETE}
        )
        results = search("kasparov")
        assert len(results) == 2


class TestLichessGetProfile:
    @patch("lookup.lichess.requests.get")
    def test_fetches_and_slims(self, mock_get):
        mock_get.return_value = _mock_response(json_data=LICHESS_USER)
        profile = get_profile("gmkasparov")
        assert profile["username"] == "gmkasparov"
        assert profile["ratings"]["classical"] == 2800


class TestLichessGetGames:
    @patch("lookup.lichess.requests.get")
    @patch("lookup.lichess.time.sleep")
    def test_parses_ndjson(self, mock_sleep, mock_get):
        mock_get.return_value = _mock_response(text_data=LICHESS_GAME_NDJSON)
        games = get_games("gmkasparov", max=2)
        assert len(games) == 2
        assert games[0]["id"] == "abc1"

    @patch("lookup.lichess.requests.get")
    @patch("lookup.lichess.time.sleep")
    def test_returns_pgn_string(self, mock_sleep, mock_get):
        pgn = "[Event \"?\"]\n\n1. e4 *"
        mock_get.return_value = _mock_response(text_data=pgn)
        result = games_as_pgn("gmkasparov", max=1)
        assert "1. e4" in result


# ---------------------------------------------------------------------------
# chess.com tests
# ---------------------------------------------------------------------------

class TestChesscomGuessUsernames:
    def test_last_first_format(self):
        candidates = guess_usernames("Carlsen, Magnus")
        assert "magnuscarlsen" in candidates
        assert "carlsenmagnус" not in candidates  # no cyrillic
        assert any("carlsen" in c for c in candidates)

    def test_first_last_format(self):
        candidates = guess_usernames("Magnus Carlsen")
        assert "magnuscarlsen" in candidates

    def test_no_duplicates_from_same_input(self):
        candidates = guess_usernames("Smith, John")
        # All should be unique
        assert len(candidates) == len(set(candidates))


class TestChesscomSlimProfile:
    def test_extracts_ratings(self):
        p = cc_slim_profile("MagnusCarlsen", CHESSCOM_PROFILE, CHESSCOM_STATS)
        assert p["ratings"]["rapid"] == 2850
        assert p["ratings"]["classical"] == 2875

    def test_extracts_country_code(self):
        p = cc_slim_profile("MagnusCarlsen", CHESSCOM_PROFILE, CHESSCOM_STATS)
        assert p["country"] == "NO"

    def test_empty_stats(self):
        p = cc_slim_profile("user", CHESSCOM_PROFILE, {})
        assert p["ratings"] == {}


class TestChesscomGetProfile:
    @patch("lookup.chesscom.requests.get")
    def test_fetches_profile_and_stats(self, mock_get):
        mock_get.side_effect = [
            _mock_response(json_data=CHESSCOM_PROFILE),
            _mock_response(json_data=CHESSCOM_STATS),
        ]
        profile = cc_get_profile("MagnusCarlsen")
        assert profile["display_name"] == "Magnus Carlsen"
        assert profile["ratings"]["rapid"] == 2850


class TestChesscomGetGames:
    @patch("lookup.chesscom.requests.get")
    def test_fetches_monthly_games(self, mock_get):
        mock_get.return_value = _mock_response(json_data=CHESSCOM_GAMES)
        games = cc_get_games("MagnusCarlsen", months=1)
        assert len(games) == 2

    @patch("lookup.chesscom.requests.get")
    def test_skips_missing_months(self, mock_get):
        from requests import HTTPError
        err = MagicMock()
        err.raise_for_status.side_effect = HTTPError("404")
        mock_get.return_value = err
        games = cc_get_games("unknown_user", months=1)
        assert games == []

    @patch("lookup.chesscom.requests.get")
    def test_pgn_concatenated(self, mock_get):
        mock_get.return_value = _mock_response(text_data="[White \"A\"]\n\n1. e4 *")
        pgn = cc_games_as_pgn("MagnusCarlsen", months=1)
        assert "1. e4" in pgn


class TestRecentMonths:
    def test_returns_n_months(self):
        months = _recent_months(3)
        assert len(months) == 3

    def test_descending_order(self):
        months = _recent_months(3)
        assert months[0] >= months[1] >= months[2]

    def test_month_values_valid(self):
        for year, month in _recent_months(12):
            assert 1 <= month <= 12
            assert year >= 2020


class TestChesscomFindProfile:
    @patch("lookup.chesscom.get_profile")
    def test_returns_first_match(self, mock_get_profile):
        profile = {"username": "johnsmith", "ratings": {}}
        mock_get_profile.side_effect = [
            __import__("requests").HTTPError(),
            profile,
        ]
        result = find_profile("Smith, John")
        assert result == profile

    @patch("lookup.chesscom.get_profile")
    def test_returns_none_when_no_match(self, mock_get_profile):
        mock_get_profile.side_effect = __import__("requests").HTTPError()
        assert find_profile("Zzz, Qqq") is None
