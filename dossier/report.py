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
    parser.add_argument("--output", choices=["markdown", "json"], default="markdown")
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
    else:
        print(render_markdown(dossier))


if __name__ == "__main__":
    main()
