"""
Unit tests for lookup.lichess and lookup.chesscom — all HTTP calls mocked.
"""

import json
from unittest.mock import patch, MagicMock
from datetime import date

import pytest

from lookup.lichess import (
    search, get_profile, get_games, games_as_pgn, _slim_profile,
    get_studies, get_study_pgn, find_usernames_via_search as lichess_find_usernames_via_search,
)
from lookup.chesscom import (
    get_profile as cc_get_profile, get_games as cc_get_games,
    games_as_pgn as cc_games_as_pgn, guess_usernames, find_profile,
    find_usernames_via_search, _recent_months, _slim_profile as cc_slim_profile,
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

    def test_display_name_falls_back_to_id_for_autocomplete_only_data(self):
        # /api/player/autocomplete's raw entries only ever have "id", never
        # "username" — display_name must not go empty here, or every
        # autocomplete candidate scores identically (effectively unranked)
        # in the resolver's cheap name-only first pass.
        p = _slim_profile({"id": "kasparov1", "title": None, "perfs": {}})
        assert p["display_name"] == "kasparov1"

    def test_extracts_title(self):
        assert _slim_profile(LICHESS_USER)["title"] == "GM"

    def test_url_format(self):
        assert _slim_profile(LICHESS_USER)["url"] == "https://lichess.org/@/gmkasparov"

    def test_missing_perfs_omitted(self):
        data = {**LICHESS_USER, "perfs": {}}
        assert _slim_profile(data)["ratings"] == {}

    def test_missing_profile_omitted(self):
        p = _slim_profile(LICHESS_USER)
        assert p["real_name"] is None
        assert p["country"] is None
        assert p["fide_rating"] is None

    def test_extracts_profile_fields(self):
        data = {**LICHESS_USER, "profile": {
            "realName": "Garry Kasparov", "flag": "RU", "fideRating": 2812,
        }}
        p = _slim_profile(data)
        assert p["real_name"] == "Garry Kasparov"
        assert p["country"] == "RU"
        assert p["fide_rating"] == 2812

    def test_extracts_games_count(self):
        data = {**LICHESS_USER, "count": {"all": 10450, "rated": 10435}}
        assert _slim_profile(data)["games_count"] == 10450

    def test_missing_count_omitted(self):
        assert _slim_profile(LICHESS_USER)["games_count"] is None

    def test_extracts_last_active_as_iso_date(self):
        # 2026-07-10T00:00:00Z in epoch ms
        data = {**LICHESS_USER, "seenAt": 1783641600000}
        assert _slim_profile(data)["last_active"] == "2026-07-10"

    def test_missing_seen_at_omitted(self):
        assert _slim_profile(LICHESS_USER)["last_active"] is None


class TestLichessSearch:
    @patch("lookup.lichess.requests.get")
    def test_uses_player_autocomplete_endpoint(self, mock_get):
        # Lichess renamed /api/users/autocomplete -> /api/player/autocomplete;
        # hitting the old path 404s and silently breaks all Lichess matching.
        mock_get.return_value = _mock_response(json_data=LICHESS_AUTOCOMPLETE)
        search("kasparov")
        called_url = mock_get.call_args[0][0]
        assert "/player/autocomplete" in called_url
        assert "/users/autocomplete" not in called_url

    @patch("lookup.lichess.requests.get")
    def test_strips_comma_from_last_first_name(self, mock_get):
        # The autocomplete endpoint 400s on a literal comma in the term.
        mock_get.return_value = _mock_response(json_data=LICHESS_AUTOCOMPLETE)
        search("Kasparov, Garry")
        called_params = mock_get.call_args.kwargs["params"]
        assert "," not in called_params["term"]
        assert called_params["term"] == "Kasparov Garry"

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


class TestLichessGetStudies:
    @patch("lookup.lichess.requests.get")
    def test_parses_ndjson_list(self, mock_get):
        ndjson = '{"id":"xduT8rax","name":"Brasil"}\n{"id":"abcd1234","name":"Prep"}\n'
        mock_get.return_value = _mock_response(text_data=ndjson)
        studies = get_studies("nihalsarin")
        assert len(studies) == 2
        assert studies[0]["id"] == "xduT8rax"

    @patch("lookup.lichess.requests.get")
    def test_respects_max(self, mock_get):
        ndjson = '{"id":"a","name":"A"}\n{"id":"b","name":"B"}\n{"id":"c","name":"C"}\n'
        mock_get.return_value = _mock_response(text_data=ndjson)
        studies = get_studies("someuser", max=1)
        assert len(studies) == 1

    @patch("lookup.lichess.requests.get")
    def test_empty_studies(self, mock_get):
        mock_get.return_value = _mock_response(text_data="")
        assert get_studies("nobody") == []


class TestLichessGetStudyPgn:
    @patch("lookup.lichess.requests.get")
    def test_returns_pgn_text(self, mock_get):
        pgn = '[Event "Study: Chapter 1"]\n[White "Kasparov, Garry"]\n\n1. e4 *'
        mock_get.return_value = _mock_response(text_data=pgn)
        result = get_study_pgn("xduT8rax")
        assert "Kasparov, Garry" in result


class TestLichessFindUsernamesViaSearch:
    @patch("lookup.websearch.search")
    def test_extracts_usernames_from_profile_urls(self, mock_search):
        # Real-world case: Magnus Carlsen's actual Lichess account is the
        # pseudonymous "DrNykterstein" — unrelated to his name and
        # unfindable by /player/autocomplete searching "Magnus Carlsen".
        mock_search.return_value = [
            {"title": "Magnus Carlsen", "url": "https://lichess.org/@/DrNykterstein",
             "description": "..."},
            {"title": "Unrelated", "url": "https://example.com", "description": "..."},
        ]
        usernames = lichess_find_usernames_via_search("Carlsen, Magnus", "fake-key")
        assert usernames == ["DrNykterstein"]

    @patch("lookup.websearch.search")
    def test_dedupes_case_insensitively(self, mock_search):
        mock_search.return_value = [
            {"title": "a", "url": "https://lichess.org/@/SomeHandle", "description": ""},
            {"title": "b", "url": "https://lichess.org/@/somehandle", "description": ""},
        ]
        usernames = lichess_find_usernames_via_search("Carlsen, Magnus", "fake-key")
        assert len(usernames) == 1

    @patch("lookup.websearch.search")
    def test_no_matching_results(self, mock_search):
        mock_search.return_value = [{"title": "a", "url": "https://example.com", "description": ""}]
        assert lichess_find_usernames_via_search("Nobody, N", "fake-key") == []

    @patch("lookup.websearch.search")
    def test_query_uses_natural_name_order_unquoted_bare_domain(self, mock_search):
        # Confirmed empirically against a real unresolved case: exact-phrase
        # quoting a "Last, First" name, or appending a path fragment like
        # "/@", each independently kill recall on real search backends —
        # the query must be natural-order, unquoted, bare-domain.
        mock_search.return_value = []
        lichess_find_usernames_via_search("Kowalski, Marek Antoni", "http://localhost:8080")
        query = mock_search.call_args[0][0]
        assert query == "Marek Antoni Kowalski lichess.org"


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

    @patch("lookup.lichess.requests.get")
    @patch("lookup.lichess.time.sleep")
    def test_since_param_passed_through(self, mock_sleep, mock_get):
        mock_get.return_value = _mock_response(text_data="[Event \"?\"]\n\n1. e4 *")
        games_as_pgn("gmkasparov", max=300, since=1700000000000)
        assert mock_get.call_args.kwargs["params"]["since"] == 1700000000000

    @patch("lookup.lichess.requests.get")
    @patch("lookup.lichess.time.sleep")
    def test_since_omitted_when_not_given(self, mock_sleep, mock_get):
        mock_get.return_value = _mock_response(text_data="[Event \"?\"]\n\n1. e4 *")
        games_as_pgn("gmkasparov", max=1)
        assert "since" not in mock_get.call_args.kwargs["params"]


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

    def test_extracts_real_name_when_set(self):
        p = cc_slim_profile("MagnusCarlsen", CHESSCOM_PROFILE, CHESSCOM_STATS)
        assert p["real_name"] == "Magnus Carlsen"

    def test_real_name_none_when_unset(self):
        profile = {k: v for k, v in CHESSCOM_PROFILE.items() if k != "name"}
        p = cc_slim_profile("MagnusCarlsen", profile, CHESSCOM_STATS)
        assert p["real_name"] is None
        # display_name still falls back to the username, unlike real_name
        assert p["display_name"] == "MagnusCarlsen"

    def test_sums_games_count_across_time_controls(self):
        stats = {
            "chess_rapid": {"record": {"win": 28, "loss": 11, "draw": 3}},
            "chess_bullet": {"record": {"win": 97, "loss": 69, "draw": 7}},
        }
        p = cc_slim_profile("user", CHESSCOM_PROFILE, stats)
        assert p["games_count"] == 28 + 11 + 3 + 97 + 69 + 7

    def test_games_count_none_when_no_records(self):
        p = cc_slim_profile("MagnusCarlsen", CHESSCOM_PROFILE, CHESSCOM_STATS)
        assert p["games_count"] is None

    def test_extracts_last_active_as_iso_date(self):
        profile = {**CHESSCOM_PROFILE, "last_online": 1783641600}  # 2026-07-10T00:00:00Z
        p = cc_slim_profile("MagnusCarlsen", profile, CHESSCOM_STATS)
        assert p["last_active"] == "2026-07-10"

    def test_missing_last_online_omitted(self):
        p = cc_slim_profile("MagnusCarlsen", CHESSCOM_PROFILE, CHESSCOM_STATS)
        assert p["last_active"] is None


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


class TestChesscomFindUsernamesViaSearch:
    @patch("lookup.websearch.search")
    def test_extracts_usernames_from_member_urls(self, mock_search):
        mock_search.return_value = [
            {"title": "Pierre Delacroix - Chess.com",
             "url": "https://www.chess.com/member/beaumontchess_fr", "description": "..."},
            {"title": "Unrelated", "url": "https://example.com", "description": "..."},
        ]
        usernames = find_usernames_via_search("Delacroix, Pierre", "fake-key")
        assert usernames == ["beaumontchess_fr"]

    @patch("lookup.websearch.search")
    def test_dedupes_usernames(self, mock_search):
        mock_search.return_value = [
            {"title": "a", "url": "https://www.chess.com/member/beaumontchess_fr", "description": ""},
            {"title": "b", "url": "https://www.chess.com/member/BeaumontChess_FR", "description": ""},
        ]
        usernames = find_usernames_via_search("Delacroix, Pierre", "fake-key")
        assert len(usernames) == 1

    @patch("lookup.websearch.search")
    def test_no_matching_results(self, mock_search):
        mock_search.return_value = [{"title": "a", "url": "https://example.com", "description": ""}]
        assert find_usernames_via_search("Nobody, N", "fake-key") == []

    @patch("lookup.websearch.search")
    def test_query_uses_natural_name_order_unquoted_bare_domain(self, mock_search):
        # Confirmed empirically against a real unresolved case: exact-phrase
        # quoting a "Last, First" name, or appending a path fragment like
        # "/member", each independently kill recall on real search backends
        # — the query must be natural-order, unquoted, bare-domain.
        mock_search.return_value = []
        find_usernames_via_search("Kowalski, Marek Antoni", "http://localhost:8080")
        query = mock_search.call_args[0][0]
        assert query == "Marek Antoni Kowalski chess.com"
