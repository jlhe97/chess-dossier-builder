"""
Unit tests for pipeline.resolver, pipeline.runner, and the confidence
flag in dossier.report — all network and I/O calls mocked.
"""

import textwrap
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

from pipeline.resolver import resolve_lichess, resolve_chesscom, _similarity, _strip_title
from pipeline.runner import _slug, run_pipeline, _ensure_game_links
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
# _strip_title
# ---------------------------------------------------------------------------

class TestStripTitle:
    def test_strips_cm(self):
        assert _strip_title("CM Delacroix, Pierre") == "Delacroix, Pierre"

    def test_strips_gm_case_insensitive(self):
        assert _strip_title("gm Carlsen, Magnus") == "Carlsen, Magnus"

    def test_strips_wfm(self):
        assert _strip_title("WFM Doe, Jane") == "Doe, Jane"

    def test_no_title_unchanged(self):
        assert _strip_title("Smith, John") == "Smith, John"

    def test_does_not_strip_surname_that_looks_like_title(self):
        # "Im" is a real surname (e.g. Korean); no space after it before
        # the comma, so it must not be mistaken for the IM title.
        assert _strip_title("Im, Wonseok") == "Im, Wonseok"

    def test_strips_title_even_with_title_like_surname(self):
        assert _strip_title("IM Im, Wonseok") == "Im, Wonseok"


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
        mock_resolve.return_value = ("jsmith", "high", 0.8, ["reason"])
        u, c, score, reasons = mock_resolve("Smith, John")
        assert u == "jsmith"
        assert c == "high"

    @patch("lookup.lichess.search")
    def test_returns_none_when_no_candidates(self, mock_search):
        mock_search.return_value = []
        u, c, score, reasons = resolve_lichess("Zzz, Qqq")
        assert u is None
        assert c is None

    @patch("lookup.lichess.get_profile", side_effect=Exception("no network in tests"))
    @patch("lookup.lichess.search")
    def test_low_confidence_on_weak_match(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "xyz99", "display_name": "xyz99", "title": None,
             "ratings": {}, "url": ""}
        ]
        u, c, score, reasons = resolve_lichess("Smith, John")
        # "xyz99" vs "Smith, John" → low similarity
        assert c in ("low", None)

    @patch("lookup.lichess.search", side_effect=Exception("network error"))
    def test_returns_none_on_exception(self, mock_search):
        u, c, score, reasons = resolve_lichess("Smith, John")
        assert u is None

    @patch("lookup.lichess.get_profile", side_effect=Exception("no network in tests"))
    @patch("lookup.lichess.search")
    def test_picks_best_scoring_candidate_not_just_first(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "randomuser", "display_name": "randomuser", "title": None,
             "ratings": {}, "url": ""},
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        u, c, score, reasons = resolve_lichess("Smith, John")
        assert u == "jsmith"

    @patch("lookup.lichess.get_profile", side_effect=Exception("no network in tests"))
    @patch("lookup.lichess.search")
    def test_rating_mismatch_lowers_confidence(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {"blitz": 900}, "url": ""},
        ]
        u_close, c_close, score_close, _ = resolve_lichess("Smith, John", rating=950)
        u_far, c_far, score_far, _ = resolve_lichess("Smith, John", rating=2600)
        assert score_far < score_close

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_full_profile_real_name_used_when_available(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "anon123", "display_name": "anon123", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "anon123", "display_name": "anon123", "title": None,
            "ratings": {"blitz": 1850}, "url": "", "real_name": "Hikaru Nakamura",
            "country": "US", "fide_rating": None,
        }
        u, c, score, reasons = resolve_lichess("Nakamura, Hikaru", rating=1900)
        assert u == "anon123"
        assert c == "high"

    @patch("lookup.lichess.search", return_value=[])
    def test_no_search_key_skips_search_entirely(self, mock_search):
        u, c, score, reasons = resolve_lichess("Delacroix, Pierre", rating=1984)
        assert u is None

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.find_usernames_via_search")
    @patch("lookup.lichess.search", return_value=[])
    def test_search_finds_account_with_unrelated_username(
            self, mock_autocomplete, mock_search, mock_profile):
        # Real-world case: Magnus Carlsen's actual Lichess username is the
        # pseudonymous "DrNykterstein" — unrelated to his name, only
        # findable via the real-name field on the full profile (which
        # /player/autocomplete never sees).
        mock_search.return_value = ["DrNykterstein"]
        mock_profile.return_value = {
            "username": "DrNykterstein", "display_name": "DrNykterstein",
            "ratings": {"rapid": 2800}, "real_name": "Magnus Carlsen",
            "country": "NO", "fide_rating": None,
        }
        u, c, score, reasons = resolve_lichess(
            "Carlsen, Magnus", rating=2830, searxng_url="http://localhost:8080")
        assert u == "DrNykterstein"
        assert c == "high"

    @patch("lookup.lichess.find_usernames_via_search")
    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_search_skipped_when_autocomplete_already_high_confidence(
            self, mock_search, mock_profile, mock_find):
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {}, "country": None, "fide_rating": None,
        }
        resolve_lichess("Smith, John", searxng_url="http://localhost:8080")
        mock_find.assert_not_called()

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_more_games_raises_score(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        base_profile = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {}, "country": None, "fide_rating": None,
        }
        mock_profile.return_value = {**base_profile, "games_count": 2}
        _, _, score_few, _ = resolve_lichess("Smith, John")
        mock_profile.return_value = {**base_profile, "games_count": 200}
        _, _, score_many, _ = resolve_lichess("Smith, John")
        assert score_many > score_few

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_very_few_games_caps_confidence_at_low(self, mock_search, mock_profile):
        # Name, rating, and country all line up perfectly — but only 2
        # games on record is too thin a sample to call it a confident
        # identity match, so it must be capped at "low" rather than "high".
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1800},
            "country": "US", "fide_rating": None, "games_count": 2,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert u == "jsmith"
        assert score >= 0.55  # would be "high" on score alone
        assert c == "low"
        assert any("capped" in r for r in reasons)

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_unknown_games_count_does_not_cap(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1800},
            "country": "US", "fide_rating": None,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert c == "high"

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_catastrophic_rating_mismatch_caps_confidence_at_low(self, mock_search, mock_profile):
        # Real-world case this guards against: a generic same-first-name
        # account (strong name match, plenty of games, even a "preferred"
        # country) whose rating is wildly off — a ~1700-point gap is far
        # more consistent with "different person" than "same player,
        # inflated online rating," so this must not be called "high"
        # confidence just because the other signals look good.
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 900},
            "country": "US", "fide_rating": None, "games_count": 200,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=2600)
        assert score >= 0.55  # would be "high" on score alone
        assert c == "low"
        assert any("capped" in r and "rating" in r for r in reasons)

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_moderate_rating_gap_does_not_trigger_cap(self, mock_search, mock_profile):
        # A gap inside the tolerance band is just a soft negative nudge to
        # the score, not a hard-cap trigger — only a fully clamped (0.0)
        # rating score should cap confidence.
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1900},
            "country": "US", "fide_rating": None, "games_count": 200,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert c == "high"

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_stale_account_caps_confidence_at_low(self, mock_search, mock_profile):
        # Name, rating, and country all line up, and there are plenty of
        # games — but the account hasn't been active in ~6 years, which is
        # weak evidence it's still *this* player's current account.
        stale_date = (date.today() - timedelta(days=6 * 365)).isoformat()
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1800},
            "country": "US", "fide_rating": None, "games_count": 500,
            "last_active": stale_date,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert score >= 0.55  # would be "high" on score alone
        assert c == "low"
        assert any("capped" in r and "activity" in r for r in reasons)

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_recent_activity_does_not_cap(self, mock_search, mock_profile):
        recent_date = (date.today() - timedelta(days=30)).isoformat()
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1800},
            "country": "US", "fide_rating": None, "games_count": 500,
            "last_active": recent_date,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert c == "high"
        assert any("last active" in r for r in reasons)

    @patch("lookup.lichess.get_profile")
    @patch("lookup.lichess.search")
    def test_unknown_last_active_does_not_cap(self, mock_search, mock_profile):
        mock_search.return_value = [
            {"username": "jsmith", "display_name": "jsmith", "title": None,
             "ratings": {}, "url": ""},
        ]
        mock_profile.return_value = {
            "username": "jsmith", "display_name": "jsmith",
            "real_name": "John Smith", "ratings": {"blitz": 1800},
            "country": "US", "fide_rating": None, "games_count": 500,
        }
        u, c, score, reasons = resolve_lichess("Smith, John", rating=1800)
        assert c == "high"


