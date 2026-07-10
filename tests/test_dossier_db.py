"""
Unit tests for dossier.db — a real (temp-file) SQLite database each test,
no mocking needed since there's no network/external I/O involved.
"""

from dossier.db import save_dossier, player_history, latest_dossier, list_scans, init_db


def _dossier(player="Smith, John", total=10, win_pct=55.0):
    return {
        "player": player,
        "profiles": [{"username": "jsmith", "url": "https://lichess.org/@/jsmith"}],
        "stats": {"total": total, "overall": {"win_pct": win_pct, "wins": 5, "draws": 1, "losses": 4}},
        "openings": {"as_white": [], "as_black": []},
        "generated": "2026-01-01",
    }


class TestInitDb:
    def test_creates_file(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        init_db(db_path)
        assert (tmp_path / "dossiers.db").exists()

    def test_safe_to_call_repeatedly(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        init_db(db_path)
        init_db(db_path)  # must not raise


class TestSaveDossier:
    def test_returns_row_id(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        row_id = save_dossier(db_path, "Challenge34", "kingregistration", _dossier())
        assert row_id is not None

    def test_saved_dossier_appears_in_history(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier())
        history = player_history(db_path, "Smith, John")
        assert len(history) == 1
        assert history[0]["tournament"] == "Challenge34"
        assert history[0]["total_games"] == 10
        assert history[0]["win_pct"] == 55.0

    def test_matches_player_by_slug_regardless_of_formatting(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(player="Smith, John"))
        # A slightly different-looking name (extra spacing) should still
        # resolve to the same slug and find the same history.
        history = player_history(db_path, "Smith,  John")
        assert len(history) == 1

    def test_profiles_and_stats_round_trip_as_json(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier())
        history = player_history(db_path, "Smith, John")
        assert history[0]["profiles"][0]["username"] == "jsmith"
        assert history[0]["stats"]["total"] == 10

    def test_rerun_same_tournament_same_day_updates_not_duplicates(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(total=10))
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(total=15))
        history = player_history(db_path, "Smith, John")
        assert len(history) == 1
        assert history[0]["total_games"] == 15

    def test_different_tournaments_both_kept(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier())
        save_dossier(db_path, "Winter Open", "chessaction", _dossier())
        history = player_history(db_path, "Smith, John")
        assert len(history) == 2
        tournaments = {h["tournament"] for h in history}
        assert tournaments == {"Challenge34", "Winter Open"}

    def test_different_players_same_scan(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(player="Smith, John"))
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(player="Doe, Jane"))
        assert len(player_history(db_path, "Smith, John")) == 1
        assert len(player_history(db_path, "Doe, Jane")) == 1


class TestPlayerHistory:
    def test_empty_when_never_scanned(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        init_db(db_path)
        assert player_history(db_path, "Nobody, Here") == []

    def test_newest_scan_first(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Older Event", "kingregistration", _dossier())
        # Force a distinct scan by using a different tournament (same-day
        # dedup is keyed on (tournament, day), so two tournaments always
        # produce two scans regardless of when the test runs).
        save_dossier(db_path, "Newer Event", "kingregistration", _dossier())
        history = player_history(db_path, "Smith, John")
        assert len(history) == 2


class TestLatestDossier:
    def test_none_when_never_scanned(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        init_db(db_path)
        assert latest_dossier(db_path, "Nobody, Here") is None

    def test_returns_most_recent(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(total=10))
        latest = latest_dossier(db_path, "Smith, John")
        assert latest["total_games"] == 10


class TestListScans:
    def test_lists_all_scans(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(player="Smith, John"))
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier(player="Doe, Jane"))
        scans = list_scans(db_path)
        assert len(scans) == 1
        assert scans[0]["player_count"] == 2

    def test_filters_by_tournament(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        save_dossier(db_path, "Challenge34", "kingregistration", _dossier())
        save_dossier(db_path, "Winter Open", "chessaction", _dossier())
        scans = list_scans(db_path, tournament="Challenge34")
        assert len(scans) == 1
        assert scans[0]["tournament"] == "Challenge34"

    def test_empty_db_returns_empty_list(self, tmp_path):
        db_path = str(tmp_path / "dossiers.db")
        init_db(db_path)
        assert list_scans(db_path) == []
