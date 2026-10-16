# Rarebird Forecast

A daily "weather forecast" for rare birds: where a rarity is most likely
to be *reported* on eBird in the next week, and which species to watch for.

Vagrancy is chaotic, so this doesn't claim precision — it scores each
hotspot on explainable signals:

- **History** — how often rarities have been reported at the site
  ("rarity magnets" stay magnets)
- **Seasonality** — week-of-year vs. historical notable reports in the
  site's latitude band (spring/autumn pulses, hemisphere-aware)
- **Recent activity** — rarities within 50 km in the last 14 days
- **Observer effort** — rarity *reports* need birders; heavily-watched
  sites report more
- **Weather** — sustained strong winds during peak weeks, storms
  (Open-Meteo 7-day forecast)
- **Wind fingerprints** — `fingerprints.py` learns which wind directions
  historically preceded rarity arrivals in each country×latitude band
  (Open-Meteo archive: 48h before each arrival vs. the local baseline).
  Forecast days whose dominant wind matches get boosted; opposed winds
  dampen. Shown in the header ("arrival winds: SE") and popup notes.

Data comes from the eBird API's *notable observations* endpoint — the
same feed behind rare bird alerts — plus hotspots and region lists.

## How it works

```
pipeline/                     stdlib-only Python, no dependencies
  ebird.py                    eBird API v2 client
  store.py                    SQLite schema
  fetch_notables.py           daily: recent notables, one call per country
  fetch_reference.py          weekly: hotspots + region hierarchy
  model.py                    scoring + species picks
  build_forecasts.py          writes site/data/{CC}.json + RSS feeds
  evaluate.py                 forecast-vs-reality skill tracking
  fingerprints.py             weekly: learn arrival wind signatures
  tracks.py                   weekly: back-trajectories for arrivals
  frequency.py                optional: eBird barchart frequencies
  notify.py                   Friday webhook digest
  demo.py                     synthetic data for offline testing
site/                         static frontend (Leaflet, no build)
  data/                       per-country forecast JSON + calibration.json
  feeds/                      per-country RSS weekly outlook feeds
.github/workflows/daily.yml   collector + forecast + Pages deploy
```

## Extras

- **RSS feeds** — `feeds/{CC}.xml`, one item per weekly forecast with the
  top 5 sites and species to watch. Subscribe per country; the site shows
  an RSS link once a country is selected.
- **Calibration** — every forecast is logged; `evaluate.py` checks whether
  each scored site actually produced a notable report within 7 days and
  publishes hit rates by score bucket to `data/calibration.json`, shown
  in the "Forecast skill so far" panel.
- **Life-list aware** — drop your eBird "My Data" CSV via the *Life list*
  button; species you haven't seen get a ★ lifer badge and you can filter
  "Species to watch" to lifers only. Stored in `localStorage`.
- **Deep links** — `?c=GB` selects a country on load (used by feed items).
- **Conditions explainer** — rule-based daily prose: "NW winds day 1 —
  historically an arrival direction here. Rain day 3 may ground migrants."
- **Season replay** — the history of notable arrivals plays back on the
  map (slider/play control at the bottom).
- **Back-trajectories** — `tracks.py` integrates 100m-level archive winds
  backward for 72h per recent arrival; shown as dashed "origin fan"
  polylines (toggle: tracks).
- **Missing birds** — species inside their historic arrival window (or
  past median first-date) but not yet reported this year.
- **Underwatched gems** — sites surrounded by rarity reports but with low
  observer effort: "55 rarities within 50km but only 135 species here".
- **Drought board** — days since the last notable per county/state.
- **Head-to-head** — "vs" chips on the top list, Compare for a side-by-
  side card.
- **Share card** — one-click copy of a text weekend outlook.
- **Speculation slider** — "spice" in the header re-weights the weather
  and recent-activity factors live (conservative ↔ spicy).
- **Rarity tiers** — mega / rare / scarce / regular badges on species
  picks, from reports-per-year in accumulated history. Optional deeper
  tiers via `frequency.py` (eBird barchart data — undocumented endpoint,
  off by default; enable with repo var `FREQUENCY_REGIONS=GB,US`).
- **Storm watch** — active tropical cyclones near a country surface as a
  warning line (NHC feed).
- **Webhook digest** — set secrets `DIGEST_WEBHOOK` (+ repo var
  `DIGEST_COUNTRIES`) and Fridays post a Discord/Slack outlook.
- **External links** — BirdCast dashboard (US), Trektellen counts
  (Europe) link-outs. Direct ingest was evaluated: BirdCast's data is
  embedded in a fragile JS payload and Trektellen has no public API, so
  they link out rather than scrape.

The daily Action sweeps every country (~200 API calls), accumulates
history in `pipeline/notables.sqlite` (committed back to the repo),
rebuilds per-country forecast JSON, and deploys `site/` to GitHub Pages.

## Setup

1. Get a free eBird API key: https://ebird.org/api/keygen
2. Local run:
   ```
   cd pipeline
   export EBIRD_API_KEY=yourkey
   python3 fetch_reference.py           # one-time: hotspots + regions
   python3 fetch_notables.py            # seeds ~30 days of history
   python3 fetch_notables.py --days 30  # deeper seed if you like
   python3 build_forecasts.py           # -> ../site/data/*.json
   ```
3. Serve locally:
   ```
   cd ../site && python3 -m http.server 8777   # http://localhost:8777
   ```
4. Deploy: push to GitHub, add repo secret `EBIRD_API_KEY`, enable
   Pages (Settings → Pages → "GitHub Actions"), then run the workflow
   once manually to seed.

## Demo mode (no key needed)

```
cd pipeline
python3 demo.py --db /tmp/rb.sqlite
python3 build_forecasts.py --db /tmp/rb.sqlite --out ../site/data \
    --countries XX --no-weather
cd ../site && python3 -m http.server 8777
```
Pick "Demoland" in the country dropdown.

## Honest caveats

- Cold start: real history only accumulates after go-live (~30 days of
  seed max from the API). Accuracy improves as the db grows; a proper
  backfill would need the eBird Basic Dataset.
- The score is likelihood of a *report*, not of a bird being present —
  observer effort is baked in (and that's most of the signal, honestly).
- Some countries have near-zero notable volume; they simply won't appear.