# ---------------------------------------------------------------------------
# resolve_chesscom
# ---------------------------------------------------------------------------

class TestResolveChesscom:
    @patch("lookup.chesscom.get_profile")
    def test_high_confidence_first_guess(self, mock_get):
        mock_get.return_value = {"username": "johnsmith", "ratings": {}}
        u, c, score, reasons = resolve_chesscom("Smith, John")
        assert u is not None
        assert c == "high"

    @patch("lookup.chesscom.get_profile")
    def test_low_confidence_later_guess(self, mock_get):
        # First 2 fail, 3rd succeeds, rest fail too (every guess is tried
        # now, not just until the first hit) → low confidence
        err = requests.HTTPError()
        mock_get.side_effect = [err, err, {"username": "jsmith_chess", "ratings": {}}, err, err, err, err, err, err]
        u, c, score, reasons = resolve_chesscom("Smith, John")
        assert u is not None
        assert c == "low"

    @patch("lookup.chesscom.get_profile", side_effect=requests.HTTPError())
    def test_returns_none_when_all_fail(self, mock_get):
        u, c, score, reasons = resolve_chesscom("Zzz, Qqq")
        assert u is None

    @patch("lookup.chesscom.get_profile")
    def test_rating_and_country_upgrade_later_guess_to_high(self, mock_get):
        err = requests.HTTPError()
        mock_get.side_effect = [
            err, err,
            {"username": "jsmith_chess", "ratings": {"rapid": 1800}, "country": "US"},
            err, err, err, err, err, err,
        ]
        u, c, score, reasons = resolve_chesscom("Smith, John", rating=1820)
        assert c == "high"

    @patch("lookup.chesscom.get_profile")
    def test_rating_mismatch_rejects_early_guess(self, mock_get):
        mock_get.return_value = {"username": "johnsmith", "ratings": {"rapid": 900}}
        u, c, score, reasons = resolve_chesscom("Smith, John", rating=2600)
        assert c != "high"

    @patch("lookup.chesscom.get_profile", side_effect=requests.HTTPError())
    def test_no_search_key_skips_search_entirely(self, mock_get):
        # Without a key, must not attempt to import/call the search path at all.
        u, c, score, reasons = resolve_chesscom("Delacroix, Pierre", rating=1984)
        assert u is None

    @patch("lookup.chesscom.find_usernames_via_search")
    @patch("lookup.chesscom.get_profile")
    def test_search_finds_personalized_handle_no_guess_could(self, mock_get, mock_search):
        # Real-world case: "beaumontchess_fr" for "Delacroix, Pierre" — no
        # mechanical first/last concatenation would ever produce this.
        def get_profile_side_effect(username):
            if username == "beaumontchess_fr":
                return {"username": "beaumontchess_fr", "display_name": "Pierre Andre Delacroix",
                       "ratings": {"rapid": 2142, "blitz": 2382}, "country": "PY"}
            raise requests.HTTPError()

        mock_get.side_effect = get_profile_side_effect
        mock_search.return_value = ["beaumontchess_fr"]

        u, c, score, reasons = resolve_chesscom(
            "Delacroix, Pierre", rating=1984, searxng_url="http://localhost:8080")
        assert u == "beaumontchess_fr"
        assert c == "high"

    @patch("lookup.chesscom.find_usernames_via_search")
    @patch("lookup.chesscom.get_profile")
    def test_search_skipped_when_guess_already_high_confidence(self, mock_get, mock_search):
        mock_get.return_value = {"username": "johnsmith", "ratings": {}}
        resolve_chesscom("Smith, John", searxng_url="http://localhost:8080")
        mock_search.assert_not_called()

    @patch("lookup.chesscom.find_usernames_via_search")
    @patch("lookup.chesscom.get_profile")
    def test_search_still_tried_and_preferred_over_generic_guess_collision(
            self, mock_get, mock_search):
        # A player with no rating on file: every guess 404s except the bare
        # "john" guess (index 7), which collides with an unrelated
        # stranger's real, active, US account — country/games/recency alone
        # drag that coincidence past the "high" threshold even though
        # nothing actually ties it to this player. Search finds the real
        # account (a personalized handle, with a genuine real-name match)
        # at a *lower* raw score — it must still win, since the guess score
        # is inflated by non-discriminating signals.
        def get_profile_side_effect(username):
            if username == "john":
                return {"username": "john", "ratings": {}, "country": "US",
                       "games_count": 2931, "last_active": "2026-07-09"}
            if username == "smithy_gb":
                return {"username": "smithy_gb", "real_name": "John Smith",
                       "ratings": {"rapid": 2142}, "country": "GB",
                       "games_count": 7466, "last_active": "2026-07-09"}
            raise requests.HTTPError()

        mock_get.side_effect = get_profile_side_effect
        mock_search.return_value = ["smithy_gb"]

        u, c, score, reasons = resolve_chesscom(
            "Smith, John", searxng_url="http://localhost:8080")
        mock_search.assert_called_once()
        assert u == "smithy_gb"
        assert c == "high"

    @patch("lookup.chesscom.find_usernames_via_search")
    @patch("lookup.chesscom.get_profile")
    def test_generic_guess_collision_capped_to_low_without_search(self, mock_get, mock_search):
        # Same coincidental "john" collision as above, but with no
        # searxng_url to fall back on — must not be presented as "high"
        # confidence just because it was the only candidate available.
        mock_get.side_effect = lambda username: (
            {"username": "john", "ratings": {}, "country": "US",
             "games_count": 2931, "last_active": "2026-07-09"}
            if username == "john" else (_ for _ in ()).throw(requests.HTTPError())
        )
        u, c, score, reasons = resolve_chesscom("Smith, John")
        mock_search.assert_not_called()
        assert score >= 0.55
        assert c == "low"
        assert any("generic username guess" in r for r in reasons)

    @patch("lookup.chesscom.get_profile")
    def test_real_name_on_file_upgrades_late_guess_to_high(self, mock_get):
        # A bare-surname guess ("kowalski") is generic and weak by guess
        # position alone (last of 9 candidates) — but chess.com exposes a
        # genuine real name on the account, which is direct evidence and
        # should outweigh the weak guess position, landing high confidence.
        err = requests.HTTPError()
        mock_get.side_effect = [
            err, err, err, err, err, err, err, err,
            {"username": "kowalski", "ratings": {"rapid": 1820},
             "real_name": "Marek Antoni Kowalski", "country": "PL", "games_count": 140},
        ]
        u, c, score, reasons = resolve_chesscom("Kowalski, Marek Antoni", rating=1780)
        assert u == "kowalski"
        assert c == "high"
        assert any("real name match" in r for r in reasons)

    @patch("lookup.chesscom.get_profile")
    def test_very_few_games_caps_confidence_at_low(self, mock_get):
        mock_get.return_value = {
            "username": "johnsmith", "ratings": {"rapid": 1800},
            "country": "US", "games_count": 1,
        }
        u, c, score, reasons = resolve_chesscom("Smith, John", rating=1800)
        assert score >= 0.55
        assert c == "low"

    @patch("lookup.chesscom.get_profile")
    def test_stale_account_caps_confidence_at_low(self, mock_get):
        # Plenty of games, but none in ~6 years — weak evidence this is
        # still the player's current, active account.
        stale_date = (date.today() - timedelta(days=6 * 365)).isoformat()
        mock_get.return_value = {
            "username": "johnsmith", "ratings": {"rapid": 1800},
            "country": "US", "games_count": 500, "last_active": stale_date,
        }
        u, c, score, reasons = resolve_chesscom("Smith, John", rating=1800)
        assert score >= 0.55
        assert c == "low"
        assert any("capped" in r and "activity" in r for r in reasons)

    @patch("lookup.chesscom.get_profile")
    def test_prefers_better_later_guess_over_bad_earlier_hit(self, mock_get):
        # Motivated by a real case (name changed, numbers reshuffled for
        # the test): a middle guess ("marek") resolves to a real but
        # unrelated account with a catastrophic rating mismatch (3105
        # games, but 640 vs an entry-list rating of 1780); a later guess
        # ("kowalski") resolves to the actual player, evidenced by a
        # genuine real-name match. Stopping at the first hit would have
        # locked in the wrong "marek" account and never seen the second.
        err = requests.HTTPError()
        mock_get.side_effect = [
            err, err, err, err, err, err, err,
            {"username": "marek", "ratings": {"rapid": 640}, "country": "US", "games_count": 3105},
            {"username": "kowalski", "ratings": {"rapid": 1820},
             "real_name": "Marek Antoni Kowalski", "country": "PL", "games_count": 140},
        ]
        u, c, score, reasons = resolve_chesscom("Kowalski, Marek Antoni", rating=1780)
        assert u == "kowalski"
        assert c == "high"

    @patch("lookup.chesscom.get_profile")
    def test_without_fide_country_us_bias_can_pick_wrong_candidate(self, mock_get):
        # Baseline (no FIDE data available): the blanket "US-preferred"
        # heuristic can outweigh a correct non-US candidate, motivating
        # the FIDE cross-check tested below. Both candidates score the
        # same on name similarity here (the surname-substring heuristic
        # clamps both to 0.65 regardless of first-name difference), so
        # country/games are what actually decide it.
        err = requests.HTTPError()
        mock_get.side_effect = [
            {"username": "elinberg", "real_name": "Elin Berg", "ratings": {}, "country": "SE"},
            err, err, err,
            {"username": "eberg", "real_name": "Erik Berg", "ratings": {},
             "country": "US", "games_count": 37},
            err, err, err, err,
        ]
        u, c, score, reasons = resolve_chesscom("Berg, Elin")
        assert u == "eberg"  # the wrong candidate wins without a nationality check

    @patch("lookup.chesscom.get_profile")
    def test_fide_country_cross_check_prefers_correct_nationality(self, mock_get):
        # Same two candidates as above, but now the entrant's real FIDE
        # nationality (SE) is known — the cross-check should flip the
        # outcome to the correct candidate instead of the US one.
        err = requests.HTTPError()
        mock_get.side_effect = [
            {"username": "elinberg", "real_name": "Elin Berg", "ratings": {}, "country": "SE"},
            err, err, err,
            {"username": "eberg", "real_name": "Erik Berg", "ratings": {},
             "country": "US", "games_count": 37},
            err, err, err, err,
        ]
        u, c, score, reasons = resolve_chesscom("Berg, Elin", fide_country="SE")
        assert u == "elinberg"
        assert any("matches FIDE nationality" in r for r in reasons)


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

    def test_game_with_url_renders_clickable_link(self):
        pgn = textwrap.dedent("""\
            [White "Smith, John"]
            [Black "Opp"]
            [Site "https://lichess.org/gameid123"]
            [Result "1-0"]

            1. e4 e5 1-0
        """)
        d = build_dossier("Smith, John", [pgn])
        html = render_html(d)
        assert "href='https://lichess.org/gameid123'" in html

    def test_game_without_url_has_no_link(self):
        d = self._dossier()
        html = render_html(d)
        assert "<ul class='games'><li>" in html


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


