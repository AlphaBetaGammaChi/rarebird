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
  build_forecasts.py          writes site/data/{CC}.json
  demo.py                     synthetic data for offline testing
site/                         static frontend (Leaflet, no build)
.github/workflows/daily.yml   collector + forecast + Pages deploy
```

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
