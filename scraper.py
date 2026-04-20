"""
Scraper for kingregistration.com tournament entry lists.
Usage: python scraper.py <tournament_id_or_url> [--output csv|json] [--save-html FILE]

Examples:
  python scraper.py Challenge34
  python scraper.py https://www.kingregistration.com/entrylist/Challenge34
  python scraper.py Challenge34 --output json
  python scraper.py Challenge34 --save-html page.html   # dump rendered HTML for debugging
"""

import os
import sys
import json
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://www.kingregistration.com/entrylist"

# Pre-installed Chromium path (takes priority if the default build is missing)
_CHROMIUM_CANDIDATE = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

# Map common column header variants to canonical field names
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


def scrape_entry_list(tournament: str, headless: bool = True,
                      save_html: str | None = None) -> list[dict]:
    url = resolve_url(tournament)
    print(f"Fetching: {url}", file=sys.stderr)

    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": headless}
        if os.path.exists(_CHROMIUM_CANDIDATE):
            launch_kwargs["executable_path"] = _CHROMIUM_CANDIDATE

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        try:
            page.wait_for_selector(
                "table, .entry-list, [class*='entry'], [class*='player']",
                timeout=15_000,
            )
        except PlaywrightTimeoutError:
            print("Warning: timed out waiting for table — parsing whatever loaded.",
                  file=sys.stderr)

        # Let JS finish any deferred rendering
        page.wait_for_timeout(1_500)

        if save_html:
            with open(save_html, "w", encoding="utf-8") as fh:
                fh.write(page.content())
            print(f"HTML saved to {save_html}", file=sys.stderr)

        players = _extract_players(page)
        browser.close()

    print(f"Found {len(players)} player(s).", file=sys.stderr)
    return players


def _extract_players(page) -> list[dict]:
    # Strategy 1: standard <table> with header row
    for table in page.query_selector_all("table"):
        rows = table.query_selector_all("tr")
        if len(rows) < 2:
            continue
        headers = _cells(rows[0])
        if not headers:
            continue
        players = []
        for row in rows[1:]:
            cells = _cells(row)
            if not cells or all(c == "" for c in cells):
                continue
            players.append(_normalize(dict(zip(headers, cells))))
        if players:
            return players

    # Nothing matched — show a diagnostic preview
    print("No table found. Page text preview:", file=sys.stderr)
    print(page.inner_text("body")[:2000], file=sys.stderr)
    return []


def _cells(row) -> list[str]:
    return [el.inner_text().strip() for el in row.query_selector_all("th, td")]


def _normalize(entry: dict) -> dict:
    return {
        _HEADER_MAP.get(k.lower().strip(), k.lower().strip()): v
        for k, v in entry.items()
    }


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
    parser = argparse.ArgumentParser(description="Scrape a kingregistration.com entry list.")
    parser.add_argument("tournament", help="Tournament ID (e.g. Challenge34) or full URL")
    parser.add_argument("--output", choices=["csv", "json"], default="csv",
                        help="Output format (default: csv)")
    parser.add_argument("--visible", action="store_true",
                        help="Run browser in visible (non-headless) mode for debugging")
    parser.add_argument("--save-html", metavar="FILE",
                        help="Save the rendered page HTML to FILE for debugging")
    args = parser.parse_args()

    players = scrape_entry_list(
        args.tournament,
        headless=not args.visible,
        save_html=args.save_html,
    )

    if args.output == "json":
        output_json(players)
    else:
        output_csv(players)


if __name__ == "__main__":
    main()
