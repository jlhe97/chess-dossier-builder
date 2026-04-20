# Chess Dossier Builder

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

## Roadmap

- [x] Step 1 — Scrape tournament entry lists (kingregistration, chessaction)
- [x] Step 2 — Index ChessBase MegaDatabase for fast player lookups
- [x] Step 3 — Look up each player on Lichess and chess.com
- [ ] Step 4 — Analyse openings and tendencies
- [ ] Step 5 — Generate per-opponent dossier report
