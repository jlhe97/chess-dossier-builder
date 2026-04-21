"""
Dossier report generator — ties together all pipeline steps.

Core function build_dossier() is pure: accepts PGN strings + optional
profile dicts and returns a rendered report. The CLI wires up data sources.

Usage:
  python -m dossier.report "Smith, John" --pgn games.pgn
  python -m dossier.report "Smith, John" --megabase megabase.db
  python -m dossier.report "Smith, John" --megabase megabase.db --lichess smithj
  python -m dossier.report "Smith, John" --output json
"""

import sys
import json
import argparse
import re
from datetime import date

from analysis.openings import analyse_openings
from analysis.stats import analyse_stats


# ---------------------------------------------------------------------------
# Core (pure, no I/O)
# ---------------------------------------------------------------------------

def build_dossier(player: str, pgn_strings: list[str],
                  profiles: list[dict] | None = None,
                  depth: int = 6, top: int = 8) -> dict:
    """
    Run full analysis and return a structured dossier dict.

    Args:
        player:      Player name as it appears in PGN headers.
        pgn_strings: List of PGN game strings to analyse.
        profiles:    Optional list of online profile dicts
                     (from lookup.lichess / lookup.chesscom).
        depth:       Opening depth in half-moves.
        top:         Max opening lines to include per colour.

    Returns a dict suitable for render_markdown() or render_json().
    """
    stats    = analyse_stats(pgn_strings, player)
    openings = analyse_openings(pgn_strings, player, depth=depth, top=top)

    return {
        "player":   player,
        "profiles": profiles or [],
        "stats":    stats,
        "openings": openings,
        "generated": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_markdown(dossier: dict) -> str:
    player   = dossier["player"]
    stats    = dossier["stats"]
    openings = dossier["openings"]
    profiles = dossier["profiles"]
    generated = dossier["generated"]

    lines = [
        f"# Dossier: {player}",
        f"*Generated {generated} · {stats['total']} games analysed*",
        "",
    ]

    # --- Online profiles ---
    if profiles:
        lines += ["## Online Profiles", ""]
        for p in profiles:
            site = "Lichess" if "lichess" in p.get("url", "") else "chess.com"
            ratings = ", ".join(
                f"{k.capitalize()}: {v}" for k, v in p.get("ratings", {}).items()
            )
            title = f"{p['title']} " if p.get("title") else ""
            flag = " ⚠️ *low-confidence match*" if p.get("confidence") == "low" else ""
            lines.append(f"- **{site}**: [{title}{p['display_name']}]({p['url']})"
                         + (f" — {ratings}" if ratings else "") + flag)
        lines.append("")

    # --- Overview ---
    ov = stats["overall"]
    lines += [
        "## Overview",
        "",
        f"| | White | Black | Overall |",
        f"|---|---|---|---|",
        f"| Games | {stats['as_white']['count']} | {stats['as_black']['count']} | {stats['total']} |",
        f"| Wins  | {stats['as_white']['wins']} | {stats['as_black']['wins']} | {ov['wins']} |",
        f"| Draws | {stats['as_white']['draws']} | {stats['as_black']['draws']} | {ov['draws']} |",
        f"| Losses | {stats['as_white']['losses']} | {stats['as_black']['losses']} | {ov['losses']} |",
        f"| Win % | {stats['as_white']['win_pct']}% | {stats['as_black']['win_pct']}% | {ov['win_pct']}% |",
        "",
        f"**Average game length:** {stats['avg_length']} half-moves",
        "",
    ]

    # --- As White ---
    lines += ["## As White", ""]
    if openings["as_white"]:
        lines += _opening_table(openings["as_white"])
    else:
        lines.append("*No games found as White.*")
    lines.append("")

    # --- As Black ---
    lines += ["## As Black", ""]

    if stats["vs_e4"]:
        lines.append("### vs 1. e4")
        lines.append("")
        lines += _opening_table(stats["vs_e4"])
        lines.append("")

    if stats["vs_d4"]:
        lines.append("### vs 1. d4")
        lines.append("")
        lines += _opening_table(stats["vs_d4"])
        lines.append("")

    if openings["as_black"]:
        lines.append("### All openings as Black")
        lines.append("")
        lines += _opening_table(openings["as_black"])
    else:
        lines.append("*No games found as Black.*")
    lines.append("")

    return "\n".join(lines)


_HTML_CSS = """
body { font-family: Georgia, serif; max-width: 960px; margin: 2rem auto; padding: 0 1.5rem; color: #1a1a1a; }
h1 { border-bottom: 2px solid #333; padding-bottom: .4rem; }
h2 { margin-top: 2rem; color: #2c2c2c; }
h3 { margin-top: 1.2rem; color: #444; }
.meta { color: #666; font-style: italic; margin-bottom: 1.5rem; }
.profiles { list-style: none; padding: 0; }
.profiles li { margin: .3rem 0; }
.warn { color: #c0392b; font-style: italic; }
table { border-collapse: collapse; width: 100%; margin: .8rem 0; font-size: .92rem; }
th { background: #2c3e50; color: #fff; padding: .45rem .7rem; text-align: left; }
td { padding: .35rem .7rem; border-bottom: 1px solid #ddd; }
tr:nth-child(even) td { background: #f7f7f7; }
.line { font-family: monospace; font-size: .85rem; }
.wp-hi  { background: #c8f7c5 !important; }
.wp-mid { background: #fef9c3 !important; }
.wp-lo  { background: #fcd6d6 !important; }
.overview td:first-child { font-weight: bold; }
.section-white { border-left: 4px solid #f0c040; padding-left: .8rem; }
.section-black { border-left: 4px solid #444; padding-left: .8rem; }
@media print { .player-section { page-break-after: always; } }
"""


def render_html(dossier: dict) -> str:
    player = dossier["player"]
    body = _html_player_section(
        player, dossier["stats"], dossier["openings"],
        dossier["profiles"], dossier["generated"]
    )
    return (
        f"<!doctype html><html lang='en'><head>"
        f"<meta charset='utf-8'><title>Dossier: {_esc(player)}</title>"
        f"<style>{_HTML_CSS}</style></head><body>"
        + body + "</body></html>"
    )


def render_html_combined(dossiers: list[dict]) -> str:
    def _nav_item(d):
        pid = _slug_id(d["player"])
        return f"<li><a href='#{pid}'>{_esc(d['player'])}</a></li>"

    nav_links = "".join(_nav_item(d) for d in dossiers)
    nav = f"<nav><h2>Players</h2><ul>{nav_links}</ul></nav><hr>"

    sections = "".join(
        _html_player_section(
            d["player"], d["stats"], d["openings"], d["profiles"], d["generated"],
            anchor=_slug_id(d["player"])
        )
        for d in dossiers
    )
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'><title>Tournament Dossiers</title>"
        f"<style>{_HTML_CSS}</style></head><body>"
        + nav + sections + "</body></html>"
    )


