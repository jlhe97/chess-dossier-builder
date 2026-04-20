"""
Build a SQLite index from a ChessBase MegaDatabase PGN export.

Run once after exporting from ChessBase (File → Export → Export Database as PGN).

Usage:
  python -m megabase.indexer <pgn_file> [--db megabase.db] [--batch 1000]

Example:
  python -m megabase.indexer mega.pgn
  python -m megabase.indexer mega.pgn --db /data/megabase.db
"""

import io
import sys
import argparse
import sqlite3

import chess.pgn


DEFAULT_DB = "megabase.db"
DEFAULT_BATCH = 1_000
PROGRESS_EVERY = 10_000


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            id      INTEGER PRIMARY KEY,
            white   TEXT NOT NULL,
            black   TEXT NOT NULL,
            date    TEXT,
            event   TEXT,
            result  TEXT,
            pgn     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_white ON games (white COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_black ON games (black COLLATE NOCASE);
    """)


def _pgn_text(game: chess.pgn.Game) -> str:
    buf = io.StringIO()
    exporter = chess.pgn.FileExporter(buf)
    game.accept(exporter)
    return buf.getvalue().strip()


def build_index(pgn_path: str, db_path: str = DEFAULT_DB,
                batch_size: int = DEFAULT_BATCH) -> int:
    """Stream pgn_path into db_path. Returns total games indexed."""
    conn = sqlite3.connect(db_path)
    create_schema(conn)

    total = 0
    batch: list[tuple] = []

    print(f"Indexing {pgn_path} → {db_path}", file=sys.stderr)

    with open(pgn_path, encoding="utf-8", errors="replace") as fh:
        while True:
            try:
                game = chess.pgn.read_game(fh)
            except Exception:
                continue
            if game is None:
                break

            headers = game.headers
            white = headers.get("White", "?")
            black = headers.get("Black", "?")
            if white == "?" and black == "?":
                continue

            batch.append((
                white,
                black,
                headers.get("Date"),
                headers.get("Event"),
                headers.get("Result"),
                _pgn_text(game),
            ))

            if len(batch) >= batch_size:
                _flush(conn, batch)
                total += len(batch)
                batch = []
                if total % PROGRESS_EVERY == 0:
                    print(f"  {total:,} games indexed…", file=sys.stderr)

    if batch:
        _flush(conn, batch)
        total += len(batch)

    conn.close()
    print(f"Done. {total:,} games indexed into {db_path}.", file=sys.stderr)
    return total


def _flush(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO games (white, black, date, event, result, pgn) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        batch,
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a MegaDatabase PGN export into SQLite.")
    parser.add_argument("pgn", help="Path to exported PGN file")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help=f"Insert batch size (default: {DEFAULT_BATCH})")
    args = parser.parse_args()

    build_index(args.pgn, db_path=args.db, batch_size=args.batch)


if __name__ == "__main__":
    main()
