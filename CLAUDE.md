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
  websearch.py     → SearXNG JSON API client (general web search via a self-hosted instance, no account/key)
  broadcasts.py    → find Lichess broadcast (relay) games for a player via websearch.py,
                     since Lichess has no "search broadcasts by player" API
  uscf.py          → look up a tournament entrant's real FIDE nationality via their public
                     USCF record — a resolver confidence signal, not a game/profile source
analysis/          → PGN strings → opening repertoire + tendency stats
dossier/           → all of the above → rendered Markdown/HTML/JSON report
  report.py        → build_dossier() + render_markdown()/render_html()/render_json()
  db.py            → SQLite history of dossiers across repeated scans (separate from megabase —
                     this one is written to on every run, not built once and read-only)
pipeline/          → end-to-end orchestrator: tournament → dossier folder
  resolver.py      → name → (username, confidence, score, reasons) for Lichess and chess.com
  runner.py        → run_pipeline(): scrape → resolve → fetch (5 sources) → build → write
```

### Data flow

1. `scraper.scrape_entry_list(tournament, site)` → `list[dict]` of players with name, rating, section etc.
2. `megabase.query.get_player_games(name, db_path)` → `list[dict]` each with a `pgn` key
3. `lookup.lichess.search(name)` / `lookup.chesscom.find_profile(name)` → profile dicts; `get_games()` / `games_as_pgn()` / `get_studies()` + `get_study_pgn()` → PGN strings
4. `lookup.broadcasts.find_games(name, searxng_url)` → PGN strings from Lichess broadcast rounds mentioning the player (optional, needs a SearXNG instance)
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
- **Games with no public URL get a local interactive board** — `pipeline.runner._ensure_game_links` collects them into one games-browser page per player at `<output_dir>/games/<slug>/index.html` (game list + click-to-load traversable board, pieces from `python-chess`'s bundled Cburnett SVG set — the same one Lichess's default theme uses) and injects a `GameURL` header pointing at that game's anchor; a real `GameURL`/`Link`/`Site` URL already on the PGN (Lichess, chess.com, Lichess broadcasts) is left alone. The same viewer (`_games_browser_html`) also powers the high-confidence "recent games" pages (see below) and supports flipping the board and attaching a client-side engine.
- **The games-browser viewer runs a real UCI engine client-side** — a Web Worker loaded from a CDN (Stockfish, an asm.js build, attached by default; a custom UCI-engine script URL is also accepted) drives an eval bar/number and a highlighted suggested move, updating as the user steps through the game. `new Worker(crossOriginUrl)` is rejected outright by browsers (`SecurityError`) regardless of CORS headers, so the worker is actually a same-origin `Blob` script that calls `importScripts(url)` — which *does* follow CORS. This only works for self-contained, single-file engine builds: one that fetches a separate `.wasm` file via a path relative to its own script location breaks, because that location is now the `blob:` URL, not the CDN directory (confirmed empirically against jsdelivr's WASM/NNUE Stockfish builds, which fail this way — the older asm.js build, with everything inlined, does not).
- **Lichess broadcasts can't be searched by player name** — only by broadcast/tournament title (`/api/broadcast/search`) or by organizer username (`/api/broadcast/by/{username}`, not useful for a competitor). `lookup.broadcasts` works around this via a general web search (self-hosted SearXNG — see below) for `"<name>" lichess.org/broadcast`, then fetches whatever round(s) it finds.
- **Neither Lichess nor chess.com lets you search accounts by real name** — confirmed directly: Lichess's `/api/player/autocomplete?term=Magnus+Carlsen` returns unrelated `Magnus5`/`Magnus-`/etc. accounts (pure username prefix-matching, real-name field never searched), and chess.com's own site-search endpoint (`/callback/user/search`, internally named `web_user_callback_username_search`) is also username-only *and* requires an authenticated session (401 without one). This is a hard platform limitation on both sides, not a gap in this codebase — a general web search is the only way to bridge "real name" → "unrelated username."
- **Web search is via a self-hosted SearXNG instance, not a hosted API** — `lookup.websearch.search(query, searxng_url)` hits `<searxng_url>/search?format=json`. Chosen over a hosted option (e.g. Brave Search API) specifically to avoid an account-signup-plus-credit-card requirement (Brave requires a card even on its free tier, for identity verification) and per-query cost; the tradeoff is you host and maintain the instance yourself. JSON output must be enabled in the instance's `settings.yml` (`search: formats: [html, json]`) — it's off by default, including on public instances, for anti-abuse reasons, which is also why public instances can't just be pointed at directly (they 403/429 unauthenticated JSON requests in practice).
- **Name-search queries must be natural-order and unquoted, with a bare domain** — `lookup.lichess.find_usernames_via_search` / `lookup.chesscom.find_usernames_via_search` reformat the entry-list "Last, First" name to natural "First Last" order (`_natural_name_order`) before searching, and build a query like `Marek Antoni Kowalski chess.com` — no exact-phrase quotes, no path fragment (`chess.com/member`, `lichess.org/@`). Confirmed empirically against a real unresolved case: exact-phrase-quoting the raw "Last, First" order finds nothing (real pages essentially never contain that literal comma-ordered substring), and separately, appending a path fragment as query text also kills recall on real search backends (it gets tokenized as unrelated keyword noise, e.g. "member", rather than treated as a URL/site hint) — either mistake alone is enough to silently miss an otherwise easily-findable profile page. A single self-hosted SearXNG instance backed by only 1–2 actually-responding engines (others get rate-limited/CAPTCHA'd quickly on a non-residential IP) is also inherently a bit flaky call-to-call — a miss is worth a retry before concluding the account isn't findable.
- **chess.com username guessing must drop middle names** — `lookup.chesscom.guess_usernames` splits `"Last, First Middle"` on the comma, but without trimming to just the first given name, every guess embeds a literal space (e.g. `"john derekheinichen"`) and 404s, silently breaking chess.com matching for anyone with a middle name on the entry list.
- **Resolver confidence factors in games played, not just name/rating/country** — `pipeline.resolver._composite_score` adds an optional games-count signal (`lookup.lichess`/`lookup.chesscom` `_slim_profile` expose `games_count`); more games raises confidence, scaling up to `_GAMES_FOR_FULL_SCORE` (50). Separately, `_confidence_for` hard-caps confidence at `"low"` when games_count is known and below `_MIN_GAMES_FOR_HIGH` (5) — a same-name account with almost no games is too thin a sample to call "high confidence" even if name/rating/country line up perfectly, since that combination is just as consistent with a different person. chess.com's `_slim_profile` also now exposes `real_name` (the account's real-name field, distinct from `display_name` which falls back to the username) — a genuine real-name match is direct evidence and can upgrade a late, generic-looking username guess to high confidence, the same way Lichess's `real_name` already could.
- **A catastrophic rating mismatch is a second hard confidence cap, not just a scoring penalty** — a real production case exposed this: a generic guess ("john") landed on an unrelated stranger's real chess.com account, and because that account happened to have thousands of games and a "preferred" country, the composite score nearly reached "high" despite an ~1000-point rating gap. `_composite_score` now also returns `rating_ok` (`False` only when a rating comparison was actually made *and* came back fully clamped to the 0.0 floor — i.e. the gap is at or past `_RATING_TOLERANCE`), and `_confidence_for` caps confidence at `"low"` whenever `rating_ok` is `False`, the same way it does for a too-small `games_count`. No rating data at all leaves `rating_ok` `True` — absence of evidence isn't evidence of a mismatch.
- **chess.com guessing scores every guess and keeps the best, not just the first hit** — previously `_resolve_chesscom_by_guessing` stopped at the first guess that resolved to a real profile, so a weak, coincidental match early in the guess list (e.g. a bare first name colliding with an unrelated stranger) could block a later, better-evidenced guess from ever being considered. Now it behaves like the Lichess candidate-ranking path: try every guess, score each hit, keep the best.
- **The country signal cross-checks actual FIDE nationality instead of assuming every entrant is US-based** — `lookup.uscf.get_fide_country(uscf_id)` scrapes the public USCF MSA member page (`https://www.uschess.org/msa/MbrDtlMain.php?<uscf_id>`, keyed by the `uscf_id` already scraped from the entry list — no name-based lookup or disambiguation needed) for the entrant's "FIDE Country" field, and converts the FIDE/IOC-style 3-letter federation code to ISO alpha-2 via `_FIDE_TO_ISO2` for comparison against a candidate's Lichess/chess.com country. `pipeline.runner.run_pipeline` looks this up once per player and passes it to both resolvers as `fide_country`; `_country_score` uses it when available and only falls back to the old blanket "US-preferred" heuristic when it isn't (most club-level players have no FIDE record at all). This was a real, demonstrated bug, not a hypothetical: the blanket US bias caused a correct non-US candidate with a 0.90 real-name match to lose to a wrong US candidate with only a 0.65 match, purely because of nationality — confirmed fixed against live data (score flipped from 0.75-wrong/0.64-correct to 0.53-wrong/0.93-correct once the actual FIDE nationality was known).

