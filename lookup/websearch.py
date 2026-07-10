"""
General web search via the Brave Search API — used to find things no
site-specific API exposes, e.g. "did this opponent appear in any Lichess
broadcast?" (Lichess has no search-by-player-name endpoint for broadcasts).

Requires a Brave Search API key: https://api-dashboard.search.brave.com/
Free tier covers light, occasional use.

Usage:
  python -m lookup.websearch '"Magnus Carlsen" lichess.org/broadcast' --api-key BSA...
"""

import sys
import json
import argparse

import requests

_BASE = "https://api.search.brave.com/res/v1/web/search"


def search(query: str, api_key: str, count: int = 5) -> list[dict]:
    """
    Run a web search. Returns a list of {"title", "url", "description"} dicts.
    Raises requests.HTTPError on an API error (e.g. bad/missing key).
    """
    resp = requests.get(
        _BASE,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("web", {}).get("results", [])
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "description": r.get("description", "")}
        for r in results
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the web via the Brave Search API.")
    parser.add_argument("query")
    parser.add_argument("--api-key", required=True, help="Brave Search API key")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    results = search(args.query, args.api_key, count=args.count)
    print(f"Found {len(results)} result(s).", file=sys.stderr)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
