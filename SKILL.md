---
name: jojo-cheap-flights
description: Find cheap flights and hidden flight deals by comparing current Google Flights prices against ~61 days of real historical pricing for the same route, not just absolute lowest price. Use this whenever the user asks to find cheap flights, check if a flight price is a good deal, search flights across flexible dates/origins/destinations, or mentions the "JoJo 便宜機票查詢台" / "JoJoNowhere" flight tool — even if they just paste a JSON query spec from that tool without further explanation, or ask things like "幫我查機票", "這張票貴不貴", "有沒有比較便宜的時候", "查一下這個航線的優惠". Also use it if the user wants to update or debug the flight_query_console.html Artifact or the flight_deal_check.py script themselves.
---

# JoJo 便宜機票查詢台 — cheap flight deal finder

This skill reproduces a system built and validated (through real, live queries
against Google Flights) over a long conversation. Read this whole file before
touching anything — most of what's here exists because an earlier attempt
looked reasonable but was quietly wrong, and re-discovering that costs real
API calls and real time. `references/data-format.md` has the byte-level
detail; this file is the workflow and the "don't do this" list.

## What this system does, and why it's not just "lowest price"

The point isn't finding the absolute cheapest ticket — it's finding when a
price is a **genuine discount relative to that route's own recent history**.
Google Flights already computes this for every search (a "low / typical /
high" judgment plus ~61 days of daily price history) and embeds it in the
page; nobody exposes it as a clean API, so this system scrapes it directly
(details in `references/data-format.md`).

Three search modes, all expressible as "which of {time, origin, destination}
is fixed vs. flexible, with price/discount as the thing you're optimizing":

1. **Fixed origin + destination, flexible dates** → cheapest date(s) to fly a specific route.
2. **Fixed origin, narrow fixed dates, flexible/free-text destination** → whichever destination is most discounted vs. its own history *right now* (the user describes the destination in plain language — "東南亞", "有雪的地方" — Claude interprets it, not a dropdown).
3. **Fixed destination (or a short list of destination airports), flexible/open origin** → cheapest city to depart from (radius-based: same metro's other airports / same country / neighboring region).

## The two pieces and how they talk to each other

**`flight_query_console.html`** — a published Artifact (title "JoJo 便宜機票查詢台"), the query-builder UI. Four cards (時間/價格/起站/終點站), each field maps onto one of the three modes above. It builds a JSON spec and a plain-language summary, which the user copies with the page's own copy button and pastes into chat.

**`scripts/flight_deal_check.py`** (bundled copy in this skill; canonical copy lives wherever the user last worked on it — check both, prefer whichever is newer) — the actual query engine. Fetches Google Flights through Bright Data's SERP API, parses the embedded data, computes the discount-vs-history numbers, and self-validates its own output.

**The Artifact cannot call Bright Data itself.** No Artifact runtime capability lets a published page hold a secret API key and call an external service with it — embedding the key in the page would leak it to anyone with the link, and no capability grants a safe server-side proxy for arbitrary external APIs. So the actual query always runs through *you* (Claude, via Bash), reading the pasted spec, calling the script, and then editing the Artifact's `RESULTS_HISTORY` array with the real results and republishing. This is a hard platform constraint, not a shortcut you're missing — don't try to make the button call Bright Data directly.

### The workflow, end to end

1. User fills the Artifact (or just describes a trip in chat — the spec format is simple enough to build by hand for a one-off query).
2. You get either a pasted JSON spec or a plain-language request. Either way, translate it into concrete `(origin, destination, date)` triples to query. Ambiguity is common here (a "country"-scope origin like "上海/country" technically means "every airport in China" — that's usually not what's wanted; ask or use judgment, e.g. limit to major cities with actual connectivity, and say what you assumed).
3. Set `BRIGHTDATA_API_KEY` and `BRIGHTDATA_SERP_ZONE` env vars (ask the user if not already known/persisted — see "Setup" below) and run `flight_deal_check.py` for each combination, or write a small batch loop calling its functions directly (`build_flights_url`, `fetch_raw_html`, `parse_flights`, `parse_price_insights`, `validate_result`) when querying many combinations — see the script's own `check_deal()` for the pattern.
4. **Actually read `validate_result()`'s output.** It's not decoration — it's what caught the itinerary-vs-leg field bug and the all-or-nothing item-collection bug during development. If it flags something, don't report the numbers as-is; go check a live browser render of the same query before trusting the scrape.
5. Report results honestly, including gaps — see "Sparse routes" below.
6. Edit `RESULTS_HISTORY` in `flight_query_console.html` (push a new entry, don't overwrite — see "Artifact rules" below) and republish via the Artifact tool.

## Setup — none needed to start

`fetch_raw_html()` tries a **plain direct HTTP request first** (a normal
browser User-Agent, no API key, no proxy) — this succeeds on its own most of
the time for occasional queries. Confirmed empirically: a cold direct request
from a fresh environment got the full page with real data on the first try.
Don't ask the user to set up Bright Data before even attempting a query — the
setup cost only exists for the fallback path, and most single-route or
small-batch queries never need it at all.

**When direct fetch actually fails** (Google rate-limits after enough
requests in a short window — this mostly shows up on larger batches, e.g.
15-40+ requests for a multi-city/multi-date search), the script raises
`FetchBlocked` with a ready-to-show explanation: what happened (temporary
rate limit, not a problem with their account/computer), and how to get a free
Bright Data account (SERP API zone, 5,000 requests/month free) as a fallback —
worded to make clear this is a third-party free service unrelated to
JojoNowhere, not something she benefits from. Surface that message as-is
rather than writing your own; it was worded carefully after the user
specifically asked not to come across as pushing people to sign up for
something for her benefit.

If Bright Data credentials end up needed: the user has an account with a
**SERP API** zone (not Web Unlocker, not Browser API, not the pre-built
Scrapers — those don't return the page structure this needs). Check
`~/.bash_profile` for `BRIGHTDATA_API_KEY` / `BRIGHTDATA_SERP_ZONE` (persisted
there earlier) before asking the user to dig up the key again.

## Gotchas (see `references/data-format.md` for the why and the exact byte layout)

- Bright Data request body needs `"data_format": "html"`, not just `"format": "raw"`.
- The Google Flights URL needs `&gl=us` or you'll silently get an empty/incomplete page most of the time. Retry a few times with a short sleep as a fallback even with `gl=us` — it's not fully deterministic.
- Use itinerary-level fields (`item[0][3..10]`) for date/time/duration, never leg-level — leg-level is only correct for nonstop flights and quietly wrong for connections.
- Collect flight items by testing each node individually, not by requiring a whole sibling group to pass together — one price-less duplicate entry in a group can otherwise wipe out every valid sibling.
- Default currency is `TWD` (this user is Taiwan-based); keep `gl=us` / `hl=en` as-is when changing currency — those were validated for reliability and currency doesn't need them changed.

## Sparse routes: report gaps, don't paper over them

Low-traffic or sanctioned/limited-service markets (verified case: China →
Russia routes during the ongoing airspace-sanctions period) often return real
flight prices with **no price-insights block at all** — Google simply hasn't
got enough search volume to compute a historical distribution — and some
specific date/origin combinations legitimately have zero flights that day
(weekly-frequency schedules, not a scraping failure — cross-check a couple
with a live browser render before assuming it's a bug). Show these states
explicitly in results: "資料不足,無法算優惠%" for a real price with no
discount data, "查無班機資料" for a genuine zero-result query — never invent a
discount percentage or silently drop a row to make the table look complete.
If you suspect real-world causes (sanctions, safety reroutes, seasonal
schedules) rather than a scraping bug, a quick web search to confirm is
worth it — it changes what you tell the user and whether it's worth debugging
further.

## Artifact rules (`flight_query_console.html`)

These exist because each one was a real bug the user caught and made you fix
— don't regress them when editing the page.

1. **Results accumulate, never overwrite.** `RESULTS_HISTORY` is an array; push new entries, don't replace it. The newest renders expanded at the top; older ones collapse into a `<details class="history-fold">` at the bottom, each with its own 🗑 delete button (tracked in `localStorage` under `jojo_flight_console_dismissed_v1`) so the page doesn't grow forever.
2. **Render results from data, not bespoke markup per query.** `resultBlockHtml(data)` takes `{title, subtitle, searched_at, threshold_pct, currency, rows: [{origin_code, date, price, discount_pct, airline, search_url}]}` and produces the whole block. Updating results after a real query means pushing a new object, not redesigning the HTML or drawing new charts each time.
3. **`discount_pct`, `price`, and `airline` can all be legitimately absent** on a row (see "Sparse routes"). The renderer already sorts discount-available rows first, then priced-but-no-discount, then no-data-at-all, and shows the right pill text for each — don't "simplify" this back down to assuming every row has a clean discount number.
4. **Form field values persist via `localStorage`** (`jojo_flight_console_form_v1`), restored on load so a republish doesn't wipe out whatever the user was filling in.
5. **"Output was already shown" persists independently of "form fields have saved values."** These are two different `localStorage` keys, checked independently. Gating the output/copy-button restoration behind the form-fields key existing was a real shipped bug — a user who clicks 查詢 without ever touching a field (very plausible, given the sensible defaults) would see the copy button "disappear" after every republish, because the whole restore function returned early before reaching the output-restore check. If you touch `restoreFormState()`, test exactly that path: clear `localStorage`, click 查詢 without editing anything, reload, confirm the copy button is still there.
6. **Every result row needs an actual date (MM/DD + weekday) and a direct Google Flights link** (`search_url`, from `build_flights_url()`) so the user can independently verify/book — a bare "airline + historical average" row isn't scannable or actionable enough on its own.
7. Prices display with a `fmtPrice(price, currency)` helper — `currency: "TWD"` shows as-is, `currency: "USD"` gets converted at a fixed rate constant (labeled in the UI as an approximation, not a live rate) for older entries fetched before the TWD default was added. New queries should just fetch in TWD directly rather than relying on the conversion.
8. **Every row needs to show its destination, not just its origin** — the table didn't have a 終點站 column at all until a real user complaint ("查詢複數終點站的時候，我就不知道我終點站去哪裏"). When a query has more than one destination (multi-destination `range`/`explore` mode), give each row its own `dest_code`; when the whole entry is a single fixed destination (the common single-route case), set `dest_default_code` once on the entry object instead of repeating it on every row. The renderer's `destCode(r)` helper checks the row first and falls back to the entry default — don't remove that fallback, it's what keeps the three older single-destination history entries rendering correctly without editing every one of their rows.
9. **The table always displays sorted by price, ascending — not by discount%/PR.** An earlier version sorted discount-available rows first (by discount desc), then priced-no-discount rows (by price asc), then no-data rows — this mixed two different orderings in one table and a real user found it confusing ("排序順序要是從低價開始排，因爲你這裏顯得有些亂"). The "which row is the best deal" logic (the takeaway banner + bell-curve chart) is computed separately from `data.rows` sorted by discount/PR — it does NOT follow the display row order anymore, so don't accidentally couple them back together. The best-deal row is still highlighted in the table via `r === best` object-identity comparison, wherever it happens to fall in the price-sorted list.
10. **Rows carry optional `dep_time`/`arr_time`/`duration_min` fields** (from `FlightOffer.dep_time`/`.arr_time`/`.duration_min` — already computed by `check_deal()`/the batch pattern, just wire them into the row dict when building `RESULTS_HISTORY` rows). `dep_time` renders as a small line under the 起站 cell, `arr_time` renders under 終點站 — both origin/destination airports' own local time, not converted to a shared timezone. `duration_min` (via `fmtDuration()`) gets **its own column between 起站 and 終點站**, not appended onto either cell — an earlier version crammed "抵達時間・飛行 X小時Y分" into the narrow 終點站 cell and it wrapped across 3 lines, unreadable; a real user caught this ("起站跟終點站中間只有一個飛行時數"). These are all optional; rows without them (e.g. the three older history entries, captured before this field existed) just show `—` in the duration column and no time line under origin/destination, no special-casing needed. **When adding a field to one `<td>`, double-check it actually lands inside that `<td>`'s template-literal string** — a real mistake during development put a new span after the closing `</td>` instead of before it, which renders as a stray text node next to the cell instead of inside it, easy to miss without opening the actual rendered table.
11. **Keep the discount/insufficient-data pill text short.** `資料不足` (not the longer `資料不足，無法算優惠%`) for priced-but-no-insights rows — the extra words don't add information (the column header + the row already make clear this is about the discount%), and a real user flagged the longer version as making the column too wide. The 相對歷史均價 column also has `width: 1%` in CSS specifically to shrink-wrap to its (now short) content instead of taking a fixed/flexible share of the table's width.

## Files

- `scripts/flight_deal_check.py` — the query engine (bundled copy; check for a more recently edited working copy first).
- `references/data-format.md` — full reverse-engineered data layout, the exact fields, and the reasoning behind each gotcha above.
- `assets/flight_query_console.html` — a snapshot of the Artifact as of 2026-08-28 (includes PR-value framing, the bell-curve chart, and the one-time welcome banner). **The published Artifact is the live source of truth, not this file** — it gets edited directly in conversation and republished, and this snapshot won't stay in sync. Published at `https://claude.ai/code/artifact/08ac05bc-9841-4ea1-8c59-bccc6e19b22e` — update that same URL (pass it as `url` to the Artifact tool) rather than creating a new artifact, and ask the user to confirm this is still the right link if it's been a while.
