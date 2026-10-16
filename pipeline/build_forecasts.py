"""Build site/data/*.json forecast files from the SQLite store.

Outputs:
  data/meta.json            build timestamp, country list
  data/regions.json         region picker hierarchy
  data/{CC}.json            per-country forecast: scored hotspots,
                            species picks, recent notables, weather points

Usage:
    python3 build_forecasts.py [--db notables.sqlite] [--out ../site/data]
                               [--countries GB,US] [--no-weather]
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.request
import xml.sax.saxutils as xml

import model
import store


def open_meteo(points):
    """One call, up to ~10 points. Returns list of daily dicts (or None)."""
    if not points:
        return []
    lats = ",".join(f"{p[0]:.3f}" for p in points)
    lngs = ",".join(f"{p[1]:.3f}" for p in points)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lngs}"
        "&daily=windspeed_10m_max,winddirection_10m_dominant,precipitation_sum"
        "&forecast_days=7&timezone=UTC"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode())
        if isinstance(data, dict):
            return [data.get("daily")]
        return [d.get("daily") for d in data]
    except Exception as e:
        print(f"  weather fetch failed: {e}", file=sys.stderr)
        return [None] * len(points)


def _coord(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v).strip().upper()
    if v and v[-1] in "NSEW":
        try:
            f = float(v[:-1])
            return f if v[-1] in "NE" else -f
        except ValueError:
            return None
    try:
        return float(v)
    except ValueError:
        return None


def active_storms():
    """Active tropical cyclones from NHC's CurrentStorms feed."""
    out = []
    try:
        with urllib.request.urlopen(
            "https://www.nhc.noaa.gov/CurrentStorms.json", timeout=30
        ) as r:
            data = json.loads(r.read().decode())
        for s in data.get("activeStorms", []):
            lat = _coord(s.get("latitude") or s.get("latitudeDeg"))
            lng = _coord(s.get("longitude") or s.get("longitudeDeg"))
            if lat is None:
                continue
            out.append({
                "name": s.get("binomial") or s.get("name"),
                "class": s.get("classification"),
                "lat": lat, "lng": lng,
                "basin": s.get("basin"),
            })
    except Exception:
        pass
    return out


def storms_near(storms, hotspots, km=900):
    """Storms whose centre is within km of the country's hotspot bbox."""
    if not storms or not hotspots:
        return []
    lats = [h["lat"] for h in hotspots]
    lngs = [h["lng"] for h in hotspots]
    lat0, lat1, lng0, lng1 = min(lats), max(lats), min(lngs), max(lngs)
    near = []
    for s in storms:
        d = min(
            model.haversine_km(s["lat"], s["lng"], la, lo)
            for la in (lat0, (lat0 + lat1) / 2, lat1)
            for lo in (lng0, (lng0 + lng1) / 2, lng1)
        )
        if d <= km:
            near.append(dict(s, distKm=round(d)))
    return near


