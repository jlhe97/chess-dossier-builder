# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_scraper.py -v

# Run a single test
pytest tests/test_scraper.py::TestParseEntryList::test_kingregistration_columns -v
```

## Architecture

The project is a **pipeline** that builds chess opponent dossiers from tournament entry lists. Each package is a self-contained pipeline stage:

```
scraper.py         → fetch entry list from tournament site → player names
pgnutil.py         → shared PGN text utility (split_pgn_games) used by every stage below
megabase/          → one-time SQLite index of ChessBase PGN export → game PGNs by name
lookup/            → Lichess + chess.com + web search → online profiles + game PGNs
  lichess.py       → search/profile/games/studies via the Lichess API
  chesscom.py      → username guessing + profile/games via the chess.com API
  websearch.py     → Brave Search API client (general web search, needs an API key)
  broadcasts.py    → find Lichess broadcast (relay) games for a player via websearch.py,
                     since Lichess has no "search broadcasts by player" API
analysis/          → PGN strings → opening repertoire + tendency stats
dossier/           → all of the above → rendered Markdown/HTML/JSON report
pipeline/          → end-to-end orchestrator: tournament → dossier folder
  resolver.py      → name → (username, confidence, score, reasons) for Lichess and chess.com
  runner.py        → run_pipeline(): scrape → resolve → fetch (5 sources) → build → write