class TestEnsureGameLinks:
    def test_generates_games_browser_for_url_less_game(self, tmp_path):
        pgn = _pgn("Smith, John", "Opp", "1-0")
        out = _ensure_game_links([pgn], tmp_path, "smith_john")
        assert "GameURL \"games/smith_john/index.html#g1\"" in out[0]
        assert (tmp_path / "games" / "smith_john" / "index.html").exists()

    def test_browser_page_embeds_game_data(self, tmp_path):
        pgn = _pgn("Smith, John", "Opp", "1-0")
        _ensure_game_links([pgn], tmp_path, "smith_john")
        html = (tmp_path / "games" / "smith_john" / "index.html").read_text()
        assert '"white": "Smith, John"' in html or '"white":"Smith, John"' in html
        assert '"id": "g1"' in html or '"id":"g1"' in html
        assert "const GAMES" in html

    def test_browser_page_has_flip_and_engine_controls(self, tmp_path):
        pgn = _pgn("Smith, John", "Opp", "1-0")
        _ensure_game_links([pgn], tmp_path, "smith_john")
        html = (tmp_path / "games" / "smith_john" / "index.html").read_text()
        assert "Flip board" in html
        assert "id='engine-select'" in html
        assert "stockfish.js" in html  # default engine preset URL

    def test_preserves_existing_lichess_site_url(self):
        pgn = textwrap.dedent("""\
            [White "Smith, John"]
            [Black "Opp"]
            [Site "https://lichess.org/abcd1234"]
            [Result "1-0"]

            1. e4 e5 1-0
        """)
        out = _ensure_game_links([pgn], Path("/tmp/unused"), "smith_john")
        assert "https://lichess.org/abcd1234" in out[0]
        assert "GameURL" not in out[0]

    def test_preserves_existing_gameurl_header(self):
        # Broadcast round PGN exports carry their own real GameURL, with
        # a non-URL Site field ("Schaumburg, IL...") — must not overwrite it.
        pgn = textwrap.dedent("""\
            [White "Smith, John"]
            [Black "Opp"]
            [Site "Schaumburg, IL, United States"]
            [GameURL "https://lichess.org/broadcast/x/round-1/abc/def"]
            [Result "1-0"]

            1. e4 e5 1-0
        """)
        out = _ensure_game_links([pgn], Path("/tmp/unused"), "smith_john")
        assert "https://lichess.org/broadcast/x/round-1/abc/def" in out[0]

    def test_multiple_games_share_one_browser_page(self, tmp_path):
        pgns = [_pgn("Smith, John", "A", "1-0"), _pgn("Smith, John", "B", "0-1")]
        out = _ensure_game_links(pgns, tmp_path, "smith_john")
        assert "#g1" in out[0]
        assert "#g2" in out[1]
        assert (tmp_path / "games" / "smith_john" / "index.html").exists()
        # Only one file for both games, not one per game.
        assert len(list((tmp_path / "games" / "smith_john").iterdir())) == 1

    def test_no_local_games_writes_nothing(self, tmp_path):
        pgn = textwrap.dedent("""\
            [White "Smith, John"]
            [Black "Opp"]
            [Site "https://lichess.org/abcd1234"]
            [Result "1-0"]

            1. e4 e5 1-0
        """)
        _ensure_game_links([pgn], tmp_path, "smith_john")
        assert not (tmp_path / "games").exists()


