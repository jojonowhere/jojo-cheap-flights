# Google Flights' embedded data format (reverse-engineered, 2026-08)

Google Flights' server-rendered HTML embeds its own data as several
`AF_initDataCallback({key: 'ds:N', ..., data: [...]})` blobs. `ds:1` is the one
that matters — it holds both the flight offers and Google's own price-history
judgment for the route. Everything below was found by diffing real captured
pages against what the Google Flights UI displayed for the same query, not by
reading any official documentation (there isn't any — this is unlisted internal
data). Treat field positions as liable to drift if Google changes its frontend;
`flight_deal_check.py`'s `validate_result()` is there specifically to catch
that drift instead of silently returning wrong numbers.

## Locating the blobs

`extract_data_blobs()` regex-matches every `AF_initDataCallback({key: 'ds:N', ...})`
in the raw HTML and JSON-decodes each `data:` payload. `ds:1` isn't always
present in a given fetch — see the retry note in `SKILL.md`.

## Price insights block

Somewhere inside `ds:1` (found by recursive shape-matching in
`_is_price_history_block`, not a fixed top-level index — it moves around) sits
a block shaped like:

```
[
  level_or_null,           # [0] not used
  [null, current_price],   # [1] block[1][1] = current lowest price found
  [null, X],                # [2] unconfirmed meaning
  [null, Y],                # [3] unconfirmed meaning
  [null, typical_low],     # [4] block[4][1] = low end of Google's "typical" band
  [null, typical_high],    # [5] block[5][1] = high end of Google's "typical" band
  level_code,               # [6] 0/1/2 -> low/typical/high (matches the on-page label)
  null, null, null,         # [7..9]
  [[[timestamp_ms, price], ...]],  # [10] block[10][0] = ~61 daily [ts, price] points, trailing up to "yesterday"
  metadata,                  # [11] request/session ids, not useful
  "Destination City Name"    # [12] plain-text confirmation of the destination
]
```

`block[2]` and `block[3]` (`X`/`Y` above) were never fully identified —
cross-checking against the live "$143 cheaper than usual" style banner
suggests one of them may be a raw savings amount, but this wasn't confirmed
with a second real example. Don't rely on them; `block[1]`, `block[4]`,
`block[5]`, `block[6]`, `block[10]` are the ones verified against the live UI
(a real Google Flights price-insights panel, screenshotted and compared
number-for-number).

**Not every route has this block at all.** Low-traffic markets (see the
"sparse routes" note below) can return real flight offers with no
price-insights block anywhere in `ds:1` — `parse_price_insights()` correctly
returns `None` in that case, and the UI must show "資料不足" rather than
inventing a discount percentage.

## What "typical" actually means (researched 2026-08-26)

Google doesn't publish the algorithm — this section separates what's directly
verifiable from what's just travel-blog speculation, so future work doesn't
accidentally treat marketing copy as fact.