```

### Data flow

1. `scraper.scrape_entry_list(tournament, site)` → `list[dict]` of players with name, rating, section etc.
2. `megabase.query.get_player_games(name, db_path)` → `list[dict]` each with a `pgn` key
3. `lookup.lichess.search(name)` / `lookup.chesscom.find_profile(name)` → profile dicts; `get_games()` / `games_as_pgn()` / `get_studies()` + `get_study_pgn()` → PGN strings
4. `lookup.broadcasts.find_games(name, brave_api_key)` → PGN strings from Lichess broadcast rounds mentioning the player (optional, needs an API key)
5. `analysis.openings.analyse_openings(pgn_strings, player)` + `analysis.stats.analyse_stats(pgn_strings, player)` → dicts, each opening-line row carrying a capped list of the underlying games (with a URL when one exists)
6. `dossier.report.build_dossier(player, pgn_strings, profiles)` → dossier dict; `render_markdown()` / `render_html()` / `render_json()` → string output

### Key design decisions

- **All analysis functions are pure** — they accept `list[str]` (PGN strings) and return dicts. No I/O. CLIs and `dossier/report.py`/`pipeline/runner.py` handle all sourcing.
- **Player name matching is token-based, not plain substring** — `analysis.openings._name_matches(player, header_name)` requires every word in `player` to appear somewhere in `header_name`, so a tournament entry's truncated/compound surname (e.g. "Lagrave, Maxime" vs. a PGN's "Vachier-Lagrave, Maxime") still matches, while a same-surname different-person doesn't. `megabase.query.get_player_games` does the equivalent as ANDed SQL `LIKE` clauses, one per name token.
- **Tournament entry names get title-stripped before any matching** — `pipeline.resolver._strip_title` removes a leading FIDE/USCF title ("GM Vachier-Lagrave, Maxime" → "Vachier-Lagrave, Maxime"); titles never appear in PGN headers or usernames and would otherwise poison every downstream match.
- **Never split multi-game PGN text with a `\n(?=\[)` regex** — it splits between every header *line*, not between games, since a normal header block has no blank lines between tags. Always use `pgnutil.split_pgn_games()`, which round-trips through `chess.pgn.read_game()`.
- **`scraper.parse_entry_list`** requires at least one recognised column header from `_HEADER_MAP` before accepting a table, to skip nav/layout tables.
- **chess.com has no search API** — `lookup.chesscom.guess_usernames(name)` generates candidates from `Last, First` / `First Last` patterns and `find_profile()` tries each until one resolves.
- **Lichess rate limiting** — `lookup.lichess` sleeps 1s before game fetch requests. Its autocomplete endpoint is `/api/player/autocomplete` (not `/api/users/autocomplete`, which 404s) and 400s on a literal comma in the search term — `search()` strips it.
- **Lichess profile enrichment is opt-in per candidate** — `/api/player/autocomplete` returns no rating/country/real-name data, only `/api/user/{username}` does, and only if the account owner filled it in. `resolve_lichess` fetches the full profile for just the top 2 name-ranked candidates to bound request volume.
- **megabase index** is built once from a ChessBase PGN export (`python -m megabase.indexer mega.pgn`) and then queried read-only.
- **megabase name matching is whole-word, not substring, at the SQL level** — `megabase.query.get_player_games` wraps White/Black in comma delimiters and matches `LIKE '%,token,%'`; a bare `LIKE '%token%'` matches a short token *inside* an unrelated word (e.g. `"an"` inside `"Anderson"`), which on an 11M-game database turns one token into millions of false positives, not a rare edge case. Passing `rating` additionally drops candidates whose matched side's Elo (read straight from the PGN's `WhiteElo`/`BlackElo`) is more than `rating_tolerance` points off — even whole-word matching can't disambiguate two different real people who share a common name.
- **Games with no public URL get a local interactive board** — `pipeline.runner._ensure_game_links` collects them into one games-browser page per player at `<output_dir>/games/<slug>/index.html` (game list + click-to-load traversable board, pieces from `python-chess`'s bundled Cburnett SVG set — the same one Lichess's default theme uses) and injects a `GameURL` header pointing at that game's anchor; a real `GameURL`/`Link`/`Site` URL already on the PGN (Lichess, chess.com, Lichess broadcasts) is left alone.
- **Lichess broadcasts can't be searched by player name** — only by broadcast/tournament title (`/api/broadcast/search`) or by organizer username (`/api/broadcast/by/{username}`, not useful for a competitor). `lookup.broadcasts` works around this via a general web search (Brave Search API) for `"<name>" lichess.org/broadcast`, then fetches whatever round(s) it finds.

### Supported tournament sites

| Site | `--site` flag | URL pattern |
|---|---|---|
| kingregistration.com | `kingregistration` (default) | `/entrylist/<id>` |
| chessaction.com | `chessaction` | `/tournaments/advance_entry_list.php?tid=<id>` |

Full URLs are auto-detected; `--site` is only needed for ID shorthands.

### Step 6 pipeline details

`pipeline/resolver.py`:
- `_strip_title(name)` — strips a leading FIDE/USCF title
- `resolve_lichess(name, rating=None, search_api_key=None)` / `resolve_chesscom(name, rating=None, search_api_key=None)` → `(username, "high"|"low"|None, score, reasons)` — each candidate is scored on name/handle similarity (weight 0.5), rating closeness to `rating` when available (weight 0.3, tighter tolerance if it's a FIDE rating rather than an online blitz/rapid one), and account country (weight 0.2, "US-preferred" since both supported tournament sites are US-based); missing signals are dropped from the weighted average rather than penalising the candidate. `score >= 0.55` → high, `>= 0.30` → low, else rejected (`None`).
  - Lichess: scores all `search()` candidates (cheap name-only pass first, then fetches full profiles for just the top 2)
  - chess.com: stops at the first guess that resolves to a real profile (guess specificity — "firstlast" vs. a bare "first" — stands in for name similarity, since every guess is mechanically derived from the name)
  - Both: if `search_api_key` is given and the above didn't already reach high confidence, also try `find_usernames_via_search()` (Brave Search for `"<name>" lichess.org/@` or `chess.com/member`) and keep whichever candidate scores best — catches a personalized handle with no relation to the player's name (e.g. Magnus Carlsen's real Lichess account is the pseudonymous `DrNykterstein`), findable only via the account's linked real name, which neither Lichess's username-only autocomplete nor any mechanical chess.com guess would ever surface

`pipeline/runner.py`:
- `run_pipeline(tournament, ...)` — full orchestration; returns `list[Path]` of written files
- Per player, pulls games from up to 5 sources in order: megabase, Lichess games, Lichess studies, chess.com games, Lichess broadcasts (only if `search_api_key` is set)
- Writes `<output_dir>/<slug>.{html,md,json}` per player and a combined file in html/markdown mode
- `exclude` filters the scraped player list by substring before the loop (e.g. to skip your own entry)
- `match_score`/`match_reasons` from the resolver get injected into each profile dict alongside `confidence`, and rendered next to it in the report
- `_ensure_game_links()` runs on the collected PGNs (html/markdown modes only) before `build_dossier()`, generating the per-player games-browser page for anything without a public URL

### Roadmap

- Steps 1–6 are complete, including MegaDatabase integration.
- Remaining: combined PDF output.
