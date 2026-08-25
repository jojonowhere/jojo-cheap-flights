# jojo-cheap-flights

A Claude Code skill that finds cheap flights and hidden flight deals by
comparing current Google Flights prices against ~61 days of real historical
pricing for the same route — not just the absolute lowest price.

Built for [JojoNowhere](https://github.com/jojonowhere)'s flight-deal
research workflow. Paired with a published Claude Artifact query-builder UI
("JoJo 便宜機票查詢台").

## What's in here

- **`SKILL.md`** — the skill definition: search modes, workflow, gotchas.
  Load this in Claude Code (`~/.claude/skills/jojo-cheap-flights/`) to
  reproduce the whole system in a fresh session.
- **`scripts/flight_deal_check.py`** — the query engine. Scrapes Google
  Flights via Bright Data's SERP API and reverse-engineers the page's
  embedded data to extract flight offers plus Google's own price-history
  judgment for the route.
- **`references/data-format.md`** — the reverse-engineered data layout in
  full detail (exact field positions, what broke and why).
- **`assets/flight_query_console.html`** — a snapshot of the query-builder
  Artifact UI. The live version is published separately as a Claude Artifact
  and edited directly there; this is a point-in-time copy for reference.

## Setup

Needs a [Bright Data](https://brightdata.com) account with a **SERP API**
zone (not Web Unlocker / Browser API / pre-built Scrapers).

```bash
export BRIGHTDATA_API_KEY="your-api-key"
export BRIGHTDATA_SERP_ZONE="your-zone-name"

python3 scripts/flight_deal_check.py --from TPE --to NRT --depart 2026-10-09 --return 2026-10-16
```

No API keys are committed to this repo.
