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

import re
import sys
import argparse
from pathlib import Path

from scraper import scrape_entry_list
from dossier.report import build_dossier, render_markdown, render_html, render_html_combined, render_json
from pipeline.resolver import resolve_lichess, resolve_chesscom


def _slug(name: str) -> str:
    """'Smith, John' → 'smith_john'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _fetch_lichess_games(username: str, max_games: int) -> tuple[list[str], dict | None]:
    try:
        from lookup.lichess import get_profile, games_as_pgn
        import io, chess.pgn, time
        profile = get_profile(username)
        time.sleep(1.0)
        pgn_text = games_as_pgn(username, max=max_games)
        pgns = []
        buf = io.StringIO(pgn_text)
        while True:
            game = chess.pgn.read_game(buf)
            if game is None:
                break
            import io as _io
            out = _io.StringIO()
            game.accept(chess.pgn.FileExporter(out))
            pgns.append(out.getvalue().strip())
        return pgns, profile
    except Exception as exc:
        print(f"  Lichess fetch failed ({username}): {exc}", file=sys.stderr)
        return [], None


def _fetch_chesscom_games(username: str, months: int) -> tuple[list[str], dict | None]:
    try:
        from lookup.chesscom import get_profile, games_as_pgn
        import re as _re
        profile = get_profile(username)
        pgn_text = games_as_pgn(username, months=months)
        pgns = [g for g in _re.split(r"\n(?=\[)", pgn_text.strip()) if g.strip()]
        return pgns, profile
    except Exception as exc:
        print(f"  chess.com fetch failed ({username}): {exc}", file=sys.stderr)
        return [], None


def run_pipeline(
    tournament: str,
    site: str = "kingregistration",
    output_dir: str = "dossiers",
    max_games: int = 50,
    chesscom_months: int = 3,
    depth: int = 6,
    top: int = 8,
    fmt: str = "html",
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
    print(f"Found {len(players)} player(s).", file=sys.stderr)

    written: list[Path] = []
    dossiers: list[dict] = []

    for i, player in enumerate(players, 1):
        name = player.get("name", "").strip()
        if not name:
            continue

        print(f"\n[{i}/{len(players)}] {name}", file=sys.stderr)

        pgn_strings: list[str] = []
        profiles: list[dict] = []

        # --- Lichess ---
        lichess_user, lc_conf = resolve_lichess(name)
        if lichess_user:
            print(f"  Lichess: {lichess_user} ({lc_conf} confidence)", file=sys.stderr)
            pgns, profile = _fetch_lichess_games(lichess_user, max_games)
            print(f"  Lichess games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                profiles.append({**profile, "confidence": lc_conf})
        else:
            print("  Lichess: no match found", file=sys.stderr)

        # --- chess.com ---
        cc_user, cc_conf = resolve_chesscom(name)
        if cc_user:
            print(f"  chess.com: {cc_user} ({cc_conf} confidence)", file=sys.stderr)
            pgns, profile = _fetch_chesscom_games(cc_user, chesscom_months)
            print(f"  chess.com games: {len(pgns)}", file=sys.stderr)
            pgn_strings += pgns
            if profile:
                profiles.append({**profile, "confidence": cc_conf})
        else:
            print("  chess.com: no match found", file=sys.stderr)

        if not pgn_strings:
            print("  No games found — generating skeleton dossier.", file=sys.stderr)

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
    parser.add_argument("--depth", type=int, default=6,
                        help="Opening depth in half-moves (default: 6)")
    parser.add_argument("--top", type=int, default=8,
                        help="Top N opening lines per colour (default: 8)")
    parser.add_argument("--format", dest="fmt", choices=["markdown", "html", "json"],
                        default="html")
    args = parser.parse_args()

    run_pipeline(
        args.tournament,
        site=args.site,
        output_dir=args.output_dir,
        max_games=args.max_games,
        chesscom_months=args.chesscom_months,
        depth=args.depth,
        top=args.top,
        fmt=args.fmt,
    )


if __name__ == "__main__":
    main()
