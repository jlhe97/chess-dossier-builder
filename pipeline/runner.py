"""
End-to-end pipeline: tournament URL → per-opponent dossiers.

Usage:
  python -m pipeline.runner Challenge34
  python -m pipeline.runner Challenge34 --site kingregistration --output-dir ./dossiers
  python -m pipeline.runner "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="
  python -m pipeline.runner Challenge34 --max-games 30 --format html

Output (default html):
  <output-dir>/
    smith_john.html     ← one file per opponent
    combined.html       ← all dossiers with nav (printable)
"""

import io
import os
import re
import sys
import json
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import chess.pgn

from scraper import scrape_entry_list
from pgnutil import split_pgn_games
from dossier.report import build_dossier, render_markdown, render_html, render_html_combined, render_json
from pipeline.resolver import resolve_lichess, resolve_chesscom, _strip_title


def _slug(name: str) -> str:
    """'Smith, John' → 'smith_john'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _parse_rating(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fetch_lichess_games(username: str, max_games: int) -> tuple[list[str], dict | None]:
    try:
        from lookup.lichess import get_profile, games_as_pgn
        import time
        profile = get_profile(username)
        time.sleep(1.0)
        pgn_text = games_as_pgn(username, max=max_games)
        return split_pgn_games(pgn_text), profile
    except Exception as exc:
        print(f"  Lichess fetch failed ({username}): {exc}", file=sys.stderr)
        return [], None


_RECENT_GAMES_DAYS = 365


def _fetch_lichess_recent_games(username: str, days: int = _RECENT_GAMES_DAYS,
                                max_games: int = 300) -> list[str]:
    """
    Fetch up to a year of games via the `since` filter — independent of
    whatever `max_games` the main analysis pool uses (which stays small
    for speed), so the "recent games" viewer isn't truncated to whatever a
    handful of the very latest games happen to be for an active player.
    """
    try:
        from lookup.lichess import games_as_pgn
        import time
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        time.sleep(1.0)
        pgn_text = games_as_pgn(username, max=max_games, since=since_ms)
        return split_pgn_games(pgn_text)
    except Exception as exc:
        print(f"  Lichess recent-games fetch failed ({username}): {exc}", file=sys.stderr)
        return []


def _fetch_lichess_studies(username: str, max_studies: int = 10) -> list[str]:
    try:
        from lookup.lichess import get_studies, get_study_pgn
        pgns = []
        for s in get_studies(username, max=max_studies):
            study_id = s.get("id")
            if not study_id:
                continue
            pgns += split_pgn_games(get_study_pgn(study_id))
        return pgns
    except Exception as exc:
        print(f"  Lichess studies fetch failed ({username}): {exc}", file=sys.stderr)
        return []


def _fetch_chesscom_games(username: str, months: int) -> tuple[list[str], dict | None]:
    try:
        from lookup.chesscom import get_profile, games_as_pgn
        profile = get_profile(username)
        pgn_text = games_as_pgn(username, months=months)
        return split_pgn_games(pgn_text), profile
    except Exception as exc:
        print(f"  chess.com fetch failed ({username}): {exc}", file=sys.stderr)
        return [], None


def _fetch_megabase_games(name: str, db_path: str, limit: int | None = None,
                          rating: int | None = None) -> list[str]:
    try:
        from megabase.query import get_player_games
        games = get_player_games(name, db_path=db_path, limit=limit, rating=rating)
        return [g["pgn"] for g in games]
    except Exception as exc:
        print(f"  Megabase lookup failed ({name}): {exc}", file=sys.stderr)
        return []


def _fetch_broadcast_games(name: str, searxng_url: str, max_results: int = 5) -> list[str]:
    try:
        from lookup.broadcasts import find_games
        return find_games(name, searxng_url, max_results=max_results)
    except Exception as exc:
        print(f"  Broadcast search failed ({name}): {exc}", file=sys.stderr)
        return []


def _fetch_fide_country(uscf_id: str | None) -> str | None:
    """
    Look up a tournament entrant's actual FIDE nationality from their USCF
    record, so the resolver can cross-check candidates' countries against
    it instead of assuming every entrant is US-based. Most club-level
    players have no FIDE Country on file — that's normal, not a failure.
    """
    if not uscf_id:
        return None
    try:
        from lookup.uscf import get_fide_country
        return get_fide_country(uscf_id)
    except Exception as exc:
        print(f"  FIDE nationality lookup failed ({uscf_id}): {exc}", file=sys.stderr)
        return None


def _game_positions(game: chess.pgn.Game) -> tuple[list[str], list[str]]:
    """Every FEN position and SAN move of a game, in order (fens has one more entry than sans)."""
    board = game.board()
    fens = [board.fen()]
    sans = []
    node = game
    while node.variations:
        node = node.variations[0]
        sans.append(board.san(node.move))
        board.push(node.move)
        fens.append(board.fen())
    return fens, sans


def _parse_pgn_date(date_str: str) -> date | None:
    """'2026.03.05' -> date(2026,3,5); None for missing/partial ('????.??.??') dates."""
    parts = (date_str or "").split(".")
    if len(parts) != 3:
        return None
    try:
        y, m, d = (int(p) for p in parts)
        return date(y, m, d)
    except ValueError:
        return None


def _recent_games_page(pgn_strings: list[str], out_dir: Path, slug: str, source: str,
                       player: str, days: int = _RECENT_GAMES_DAYS) -> tuple[str | None, int]:
    """
    Build an openingtree-style games list + traversable board restricted to
    the last calendar year, for a high-confidence online profile match —
    reuses the same viewer as _ensure_game_links's local-fallback games,
    just fed a different (date-filtered, source-specific) dataset. Returns
    (relative URL, game count), or (None, 0) if nothing falls in the window.
    """
    cutoff = date.today() - timedelta(days=days)
    dated: list[tuple[date, chess.pgn.Game]] = []
    for pgn_text in pgn_strings:
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
        except Exception:
            game = None
        if game is None:
            continue
        d = _parse_pgn_date(game.headers.get("Date", ""))
        if d and d >= cutoff:
            dated.append((d, game))
    dated.sort(key=lambda t: t[0], reverse=True)

    if not dated:
        return None, 0

    games_list = []
    for d, game in dated:
        h = game.headers
        fens, sans = _game_positions(game)
        url = next((h[k] for k in ("GameURL", "Link", "Site") if h.get(k, "").startswith("http")), None)
        entry = {
            "id": f"g{len(games_list) + 1}",
            "white": h.get("White", "?"), "black": h.get("Black", "?"),
            "date": h.get("Date", "?"), "result": h.get("Result", "*"),
            "event": h.get("Event", ""),
            "fens": fens, "sans": sans,
        }
        if url:
            entry["url"] = url
        games_list.append(entry)

    games_dir = out_dir / "games" / slug
    games_dir.mkdir(parents=True, exist_ok=True)
    filename = f"recent_{source}.html"
    source_label = {"lichess": "Lichess", "chesscom": "chess.com"}.get(source, source)
    title = f"{player} — Recent {source_label} games (last 12 months)"
    (games_dir / filename).write_text(_games_browser_html(games_list, title=title), encoding="utf-8")
    return f"games/{slug}/{filename}", len(games_list)


_SQ = 72  # board square size in px — bump this one number to rescale the whole board
_GAMES_BROWSER_CSS = f"""
html {{ font-size: 18px; }}
body {{ font-family: Georgia, serif; margin: 0 auto; padding: 1.5rem; color: #1a1a1a;
  max-width: 1400px; }}
