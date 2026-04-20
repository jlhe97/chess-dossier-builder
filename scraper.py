"""
Scrape tournament entry lists from kingregistration.com or chessaction.com.

Usage:
  python scraper.py <tournament> [--site kingregistration|chessaction] [--output csv|json]

Tournament can be:
  - A tournament ID shorthand  (resolved using --site, default: kingregistration)
  - A full URL                 (site auto-detected; --site flag ignored)

Examples:
  python scraper.py Challenge34
  python scraper.py Challenge34 --output json
  python scraper.py nKGioA== --site chessaction
  python scraper.py https://www.kingregistration.com/entrylist/Challenge34
  python scraper.py "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="
  python scraper.py Challenge34 --save-html page.html
"""

import sys
import json
import argparse

import requests
from bs4 import BeautifulSoup


# --- Site URL templates ---------------------------------------------------

_SITES = {
    "kingregistration": "https://www.kingregistration.com/entrylist/{tid}",
    "chessaction":      "https://chessaction.com/tournaments/advance_entry_list.php?tid={tid}",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Normalise common column header variants to canonical field names
_HEADER_MAP = {
    "name": "name",
    "player": "name",
    "player name": "name",
    "full name": "name",
    "last, first": "name",
    "rating": "rating",
    "rtng": "rating",
    "uscf rating": "rating",
    "pre-rating": "rating",
    "pre rating": "rating",
    "uscf": "uscf_id",
    "uscf id": "uscf_id",
    "uscf#": "uscf_id",
    "id": "uscf_id",
    "section": "section",
    "division": "section",
    "club": "club",
    "team": "club",
    "state": "state",
    "grade": "grade",
    "school": "school",
    "city": "city",
}


# --- URL resolution -------------------------------------------------------

def _detect_site(url: str) -> str | None:
    for site in _SITES:
        if site in url:
            return site
    return None


def resolve_url(tournament: str, site: str = "kingregistration") -> str:
    if tournament.startswith("http"):
        return tournament
    tid = tournament.rstrip("/").split("/")[-1]
    return _SITES[site].format(tid=tid)


# --- Fetching & parsing ---------------------------------------------------

def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_entry_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
        if not headers:
            continue

        # Skip tables with no recognised chess columns (e.g. nav/layout tables)
        known = {_HEADER_MAP.get(h.lower().strip()) for h in headers} - {None}
        if not known:
            continue

        players = []
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            if not cells or all(c == "" for c in cells):
                continue
            players.append(_normalize(dict(zip(headers, cells))))

        if players:
            return players

    print("No player table found. Page text preview:", file=sys.stderr)
    print(soup.get_text(separator="\n", strip=True)[:2000], file=sys.stderr)
    return []


def _normalize(entry: dict) -> dict:
    return {
        _HEADER_MAP.get(k.lower().strip(), k.lower().strip()): v
        for k, v in entry.items()
    }


# --- Main entry point -----------------------------------------------------

def scrape_entry_list(tournament: str, site: str = "kingregistration",
                      save_html: str | None = None) -> list[dict]:
    # Auto-detect site from full URLs so --site flag is optional
    if tournament.startswith("http"):
        detected = _detect_site(tournament)
        if detected:
            site = detected

    url = resolve_url(tournament, site)
    print(f"[{site}] Fetching: {url}", file=sys.stderr)

    html = fetch_html(url)

    if save_html:
        with open(save_html, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"HTML saved to {save_html}", file=sys.stderr)

    players = parse_entry_list(html)
    print(f"Found {len(players)} player(s).", file=sys.stderr)
    return players


# --- Output helpers -------------------------------------------------------

def output_csv(players: list[dict]) -> None:
    if not players:
        print("No data.")
        return
    headers = list(players[0].keys())
    print(",".join(f'"{h}"' for h in headers))
    for player in players:
        print(",".join(f'"{player.get(h, "")}"' for h in headers))


def output_json(players: list[dict]) -> None:
    print(json.dumps(players, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Scrape a chess tournament entry list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("tournament", help="Tournament ID shorthand or full URL")
    parser.add_argument(
        "--site", choices=list(_SITES), default="kingregistration",
        help="Site to scrape (default: kingregistration). Ignored when a full URL is given.",
    )
    parser.add_argument(
        "--output", choices=["csv", "json"], default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--save-html", metavar="FILE",
        help="Save raw HTML to FILE for debugging",
    )
    args = parser.parse_args()

    players = scrape_entry_list(args.tournament, site=args.site, save_html=args.save_html)

    if args.output == "json":
        output_json(players)
    else:
        output_csv(players)


if __name__ == "__main__":
    main()
