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


def build_country(db, cc, week, use_weather):
    hs = model.score_country(db, cc, week, lambda lat, lng: (1.0, []))
    if not hs:
        return None

    # weather on a 3x3 grid over the hotspot extent
    wnotes_all = []
    weather_for = lambda lat, lng: (1.0, [])
    if use_weather:
        grid = weather_grid(hs)
        dailies = open_meteo(grid)
        point_idx = []
        for (plat, plng), daily in zip(grid, dailies):
            idx, notes = model.weather_index(daily or {}, week, plat)
            point_idx.append(((plat, plng), idx, notes))
            wnotes_all.extend(notes)

        def weather_for(lat, lng):
            best = min(
                point_idx,
                key=lambda p: model.haversine_km(lat, lng, p[0][0], p[0][1]),
            )
            return best[1], best[2]

        hs = model.score_country(db, cc, week, weather_for)

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
        "species": model.species_picks(db, cc, week),
        "recent": recent,
        "weatherNotes": sorted(set(wnotes_all))[:5],
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
        written.append(cc)
        print(
            f"  {len(fc['hotspots'])} hotspots, {len(fc['species'])} picks, "
            f"top: {fc['top'][0]['name'] if fc['top'] else '-'}"
        )

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
