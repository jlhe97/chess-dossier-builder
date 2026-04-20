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

## Roadmap

- [x] Step 1 — Scrape tournament entry lists (kingregistration, chessaction)
- [ ] Step 2 — Look up each player on USCF / chess.com / Lichess
- [ ] Step 3 — Fetch recent games per player
- [ ] Step 4 — Analyse openings and tendencies
- [ ] Step 5 — Generate per-opponent dossier report
