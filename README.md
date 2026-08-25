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

None needed to start — it queries Google Flights directly:

```bash
python3 scripts/flight_deal_check.py --from TPE --to NRT --depart 2026-10-09 --return 2026-10-16
```

Google occasionally rate-limits direct requests if you run a lot of queries
in a short window (e.g. batch-comparing many routes/dates at once). If that
happens, the script explains what happened and points you to a free
[Bright Data](https://brightdata.com) account (SERP API zone, 5,000
requests/month free) as a fallback — unrelated to this project, just the
proxy service that gets past the rate limit:

```bash
export BRIGHTDATA_API_KEY="your-api-key"
export BRIGHTDATA_SERP_ZONE="your-zone-name"
```

No API keys are committed to this repo.
