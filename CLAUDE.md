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
megabase/          → one-time SQLite index of ChessBase PGN export → game PGNs by name
lookup/            → Lichess + chess.com API → online profiles + game PGNs
analysis/          → PGN strings → opening repertoire + tendency stats
dossier/           → all of the above → rendered Markdown/JSON report
pipeline/          → end-to-end orchestrator: tournament → dossier folder
  resolver.py      → name → (username, confidence) for Lichess and chess.com
  runner.py        → run_pipeline(): scrape → resolve → fetch → build → write
```

### Data flow

1. `scraper.scrape_entry_list(tournament, site)` → `list[dict]` of players with name, rating, section etc.
2. `megabase.query.get_player_games(name, db_path)` → `list[dict]` each with a `pgn` key
3. `lookup.lichess.search(name)` / `lookup.chesscom.find_profile(name)` → profile dicts; `get_games()` / `games_as_pgn()` → PGN strings
4. `analysis.openings.analyse_openings(pgn_strings, player)` + `analysis.stats.analyse_stats(pgn_strings, player)` → dicts
5. `dossier.report.build_dossier(player, pgn_strings, profiles)` → dossier dict; `render_markdown()` / `render_json()` → string output

### Key design decisions

- **All analysis functions are pure** — they accept `list[str]` (PGN strings) and return dicts. No I/O. CLIs and `dossier/report.py` handle all sourcing.
- **Player name matching is case-insensitive substring** — `"smith"` matches `"Smith, John"`. This applies in both `scraper._HEADER_MAP` normalisation and `megabase.query` SQL `LIKE` queries.
- **`scraper.parse_entry_list`** requires at least one recognised column header from `_HEADER_MAP` before accepting a table, to skip nav/layout tables.
- **chess.com has no search API** — `lookup.chesscom.guess_usernames(name)` generates candidates from `Last, First` / `First Last` patterns and `find_profile()` tries each until one resolves.
- **Lichess rate limiting** — `lookup.lichess` sleeps 1s before game fetch requests.
- **megabase index** is built once from a ChessBase PGN export (`python -m megabase.indexer mega.pgn`) and then queried read-only.

### Supported tournament sites

| Site | `--site` flag | URL pattern |
|---|---|---|
| kingregistration.com | `kingregistration` (default) | `/entrylist/<id>` |
| chessaction.com | `chessaction` | `/tournaments/advance_entry_list.php?tid=<id>` |

Full URLs are auto-detected; `--site` is only needed for ID shorthands.

### Step 6 pipeline details

`pipeline/resolver.py`:
- `_similarity(a, b)` — case-insensitive `SequenceMatcher` ratio on normalised strings
- `resolve_lichess(name)` → `(username, "high"|"low"|None)` — calls `lookup.lichess.search()`, scores top result against player name; `>=0.55` → high, `>=0.30` → low
- `resolve_chesscom(name)` → `(username, "high"|"low"|None)` — tries `guess_usernames()` patterns; first 2 hits → high, later → low

`pipeline/runner.py`:
- `run_pipeline(tournament, ...)` — full orchestration; returns `list[Path]` of written files
- Writes `<output_dir>/<slug>.md` per player and `combined.md` in markdown mode
- Low-confidence profiles get `"confidence": "low"` injected before being passed to `build_dossier()`

### Roadmap

- Steps 1–6 are complete.
- Remaining: MegaDatabase integration into Step 6, combined PDF output.