def _slug_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html_player_section(player, stats, openings, profiles, generated, anchor=None) -> str:
    aid = f" id='{anchor}'" if anchor else ""
    ov  = stats["overall"]

    def wp_class(pct):
        if pct >= 55:  return "wp-hi"
        if pct >= 40:  return "wp-mid"
        return "wp-lo"

    # --- profiles ---
    prof_html = ""
    if profiles:
        items = []
        for p in profiles:
            site    = "Lichess" if "lichess" in p.get("url", "") else "chess.com"
            ratings = ", ".join(f"{k.capitalize()}: {v}" for k, v in p.get("ratings", {}).items())
            title   = f"{p['title']} " if p.get("title") else ""
            warn    = " <span class='warn'>⚠ low-confidence match</span>" if p.get("confidence") == "low" else ""
            rat_str = f" — {_esc(ratings)}" if ratings else ""
            items.append(
                f"<li><strong>{site}</strong>: "
                f"<a href='{p['url']}'>{_esc(title)}{_esc(p['display_name'])}</a>"
                f"{rat_str}{warn}</li>"
            )
        prof_html = f"<h2>Online Profiles</h2><ul class='profiles'>{''.join(items)}</ul>"

    # --- overview table ---
    aw, ab = stats["as_white"], stats["as_black"]
    overview = (
        "<h2>Overview</h2>"
        "<table class='overview'>"
        "<tr><th></th><th>White</th><th>Black</th><th>Overall</th></tr>"
        f"<tr><td>Games</td><td>{aw['count']}</td><td>{ab['count']}</td><td>{stats['total']}</td></tr>"
        f"<tr><td>Wins</td><td>{aw['wins']}</td><td>{ab['wins']}</td><td>{ov['wins']}</td></tr>"
        f"<tr><td>Draws</td><td>{aw['draws']}</td><td>{ab['draws']}</td><td>{ov['draws']}</td></tr>"
        f"<tr><td>Losses</td><td>{aw['losses']}</td><td>{ab['losses']}</td><td>{ov['losses']}</td></tr>"
        f"<tr><td>Win %</td>"
        f"<td class='{wp_class(aw['win_pct'])}'>{aw['win_pct']}%</td>"
        f"<td class='{wp_class(ab['win_pct'])}'>{ab['win_pct']}%</td>"
        f"<td class='{wp_class(ov['win_pct'])}'>{ov['win_pct']}%</td>"
        "</tr></table>"
        f"<p><strong>Average game length:</strong> {stats['avg_length']} half-moves</p>"
    )

    # --- as white ---
    white_body = (
        _html_opening_table(openings["as_white"], wp_class)
        if openings["as_white"] else "<p><em>No games found as White.</em></p>"
    )
    as_white = f"<div class='section-white'><h2>As White</h2>{white_body}</div>"

    # --- as black ---
    black_parts = []
    if stats["vs_e4"]:
        black_parts.append(f"<h3>vs 1. e4</h3>{_html_opening_table(stats['vs_e4'], wp_class)}")
    if stats["vs_d4"]:
        black_parts.append(f"<h3>vs 1. d4</h3>{_html_opening_table(stats['vs_d4'], wp_class)}")
    if openings["as_black"]:
        black_parts.append(
            f"<h3>All openings as Black</h3>{_html_opening_table(openings['as_black'], wp_class)}"
        )
    if not black_parts:
        black_parts.append("<p><em>No games found as Black.</em></p>")
    as_black = f"<div class='section-black'><h2>As Black</h2>{''.join(black_parts)}</div>"

    return (
        f"<section class='player-section'{aid}>"
        f"<h1>Dossier: {_esc(player)}</h1>"
        f"<p class='meta'>Generated {_esc(generated)} · {stats['total']} games analysed</p>"
        + prof_html + overview + as_white + as_black
        + "</section>"
    )