### Supported tournament sites

| Site | `--site` flag | URL pattern |
|---|---|---|
| kingregistration.com | `kingregistration` (default) | `/entrylist/<id>` |
| chessaction.com | `chessaction` | `/tournaments/advance_entry_list.php?tid=<id>` |

Full URLs are auto-detected; `--site` is only needed for ID shorthands.

### Step 6 pipeline details

`pipeline/resolver.py`:
- `_strip_title(name)` — strips a leading FIDE/USCF title
- `resolve_lichess(name, rating=None, searxng_url=None, fide_country=None)` / `resolve_chesscom(name, rating=None, searxng_url=None, fide_country=None)` → `(username, "high"|"low"|None, score, reasons)` — each candidate is scored on name/handle similarity (weight 0.5), rating closeness to `rating` when available (weight 0.3, tighter tolerance if it's a FIDE rating rather than an online blitz/rapid one), account country (weight 0.2 — see below), and games played (weight 0.2, scaling up to `_GAMES_FOR_FULL_SCORE`); missing signals are dropped from the weighted average rather than penalising the candidate. `score >= 0.55` → high, `>= 0.30` → low, else rejected (`None`) — except `_confidence_for` overrides a would-be "high" down to "low" when the account has fewer than `_MIN_GAMES_FOR_HIGH` games on record, or when the rating comparison came back catastrophically bad (`rating_ok is False`), regardless of score (see the games-count and rating-mismatch design notes above).
  - Lichess: scores all `search()` candidates (cheap name-only pass first, then fetches full profiles for just the top 2)
  - chess.com: scores every guess that resolves to a real profile and keeps the best, on guess specificity ("firstlast" vs. a bare "first" stands in for name similarity, since every guess is mechanically derived from the name) *unless* the account has a genuine `real_name` on file, in which case an actual name-similarity score is used instead when it's higher
  - Both: if `searxng_url` is given and the above didn't already reach high confidence, also try `find_usernames_via_search()` (SearXNG search for the player's name plus `lichess.org` or `chess.com`) and keep whichever candidate scores best — catches a personalized handle with no relation to the player's name (e.g. Magnus Carlsen's real Lichess account is the pseudonymous `DrNykterstein`), findable only via the account's linked real name, which neither Lichess's username-only autocomplete nor any mechanical chess.com guess would ever surface. **This path needs a running SearXNG instance** (`$SEARXNG_URL` / `--searxng-url`) — without one, personalized handles like this are simply not discoverable; see the SearXNG setup note below.