h1 {{ margin-top: 0; font-size: 1.6rem; text-align: center; }}
.layout {{ display: flex; gap: 2rem; align-items: flex-start; justify-content: center; flex-wrap: wrap; }}
.list {{ width: 360px; max-height: 80vh; overflow-y: auto; border: 1px solid #ddd; border-radius: 6px; flex-shrink: 0; }}
.list button {{ display: block; width: 100%; text-align: left; padding: .7rem .9rem; border: none;
  background: none; cursor: pointer; font-size: .95rem; border-bottom: 1px solid #eee; font-family: inherit; }}
.list button:hover {{ background: #f2f2f2; }}
.list button.active {{ background: #2c3e50; color: #fff; }}
.list a.ext {{ float: right; text-decoration: none; }}
.viewer {{ flex: 0 0 auto; }}
.board-row {{ display: flex; align-items: flex-start; gap: .8rem; justify-content: center; }}
.board {{ display: grid; grid-template-columns: repeat(8, {_SQ}px); grid-template-rows: repeat(8, {_SQ}px);
  border: 3px solid #333; width: fit-content; }}
.sq {{ display: flex; align-items: center; justify-content: center; font-size: {_SQ * 0.7:.0f}px; user-select: none; position: relative; }}
.sq.light {{ background: #eeeed2; }}
.sq.dark {{ background: #769656; }}
.sq .piece {{ width: {_SQ * 0.86:.0f}px; height: {_SQ * 0.86:.0f}px; display: block; }}
.sq.from {{ box-shadow: inset 0 0 0 4px #f7d038; }}
.sq.to {{ box-shadow: inset 0 0 0 4px #e8890c; }}
.eval-bar {{ width: 30px; height: {_SQ * 8}px; background: #333; border-radius: 3px; overflow: hidden;
  display: flex; flex-direction: column-reverse; flex-shrink: 0; }}
.eval-bar-white {{ background: #eee; width: 100%; height: 50%; transition: height .25s ease; }}
.eval-readout {{ margin: .7rem 0; font-size: 1.15rem; text-align: center; }}
.eval-readout .score {{ font-weight: bold; margin-right: .8rem; }}
.eval-readout .best {{ color: #2c3e50; }}
.engine-lines {{ margin: .6rem 0; font-size: .95rem; list-style: decimal; padding-left: 1.4rem; }}
.engine-lines li {{ margin: .25rem 0; font-family: monospace; min-height: 1.3em; }}
.engine-lines .line-score {{ font-weight: bold; font-family: Georgia, serif; margin-right: .5rem; }}
.engine-lines .line-pending {{ color: #999; font-style: italic; font-family: Georgia, serif; }}
.controls {{ margin: .8rem 0; text-align: center; }}
.controls button {{ font-size: 1.3rem; padding: .4rem 1rem; margin-right: .4rem; cursor: pointer; }}
.movelist {{ margin-top: .8rem; font-size: 1.05rem; line-height: 1.8; width: {_SQ * 8}px; }}
.movelist span {{ cursor: pointer; padding: 2px 5px; border-radius: 3px; }}
.movelist span:hover {{ background: #eee; }}
.movelist span.current {{ background: #2c3e50; color: #fff; }}
.meta {{ color: #555; margin: .3rem 0 .8rem; font-size: 1rem; text-align: center; }}
.empty {{ color: #888; padding: 2rem; text-align: center; }}
.engine-panel {{ margin: .5rem auto 1.2rem; padding: .7rem 1rem; background: #f4f4f0; border: 1px solid #ddd;
  border-radius: 6px; font-size: 1rem; display: flex; align-items: center; gap: .8rem; flex-wrap: wrap;
  justify-content: center; max-width: {_SQ * 8 + 400}px; }}
.engine-panel select, .engine-panel input {{ font-family: inherit; font-size: 1rem; padding: .3rem .4rem; }}
.engine-status {{ color: #555; }}
"""

_GAMES_BROWSER_JS = """
const PIECE_ID = {K:'wK',Q:'wQ',R:'wR',B:'wB',N:'wN',P:'wP',
                  k:'bK',q:'bQ',r:'bR',b:'bB',n:'bN',p:'bP'};
let current = null, ply = 0, flipped = false;

// --- Engine (client-side UCI over a Web Worker) --------------------------
// Runs entirely in the browser, no server needed — same reasoning as the
// bundled Cburnett piece sprite, except an engine binary is too large to
// generate locally, so it's fetched from a CDN on demand instead. Browsers
// refuse `new Worker(crossOriginUrl)` outright (SecurityError) regardless
// of CORS headers, so the worker is instead a tiny same-origin blob script
// that calls importScripts(url) — importScripts *does* follow CORS, so
// this loads a cross-origin engine script while satisfying the same-origin
// check on the Worker constructor itself. Only self-contained, single-file
// engine builds work this way: one that fetches a separate .wasm file via
// a path relative to its own script location breaks, because that location
// is now the blob: URL, not the CDN directory (confirmed empirically —
// jsdelivr's WASM/NNUE Stockfish builds fail this way; the older asm.js
// build, with everything inlined, does not).
const ENGINE_PRESETS = {
  stockfish: 'https://cdn.jsdelivr.net/npm/stockfish.js@10.0.2/stockfish.js',
};
const ENGINE_DEPTH = 15;
const MULTIPV_COUNT = 3;
const MULTIPV_MAX_PLIES = 12;  // ~6 full moves per displayed line, so it reads as a short preview
let engine = null, engineReady = false, engineOn = false;
let pendingPly = null, pendingGameId = null;
let engineLines = {};  // multipv index (1..MULTIPV_COUNT) -> {cp, mate, pv: [uci, ...]}

function setEngineStatus(msg) {
  const el = document.getElementById('engine-status');
  if (el) el.textContent = msg;
}

function stopEngine() {
  if (engine) { try { engine.terminate(); } catch (e) {} }
  engine = null; engineReady = false; engineOn = false;
  setEngineStatus('Engine off.');
  clearEvalDisplay();
}

function loadEngine(url) {
  if (!url) return;
  if (engine) { try { engine.terminate(); } catch (e) {} }
  engineReady = false; engineOn = true;
  setEngineStatus('Loading engine\\u2026');
  clearEvalDisplay();
  try {
    const wrapper = "importScripts('" + url + "');";
    const blobUrl = URL.createObjectURL(new Blob([wrapper], {type: 'application/javascript'}));
    engine = new Worker(blobUrl);
  } catch (e) {
    setEngineStatus('Failed to load engine (network/CORS?).');
    engineOn = false;
    return;
  }
  engine.onerror = () => { setEngineStatus('Engine error \\u2014 check network/CORS, or this build needs same-origin assets.'); engineOn = false; };
  engine.onmessage = e => handleEngineMessage(typeof e.data === 'string' ? e.data : '');
  engine.postMessage('uci');
}

function handleEngineMessage(line) {
  if (line === 'uciok') {
    engine.postMessage('setoption name MultiPV value ' + MULTIPV_COUNT);
    engine.postMessage('isready');
  } else if (line === 'readyok') {
    engineReady = true;
    setEngineStatus('Engine ready.');
    if (current) requestEval();
  } else if (line.indexOf('score ') !== -1 && line.indexOf(' pv ') !== -1) {
    parseInfoLine(line);
  } else if (line.indexOf('bestmove') === 0) {
    const parts = line.split(' ');
    if (isStalePending()) return;
    highlightMove(parts[1]);
  }
}

function isStalePending() {
  return !current || current.id !== pendingGameId || ply !== pendingPly;
}

function requestEval() {
  if (!engine || !engineReady || !current) return;
  pendingPly = ply; pendingGameId = current.id;
  engineLines = {};
  renderEngineLines();
  engine.postMessage('stop');
  engine.postMessage('position fen ' + current.fens[ply]);
  engine.postMessage('go depth ' + ENGINE_DEPTH);
}

function parseInfoLine(line) {
  if (isStalePending()) return;
  const multipvMatch = line.match(/multipv (\\d+)/);
  const multipv = multipvMatch ? parseInt(multipvMatch[1], 10) : 1;
  const cpMatch = line.match(/score cp (-?\\d+)/);
  const mateMatch = line.match(/score mate (-?\\d+)/);
  const pvMatch = line.match(/ pv (.+)$/);
  let cp = cpMatch ? parseInt(cpMatch[1], 10) : null;
  let mate = mateMatch ? parseInt(mateMatch[1], 10) : null;
  if (!pvMatch) return;
  // UCI scores are from the side-to-move's perspective; flip to White's for display.
  if (current.fens[ply].split(' ')[1] === 'b') {
    if (cp !== null) cp = -cp;
    if (mate !== null) mate = -mate;
  }
  const pv = pvMatch[1].trim().split(/\\s+/).slice(0, MULTIPV_MAX_PLIES);
  engineLines[multipv] = {cp: cp, mate: mate, pv: pv};
  renderEngineLines();
  if (multipv === 1) {
    updateEvalDisplay(cp, mate);
    highlightMove(pv[0], false);
  }
}

function formatEval(cp, mate) {
  if (mate !== null) return (mate > 0 ? '+M' : '-M') + Math.abs(mate);
  if (cp !== null) return (cp >= 0 ? '+' : '') + (cp / 100).toFixed(2);
  return '\\u2026';
}

function renderEngineLines() {
  const el = document.getElementById('engine-lines');
  if (!el) return;
  // Always render exactly MULTIPV_COUNT slots, even before the engine has
  // reported anything — otherwise the list grows from 0 to 3 items as
  // results trickle in, and everything below it (the move buttons) visibly
  // jumps down each time.
  let html = '';
  for (let i = 1; i <= MULTIPV_COUNT; i++) {
    const line = engineLines[i];
    if (line) {
      html += '<li><span class="line-score">' + formatEval(line.cp, line.mate) + '</span>' +
        line.pv.join(' ') + '</li>';
    } else {
      html += '<li class="line-pending">\\u2026</li>';
    }
  }
  el.innerHTML = html;
}

function evalToWhitePercent(cp, mate) {
  if (mate !== null) return mate > 0 ? 100 : 0;
  if (cp === null) return 50;
  const clamped = Math.max(-1000, Math.min(1000, cp));
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * clamped)) - 1);
}

function updateEvalDisplay(cp, mate) {
  const bar = document.getElementById('eval-bar-white');
  const label = document.getElementById('eval-score');
  if (bar) bar.style.height = evalToWhitePercent(cp, mate) + '%';
  if (label) label.textContent = formatEval(cp, mate);
}

function clearEvalDisplay() {
  const bar = document.getElementById('eval-bar-white');
  const label = document.getElementById('eval-score');
  const best = document.getElementById('eval-best');
  engineLines = {};
  if (bar) bar.style.height = '50%';
  if (label) label.textContent = '\\u2026';
  if (best) best.textContent = '';
  renderEngineLines();  // still renders MULTIPV_COUNT placeholder slots, so the reserved height never changes
  document.querySelectorAll('.sq.from,.sq.to').forEach(el => el.classList.remove('from', 'to'));
}

function squareEl(sq) {
  return document.querySelector('.sq[data-sq="' + sq + '"]');
}

function highlightMove(uci, isBest) {
  document.querySelectorAll('.sq.from,.sq.to').forEach(el => el.classList.remove('from', 'to'));
  if (!uci || uci.length < 4) return;
  const from = uci.slice(0, 2), to = uci.slice(2, 4);
  const fromEl = squareEl(from), toEl = squareEl(to);
  if (fromEl) fromEl.classList.add('from');
  if (toEl) toEl.classList.add('to');
  const best = document.getElementById('eval-best');
  if (best && isBest !== false) best.textContent = 'Best: ' + from + '\\u2192' + to;
}

function renderList() {
  const list = document.getElementById('list');
  GAMES.forEach(g => {
    const btn = document.createElement('button');
    btn.textContent = (g.date || '?') + ' \\u00b7 ' + g.white + ' vs ' + g.black + ' \\u00b7 ' + g.result;
    if (g.url) {
      const a = document.createElement('a');
      a.href = g.url; a.target = '_blank'; a.rel = 'noopener'; a.className = 'ext';
      a.textContent = '\\u2197'; a.title = 'Open on source site';
      a.onclick = ev => ev.stopPropagation();
      btn.appendChild(a);
    }
    btn.id = 'row-' + g.id;
    btn.onclick = () => selectGame(g.id, true);
    list.appendChild(btn);
  });
}

function selectGame(id, setHash) {
  current = GAMES.find(g => g.id === id);
  if (!current) return;
  ply = 0;
  document.querySelectorAll('.list button').forEach(b => b.classList.remove('active'));
  const row = document.getElementById('row-' + id);
  if (row) { row.classList.add('active'); row.scrollIntoView({block: 'nearest'}); }
  if (setHash) location.hash = id;
  renderViewer();
}

function renderBoard(fen) {
  const rows = fen.split(' ')[0].split('/');
  const board = rows.map(row => {
    const squares = [];
    for (const ch of row) {
      if (/[1-8]/.test(ch)) {
        for (let i = 0; i < parseInt(ch, 10); i++) squares.push(null);
      } else {
        squares.push(ch);
      }
    }
    return squares;
  });
  let html = '';
  for (let vr = 0; vr < 8; vr++) {
    for (let vf = 0; vf < 8; vf++) {
      const r = flipped ? 7 - vr : vr;
      const f = flipped ? 7 - vf : vf;
      const ch = board[r][f];
      const sq = String.fromCharCode(97 + f) + (8 - r);
      const light = (vr + vf) % 2 === 0;
      const id = ch ? PIECE_ID[ch] : null;
      const glyph = id ? '<svg class="piece"><use href="#' + id + '"></use></svg>' : '';
      html += '<div class="sq ' + (light ? 'light' : 'dark') + '" data-sq="' + sq + '">' + glyph + '</div>';
    }
  }
  return html;
}

function renderMoveList() {
  let html = '';
  current.sans.forEach((san, i) => {
    const moveNum = Math.floor(i / 2) + 1;
    const prefix = i % 2 === 0 ? moveNum + '. ' : '';
    html += '<span data-ply="' + (i + 1) + '" class="' + (i + 1 === ply ? 'current' : '') + '">' + prefix + san + '</span> ';
  });
  return html;
}

function renderViewer() {
  const v = document.getElementById('viewer');
  v.innerHTML =
    '<p class="meta">' + (current.event || '') + ' \\u00b7 ' + (current.date || '?') + ' \\u00b7 ' + current.result + '</p>' +
    '<div class="board-row">' +
      '<div class="eval-bar" id="eval-bar"><div class="eval-bar-white" id="eval-bar-white"></div></div>' +
      '<div class="board" id="board"></div>' +
    '</div>' +
    '<p class="eval-readout"><span class="score" id="eval-score">\\u2026</span>' +
      '<span class="best" id="eval-best"></span></p>' +
    '<ol class="engine-lines" id="engine-lines"></ol>' +
    '<div class="controls">' +
      '<button id="btn-start">|&lt;</button>' +
      '<button id="btn-prev">&lt;</button>' +
      '<button id="btn-next">&gt;</button>' +
      '<button id="btn-end">&gt;|</button>' +
      '<button id="btn-flip">Flip board</button>' +
    '</div>' +
    '<div class="movelist" id="movelist"></div>';
  document.getElementById('btn-start').onclick = () => goTo(0);
  document.getElementById('btn-prev').onclick = () => goTo(ply - 1);
  document.getElementById('btn-next').onclick = () => goTo(ply + 1);
  document.getElementById('btn-end').onclick = () => goTo(current.sans.length);
  document.getElementById('btn-flip').onclick = () => { flipped = !flipped; update(); };
  document.getElementById('movelist').addEventListener('click', e => {
    if (e.target.dataset.ply) goTo(parseInt(e.target.dataset.ply, 10));
  });
  update();
}

function goTo(n) {
  if (!current) return;
  ply = Math.max(0, Math.min(current.sans.length, n));
  update();
}

function update() {
  document.getElementById('board').innerHTML = renderBoard(current.fens[ply]);
  document.getElementById('movelist').innerHTML = renderMoveList();
  clearEvalDisplay();
  if (engineOn && engineReady) requestEval();
}

function initEnginePanel() {
  const sel = document.getElementById('engine-select');
  const customInput = document.getElementById('engine-custom-url');
  if (!sel) return;
  function apply() {
    const val = sel.value;
    if (val === 'off') {
      customInput.style.display = 'none';
      stopEngine();
      return;
    }
    if (val === 'custom') {
      customInput.style.display = 'inline-block';
      setEngineStatus('Paste a UCI engine script URL and press Enter.');
      return;
    }
    customInput.style.display = 'none';
    loadEngine(ENGINE_PRESETS[val]);
  }
  sel.addEventListener('change', apply);
  customInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') loadEngine(customInput.value.trim());
  });
  apply();
}

document.addEventListener('keydown', e => {
  if (!current) return;
  if (e.key === 'ArrowLeft') goTo(ply - 1);
  if (e.key === 'ArrowRight') goTo(ply + 1);
});

renderList();
initEnginePanel();
if (location.hash) {
  const id = location.hash.slice(1);
  if (GAMES.some(g => g.id === id)) selectGame(id, false);
}
"""


_PIECE_SPRITE_IDS = {
    (chess.KING, True): "wK", (chess.QUEEN, True): "wQ", (chess.ROOK, True): "wR",
    (chess.BISHOP, True): "wB", (chess.KNIGHT, True): "wN", (chess.PAWN, True): "wP",
    (chess.KING, False): "bK", (chess.QUEEN, False): "bQ", (chess.ROOK, False): "bR",
    (chess.BISHOP, False): "bB", (chess.KNIGHT, False): "bN", (chess.PAWN, False): "bP",
}


def _piece_sprite_svg() -> str:
    """
    One-time SVG <symbol> sprite of all 12 pieces, referenced via <use> from
    the board grid — the same "Cburnett" piece set Lichess's default board
    theme uses, already bundled with python-chess's chess.svg module (no new
    dependency, no external asset fetch).
    """
    import chess.svg

    symbols = []
    for (piece_type, color), sym_id in _PIECE_SPRITE_IDS.items():
        piece_svg = chess.svg.piece(chess.Piece(piece_type, color))
        inner = re.search(r"<svg[^>]*>(.*)</svg>", piece_svg, re.S).group(1)
        symbols.append(f'<symbol id="{sym_id}" viewBox="0 0 45 45">{inner}</symbol>')
    return (
        "<svg style='display:none' xmlns='http://www.w3.org/2000/svg' "
        "xmlns:xlink='http://www.w3.org/1999/xlink'>" + "".join(symbols) + "</svg>"
    )


def _games_browser_html(games: list[dict], title: str = "Games") -> str:
    """
    Self-contained games list + interactive board: click a game in the
    list to load it, then step through with buttons, click-a-move, or
    arrow keys. `games` items need id/white/black/date/result/event/fens/sans
    (plus an optional `url` for an external link icon in the list).

    Also wires up client-side engine analysis (a UCI engine loaded from a
    CDN into a Web Worker — see _GAMES_BROWSER_JS): an eval bar/number and
    best-move highlight that update as the user steps through the game,
    and a flip-board button. Stockfish is attached by default; the engine
    picker also accepts a custom UCI-engine script URL.
    """
    # Guard against a PGN header (e.g. an Event name) containing a
    # "</script>"-like sequence and breaking out of the embedded script.
    payload = json.dumps(games, ensure_ascii=False).replace("</script", "<\\/script")
    engine_panel = (
        "<div class='engine-panel'>"
        "<label>Engine: <select id='engine-select'>"
        "<option value='off' selected>Off</option>"
        "<option value='stockfish'>Stockfish</option>"
        "<option value='custom'>Custom URL…</option>"
        "</select></label> "
        "<input type='text' id='engine-custom-url' "
        "placeholder='https://.../engine.js (single-file, no separate .wasm)' "
        "style='display:none;width:340px'> "
        "<span class='engine-status' id='engine-status'>Engine off.</span>"
        "</div>"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_esc_title(title)}</title>"
        f"<style>{_GAMES_BROWSER_CSS}</style>"
        "</head><body>"
        f"{_piece_sprite_svg()}"
        f"<h1>{_esc_title(title)}</h1>"
        f"{engine_panel}"
        "<div class='layout'>"
        "<div class='list' id='list'></div>"
        "<div class='viewer' id='viewer'><p class='empty'>Select a game from the list.</p></div>"
        "</div>"
        f"<script>const GAMES = {payload};\n{_GAMES_BROWSER_JS}</script>"
        "</body></html>"
    )


def _esc_title(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ensure_game_links(pgn_strings: list[str], out_dir: Path, slug: str) -> list[str]:
    """
    Games with no public URL (e.g. from the megabase, unlike Lichess/chess.com/
    broadcast games whose PGN already carries one in GameURL/Link/Site) are
    collected into a single interactive game-browser page at
    <out_dir>/games/<slug>/index.html — a game list you click through to
    load onto a traversable board — with a GameURL header (pointing at that
    game's anchor within the page) injected so opening tables can link to it.
    """
    result: list[str] = []
    local_games: list[dict] = []

    for pgn_text in pgn_strings:
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
        except Exception:
            game = None
        if game is None:
            result.append(pgn_text)
            continue

        has_public_url = any(
            game.headers.get(k, "").startswith("http") for k in ("GameURL", "Link", "Site")
        )
        if has_public_url:
            result.append(pgn_text)
            continue

        h = game.headers
        game_id = f"g{len(local_games) + 1}"
        fens, sans = _game_positions(game)
        local_games.append({
            "id": game_id,
            "white": h.get("White", "?"), "black": h.get("Black", "?"),
            "date": h.get("Date", "?"), "result": h.get("Result", "*"),
            "event": h.get("Event", ""),
            "fens": fens, "sans": sans,
        })
        game.headers["GameURL"] = f"games/{slug}/index.html#{game_id}"

        buf = io.StringIO()
        game.accept(chess.pgn.FileExporter(buf))
        result.append(buf.getvalue().strip())

    if local_games:
        games_dir = out_dir / "games" / slug
        games_dir.mkdir(parents=True, exist_ok=True)
        (games_dir / "index.html").write_text(_games_browser_html(local_games), encoding="utf-8")

    return result


def run_pipeline(
    tournament: str,
    site: str = "kingregistration",
    output_dir: str = "dossiers",
    max_games: int = 50,
    chesscom_months: int = 3,
    megabase: str | None = None,
    megabase_limit: int | None = None,
    lichess_studies: bool = True,
    searxng_url: str | None = None,
    depth: int = 6,
    top: int = 8,
    fmt: str = "html",
    exclude: list[str] | None = None,
    dossier_db: str | None = None,
) -> list[Path]:
    """
    Run the full pipeline for a tournament. Returns list of written file paths.

    `dossier_db`, if given, is a SQLite database path (see dossier.db) that
    every player's dossier is also saved into, grouped under a scan for
    this tournament — letting the same player scanned across multiple
    tournaments, or the same tournament rescanned later, build up a
    queryable history instead of each run just overwriting the last one's
    report files.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Scraping entry list: {tournament}", file=sys.stderr)
    players = scrape_entry_list(tournament, site=site)
    if not players:
        print("No players found — check the tournament URL.", file=sys.stderr)
        return []

    if exclude:
        needles = [e.lower() for e in exclude]
        players = [p for p in players
                  if not any(n in p.get("name", "").lower() for n in needles)]
    print(f"Found {len(players)} player(s).", file=sys.stderr)

    written: list[Path] = []
    dossiers: list[dict] = []

    for i, player in enumerate(players, 1):
        name = _strip_title(player.get("name", "").strip())
        if not name:
            continue

        entry_rating = _parse_rating(player.get("rating"))
        fide_country = _fetch_fide_country(player.get("uscf_id"))

        print(f"\n[{i}/{len(players)}] {name}", file=sys.stderr)
        if fide_country:
            print(f"  FIDE nationality: {fide_country}", file=sys.stderr)

        pgn_strings: list[str] = []
        profiles: list[dict] = []

        # --- Megabase ---
        if megabase:
            mb_pgns = _fetch_megabase_games(name, megabase, limit=megabase_limit, rating=entry_rating)
            print(f"  Megabase games: {len(mb_pgns)}", file=sys.stderr)
            pgn_strings += mb_pgns

        # --- Lichess ---
        lichess_user, lc_conf, lc_score, lc_reasons = resolve_lichess(
            name, rating=entry_rating, searxng_url=searxng_url, fide_country=fide_country)
        if lichess_user:
            print(f"  Lichess: {lichess_user} ({lc_conf} confidence, {lc_score:.2f} — {'; '.join(lc_reasons)})",
                 file=sys.stderr)
            pgns, profile = _fetch_lichess_games(lichess_user, max_games)
            print(f"  Lichess games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                prof_dict = {**profile, "confidence": lc_conf,
                            "match_score": lc_score, "match_reasons": lc_reasons}
                # Only worth a dedicated "recent games" page once we actually
                # trust the match — a low-confidence account's game history
                # may well belong to someone else entirely.
                if lc_conf == "high" and fmt != "json":
                    recent_pgns = _fetch_lichess_recent_games(lichess_user)
                    url, count = _recent_games_page(recent_pgns, out, _slug(name), "lichess", name)
                    print(f"  Lichess recent games (last 12mo): {count}", file=sys.stderr)
                    if url:
                        prof_dict["recent_games_url"] = url
                        prof_dict["recent_games_count"] = count
                profiles.append(prof_dict)
            if lichess_studies:
                study_pgns = _fetch_lichess_studies(lichess_user)
                print(f"  Lichess study games: {len(study_pgns)}", file=sys.stderr)
                pgn_strings += study_pgns
        else:
            print("  Lichess: no match found", file=sys.stderr)

        # --- chess.com ---
        cc_user, cc_conf, cc_score, cc_reasons = resolve_chesscom(
            name, rating=entry_rating, searxng_url=searxng_url, fide_country=fide_country)
        if cc_user:
            print(f"  chess.com: {cc_user} ({cc_conf} confidence, {cc_score:.2f} — {'; '.join(cc_reasons)})",
                 file=sys.stderr)
            pgns, profile = _fetch_chesscom_games(cc_user, chesscom_months)
            print(f"  chess.com games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                prof_dict = {**profile, "confidence": cc_conf,
                            "match_score": cc_score, "match_reasons": cc_reasons}
                if cc_conf == "high" and fmt != "json":
                    # A dedicated 12-month archive pull, independent of
                    # `chesscom_months` (which stays small for the main
                    # analysis pool) — "last calendar year" shouldn't be at
                    # the mercy of an unrelated speed knob.
                    recent_pgns, _ = _fetch_chesscom_games(cc_user, 12)
                    url, count = _recent_games_page(recent_pgns, out, _slug(name), "chesscom", name)
                    print(f"  chess.com recent games (last 12mo): {count}", file=sys.stderr)
                    if url:
                        prof_dict["recent_games_url"] = url
                        prof_dict["recent_games_count"] = count
                profiles.append(prof_dict)
        else:
            print("  chess.com: no match found", file=sys.stderr)

        # --- Broadcasts (opt-in: needs a SearXNG instance) ---
        if searxng_url:
            bc_pgns = _fetch_broadcast_games(name, searxng_url)
            print(f"  Broadcast games: {len(bc_pgns)}", file=sys.stderr)
            pgn_strings += bc_pgns

        if not pgn_strings:
            print("  No games found — generating skeleton dossier.", file=sys.stderr)
        elif fmt != "json":
            # Games with no public URL (megabase) get a local static view
            # generated so opening-table rows can still link somewhere.
            pgn_strings = _ensure_game_links(pgn_strings, out, _slug(name))

        dossier = build_dossier(name, pgn_strings, profiles=profiles,
                                depth=depth, top=top)
        dossiers.append(dossier)

        if dossier_db:
            from dossier.db import save_dossier
            try:
                save_dossier(dossier_db, tournament, site, dossier)
            except Exception as exc:
                print(f"  Dossier DB save failed: {exc}", file=sys.stderr)

        if fmt == "json":
            content = render_json(dossier)
            ext = "json"
        elif fmt == "html":
            content = render_html(dossier)
            ext = "html"
        else:
            content = render_markdown(dossier)
            ext = "md"

        path = out / f"{_slug(name)}.{ext}"
        path.write_text(content, encoding="utf-8")
        written.append(path)
        print(f"  Saved → {path}", file=sys.stderr)

    # --- Combined output ---
    if dossiers and fmt == "markdown":
        combined = out / "combined.md"
        combined.write_text(
            "\n\n---\n\n".join(render_markdown(d) for d in dossiers), encoding="utf-8"
        )
        written.append(combined)
        print(f"\nCombined → {combined}", file=sys.stderr)
    elif dossiers and fmt == "html":
        combined = out / "combined.html"
        combined.write_text(render_html_combined(dossiers), encoding="utf-8")
        written.append(combined)
        print(f"\nCombined → {combined}", file=sys.stderr)

    print(f"\nDone. {len(players)} dossier(s) written to {out}/", file=sys.stderr)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate dossiers for all opponents in a tournament."
    )
    parser.add_argument("tournament", help="Tournament ID or full URL")
    parser.add_argument("--site", choices=["kingregistration", "chessaction"],
                        default="kingregistration")
    parser.add_argument("--output-dir", default="dossiers",
                        help="Directory to write dossier files (default: dossiers/)")
    parser.add_argument("--max-games", type=int, default=50,
                        help="Max games to fetch per player from Lichess (default: 50)")
    parser.add_argument("--chesscom-months", type=int, default=3,
                        help="Months of chess.com history to fetch (default: 3)")
    parser.add_argument("--megabase", metavar="DB",
                        help="SQLite megabase index to pull historical games from")
    parser.add_argument("--megabase-limit", type=int, default=None,
                        help="Max games to pull from the megabase per player (default: no limit)")
    parser.add_argument("--no-lichess-studies", dest="lichess_studies", action="store_false",
                        help="Skip pulling games from the opponent's own public Lichess studies")
    parser.add_argument("--searxng-url", default=os.environ.get("SEARXNG_URL"),
                        dest="searxng_url",
                        help="Base URL of a self-hosted SearXNG instance (e.g. http://localhost:8080) — "
                             "enables finding personalized online handles and searching Lichess "
                             "broadcasts for opponent games. Defaults to $SEARXNG_URL. Omit to skip.")
    parser.add_argument("--depth", type=int, default=6,
                        help="Opening depth in half-moves (default: 6)")
    parser.add_argument("--top", type=int, default=8,
                        help="Top N opening lines per colour (default: 8)")
    parser.add_argument("--format", dest="fmt", choices=["markdown", "html", "json"],
                        default="html")
    parser.add_argument("--exclude", action="append", metavar="NAME",
                        help="Skip players whose name contains this text (repeatable)")
    parser.add_argument("--dossier-db", metavar="DB",
                        help="SQLite database to also save every dossier into (see dossier.db) — "
                             "builds a queryable history across repeated scans over time")
    args = parser.parse_args()

    run_pipeline(
        args.tournament,
        site=args.site,
        output_dir=args.output_dir,
        max_games=args.max_games,
        chesscom_months=args.chesscom_months,
        megabase=args.megabase,
        megabase_limit=args.megabase_limit,
        lichess_studies=args.lichess_studies,
        searxng_url=args.searxng_url,
        depth=args.depth,
        top=args.top,
        fmt=args.fmt,
        exclude=args.exclude,
        dossier_db=args.dossier_db,
    )


if __name__ == "__main__":
    main()