def _html_opening_table(rows: list[dict], wp_class) -> str:
    header = "<tr><th>Opening</th><th>Games</th><th>W</th><th>D</th><th>L</th><th>Win%</th></tr>"
    def _row(r):
        cls = wp_class(r["win_pct"])
        return (
            f"<tr><td class='line'>{_esc(r['line'])}</td>"
            f"<td>{r['count']}</td><td>{r['wins']}</td>"
            f"<td>{r['draws']}</td><td>{r['losses']}</td>"
            f"<td class='{cls}'>{r['win_pct']}%</td></tr>"
        )
    return f"<table>{header}{''.join(_row(r) for r in rows)}</table>"


def render_json(dossier: dict) -> str:
    return json.dumps(dossier, indent=2, ensure_ascii=False)


def _opening_table(rows: list[dict]) -> list[str]:
    out = [
        "| Opening | Games | W | D | L | Win% |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['line']}` | {r['count']} | {r['wins']} "
            f"| {r['draws']} | {r['losses']} | {r['win_pct']}% |"
        )
    return out


# ---------------------------------------------------------------------------
# CLI — wires up data sources
# ---------------------------------------------------------------------------

def _load_pgns_from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    return [g for g in re.split(r"\n(?=\[)", content.strip()) if g.strip()]


def _load_pgns_from_megabase(player: str, db_path: str) -> list[str]:
    from megabase.query import get_player_games
    games = get_player_games(player, db_path=db_path)
    print(f"Megabase: {len(games)} game(s) found.", file=sys.stderr)
    return [g["pgn"] for g in games]


def _load_profile_lichess(username: str) -> dict | None:
    try:
        from lookup.lichess import get_profile
        return get_profile(username)
    except Exception as e:
        print(f"Lichess lookup failed: {e}", file=sys.stderr)
        return None


def _load_profile_chesscom(username: str) -> dict | None:
    try:
        from lookup.chesscom import get_profile
        return get_profile(username)
    except Exception as e:
        print(f"chess.com lookup failed: {e}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a chess dossier for an opponent."
    )
    parser.add_argument("player", help="Player name (as in PGN headers)")
    parser.add_argument("--pgn",      metavar="FILE", help="PGN file of games")
    parser.add_argument("--megabase", metavar="DB",   help="SQLite megabase index")
    parser.add_argument("--lichess",  metavar="USER", help="Lichess username for profile")
    parser.add_argument("--chesscom", metavar="USER", help="chess.com username for profile")
    parser.add_argument("--depth", type=int, default=6,
                        help="Opening depth in half-moves (default: 6)")
    parser.add_argument("--top",   type=int, default=8,
                        help="Top N opening lines per colour (default: 8)")
    parser.add_argument("--output", choices=["markdown", "html", "json"], default="html")
    args = parser.parse_args()

    pgn_strings: list[str] = []
    if args.pgn:
        pgn_strings += _load_pgns_from_file(args.pgn)
    if args.megabase:
        pgn_strings += _load_pgns_from_megabase(args.player, args.megabase)

    if not pgn_strings:
        print("Error: provide at least one game source (--pgn or --megabase).",
              file=sys.stderr)
        sys.exit(1)

    print(f"Total: {len(pgn_strings)} game(s) to analyse.", file=sys.stderr)

    profiles: list[dict] = []
    if args.lichess:
        p = _load_profile_lichess(args.lichess)
        if p:
            profiles.append(p)
    if args.chesscom:
        p = _load_profile_chesscom(args.chesscom)
        if p:
            profiles.append(p)

    dossier = build_dossier(args.player, pgn_strings, profiles=profiles,
                            depth=args.depth, top=args.top)

    if args.output == "json":
        print(render_json(dossier))
    elif args.output == "html":
        print(render_html(dossier))
    else:
        print(render_markdown(dossier))


if __name__ == "__main__":
    main()