class TestRunPipeline:
    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_output_dir(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        out = tmp_path / "out"
        run_pipeline("Challenge34", output_dir=str(out))
        assert out.exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_per_player_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        assert (tmp_path / "smith_john.md").exists()
        assert (tmp_path / "doe_jane.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_creates_combined_md(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        assert (tmp_path / "combined.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_combined_contains_all_players(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        combined = (tmp_path / "combined.md").read_text()
        assert "Smith, John" in combined
        assert "Doe, Jane" in combined

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=[])
    def test_empty_entry_list_returns_no_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        result = run_pipeline("Challenge34", output_dir=str(tmp_path))
        assert result == []

    @patch("pipeline.runner._fetch_lichess_recent_games", return_value=[])
    @patch("pipeline.runner._fetch_lichess_studies", return_value=[])
    @patch("pipeline.runner._fetch_chesscom_games",
           return_value=([], {"username": "jdoe", "display_name": "jdoe",
                              "title": None, "ratings": {},
                              "url": "https://www.chess.com/member/jdoe",
                              "country": "US"}))
    @patch("pipeline.runner._fetch_lichess_games",
           return_value=([SAMPLE_PGN], {"username": "jsmith", "display_name": "jsmith",
                                        "title": None, "ratings": {"rapid": 1800},
                                        "url": "https://lichess.org/@/jsmith"}))
    @patch("pipeline.runner.resolve_chesscom", return_value=("jdoe", "low", 0.4, ["reason"]))
    @patch("pipeline.runner.resolve_lichess",  return_value=("jsmith", "high", 0.8, ["reason"]))
    @patch("pipeline.runner.scrape_entry_list",
           return_value=[{"name": "Smith, John", "rating": "1800"}])
    def test_games_fed_into_dossier(self, mock_scrape, mock_lich, mock_cc,
                                    mock_fetch_lich, mock_fetch_cc, mock_fetch_studies,
                                    mock_fetch_recent, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        md = (tmp_path / "smith_john.md").read_text()
        assert "Smith, John" in md
        assert "## Overview" in md

    @patch("pipeline.runner._fetch_lichess_recent_games")
    @patch("pipeline.runner._fetch_lichess_studies", return_value=[])
    @patch("pipeline.runner._fetch_chesscom_games", return_value=([], None))
    @patch("pipeline.runner._fetch_lichess_games",
           return_value=([SAMPLE_PGN], {"username": "jsmith", "display_name": "jsmith",
                                        "title": None, "ratings": {"rapid": 1800},
                                        "url": "https://lichess.org/@/jsmith"}))
    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=("jsmith", "high", 0.8, ["reason"]))
    @patch("pipeline.runner.scrape_entry_list",
           return_value=[{"name": "Smith, John", "rating": "1800"}])
    def test_high_confidence_gets_recent_games_page(self, mock_scrape, mock_lich, mock_cc,
                                                     mock_fetch_lich, mock_fetch_cc,
                                                     mock_fetch_studies, mock_fetch_recent, tmp_path):
        recent_pgn = _pgn("Smith, John", "Opp", "1-0").replace(
            '[Result "1-0"]', f'[Date "{date.today().isoformat().replace("-", ".")}"]\n[Result "1-0"]')
        mock_fetch_recent.return_value = [recent_pgn]
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        html = (tmp_path / "smith_john.html").read_text()
        assert "Recent games (1)" in html
        assert (tmp_path / "games" / "smith_john" / "recent_lichess.html").exists()

    @patch("pipeline.runner._fetch_lichess_recent_games")
    @patch("pipeline.runner._fetch_lichess_studies", return_value=[])
    @patch("pipeline.runner._fetch_chesscom_games", return_value=([], None))
    @patch("pipeline.runner._fetch_lichess_games",
           return_value=([SAMPLE_PGN], {"username": "jsmith", "display_name": "jsmith",
                                        "title": None, "ratings": {"rapid": 1800},
                                        "url": "https://lichess.org/@/jsmith"}))
    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=("jsmith", "low", 0.4, ["reason"]))
    @patch("pipeline.runner.scrape_entry_list",
           return_value=[{"name": "Smith, John", "rating": "1800"}])
    def test_low_confidence_skips_recent_games_fetch(self, mock_scrape, mock_lich, mock_cc,
                                                      mock_fetch_lich, mock_fetch_cc,
                                                      mock_fetch_studies, mock_fetch_recent, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        mock_fetch_recent.assert_not_called()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_json_format_no_combined(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="json")
        assert (tmp_path / "smith_john.json").exists()
        assert not (tmp_path / "combined.md").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_html_format_creates_files(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        assert (tmp_path / "smith_john.html").exists()
        assert (tmp_path / "doe_jane.html").exists()
        assert (tmp_path / "combined.html").exists()

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_html_combined_has_nav(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="html")
        html = (tmp_path / "combined.html").read_text()
        assert "<nav>" in html
        assert "Smith, John" in html
        assert "Doe, Jane" in html

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_dossier_db_opt_in_saves_every_player(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        from dossier.db import player_history
        db_path = str(tmp_path / "history.db")
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown", dossier_db=db_path)
        assert player_history(db_path, "Smith, John")
        assert player_history(db_path, "Doe, Jane")

    @patch("pipeline.runner.resolve_chesscom", return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.resolve_lichess",  return_value=(None, None, 0.0, []))
    @patch("pipeline.runner.scrape_entry_list", return_value=MOCK_PLAYERS)
    def test_no_dossier_db_by_default(self, mock_scrape, mock_lich, mock_cc, tmp_path):
        run_pipeline("Challenge34", output_dir=str(tmp_path), fmt="markdown")
        assert not (tmp_path / "history.db").exists()
        assert not any(tmp_path.glob("*.db"))
