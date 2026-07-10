"""
General web search via a self-hosted SearXNG instance — used to find things
no site-specific API exposes, e.g. "did this opponent appear in any Lichess
broadcast?" (Lichess has no search-by-player-name endpoint for broadcasts),
or a personalized online handle with no relation to a player's real name
(neither Lichess's nor chess.com's own search covers real names — see
pipeline/resolver.py's module docstring).

Requires a running SearXNG instance (https://docs.searxng.org/) with the
JSON output format enabled — disabled by default, including on public
instances, for anti-abuse reasons. In your instance's settings.yml:

  search:
    formats:
      - html
      - json

Self-hosting (e.g. `docker run -p 8080:8080 searxng/searxng`) avoids the
account/credit-card signup and per-query cost of a hosted search API.

Usage:
  python -m lookup.websearch '"Magnus Carlsen" lichess.org/broadcast' --searxng-url http://localhost:8080
"""

import sys
import json
import argparse

import requests


def search(query: str, searxng_url: str, count: int = 5) -> list[dict]:
    """
    Run a web search against a SearXNG instance. Returns a list of
    {"title", "url", "description"} dicts. Raises requests.HTTPError on
    an API error (e.g. JSON format not enabled) and requests.ConnectionError
    if the instance isn't reachable.
    """
    resp = requests.get(
        f"{searxng_url.rstrip('/')}/search",
        headers={"Accept": "application/json"},
        params={"q": query, "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])[:count]
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "description": r.get("content", "")}
        for r in results
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the web via a self-hosted SearXNG instance.")
    parser.add_argument("query")
    parser.add_argument("--searxng-url", required=True,
                        help="Base URL of your SearXNG instance, e.g. http://localhost:8080")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    results = search(args.query, args.searxng_url, count=args.count)
    print(f"Found {len(results)} result(s).", file=sys.stderr)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
