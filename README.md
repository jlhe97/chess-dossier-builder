# Chess Dossier Builder

Build opponent dossiers for players registered in the same chess tournament.

## Step 1 — Scrape tournament entry lists

`scraper.py` fetches a **kingregistration.com** entry list and returns the registered players as CSV or JSON.

### Install

```bash
pip install -r requirements.txt
```

### Usage

```bash
# by tournament ID
python scraper.py Challenge34

# by full URL
python scraper.py https://www.kingregistration.com/entrylist/Challenge34

# JSON output
python scraper.py Challenge34 --output json

# save raw HTML for debugging unknown page layouts
python scraper.py Challenge34 --save-html page.html
```

### Output

**CSV (default)**
```
"name","rating","uscf_id","section","club","state"
"Smith, John","1850","12345678","Open","Metro Chess Club","NY"
...
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
  },
  ...
]
```

### Generalising to other tournaments

Any `kingregistration.com/entrylist/<id>` URL works.  Column headers are
normalised automatically; unknown headers are passed through lowercased.

If a tournament uses a non-standard layout, run with `--save-html page.html`
to inspect the rendered HTML and identify the selector to add.

### Piping output

```bash
# save to file
python scraper.py Challenge34 > entries.csv
python scraper.py Challenge34 --output json > entries.json

# quick preview with jq
python scraper.py Challenge34 --output json | jq '.[].name'
```

## Roadmap

- [ ] Step 2 — Look up each player on USCF / chess.com / Lichess
- [ ] Step 3 — Fetch recent games per player
- [ ] Step 4 — Analyse openings and tendencies
- [ ] Step 5 — Generate per-opponent dossier report
