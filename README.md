# Chess Dossier Builder

[![CI](https://github.com/jlhe97/chess-dossier-builder/actions/workflows/ci.yml/badge.svg)](https://github.com/jlhe97/chess-dossier-builder/actions/workflows/ci.yml)

Build opponent dossiers for players registered in the same chess tournament.

## Step 1 — Scrape tournament entry lists

`scraper.py` fetches a tournament entry list and returns the registered players as CSV or JSON.

**Supported sites**
| Site | URL pattern |
|---|---|
| [kingregistration.com](https://www.kingregistration.com) | `/entrylist/<id>` |
| [chessaction.com](https://chessaction.com) | `/tournaments/advance_entry_list.php?tid=<id>` |

### Install

```bash
pip install -r requirements.txt
```

### Tests

```bash
pytest tests/ -v
```

All tests run offline using HTML fixtures — no network required.

### Usage

**kingregistration.com** (default)
```bash
python scraper.py Challenge34
python scraper.py Challenge34 --output json
python scraper.py https://www.kingregistration.com/entrylist/Challenge34
```

**chessaction.com**
```bash
python scraper.py nKGioA== --site chessaction
python scraper.py "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="
```

When a full URL is passed, `--site` is auto-detected and can be omitted.

**All flags**
```
python scraper.py <tournament> [--site kingregistration|chessaction]
                               [--output csv|json]
                               [--save-html FILE]
```

| Flag | Default | Description |
|---|---|---|
| `--site` | `kingregistration` | Site to use for ID shorthands |
| `--output` | `csv` | Output format: `csv` or `json` |
| `--save-html FILE` | — | Save the raw HTML for debugging |

### Output

**CSV (default)**
```
"name","rating","uscf_id","section","club","state"
"Smith, John","1850","12345678","Open","Metro Chess Club","NY"
```

**JSON**
```json
[
  {
    "name": "Smith, John",
    "rating": "1850",
    "uscf_id": "12345678",
    "section": "Open",
    "club": "Metro Chess Club",
    "state": "NY"
  }
]
```

Column headers are normalised automatically across both sites
(e.g. `"Rtng"`, `"Pre-Rating"`, `"USCF Rating"` all map to `"rating"`).
Unknown headers are passed through lowercased.

### Piping output

```bash
python scraper.py Challenge34 > entries.csv
python scraper.py Challenge34 --output json | jq '.[].name'
```

### Debugging an unknown layout

If the scraper prints `No player table found`, run with `--save-html` and
inspect the HTML to identify the right selector to add:

```bash
python scraper.py Challenge34 --save-html page.html
```

## Step 2 — ChessBase MegaDatabase

Export the MegaDatabase from ChessBase once (**File → Export → Export Database as PGN**),
then build a local SQLite index for fast per-player lookups.

### Build the index (once)

```bash
python -m megabase.indexer mega.pgn
python -m megabase.indexer mega.pgn --db /data/megabase.db   # custom path
```

Streams the PGN — never loads the whole file into memory. Progress is printed every 10,000 games.

### Query by player name

```bash
python -m megabase.query "Kasparov, Garry"
python -m megabase.query "Kasparov, Garry" --output json
python -m megabase.query "Kasparov" --limit 50              # partial name match
python -m megabase.query "Kasparov, Garry" --db /data/megabase.db
```

Returns PGN (default) or JSON. Matching is case-insensitive and covers both White and Black.

### Python API

```python
from megabase.query import get_player_games

games = get_player_games("Kasparov, Garry", db_path="megabase.db")
for game in games:
    print(game["event"], game["date"], game["result"])
    print(game["pgn"])
```

## Step 3 — Online profile lookup (Lichess & chess.com)

Given a player name from the tournament entry list, find their online profiles and fetch recent games.

### Lichess

```bash
# Search by name → candidate usernames
python -m lookup.lichess search "Magnus Carlsen"

# Fetch profile by known username
python -m lookup.lichess profile DrNykterstein

# Fetch recent games (PGN or JSON)
python -m lookup.lichess games DrNykterstein
python -m lookup.lichess games DrNykterstein --max 20 --output json
python -m lookup.lichess games DrNykterstein --perf classical
```

### chess.com

chess.com has no public search endpoint. Use `find` to try common username
patterns derived from the player name, or `profile` if the username is known.

```bash
# Guess username from name and try each candidate
python -m lookup.chesscom find "Carlsen, Magnus"

# Fetch profile by known username
python -m lookup.chesscom profile MagnusCarlsen

# Fetch recent games (last 3 months by default)
python -m lookup.chesscom games MagnusCarlsen
python -m lookup.chesscom games MagnusCarlsen --months 6 --output json
```

### Python API

```python
from lookup.lichess import search, get_games
from lookup.chesscom import find_profile, games_as_pgn

# Lichess
candidates = search("Smith, John")  # returns list of profile dicts
pgn = get_games("username", max=50)

# chess.com
profile = find_profile("Smith, John")  # tries username guesses, returns first match
pgn = games_as_pgn("username", months=3)
```

## Step 4 — Opening and tendency analysis

Given a list of PGN strings and a player name, produces a full opening
repertoire breakdown and broad tendency statistics.

### Opening repertoire

```bash
python -m analysis.openings games.pgn "Smith, John"
python -m analysis.openings games.pgn "Smith, John" --depth 8 --top 10
```

Output (JSON):
```json
{
  "as_white": [
    {"line": "1. e4 e5 2. Nf3 Nc6 3. Bb5", "count": 18, "wins": 10, "draws": 5, "losses": 3, "win_pct": 55.6}
  ],
  "as_black": [
    {"line": "1. e4 c5 2. Nf3 d6 3. d4 cxd4", "count": 12, "wins": 6, "draws": 4, "losses": 2, "win_pct": 50.0}
  ]
}
```

### Tendency statistics

```bash
python -m analysis.stats games.pgn "Smith, John"
```

Output (JSON):
```json
{
  "total": 50,
  "as_white": {"count": 27, "wins": 14, "draws": 8, "losses": 5, "win_pct": 51.9},
  "as_black": {"count": 23, "wins": 10, "draws": 9, "losses": 4, "win_pct": 43.5},
  "overall":  {"wins": 24, "draws": 17, "losses": 9, "win_pct": 48.0},
  "avg_length": 38.4,
  "vs_e4": [...],
  "vs_d4": [...]
}
```

### Python API

```python
from analysis.openings import analyse_openings
from analysis.stats import analyse_stats

pgn_strings = [game["pgn"] for game in games]  # from megabase or lookup

openings = analyse_openings(pgn_strings, "Smith, John", depth=6, top=10)
stats    = analyse_stats(pgn_strings, "Smith, John")
```

## Step 5 — Dossier report

Ties the full pipeline together into a single Markdown or JSON report per opponent.

```bash
# From a PGN file
python -m dossier.report "Smith, John" --pgn games.pgn

# From the MegaDatabase index
python -m dossier.report "Smith, John" --megabase megabase.db

# Both sources combined, with online profiles
python -m dossier.report "Smith, John" \
  --megabase megabase.db \
  --lichess smithj \
  --chesscom JohnSmith99 \
  --output markdown > smith_john.md

# JSON output (for further processing)
python -m dossier.report "Smith, John" --megabase megabase.db --output json
```

### Sample output

```markdown
# Dossier: Smith, John
*Generated 2026-04-21 · 50 games analysed*

## Online Profiles
- **Lichess**: [jsmith](https://lichess.org/@/jsmith) — Rapid: 1750, Blitz: 1700

## Overview
| | White | Black | Overall |
|---|---|---|---|
| Games | 27 | 23 | 50 |
| Win % | 55.6% | 43.5% | 50.0% |

## As White
| Opening | Games | W | D | L | Win% |
|---|---|---|---|---|---|
| `1. e4 e5 2. Nf3 Nc6 3. Bb5` | 18 | 10 | 5 | 3 | 55.6% |

## As Black
### vs 1. e4
| Opening | Games | W | D | L | Win% |
|---|---|---|---|---|---|
| `1. e4 c5 2. Nf3 d6 3. d4 cxd4` | 10 | 5 | 3 | 2 | 50.0% |
```

### Python API

```python
from dossier.report import build_dossier, render_markdown

pgn_strings = [game["pgn"] for game in megabase_games]
profiles    = [lichess_profile, chesscom_profile]

dossier = build_dossier("Smith, John", pgn_strings, profiles=profiles)
print(render_markdown(dossier))
```

## Step 6 — End-to-end pipeline

One command turns a tournament URL into a folder of per-opponent dossiers.

```bash
# By tournament ID (kingregistration.com, default)
python -m pipeline.runner Challenge34

# By full URL (site auto-detected)
python -m pipeline.runner "https://chessaction.com/tournaments/advance_entry_list.php?tid=nKGioA=="

# Pull historical games from a local MegaDatabase index too
python -m pipeline.runner Challenge34 --megabase megabase.db

# Custom output directory and game limits
python -m pipeline.runner Challenge34 --output-dir ./dossiers --max-games 30 --chesscom-months 6

# Skip your own entry, or anyone else's, by (partial) name
python -m pipeline.runner Challenge34 --exclude "Smith, John"

# JSON output (no combined file, no local game-view pages)
python -m pipeline.runner Challenge34 --format json
```

**All flags**
```
python -m pipeline.runner <tournament>
    [--site kingregistration|chessaction]
    [--output-dir DIR]        default: dossiers/
    [--max-games N]           Lichess games to fetch per player (default: 50)
    [--chesscom-months N]     chess.com history window in months (default: 3)
    [--megabase DB]           SQLite megabase index to pull historical games from
    [--megabase-limit N]      cap games pulled from the megabase per player (default: no limit)
    [--no-lichess-studies]    skip pulling games from the opponent's own public Lichess studies
    [--search-api-key KEY]    Brave Search API key — enables finding opponent games in Lichess
                               broadcasts, plus Lichess/chess.com accounts with a personalized
                               handle no guess could find (default: $BRAVE_API_KEY; omit to skip)
    [--depth N]               opening depth in half-moves (default: 6)
    [--top N]                 top N opening lines per colour (default: 8)
    [--format markdown|html|json]  output format (default: html)
    [--exclude NAME]          skip players whose name contains this text (repeatable)
```

**Output**
```
dossiers/
  smith_john.html    ← one file per opponent
  doe_jane.html
  combined.html       ← all dossiers with nav (html/markdown modes only)
  games/
    smith_john/       ← one games-browser page per player, for games with no
      index.html          public URL (i.e. from the megabase — Lichess/chess.com/
                           broadcast games link straight to the real game instead)
```

Every opening-table row links out to the underlying games. A real Lichess/chess.com
game already has a public URL; anything else (mainly megabase games) links into that
player's `games/<slug>/index.html` — a game list on the left, and clicking one loads
it onto a board on the right that you step through with Prev/Next, arrow keys, or by
clicking any move. Self-contained, no external JS — pieces are the same "Cburnett" SVG
set Lichess's default board theme uses (bundled with `python-chess`), not font glyphs.

Name→handle matches are scored on more than raw name similarity — rating closeness to
the tournament entry (and FIDE rating, when a Lichess profile has one linked) and
account country all factor in, with the reasoning shown next to each match. When a
Brave Search API key is set, an account with a personalized handle unrelated to the
player's name (unguessable and unfindable by Lichess's own username-only search) can
still be found and scored the same way, via its linked real name instead:
```
## Online Profiles
- **Lichess**: [xyz99](https://lichess.org/@/xyz99) (34% match) ⚠️ *low-confidence match*
```

### Python API

```python
from pipeline.runner import run_pipeline

paths = run_pipeline("Challenge34", output_dir="dossiers", max_games=50, megabase="megabase.db")
# returns list of Path objects for written files
```

## Roadmap

- [x] Step 1 — Scrape tournament entry lists (kingregistration, chessaction)
- [x] Step 2 — Index ChessBase MegaDatabase for fast player lookups
- [x] Step 3 — Look up each player on Lichess and chess.com
- [x] Step 4 — Analyse openings and tendencies
- [x] Step 5 — Generate per-opponent dossier report
- [x] Step 6 — End-to-end pipeline
  - Single command: tournament URL → dossiers for every opponent
  - Name → handle resolver scores candidates on name/handle similarity, rating
    closeness (online and/or FIDE), and account country; picks the best match
    and shows the reasoning, not just a bare confidence label — with a Brave
    Search API key, it can also find accounts with a personalized handle no
    guess or username-only search would ever surface
  - Pulls games from Lichess, chess.com, the local MegaDatabase, the opponent's
    own public Lichess studies, and (with a Brave Search API key) Lichess
    broadcasts they appeared in
  - Clickable links from every opening line to the actual game — a real
    Lichess/chess.com/broadcast URL, or a self-contained interactive replay
    board (Lichess's own SVG piece set) for anything without one
  - Output: folder of HTML (or Markdown/JSON) files, one per opponent, + combined view
  - [ ] Combined PDF output
- [ ] Step 7 — Pre-game preparation agent
  - `agent/coach.py` calls Claude (Anthropic SDK) with the structured dossier dict as input
  - System prompt: chess coach persona; task is to produce concrete prep notes from the statistics
  - Output: a **Preparation Notes** section injected into the HTML dossier with:
    - Recommended opening to play as White vs this opponent (based on their worst scoring Black defences)
    - Recommended opening to play as Black vs this opponent (based on their worst scoring White systems)
    - Key tendencies to exploit (e.g. weak endgame conversion, time pressure patterns, avoids long games)
  - Runs as a post-processing pass in `run_pipeline` or standalone: `python -m agent.coach smith_john.html`
  - Operates on the **statistical layer only** — no move calculation, so hallucination risk is low
- [ ] Step 8 — Interactive game walkthrough coach
  - Hybrid Stockfish + Claude agent for replaying an opponent's past games move by move
  - Stockfish (via `python-chess` engine interface) evaluates each position and identifies critical moments
  - Claude narrates in plain English, grounding its commentary in the dossier: "They played the Nimzo-Indian again — consistent with their repertoire. Stockfish prefers Bg5; note they've scored 2W/5L when White plays Bg5 early."
  - Step-through CLI (`python -m agent.walkthrough smith_john.html --pgn game.pgn`) or interactive REPL
  - Batches Claude calls every N moves to keep API cost low
  - Complements Maia Chess (human-move prediction) for deeper understanding of why an opponent chooses a line
