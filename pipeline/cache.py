"""
Local SQLite cache for repeat pipeline runs — cuts the cost of redoing
work that's expensive (chess.com's up-to-9-guess sweep, Lichess
autocomplete, a SearXNG search) or that never changes once found (a
Lichess broadcast game, immutable the moment it's played).

Two tables, independent of dossier.db's opt-in cross-scan history (that
one is about building a queryable record across scans; this one is purely
a performance cache with no user-facing query interface):

  resolutions      — the last Lichess/chess.com resolver result per
                      player, expiring after a TTL so a stale wrong match
                      (or a player who's since created/renamed an
                      account) doesn't stick forever.
  broadcast_games   — every broadcast-round PGN ever found for a player,
                      deduped by a hash of the PGN text. Never expires (a
                      played game doesn't change) and also raises
                      effective recall against a flaky SearXNG instance —
                      a game found on one run stays available even if a
                      later run's search misses it (a known, documented
                      SearXNG reliability limit, not something caching
                      alone fixes, but it softens the impact).

Usage:
  from pipeline.cache import get_resolution, save_resolution, \\
      get_broadcast_games, save_broadcast_games
"""

import re
import json
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

_RESOLUTION_CACHE_TTL_DAYS = 7.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resolutions (
    player_slug TEXT NOT NULL,
    platform TEXT NOT NULL,
    username TEXT,
    confidence TEXT,
    score REAL,
    reasons_json TEXT,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (player_slug, platform)
);

CREATE TABLE IF NOT EXISTS broadcast_games (
    player_slug TEXT NOT NULL,
    pgn_hash TEXT NOT NULL,
    pgn TEXT NOT NULL,
    source_url TEXT,
    found_at TEXT NOT NULL,
    PRIMARY KEY (player_slug, pgn_hash)
);
"""

_HEADER_URL_RE = re.compile(r'\[(GameURL|Site|Link) "([^"]*)"\]')


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def get_resolution(db_path: str, player: str, platform: str,
                   ttl_days: float = _RESOLUTION_CACHE_TTL_DAYS) -> dict | None:
    """
    Cached {username, confidence, score, reasons} for a player/platform,
    or None on a cache miss or an entry older than `ttl_days`. A cached
    `username` of None is a valid, cacheable result too ("no match
    found") — re-running the full guess/search sweep for an unresolved
    player is the single most expensive case to redo, not just the cheap
    happy path.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM resolutions WHERE player_slug = ? AND platform = ?",
            (_slug(player), platform),
        ).fetchone()
        if not row:
            return None
        cached_at = datetime.fromisoformat(row["cached_at"])
        if datetime.now(timezone.utc) - cached_at > timedelta(days=ttl_days):
            return None
        return {
            "username": row["username"],
            "confidence": row["confidence"],
            "score": row["score"],
            "reasons": json.loads(row["reasons_json"]) if row["reasons_json"] else [],
        }
    finally:
        conn.close()


def save_resolution(db_path: str, player: str, platform: str, username: str | None,
                    confidence: str | None, score: float, reasons: list[str]) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO resolutions "
            "(player_slug, platform, username, confidence, score, reasons_json, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_slug, platform) DO UPDATE SET "
            "  username=excluded.username, confidence=excluded.confidence, "
            "  score=excluded.score, reasons_json=excluded.reasons_json, "
            "  cached_at=excluded.cached_at",
            (_slug(player), platform, username, confidence, score,
             json.dumps(reasons, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _extract_source_url(pgn_text: str) -> str | None:
    """Best-effort GameURL/Site/Link header value, for reference only — not used as the dedup key."""
    for m in _HEADER_URL_RE.finditer(pgn_text):
        if m.group(2).startswith("http"):
            return m.group(2)
    return None


def get_broadcast_games(db_path: str, player: str) -> list[str]:
    """Every previously-found broadcast game PGN for a player, oldest first."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT pgn FROM broadcast_games WHERE player_slug = ? ORDER BY found_at",
            (_slug(player),),
        ).fetchall()
        return [r["pgn"] for r in rows]
    finally:
        conn.close()


def save_broadcast_games(db_path: str, player: str, pgn_strings: list[str]) -> None:
    """
    Upsert newly-found broadcast games, deduped by a hash of the PGN text
    (not the source URL — a game can be found via slightly different
    search hits, or the URL header might be missing/reformatted, but the
    game text itself is a stable identity once fetched).
    """
    if not pgn_strings:
        return
    conn = _connect(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (_slug(player), hashlib.sha256(pgn.encode()).hexdigest(), pgn, _extract_source_url(pgn), now)
            for pgn in pgn_strings
        ]
        conn.executemany(
            "INSERT INTO broadcast_games (player_slug, pgn_hash, pgn, source_url, found_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(player_slug, pgn_hash) DO NOTHING",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
