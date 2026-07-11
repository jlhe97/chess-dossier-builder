"""
Unit tests for pipeline.cache — a real (temp-file) SQLite database each
test, no mocking needed since there's no network/external I/O involved.
"""

from datetime import datetime, timedelta, timezone

from pipeline.cache import (
    get_resolution, save_resolution, get_broadcast_games, save_broadcast_games, _connect,
)


class TestResolutionCache:
    def test_miss_returns_none(self, tmp_path):
        db = str(tmp_path / "cache.db")
        assert get_resolution(db, "Smith, John", "lichess") is None

    def test_hit_returns_saved_values(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, ["name match 0.9"])
        cached = get_resolution(db, "Smith, John", "lichess")
        assert cached == {
            "username": "jsmith", "confidence": "high", "score": 0.8,
            "reasons": ["name match 0.9"],
        }

    def test_caches_no_match_found_too(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "chesscom", None, None, 0.0, [])
        cached = get_resolution(db, "Smith, John", "chesscom")
        assert cached == {"username": None, "confidence": None, "score": 0.0, "reasons": []}

    def test_platforms_are_independent(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, [])
        assert get_resolution(db, "Smith, John", "chesscom") is None

    def test_different_players_are_independent(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, [])
        assert get_resolution(db, "Doe, Jane", "lichess") is None

    def test_name_formatting_does_not_create_phantom_entries(self, tmp_path):
        # Matched by name slug, same as dossier.db — "Smith, John" and
        # "  smith,  john " should hit the same cache row.
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, [])
        assert get_resolution(db, "  smith,  john ", "lichess") is not None

    def test_expired_entry_is_a_miss(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, [])
        # Back-date the cached_at column directly, past the default TTL.
        conn = _connect(db)
        stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("UPDATE resolutions SET cached_at = ?", (stale,))
        conn.commit()
        conn.close()
        assert get_resolution(db, "Smith, John", "lichess") is None

    def test_custom_ttl_still_hits_within_window(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "high", 0.8, [])
        conn = _connect(db)
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        conn.execute("UPDATE resolutions SET cached_at = ?", (two_days_ago,))
        conn.commit()
        conn.close()
        assert get_resolution(db, "Smith, John", "lichess", ttl_days=1) is None
        assert get_resolution(db, "Smith, John", "lichess", ttl_days=7) is not None

    def test_resave_overwrites_previous_entry(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_resolution(db, "Smith, John", "lichess", "jsmith", "low", 0.4, ["a"])
        save_resolution(db, "Smith, John", "lichess", "jsmith2", "high", 0.9, ["b"])
        cached = get_resolution(db, "Smith, John", "lichess")
        assert cached["username"] == "jsmith2"
        assert cached["confidence"] == "high"


class TestBroadcastGamesCache:
    def _pgn(self, event="Round 1"):
        return f'[Event "{event}"]\n[White "Smith, John"]\n[Black "Doe, Jane"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'

    def test_empty_for_unknown_player(self, tmp_path):
        db = str(tmp_path / "cache.db")
        assert get_broadcast_games(db, "Smith, John") == []

    def test_round_trips_saved_games(self, tmp_path):
        db = str(tmp_path / "cache.db")
        pgn = self._pgn()
        save_broadcast_games(db, "Smith, John", [pgn])
        assert get_broadcast_games(db, "Smith, John") == [pgn]

    def test_dedupes_identical_pgn_across_calls(self, tmp_path):
        db = str(tmp_path / "cache.db")
        pgn = self._pgn()
        save_broadcast_games(db, "Smith, John", [pgn])
        save_broadcast_games(db, "Smith, John", [pgn])
        assert get_broadcast_games(db, "Smith, John") == [pgn]

    def test_distinct_games_both_kept(self, tmp_path):
        db = str(tmp_path / "cache.db")
        pgn1, pgn2 = self._pgn("Round 1"), self._pgn("Round 2")
        save_broadcast_games(db, "Smith, John", [pgn1])
        save_broadcast_games(db, "Smith, John", [pgn2])
        assert set(get_broadcast_games(db, "Smith, John")) == {pgn1, pgn2}

    def test_empty_list_is_a_no_op(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_broadcast_games(db, "Smith, John", [])
        assert get_broadcast_games(db, "Smith, John") == []

    def test_different_players_are_independent(self, tmp_path):
        db = str(tmp_path / "cache.db")
        save_broadcast_games(db, "Smith, John", [self._pgn()])
        assert get_broadcast_games(db, "Doe, Jane") == []