**Verified by our own data + corroborated independently:**
- `block[10][0]` (the price-history array) covers roughly the trailing 60
  days, not a year. Independent reporting (FlyerTalk's writeup of the price-
  history graph feature) describes the same ~60-day window and the same
  green/yellow/red "typical band" framing Google's UI uses. This means the
  chart/PR-value math in this skill is comparing the current fare against
  **recent quoted-price history for this specific route (and roughly this
  time of year, since it's the same search), not an explicit multi-year
  seasonal average.**
- Google's own support page (support.google.com/travel/answer/7664728)
  describes the low/high price tips as based on "an analysis of past prices
  of **similar trips**" — deliberately vague, but it does confirm the
  comparison set is trips like this one (same-ish route/season), not a flat
  global average across all routes.

**Plausible but unverifiable (don't repeat as confirmed fact):**
- Various blog posts (mostly SEO content farms, no cited primary source —
  treat mightytravels.com-style "50 ML models" / "10 billion data points" /
  "80% accuracy" claims as unsourced marketing copy, not something to quote
  to users) describe the system as weighing seasonality, day-of-week,
  events, search-volume spikes, and time-to-departure. This is directionally
  plausible (it's consistent with how Farecast/ITA-era fare prediction
  patents like US7974863B2 and US8694346B2 describe similar systems working)
  but none of it is confirmed by Google directly, and there's no public
  documentation of *how much* each factor is weighted.

**Answering the user's specific question** (does "typical" account for
year-over-year, season, peak/off-peak, demand vs supply vs booking-curve
timing?): based on the above, the honest answer is **partially and
opaquely** — the ~60-day rolling window means it's NOT doing an explicit
year-over-year comparison (there's no multi-year data in what we can see),
but because the window is recentered on whatever dates you search, it
implicitly captures *some* seasonality (a July search sees July-ish recent
quotes). Demand/supply/booking-curve weighting is plausible given the
Farecast-lineage patents but not independently verifiable from outside
Google. **Practical implication for this skill:** the PR-value/bell-curve
math here should be described to users as "compared to recent quoted prices
for this route" — not as "compared to this time last year" or "compared to
peak-season averages," since we can't confirm either of those claims.

## Flight offer items

Each bookable itinerary is one node somewhere in `ds:1`'s tree, individually
matching this shape (`_is_flight_item`):

```
item[0] = [
  "IT",                          # [0][0] airline code
  ["Tigerair Taiwan"],           # [0][1] airline name(s)
  [[leg0], [leg1], ...],         # [0][2] one entry per flight segment (2+ = connecting)
  "PVG",                          # [0][3] origin airport code       <- itinerary-level
  [2026, 9, 14],                  # [0][4] departure date            <- itinerary-level
  [21, 40],                       # [0][5] departure time            <- itinerary-level
  "SVO",                          # [0][6] destination airport code  <- itinerary-level
  [2026, 9, 15],                  # [0][7] arrival date               <- itinerary-level
  [19, 15],                       # [0][8] arrival time               <- itinerary-level
  1595,                           # [0][9] TOTAL duration in minutes  <- itinerary-level
  1,                              # [0][10] stop count
  ...
]
item[1] = [[null, price], "booking_token..."]   # item[1][0][1] = price
```

**Use `item[0][3..10]`, never a leg's own fields, for the itinerary summary.**
A leg only describes its own segment — on a connecting itinerary, `leg[8]`
(that segment's departure time) and `leg[11]` (that segment's duration) are
NOT the same as the whole trip's departure time and total duration. This
looked fine in early testing only because the test route happened to be
nonstop (leg-level == itinerary-level when there's exactly one leg), and it
silently produced wrong times/durations the first time a connecting-heavy
route (China → Russia) was queried. `[hour, minute]` pairs sometimes arrive as
a single-element `[hour]` when minutes are 0 (protobuf drops default/zero
values) — `_fmt_time()` pads that back.

**Collect items by testing each node individually, not as a group.** The
early version required an entire sibling list to pass the shape check before
accepting any of it (`all(_is_flight_item(x) for x in list)`), on the theory
that flight items come in clean same-shaped arrays. In practice, Google
sometimes puts a genuinely price-less duplicate entry (a repeated departure
time variant with no separate fare shown) in the same array as several
perfectly valid, differently-priced items — and the `all()` check threw the
whole group away, valid entries included. This is why a real page with 25
visible flights parsed as "0 offers" the first few times a new route was
tried. `_collect_flight_items()` walks the entire tree and keeps whatever
individually matches, which is slower to explain but doesn't have this
failure mode.

## Fetching reliably

- Bright Data SERP API request body needs `"data_format": "html"` — `"format": "raw"` alone returns a small metadata-only stub, not the actual page.
- The URL needs `&gl=us` appended (fast-flights' own URL builder doesn't add
  this). Without it, Google frequently serves a page where `ds:1` is declared
  in the page's `AF_initDataKeys` manifest but never actually populated — the
  real data was meant to load async, client-side, after first paint, and a
  plain HTTP fetch never runs that JS. This was the single biggest reliability
  fix found in testing (`gl=tw` was *not* re-validated — don't swap it in
  without re-testing the same way `gl=us` was validated, since the async-page
  problem could reappear for a different geo value).
- Even with `gl=us`, it's not 100% deterministic — retry a handful of times
  with a short sleep between attempts if the first fetch comes back with an
  empty/missing `ds:1`.
- Currency (`curr=TWD` etc) is independent of `gl`/`hl` and safe to change on
  its own — it doesn't affect page structure, only the number Google reports.
