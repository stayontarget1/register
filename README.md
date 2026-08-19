# The Register

A one-page monitoring board for U.S. National Parks, National Forests and public lands.
Built the same way as The Signal: one static HTML file on GitHub Pages, feeds fetched
in the browser, no framework and no server.

**Live:** https://stayontarget1.github.io/register/

## How it is put together

Most columns fetch client-side on page load, exactly like The Signal. Two things do not:

| Piece | Source | Refresh |
|---|---|---|
| **The Judge** | `data/judge.json`, written by `judge.yml` | 2×/day |
| **THE COUNT** | `data/state.json`, written by `refresh.yml` | every 2h |
| everything else | live in the browser | on load, then every 10 min |

Both JSON files are same-origin, so those columns need no CORS proxy — the most
important column on the page is also the least likely to break.

## The two-layer filter

The NPS alerts endpoint returns ~630 active alerts. Most are not stories.

1. **Rules** (`scripts/sources.py`) cut ~630 → ~70. Pure string matching, free.
   Volume reduction only; rules never decide what matters.
2. **The judge** (`scripts/judge.py`) cuts ~70 → 3–8 using Claude Opus 5, and
   writes a one-line reason why each one matters to a general audience.

The split exists because a regex cannot tell these apart:

> Generals Highway closed — rockfall, crews on scene
> Generals Highway closed — no maintenance staff assigned this season

Same category, same words. Only the second one is news.

## Secrets

Set in **Settings → Secrets and variables → Actions**:

- `NPS_API_KEY` — free, instant: https://www.nps.gov/subjects/developer/get-started.htm
- `ANTHROPIC_API_KEY` — for the judge

Neither key is ever served to the browser.

## Local

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
NPS_API_KEY=... .venv/bin/python scripts/refresh.py
NPS_API_KEY=... ANTHROPIC_API_KEY=... .venv/bin/python scripts/judge.py
python3 -m http.server 8000    # then open http://localhost:8000
```

## Editing

- **Columns** — `RSS_COLUMNS` / `REDDIT_COLUMNS` in `index.html`. A column may merge
  several feeds; sparse sources are merged so the column stays dense.
- **What's Hot queries** — `HOT_QUERIES` in `index.html`.
- **Highlight words** — `HIGHLIGHT_KEYWORDS` in `index.html`.
- **What the judge looks for** — the `SYSTEM` prompt in `scripts/judge.py`.
- **Field Notes** — `fieldnotes.js`, indexed by day of year.
