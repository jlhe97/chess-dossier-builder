"""
Unit tests for lookup.websearch and lookup.broadcasts — all HTTP calls mocked.
"""

from unittest.mock import patch, MagicMock

import pytest
import requests

from lookup.websearch import search as web_search
from lookup.broadcasts import (
    _extract_round_id, find_broadcast_round_ids, get_round_pgn, find_games,
)


def _mock_response(json_data=None, text_data=None, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data or {}
    mock.text = text_data or ""
    mock.raise_for_status.return_value = None
    return mock


# ---------------------------------------------------------------------------
# lookup.websearch
# ---------------------------------------------------------------------------

SEARXNG_RESPONSE = {
    "query": "some query",
    "results": [
        {"title": "Round 1", "url": "https://lichess.org/broadcast/x/round-1/9PtLvze6",
         "content": "..."},
        {"title": "Unrelated", "url": "https://example.com", "content": "..."},
    ],
}


class TestWebSearch:
    @patch("lookup.websearch.requests.get")
    def test_returns_results(self, mock_get):
        mock_get.return_value = _mock_response(json_data=SEARXNG_RESPONSE)
        results = web_search("some query", "http://localhost:8080")
        assert len(results) == 2
        assert results[0]["url"] == "https://lichess.org/broadcast/x/round-1/9PtLvze6"

    @patch("lookup.websearch.requests.get")
    def test_maps_content_field_to_description(self, mock_get):
        mock_get.return_value = _mock_response(json_data=SEARXNG_RESPONSE)
        results = web_search("query", "http://localhost:8080")
        assert results[0]["description"] == "..."

    @patch("lookup.websearch.requests.get")
    def test_requests_json_format(self, mock_get):
        mock_get.return_value = _mock_response(json_data=SEARXNG_RESPONSE)
        web_search("query", "http://localhost:8080")
        params = mock_get.call_args.kwargs["params"]
        assert params["format"] == "json"

    @patch("lookup.websearch.requests.get")
    def test_strips_trailing_slash_from_base_url(self, mock_get):
        mock_get.return_value = _mock_response(json_data=SEARXNG_RESPONSE)
        web_search("query", "http://localhost:8080/")
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://localhost:8080/search"

    @patch("lookup.websearch.requests.get")
    def test_empty_results(self, mock_get):
        mock_get.return_value = _mock_response(json_data={"query": "nothing", "results": []})
        assert web_search("nothing", "http://localhost:8080") == []

    @patch("lookup.websearch.requests.get")
    def test_raises_on_http_error(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("json format not enabled")
        mock_get.return_value = resp
        with pytest.raises(requests.HTTPError):
            web_search("query", "http://localhost:8080")


# ---------------------------------------------------------------------------
# _extract_round_id
# ---------------------------------------------------------------------------

class TestExtractRoundId:
    def test_plain_round_url(self):
        url = "https://lichess.org/broadcast/tim-just-winter-open-xlii/round-5/tJ5Md3I9"
        assert _extract_round_id(url) == "tJ5Md3I9"

    def test_deep_link_with_game_id(self):
        url = "https://lichess.org/broadcast/tim-just-winter-open-xlii/round-1/9PtLvze6/beOPcXRZ"
        assert _extract_round_id(url) == "9PtLvze6"

    def test_non_broadcast_url_returns_none(self):
        assert _extract_round_id("https://lichess.org/@/DrNykterstein") is None

    def test_unrelated_url_returns_none(self):
        assert _extract_round_id("https://example.com") is None


# ---------------------------------------------------------------------------
# find_broadcast_round_ids
# ---------------------------------------------------------------------------

class TestFindBroadcastRoundIds:
    @patch("lookup.broadcasts.web_search")
    def test_extracts_and_dedupes_round_ids(self, mock_search):
        mock_search.return_value = [
            {"title": "a", "url": "https://lichess.org/broadcast/x/round-1/9PtLvze6", "description": ""},
            {"title": "b", "url": "https://lichess.org/broadcast/x/round-1/9PtLvze6/beOPcXRZ", "description": ""},
            {"title": "c", "url": "https://example.com", "description": ""},
        ]
        ids = find_broadcast_round_ids("Nakamura, Hikaru", "http://localhost:8080")
        assert ids == ["9PtLvze6"]

    @patch("lookup.broadcasts.web_search")
    def test_no_broadcast_results(self, mock_search):
        mock_search.return_value = [{"title": "a", "url": "https://example.com", "description": ""}]
        assert find_broadcast_round_ids("Nobody, N", "http://localhost:8080") == []


# ---------------------------------------------------------------------------
# get_round_pgn / find_games
# ---------------------------------------------------------------------------

ROUND_PGN = (
    '[Event "Test Open"]\n[White "Nakamura, Hikaru"]\n[Black "Opponent"]\n'
    '[GameURL "https://lichess.org/broadcast/x/round-1/9PtLvze6/g1"]\n'
    '[Result "1-0"]\n\n1. e4 e5 1-0\n\n'
    '[Event "Test Open"]\n[White "Someone Else"]\n[Black "Another"]\n'
    '[Result "0-1"]\n\n1. d4 d5 0-1\n'
)


class TestGetRoundPgn:
    @patch("lookup.broadcasts.requests.get")
    def test_returns_pgn_text(self, mock_get):
        mock_get.return_value = _mock_response(text_data=ROUND_PGN)
        assert "Nakamura, Hikaru" in get_round_pgn("9PtLvze6")


class TestFindGames:
    @patch("lookup.broadcasts.get_round_pgn")
    @patch("lookup.broadcasts.find_broadcast_round_ids")
    def test_returns_all_games_in_matching_rounds(self, mock_find_ids, mock_get_pgn):
        mock_find_ids.return_value = ["9PtLvze6"]
        mock_get_pgn.return_value = ROUND_PGN
        games = find_games("Nakamura, Hikaru", "http://localhost:8080")
        assert len(games) == 2  # both games in the round, unfiltered by name

    @patch("lookup.broadcasts.get_round_pgn")
    @patch("lookup.broadcasts.find_broadcast_round_ids")
    def test_no_rounds_found_returns_empty(self, mock_find_ids, mock_get_pgn):
        mock_find_ids.return_value = []
        assert find_games("Nobody, N", "http://localhost:8080") == []
        mock_get_pgn.assert_not_called()

    @patch("lookup.broadcasts.get_round_pgn", side_effect=requests.HTTPError())
    @patch("lookup.broadcasts.find_broadcast_round_ids")
    def test_skips_round_on_fetch_failure(self, mock_find_ids, mock_get_pgn):
        mock_find_ids.return_value = ["deadbeef"]
        assert find_games("Nakamura, Hikaru", "http://localhost:8080") == []