def make_explainer(grid_dailies, fingerprints, week, bands):
    """Rule-based conditions prose for the forecast horizon."""
    if not grid_dailies:
        return ""
    times = [len(d["time"]) for d in grid_dailies if d.get("time")]
    if not times:
        return ""
    days = min(times)

    per_day = []
    for i in range(days):
        spds, dirs, prc = [], [], []
        for d in grid_dailies:
            s = d.get("windspeed_10m_max") or []
            dd = d.get("winddirection_10m_dominant") or []
            p = d.get("precipitation_sum") or []
            if i < len(s) and s[i]:
                spds.append(s[i])
            if i < len(dd) and dd[i] is not None:
                dirs.append(dd[i])
            if i < len(p) and p[i]:
                prc.append(p[i])
        per_day.append(
            (
                max(spds) if spds else 0,
                model.dir_bin(
                    sorted(dirs)[len(dirs) // 2] if dirs else 0
                ),
                max(prc) if prc else 0,
            )
        )

    sents = []
    peak = set()
    for b in bands:
        peak |= model.seasonal_peak_weeks_cache.get(b, set())
    if week in peak:
        sents.append("Peak rarity window for this latitude.")
    else:
        sents.append("Outside the main rarity window — long shots only.")

    good_dirs = [
        i + 1 for i, (_, b, _) in enumerate(per_day)
        if b is not None and any(
            fp[b] > 0.04 for fp in fingerprints.values()
        )
    ]
    windy = [
        (i + 1, s, b) for i, (s, b, _) in enumerate(per_day) if s >= 40
    ]
    wet = [i + 1 for i, (_, _, p) in enumerate(per_day) if p >= 15]

    if good_dirs:
        d0 = good_dirs[0]
        b = per_day[d0 - 1][1]
        sents.append(
            f"{model.DIR_NAMES[b]} winds on day {d0} — "
            f"historically an arrival direction here."
        )
    if windy:
        d0, s, b = windy[0]
        sents.append(
            f"Strong {model.DIR_NAMES[b] if b is not None else ''} "
            f"winds day {d0} ({round(s)} km/h) — displacement weather."
        )
    if wet:
        sents.append(f"Rain day {wet[0]} may ground migrants.")
    if not good_dirs and not windy and not wet:
        sents.append("Settled weather — limited assisted arrivals.")
    return " ".join(sents)


def weather_grid(hotspots):
    """Up to 9 sample points spread over the country's hotspot bbox."""
    pts = [(h["lat"], h["lng"]) for h in hotspots]
    if not pts:
        return []
    lats = [p[0] for p in pts]
    lngs = [p[1] for p in pts]
    lat0, lat1 = min(lats), max(lats)
    lng0, lng1 = min(lngs), max(lngs)
    grid = []
    for i in range(3):
        for j in range(3):
            grid.append(
                (lat0 + (lat1 - lat0) * i / 2, lng0 + (lng1 - lng0) * j / 2)
            )
    return grid


def log_predictions(db, cc, scored):
    """Record today's forecast for later skill evaluation."""
    today = dt.date.today().isoformat()
    for h in scored:
        if h["index"] < 5:
            break  # scored list is sorted desc; skip the long tail
        db.execute(
            """INSERT OR REPLACE INTO predictions
               (country, locId, buildDate, horizonDays, idx, expected)
               VALUES (?,?,?,?,?,?)""",
            (cc, h["locId"], today, 7, h["index"], h["expected"]),
        )


def write_feed(fc, feeds_dir, site_url):
    """RSS 2.0 item per weekly forecast."""
    os.makedirs(feeds_dir, exist_ok=True)
    esc = xml.escape
    top5 = fc["top"][:5]
    desc = "<br>".join(
        f"{i + 1}. {esc(h['name'])} — index {h['index']}"
        for i, h in enumerate(top5)
    )
    sp = ", ".join(s["species"] for s in fc["species"][:5])
    if sp:
        desc += f"<br><br>Species to watch: {esc(sp)}"
    if fc.get("weatherNotes"):
        desc += f"<br><br>Weather: {esc('; '.join(fc['weatherNotes']))}"
    desc += (
        f"<br><br><i>Likelihood of a rarity report this week. "
        f"Full map: {esc(site_url)}</i>"
    )
    pub = dt.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    item = f"""  <item>
    <title>{esc(fc['country'])} rarity outlook — {esc(fc['week'])}</title>
    <link>{esc(site_url)}?c={esc(fc['country'])}</link>
    <guid isPermaLink="false">rbf-{esc(fc['country'])}-{esc(fc['week'])}</guid>
    <pubDate>{pub}</pubDate>
    <description>{desc}</description>
  </item>"""

    # keep history: rewrite whole file with existing items preserved
    path = os.path.join(feeds_dir, f"{fc['country']}.xml")
    existing = []
    if os.path.exists(path):
        with open(path) as f:
            body = f.read()
        existing = body.split("  <item>")[1:]
        existing = ["  <item>" + s.split("  </item>")[0] + "  </item>"
                    for s in existing]
        existing = [e for e in existing if f"rbf-{fc['country']}-{fc['week']}"
                    not in e]
    items = "\n".join([item] + existing[:12])  # ~3 months of weeks
    name = fc["country"]
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Rarebird Forecast — {esc(name)}</title>
  <link>{esc(site_url)}?c={esc(name)}</link>
  <description>Weekly rarity likelihood outlook for {esc(name)}</description>
{items}
</channel>
</rss>
"""
    with open(path, "w") as f:
        f.write(feed)


def build_country(db, cc, week, use_weather):
    hs = model.score_country(db, cc, week, lambda lat, lng: (1.0, []))
    if not hs:
        return None

    # learned arrival-wind fingerprints for this country's lat bands
    fingerprints = {}
    try:
        for band, bins in db.execute(
            "SELECT band, bins FROM fingerprints WHERE country=?", (cc,)
        ):
            fingerprints[band] = json.loads(bins)
    except Exception:
        pass

    # weather on a 3x3 grid over the hotspot extent
    wnotes_all = []
    weather_for = lambda lat, lng: (1.0, [])
    if use_weather:
        grid = weather_grid(hs)
        dailies = open_meteo(grid)
        point_idx = []
        for (plat, plng), daily in zip(grid, dailies):
            point_idx.append(((plat, plng), daily or {}))
            # country-level summary notes use the point's own band
            fp = fingerprints.get(model.lat_band(plat))
            _, notes = model.weather_index(daily or {}, week, plat, fp)
            wnotes_all.extend(notes)

        def weather_for(lat, lng):
            best = min(
                point_idx,
                key=lambda p: model.haversine_km(lat, lng, p[0][0], p[0][1]),
            )
            fp = fingerprints.get(model.lat_band(lat))
            return model.weather_index(best[1], week, lat, fp)

        hs = model.score_country(db, cc, week, weather_for)

    tiers = model.rarity_tiers(db, cc)
    model_picks = model.species_picks(db, cc, week)
    for s in model_picks:
        s["tier"] = tiers.get(s["code"], "scarce")

    tracks = [
        {"comName": r[0], "locName": r[1], "obsDt": r[2],
         "path": json.loads(r[3])}
        for r in db.execute(
            """SELECT comName, locName, obsDt, path FROM tracks
               WHERE country=? ORDER BY obsDt DESC LIMIT 12""",
            (cc,),
        )
    ]
    history = [
        {"lat": r[0], "lng": r[1], "n": r[2], "d": r[3], "loc": r[4]}
        for r in db.execute(
            """SELECT lat, lng, comName, obsDt, locName FROM notables
               WHERE country=? AND lat IS NOT NULL
               ORDER BY obsDt DESC LIMIT 3000""",
            (cc,),
        )
    ]
    history.reverse()

    storms = storms_near(active_storms(), hs)
    bands = {model.lat_band(h["lat"]) for h in hs if h["lat"] is not None}
    grid_dailies = [p[1] for p in point_idx] if use_weather else []
    explainer = make_explainer(grid_dailies, fingerprints, week, bands)
    if storms:
        explainer += " " + " ".join(
            f"{s['name']} ({s['class']}) active ~{s['distKm']}km away — "
            f"seabird displacement possible." for s in storms
        )

    week_str = dt.date.today().isocalendar()
    recent = [
        {
            "comName": r[0],
            "locName": r[1],
            "lat": r[2],
            "lng": r[3],
            "obsDt": r[4],
            "locId": r[5],
            "sub1": r[6],
            "sub2": r[7],
        }
        for r in db.execute(
            """SELECT comName, locName, lat, lng, obsDt, locId, sub1, sub2
               FROM notables WHERE country=?
               ORDER BY obsDt DESC LIMIT 60""",
            (cc,),
        )
    ]

    return {
        "country": cc,
        "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "week": f"{week_str[0]}-W{week_str[1]:02d}",
        "hotspots": hs[:1500],
        "top": hs[:20],
        "species": model_picks,
        "missing": model.species_windows(db, cc, week),
        "gems": model.hidden_gems(db, cc),
        "droughts": model.drought_board(db, cc),
        "history": history,
        "tracks": tracks,
        "storms": storms,
        "explainer": explainer,
        "recent": recent,
        "weatherNotes": sorted(set(wnotes_all))[:5],
        "windFingerprint": {
            str(band): {
                "peakDir": model.DIR_NAMES[
                    max(range(16), key=lambda i: bins[i])
                ],
                "peakResidual": round(max(bins), 3),
            }
            for band, bins in fingerprints.items()
        },
        "sub1": sorted(
            {h["sub1"] for h in hs if h["sub1"]},
        ),
        "sub2": sorted(
            {h["sub2"] for h in hs if h["sub2"]},
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--out", default="../site/data")
    ap.add_argument("--countries", default=None)
    ap.add_argument("--no-weather", action="store_true")
    ap.add_argument("--site-url", default="https://example.github.io/rarebird",
                    help="public URL, used in feed links")
    ap.add_argument("--feeds", action="store_true",
                    help="also write RSS feeds to {out}/../feeds")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    db = store.connect(args.db)
    week = dt.date.today().isocalendar()[1]

    if args.countries:
        ccs = args.countries.split(",")
    else:
        ccs = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT country FROM notables ORDER BY country"
            )
        ]

    written = []
    for cc in ccs:
        print(f"building {cc} ...")
        fc = build_country(db, cc, week, not args.no_weather)
        if fc is None:
            print("  (no hotspots / no data, skipped)")
            continue
        path = os.path.join(args.out, f"{cc}.json")
        with open(path, "w") as f:
            json.dump(fc, f, separators=(",", ":"))
        log_predictions(db, cc, fc["hotspots"])
        if args.feeds:
            write_feed(fc, os.path.join(os.path.dirname(args.out), "feeds"),
                       args.site_url)
        written.append(cc)
        print(
            f"  {len(fc['hotspots'])} hotspots, {len(fc['species'])} picks, "
            f"top: {fc['top'][0]['name'] if fc['top'] else '-'}"
        )
    db.commit()

    # region hierarchy for the picker
    regions = {"countries": [], "children": {}}
    for code, name, parent, rtype in db.execute(
        "SELECT code, name, parent, rtype FROM regions ORDER BY name"
    ):
        if rtype == "country":
            regions["countries"].append({"code": code, "name": name})
        regions["children"].setdefault(parent, []).append(
            {"code": code, "name": name, "type": rtype}
        )
    with open(os.path.join(args.out, "regions.json"), "w") as f:
        json.dump(regions, f, separators=(",", ":"))

    meta = {
        "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "countries": written,
        "notables": db.execute("SELECT COUNT(*) FROM notables").fetchone()[0],
    }
    with open(os.path.join(args.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"wrote {len(written)} country files -> {args.out}")


if __name__ == "__main__":
    main()