`pipeline/runner.py`:
- `run_pipeline(tournament, ...)` — full orchestration; returns `list[Path]` of written files
- Per player, pulls games from up to 5 sources in order: megabase, Lichess games, Lichess studies, chess.com games, Lichess broadcasts (only if `searxng_url` is set)
- Writes `<output_dir>/<slug>.{html,md,json}` per player and a combined file in html/markdown mode
- `exclude` filters the scraped player list by substring before the loop (e.g. to skip your own entry)
- `match_score`/`match_reasons` from the resolver get injected into each profile dict alongside `confidence`, and rendered next to it in the report
- `_ensure_game_links()` runs on the collected PGNs (html/markdown modes only) before `build_dossier()`, generating the per-player games-browser page for anything without a public URL
- **Recent-games viewer for high-confidence matches** — when a Lichess or chess.com match is `"high"` confidence, `_recent_games_page()` builds a dedicated openingtree-style games-browser page (`<output_dir>/games/<slug>/recent_lichess.html` / `recent_chesscom.html`) restricted to games from the last calendar year (`_RECENT_GAMES_DAYS`), fetched independently of the smaller pool used for opening analysis (`_fetch_lichess_recent_games` uses Lichess's `since` filter directly; chess.com always pulls a dedicated 12-month archive regardless of `--chesscom-months`). The profile dict gets `recent_games_url`/`recent_games_count`, rendered as a link next to that profile in the report. Low-confidence matches deliberately skip this — their game history may belong to someone else.
- **Optional cross-scan database** — pass `dossier_db=<path>` (CLI: `--dossier-db`) to also save every dossier via `dossier.db.save_dossier()`, building a queryable history across repeated scans (see below). Off by default; failures are logged and don't abort the run.

`dossier/db.py` (opt-in, off by default):
- SQLite schema: one `scans` row per (tournament, day) `run_pipeline()` call, one `dossiers` row per player per scan (stats/openings/profiles stored as JSON columns)
- `save_dossier(db_path, tournament, site, dossier)` — upserts; rerunning the same tournament the same day updates that day's row instead of duplicating, but a later rescan (or a different tournament) starts a new scan and keeps history
- `player_history(db_path, player)` / `latest_dossier(db_path, player)` — matched by name slug (`pipeline.runner._slug`-equivalent), so formatting differences in how the name is written don't create phantom separate players
- CLI: `python -m dossier.db --db dossiers.db history "Smith, John"` / `... scans [--tournament NAME]`

### Roadmap

- Steps 1–6 are complete, including MegaDatabase integration.
- Also done: game-count-aware and rating-mismatch-aware match confidence, chess.com guessing scores every candidate instead of stopping at the first hit, web search migrated from Brave to self-hosted SearXNG, a client-side engine (Stockfish by default, off until toggled, top-3 multi-PV lines) with flip/eval/best-move in an enlarged/centered games viewer, a recent-games (last 12 months) viewer for high-confidence matches, and an opt-in cross-scan dossier database.
- Verified against real data across two real tournament scans: with the games-count and rating-mismatch hard caps in place, a wrong-but-plausible candidate (large game count, matching country, but a rating far outside tolerance) correctly stays at *low* confidence instead of being fooled by the incidental signals; separately, fixing the search query construction (natural name order, unquoted, bare domain — see above) took a personalized-handle case that the guess-only path could never reach from "not discoverable at all" to "found, high confidence" once SearXNG was live, though a single self-hosted instance with only 1–2 reliably-responding engines means recall on any one query isn't 100% consistent call to call.
- Remaining: combined PDF output. A SearXNG instance still needs a card-free signup-free way to run reliably 24/7 outside a sandbox with no Docker (the one used for development here was run from source in a venv — see the setup note below) — and even with search enabled, it depends on which upstream engines happen to be responding, so this stays a best-effort discovery path, not a guarantee.

### SearXNG setup

Self-host an instance to enable `--searxng-url` (personalized-handle discovery + Lichess broadcast search):

```bash
docker run -d --name searxng -p 8080:8080 \
  -v "$(pwd)/searxng-config:/etc/searxng" \
  searxng/searxng
```

On first run, it writes a default `searxng-config/settings.yml` — edit it to add:

```yaml
search:
  formats:
    - html
    - json
```

...then restart the container (`docker restart searxng`). Verify with:

```bash
curl "http://localhost:8080/search?q=test&format=json"
```

Point the pipeline at it with `--searxng-url http://localhost:8080` or `$SEARXNG_URL`.
