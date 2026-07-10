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


def _fetch_broadcast_games(name: str, api_key: str, max_results: int = 5) -> list[str]:
    try:
        from lookup.broadcasts import find_games
        return find_games(name, api_key, max_results=max_results)
    except Exception as exc:
        print(f"  Broadcast search failed ({name}): {exc}", file=sys.stderr)
        return []


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


_GAMES_BROWSER_CSS = """
body { font-family: Georgia, serif; margin: 0; padding: 1.5rem; color: #1a1a1a; }
h1 { margin-top: 0; font-size: 1.3rem; }
.layout { display: flex; gap: 1.5rem; align-items: flex-start; flex-wrap: wrap; }
.list { width: 320px; max-height: 640px; overflow-y: auto; border: 1px solid #ddd; border-radius: 6px; flex-shrink: 0; }
.list button { display: block; width: 100%; text-align: left; padding: .5rem .7rem; border: none;
  background: none; cursor: pointer; font-size: .85rem; border-bottom: 1px solid #eee; font-family: inherit; }
.list button:hover { background: #f2f2f2; }
.list button.active { background: #2c3e50; color: #fff; }
.viewer { flex: 1; min-width: 320px; }
.board { display: grid; grid-template-columns: repeat(8, 44px); grid-template-rows: repeat(8, 44px);
  border: 2px solid #333; width: fit-content; }
.sq { display: flex; align-items: center; justify-content: center; font-size: 30px; user-select: none; }
.sq.light { background: #eeeed2; }
.sq.dark { background: #769656; }
.sq .piece { width: 40px; height: 40px; display: block; }
.controls { margin: .6rem 0; }
.controls button { font-size: 1rem; padding: .3rem .7rem; margin-right: .3rem; cursor: pointer; }
.movelist { margin-top: .6rem; font-size: .9rem; line-height: 1.6; max-width: 392px; }
.movelist span { cursor: pointer; padding: 1px 3px; border-radius: 3px; }
.movelist span:hover { background: #eee; }
.movelist span.current { background: #2c3e50; color: #fff; }
.meta { color: #555; margin: .3rem 0 .8rem; font-size: .9rem; }
.empty { color: #888; padding: 2rem; text-align: center; }
"""

