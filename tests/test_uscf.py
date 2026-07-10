"""
Unit tests for lookup.uscf — all HTTP calls mocked.
"""

from unittest.mock import patch, MagicMock

from lookup.uscf import get_fide_country


def _mock_response(text, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.text = text
    mock.raise_for_status.return_value = None
    return mock


# A stand-in for the real page structure (verified against a live USCF
# member page — see CLAUDE.md), with fictional content.
_MEMBER_PAGE_WITH_FIDE = (
    "<tr><td>FIDE Rating</td></tr>"
    "<tr><td>FIDE Country</td><td><b>TUR</b></td></tr>"
    "<tr><td>Last Change Dt.</td><td><b>2026-06-27</b></td></tr>"
)

_MEMBER_PAGE_NO_FIDE = "<html><body>US Chess MSA - Member Details</body></html>"


class TestGetFideCountry:
    @patch("lookup.uscf.requests.get")
    def test_extracts_and_converts_to_iso2(self, mock_get):
        mock_get.return_value = _mock_response(_MEMBER_PAGE_WITH_FIDE)
        assert get_fide_country("12345678") == "TR"

    @patch("lookup.uscf.requests.get")
    def test_no_fide_country_on_file_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(_MEMBER_PAGE_NO_FIDE)
        assert get_fide_country("12345678") is None

    @patch("lookup.uscf.requests.get")
    def test_paraguay_code(self, mock_get):
        mock_get.return_value = _mock_response(
            "<tr><td>FIDE Country</td><td><b>PAR</b></td></tr>")
        assert get_fide_country("12345678") == "PY"

    @patch("lookup.uscf.requests.get")
    def test_unmapped_federation_code_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(
            "<tr><td>FIDE Country</td><td><b>ZZZ</b></td></tr>")
        assert get_fide_country("12345678") is None

    @patch("lookup.uscf.requests.get")
    def test_queries_by_bare_uscf_id(self, mock_get):
        mock_get.return_value = _mock_response(_MEMBER_PAGE_WITH_FIDE)
        get_fide_country("30288191")
        assert "30288191" in mock_get.call_args.kwargs["params"]
