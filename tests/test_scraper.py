"""
Unit tests for scraper.py — all tests run offline using HTML fixtures.
"""

import pytest
from scraper import resolve_url, _detect_site, _normalize, parse_entry_list, _SITES


# --- Fixtures -------------------------------------------------------------

KINGREGISTRATION_HTML = """
<html><body>
<table>
  <tr>
    <th>Name</th><th>Rating</th><th>USCF ID</th><th>Section</th><th>State</th>
  </tr>
  <tr>
    <td>Smith, John</td><td>1850</td><td>12345678</td><td>Open</td><td>NY</td>
  </tr>
  <tr>
    <td>Doe, Jane</td><td>1420</td><td>87654321</td><td>U1600</td><td>CA</td>
  </tr>
</table>
</body></html>
"""

CHESSACTION_HTML = """
<html><body>
<table>
  <tr>
    <th>Player Name</th><th>Pre-Rating</th><th>USCF#</th><th>Division</th><th>Club</th>
  </tr>
  <tr>
    <td>Garcia, Maria</td><td>2100</td><td>11223344</td><td>Masters</td><td>City Chess</td>
  </tr>
  <tr>
    <td>Lee, Kevin</td><td>1780</td><td>44332211</td><td>U2000</td><td>East Side CC</td>
  </tr>
</table>
</body></html>
"""

EMPTY_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Name</th><th>Rating</th></tr>
</table>
</body></html>
"""

BLANK_ROWS_HTML = """
<html><body>
<table>
  <tr><th>Name</th><th>Rating</th></tr>
  <tr><td></td><td></td></tr>
  <tr><td>Jones, Bob</td><td>1600</td></tr>
</table>
</body></html>
"""

NO_TABLE_HTML = "<html><body><p>No results yet.</p></body></html>"

MULTI_TABLE_HTML = """
<html><body>
<table><tr><th>Nav</th></tr><tr><td>Home</td></tr></table>
<table>
  <tr><th>Name</th><th>Rating</th></tr>
  <tr><td>Player, One</td><td>1500</td></tr>
</table>
</body></html>
"""


# --- resolve_url ----------------------------------------------------------

class TestResolveUrl:
    def test_id_kingregistration(self):
        url = resolve_url("Challenge34", site="kingregistration")
        assert url == "https://www.kingregistration.com/entrylist/Challenge34"

    def test_id_chessaction(self):
        url = resolve_url("nKGioA==", site="chessaction")
        assert url == "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="

    def test_full_url_passthrough(self):
        full = "https://www.kingregistration.com/entrylist/Challenge34"
        assert resolve_url(full) == full

    def test_full_url_chessaction_passthrough(self):
        full = "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="
        assert resolve_url(full) == full

    def test_strips_trailing_slash(self):
        url = resolve_url("Challenge34/", site="kingregistration")
        assert url.endswith("/Challenge34")

    def test_extracts_id_from_path_segment(self):
        url = resolve_url("some/path/Challenge34", site="kingregistration")
        assert url.endswith("/Challenge34")


# --- _detect_site ---------------------------------------------------------

class TestDetectSite:
    def test_detects_kingregistration(self):
        assert _detect_site("https://www.kingregistration.com/entrylist/X") == "kingregistration"

    def test_detects_chessaction(self):
        assert _detect_site("https://chessaction.com/tournaments/advance_entry_list.php?tid=X") == "chessaction"

    def test_unknown_returns_none(self):
        assert _detect_site("https://chess.com/some/page") is None


# --- _normalize -----------------------------------------------------------

class TestNormalize:
    def test_canonical_name(self):
        assert _normalize({"Name": "Smith"}) == {"name": "Smith"}

    def test_player_alias(self):
        assert _normalize({"Player": "Smith"}) == {"name": "Smith"}

    def test_player_name_alias(self):
        assert _normalize({"Player Name": "Smith"}) == {"name": "Smith"}

    def test_rating_aliases(self):
        for header in ("Rating", "Rtng", "USCF Rating", "Pre-Rating", "Pre Rating"):
            assert _normalize({header: "1800"}) == {"rating": "1800"}, header

    def test_uscf_id_aliases(self):
        for header in ("USCF", "USCF ID", "USCF#", "ID"):
            assert _normalize({header: "12345678"}) == {"uscf_id": "12345678"}, header

    def test_section_aliases(self):
        assert _normalize({"Division": "Open"}) == {"section": "Open"}

    def test_club_alias(self):
        assert _normalize({"Team": "Metro CC"}) == {"club": "Metro CC"}

    def test_unknown_header_lowercased(self):
        assert _normalize({"SomeUnknown": "val"}) == {"someunknown": "val"}

    def test_strips_whitespace_from_keys(self):
        assert _normalize({" Name ": "Smith"}) == {"name": "Smith"}


# --- parse_entry_list -----------------------------------------------------

class TestParseEntryList:
    def test_kingregistration_columns(self):
        players = parse_entry_list(KINGREGISTRATION_HTML)
        assert len(players) == 2
        assert players[0] == {
            "name": "Smith, John",
            "rating": "1850",
            "uscf_id": "12345678",
            "section": "Open",
            "state": "NY",
        }

    def test_chessaction_columns(self):
        players = parse_entry_list(CHESSACTION_HTML)
        assert len(players) == 2
        assert players[0] == {
            "name": "Garcia, Maria",
            "rating": "2100",
            "uscf_id": "11223344",
            "section": "Masters",
            "club": "City Chess",
        }

    def test_empty_table_returns_empty_list(self):
        assert parse_entry_list(EMPTY_TABLE_HTML) == []

    def test_blank_rows_are_skipped(self):
        players = parse_entry_list(BLANK_ROWS_HTML)
        assert len(players) == 1
        assert players[0]["name"] == "Jones, Bob"

    def test_no_table_returns_empty_list(self):
        assert parse_entry_list(NO_TABLE_HTML) == []

    def test_skips_nav_table_picks_player_table(self):
        players = parse_entry_list(MULTI_TABLE_HTML)
        assert len(players) == 1
        assert players[0]["name"] == "Player, One"

    def test_second_player_chessaction(self):
        players = parse_entry_list(CHESSACTION_HTML)
        assert players[1]["name"] == "Lee, Kevin"
        assert players[1]["rating"] == "1780"
