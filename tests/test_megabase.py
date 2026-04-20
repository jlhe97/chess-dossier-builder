"""
Unit tests for megabase indexer and query — all offline, using in-memory SQLite.
"""

import io
import sqlite3
import textwrap
import pytest

from megabase.indexer import build_index, create_schema, _pgn_text, _flush
from megabase.query import get_player_games


# --- Sample PGN fixtures --------------------------------------------------

PGN_TWO_GAMES = textwrap.dedent("""\
    [Event "World Championship"]
    [White "Kasparov, Garry"]
    [Black "Karpov, Anatoly"]
    [Date "1985.10.15"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 1-0

    [Event "Candidates Match"]
    [White "Karpov, Anatoly"]
    [Black "Kasparov, Garry"]
    [Date "1984.09.10"]
    [Result "0-1"]

    1. d4 Nf6 2. c4 g6 0-1
""")

PGN_NO_NAMES = textwrap.dedent("""\
    [Event "Unknown"]
    [White "?"]
    [Black "?"]
    [Date "2000.01.01"]
    [Result "*"]

    *
""")

PGN_PARTIAL_NAME = textwrap.dedent("""\
    [Event "Open"]
    [White "Smith, John"]
    [Black "Doe, Jane"]
    [Date "2023.05.01"]
    [Result "1/2-1/2"]

    1. e4 e5 1/2-1/2
""")


# --- Helpers --------------------------------------------------------------

def _make_db(pgn_text: str) -> str:
    """Write pgn_text to a temp file, index into a temp DB, return db path."""
    import tempfile, os

    with tempfile.NamedTemporaryFile("w", suffix=".pgn", delete=False) as f:
        f.write(pgn_text)
        pgn_path = f.name

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.unlink(db_path)  # let build_index create it fresh

    build_index(pgn_path, db_path=db_path, batch_size=10)
    os.unlink(pgn_path)
    return db_path


# --- Indexer tests --------------------------------------------------------

class TestIndexer:
    def test_indexes_two_games(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        total = build_index(str(pgn), db_path=db)
        assert total == 2

    def test_skips_games_with_no_player_names(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_NO_NAMES)
        db = str(tmp_path / "test.db")
        total = build_index(str(pgn), db_path=db)
        assert total == 0

    def test_schema_creates_indexes(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        create_schema(conn)
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        names = {row[0] for row in indexes}
        assert "idx_white" in names
        assert "idx_black" in names
        conn.close()

    def test_stored_fields(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT * FROM games ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        # columns: id, white, black, date, event, result, pgn
        assert row[1] == "Kasparov, Garry"   # white
        assert row[2] == "Karpov, Anatoly"   # black
        assert row[3] == "1985.10.15"        # date
        assert row[5] == "1-0"              # result
        assert "1. e4" in row[6]            # pgn text


# --- Query tests ----------------------------------------------------------

class TestQuery:
    def test_finds_games_as_white(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Kasparov, Garry", db_path=db)
        assert len(games) == 2

    def test_finds_games_as_black(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Karpov, Anatoly", db_path=db)
        assert len(games) == 2

    def test_partial_name_match(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_PARTIAL_NAME)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Smith", db_path=db)
        assert len(games) == 1
        assert games[0]["white"] == "Smith, John"

    def test_case_insensitive(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_PARTIAL_NAME)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        assert get_player_games("smith", db_path=db)
        assert get_player_games("SMITH", db_path=db)

    def test_no_match_returns_empty(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        assert get_player_games("Fischer, Bobby", db_path=db) == []

    def test_limit(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Kasparov, Garry", db_path=db, limit=1)
        assert len(games) == 1

    def test_results_ordered_by_date_desc(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_TWO_GAMES)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Kasparov, Garry", db_path=db)
        assert games[0]["date"] == "1985.10.15"
        assert games[1]["date"] == "1984.09.10"

    def test_returned_dict_has_pgn(self, tmp_path):
        pgn = tmp_path / "games.pgn"
        pgn.write_text(PGN_PARTIAL_NAME)
        db = str(tmp_path / "test.db")
        build_index(str(pgn), db_path=db)

        games = get_player_games("Smith", db_path=db)
        assert "pgn" in games[0]
        assert "1. e4" in games[0]["pgn"]