_GAMES_BROWSER_JS = """
const PIECE_ID = {K:'wK',Q:'wQ',R:'wR',B:'wB',N:'wN',P:'wP',
                  k:'bK',q:'bQ',r:'bR',b:'bB',n:'bN',p:'bP'};
let current = null, ply = 0;

function renderList() {
  const list = document.getElementById('list');
  GAMES.forEach(g => {
    const btn = document.createElement('button');
    btn.textContent = (g.date || '?') + ' \\u00b7 ' + g.white + ' vs ' + g.black + ' \\u00b7 ' + g.result;
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
  let html = '';
  for (let r = 0; r < 8; r++) {
    let file = 0;
    for (const ch of rows[r]) {
      if (/[1-8]/.test(ch)) {
        for (let i = 0; i < parseInt(ch, 10); i++) {
          html += '<div class="sq ' + ((r + file) % 2 === 0 ? 'light' : 'dark') + '"></div>';
          file++;
        }
      } else {
        const id = PIECE_ID[ch];
        const glyph = id ? '<svg class="piece"><use href="#' + id + '"></use></svg>' : '';
        html += '<div class="sq ' + ((r + file) % 2 === 0 ? 'light' : 'dark') + '">' + glyph + '</div>';
        file++;
      }
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
    '<div class="board" id="board"></div>' +
    '<div class="controls">' +
      '<button id="btn-start">|&lt;</button>' +
      '<button id="btn-prev">&lt;</button>' +
      '<button id="btn-next">&gt;</button>' +
      '<button id="btn-end">&gt;|</button>' +
    '</div>' +
    '<div class="movelist" id="movelist"></div>';
  document.getElementById('btn-start').onclick = () => goTo(0);
  document.getElementById('btn-prev').onclick = () => goTo(ply - 1);
  document.getElementById('btn-next').onclick = () => goTo(ply + 1);
  document.getElementById('btn-end').onclick = () => goTo(current.sans.length);
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
}

document.addEventListener('keydown', e => {
  if (!current) return;
  if (e.key === 'ArrowLeft') goTo(ply - 1);
  if (e.key === 'ArrowRight') goTo(ply + 1);
});

renderList();
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


def _games_browser_html(games: list[dict]) -> str:
    """
    Self-contained games list + interactive board: click a game in the
    list to load it, then step through with buttons, click-a-move, or
    arrow keys. `games` items need id/white/black/date/result/event/fens/sans.
    """
    # Guard against a PGN header (e.g. an Event name) containing a
    # "</script>"-like sequence and breaking out of the embedded script.
    payload = json.dumps(games, ensure_ascii=False).replace("</script", "<\\/script")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Games</title>"
        f"<style>{_GAMES_BROWSER_CSS}</style>"
        "</head><body>"
        f"{_piece_sprite_svg()}"
        "<h1>Games</h1>"
        "<div class='layout'>"
        "<div class='list' id='list'></div>"
        "<div class='viewer' id='viewer'><p class='empty'>Select a game from the list.</p></div>"
        "</div>"
        f"<script>const GAMES = {payload};\n{_GAMES_BROWSER_JS}</script>"
        "</body></html>"
    )


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
    search_api_key: str | None = None,
    depth: int = 6,
    top: int = 8,
    fmt: str = "html",
    exclude: list[str] | None = None,
) -> list[Path]:
    """
    Run the full pipeline for a tournament. Returns list of written file paths.
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

        print(f"\n[{i}/{len(players)}] {name}", file=sys.stderr)

        pgn_strings: list[str] = []
        profiles: list[dict] = []

        # --- Megabase ---
        if megabase:
            mb_pgns = _fetch_megabase_games(name, megabase, limit=megabase_limit, rating=entry_rating)
            print(f"  Megabase games: {len(mb_pgns)}", file=sys.stderr)
            pgn_strings += mb_pgns

        # --- Lichess ---
        lichess_user, lc_conf, lc_score, lc_reasons = resolve_lichess(
            name, rating=entry_rating, search_api_key=search_api_key)
        if lichess_user:
            print(f"  Lichess: {lichess_user} ({lc_conf} confidence, {lc_score:.2f} — {'; '.join(lc_reasons)})",
                 file=sys.stderr)
            pgns, profile = _fetch_lichess_games(lichess_user, max_games)
            print(f"  Lichess games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                profiles.append({**profile, "confidence": lc_conf,
                                 "match_score": lc_score, "match_reasons": lc_reasons})
            if lichess_studies:
                study_pgns = _fetch_lichess_studies(lichess_user)
                print(f"  Lichess study games: {len(study_pgns)}", file=sys.stderr)
                pgn_strings += study_pgns
        else:
            print("  Lichess: no match found", file=sys.stderr)

        # --- chess.com ---
        cc_user, cc_conf, cc_score, cc_reasons = resolve_chesscom(
            name, rating=entry_rating, search_api_key=search_api_key)
        if cc_user:
            print(f"  chess.com: {cc_user} ({cc_conf} confidence, {cc_score:.2f} — {'; '.join(cc_reasons)})",
                 file=sys.stderr)
            pgns, profile = _fetch_chesscom_games(cc_user, chesscom_months)
            print(f"  chess.com games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                profiles.append({**profile, "confidence": cc_conf,
                                 "match_score": cc_score, "match_reasons": cc_reasons})
        else:
            print("  chess.com: no match found", file=sys.stderr)

        # --- Broadcasts (opt-in: needs a Brave Search API key) ---
        if search_api_key:
            bc_pgns = _fetch_broadcast_games(name, search_api_key)
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
    parser.add_argument("--search-api-key", default=os.environ.get("BRAVE_API_KEY"),
                        help="Brave Search API key — enables searching Lichess broadcasts "
                             "for opponent games. Defaults to $BRAVE_API_KEY. Omit to skip.")
    parser.add_argument("--depth", type=int, default=6,
                        help="Opening depth in half-moves (default: 6)")
    parser.add_argument("--top", type=int, default=8,
                        help="Top N opening lines per colour (default: 8)")
    parser.add_argument("--format", dest="fmt", choices=["markdown", "html", "json"],
                        default="html")
    parser.add_argument("--exclude", action="append", metavar="NAME",
                        help="Skip players whose name contains this text (repeatable)")
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
        search_api_key=args.search_api_key,
        depth=args.depth,
        top=args.top,
        fmt=args.fmt,
        exclude=args.exclude,
    )


if __name__ == "__main__":
    main()
