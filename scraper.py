"""
Scraper for kingregistration.com tournament entry lists.

Usage:
  python scraper.py <tournament_id_or_url> [--output csv|json] [--save-html FILE]

Examples:
  python scraper.py Challenge34
  python scraper.py https://www.kingregistration.com/entrylist/Challenge34
  python scraper.py Challenge34 --output json
  python scraper.py Challenge34 --save-html page.html
"""

import sys
import json
import argparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.kingregistration.com/entrylist"

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
    "full name": "name",
    "rating": "rating",
    "rtng": "rating",
    "uscf rating": "rating",
    "uscf": "uscf_id",
    "uscf id": "uscf_id",
    "id": "uscf_id",
    "section": "section",
    "division": "section",
    "club": "club",
    "state": "state",
    "grade": "grade",
    "school": "school",
}


def resolve_url(tournament: str) -> str:
    if tournament.startswith("http"):
        return tournament
    tournament = tournament.rstrip("/").split("/")[-1]
    return f"{BASE_URL}/{tournament}"


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

        players = []
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            if not cells or all(c == "" for c in cells):
                continue
            players.append(_normalize(dict(zip(headers, cells))))

        if players:
            return players

    # Diagnostic: show what the page actually contains
    print("No player table found. Page text preview:", file=sys.stderr)
    print(soup.get_text(separator="\n", strip=True)[:2000], file=sys.stderr)
    return []


def _normalize(entry: dict) -> dict:
    return {
        _HEADER_MAP.get(k.lower().strip(), k.lower().strip()): v
        for k, v in entry.items()
    }


def scrape_entry_list(tournament: str, save_html: str | None = None) -> list[dict]:
    url = resolve_url(tournament)
    print(f"Fetching: {url}", file=sys.stderr)

    html = fetch_html(url)

    if save_html:
        with open(save_html, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"HTML saved to {save_html}", file=sys.stderr)

    players = parse_entry_list(html)
    print(f"Found {len(players)} player(s).", file=sys.stderr)
    return players


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
        description="Scrape a kingregistration.com tournament entry list.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("tournament", help="Tournament ID (e.g. Challenge34) or full URL")
    parser.add_argument(
        "--output", choices=["csv", "json"], default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--save-html", metavar="FILE",
        help="Save raw HTML to FILE for debugging",
    )
    args = parser.parse_args()

    players = scrape_entry_list(args.tournament, save_html=args.save_html)

    if args.output == "json":
        output_json(players)
    else:
        output_csv(players)


if __name__ == "__main__":
    main()
