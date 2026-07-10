"""
Persistent SQLite store of dossiers across scans — separate from
megabase/ (a read-only index built once from a PGN export): this database
is written to by every pipeline run, so the same player scanned in
multiple tournaments over time accumulates a queryable history instead of
each run just overwriting the last one's report files.

Usage:
  python -m dossier.db --db dossiers.db history "Smith, John"
  python -m dossier.db --db dossiers.db scans
  python -m dossier.db --db dossiers.db scans --tournament Challenge34
"""

import json
import sqlite3
import argparse
import re
from datetime import date


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament TEXT NOT NULL,
    site TEXT,
    scanned_at TEXT NOT NULL,
    UNIQUE(tournament, scanned_at)
);

CREATE TABLE IF NOT EXISTS dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    player TEXT NOT NULL,
    player_slug TEXT NOT NULL,
    total_games INTEGER,
    win_pct REAL,
    profiles_json TEXT,
    stats_json TEXT,
    openings_json TEXT,
    generated TEXT,
    UNIQUE(scan_id, player_slug)
);

CREATE INDEX IF NOT EXISTS idx_dossiers_player_slug ON dossiers(player_slug);
"""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def init_db(db_path: str) -> None:
    """Create the schema if it doesn't exist yet. Safe to call repeatedly."""
    _connect(db_path).close()


def save_dossier(db_path: str, tournament: str, site: str | None, dossier: dict) -> int:
    """
    Store one dossier snapshot, grouped under a scan for (tournament, today).
    Re-running the pipeline for the same tournament on the same day updates
    that day's row per player rather than accumulating duplicates; scanning
    again on a later day starts a new scan and a new row, building history.

    Returns the dossier row id.
    """
    conn = _connect(db_path)
    try:
        today = date.today().isoformat()
        conn.execute(
            "INSERT INTO scans (tournament, site, scanned_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tournament, scanned_at) DO NOTHING",
            (tournament, site, today),
        )
        scan_id = conn.execute(
            "SELECT id FROM scans WHERE tournament = ? AND scanned_at = ?",
            (tournament, today),
        ).fetchone()["id"]

        player = dossier["player"]
        stats = dossier.get("stats", {})
        cur = conn.execute(
            "INSERT INTO dossiers "
            "(scan_id, player, player_slug, total_games, win_pct, profiles_json, "
            " stats_json, openings_json, generated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scan_id, player_slug) DO UPDATE SET "
            "  total_games=excluded.total_games, win_pct=excluded.win_pct, "
            "  profiles_json=excluded.profiles_json, stats_json=excluded.stats_json, "
            "  openings_json=excluded.openings_json, generated=excluded.generated",
            (
                scan_id, player, _slug(player),
                stats.get("total"), stats.get("overall", {}).get("win_pct"),
                json.dumps(dossier.get("profiles", []), ensure_ascii=False),
                json.dumps(stats, ensure_ascii=False),
                json.dumps(dossier.get("openings", {}), ensure_ascii=False),
                dossier.get("generated"),
            ),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        return conn.execute(
            "SELECT id FROM dossiers WHERE scan_id = ? AND player_slug = ?",
            (scan_id, _slug(player)),
        ).fetchone()["id"]
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("profiles_json", "stats_json", "openings_json"):
        short_key = key.removesuffix("_json")
        d[short_key] = json.loads(d.pop(key)) if d.get(key) else None
    return d


def player_history(db_path: str, player: str) -> list[dict]:
    """All past dossier snapshots for a player (matched by name slug), newest scan first."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT d.*, s.tournament, s.site, s.scanned_at "
            "FROM dossiers d JOIN scans s ON d.scan_id = s.id "
            "WHERE d.player_slug = ? ORDER BY s.scanned_at DESC",
            (_slug(player),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def latest_dossier(db_path: str, player: str) -> dict | None:
    """Most recent dossier snapshot for a player, or None if never scanned."""
    history = player_history(db_path, player)
    return history[0] if history else None


def list_scans(db_path: str, tournament: str | None = None) -> list[dict]:
    """All scan runs, newest first, optionally filtered to one tournament."""
    conn = _connect(db_path)
    try:
        if tournament:
            rows = conn.execute(
                "SELECT s.*, COUNT(d.id) AS player_count FROM scans s "
                "LEFT JOIN dossiers d ON d.scan_id = s.id "
                "WHERE s.tournament = ? GROUP BY s.id ORDER BY s.scanned_at DESC",
                (tournament,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT s.*, COUNT(d.id) AS player_count FROM scans s "
                "LEFT JOIN dossiers d ON d.scan_id = s.id "
                "GROUP BY s.id ORDER BY s.scanned_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the dossier history database.")
    parser.add_argument("--db", default="dossiers.db", help="SQLite database path (default: dossiers.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_history = sub.add_parser("history", help="Show every scan of a player over time")
    p_history.add_argument("player", help="Player name, e.g. 'Smith, John'")

    p_scans = sub.add_parser("scans", help="List past scan runs")
    p_scans.add_argument("--tournament", help="Filter to one tournament")

    args = parser.parse_args()

    if args.cmd == "history":
        rows = player_history(args.db, args.player)
        if not rows:
            print(f"No scan history for '{args.player}'.")
            return
        for r in rows:
            win_pct = r.get("win_pct")
            win_str = f"{win_pct}%" if win_pct is not None else "?"
            print(f"{r['scanned_at']}  {r['tournament']:<20}  "
                 f"{r['total_games']} games, {win_str} win rate")

    elif args.cmd == "scans":
        rows = list_scans(args.db, tournament=args.tournament)
        if not rows:
            print("No scans recorded.")
            return
        for r in rows:
            print(f"{r['scanned_at']}  {r['tournament']:<20}  {r['player_count']} player(s)")


if __name__ == "__main__":
    main()
