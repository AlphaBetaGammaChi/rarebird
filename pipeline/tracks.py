"""Back-trajectories for recent arrivals: 'where did it come from?'

For the most recent notable observations (old enough for the archive),
fetch hourly 100m winds (low flight level) and step a parcel backward
through them — an approximation of the air mass the bird likely arrived
with. NOAA HYSPLIT does this properly; this is the free, keyless version
good enough for a fan of origin lines on the map.

    python3 tracks.py --db notables.sqlite [--countries GB,US]
                        [--per-country 12] [--hours 72]
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
import urllib.request

import store

STEP_KEEP = 6  # store one point every N hours


def fetch_level_winds(lat, lng, end_date, days):
    """Hourly (dir_deg, speed_kmh) at ~100m AGL; falls back to 10m."""
    start = end_date - dt.timedelta(days=days - 1)
    for params in (
        "windspeed_100m,winddirection_100m",
        "windspeed_10m,winddirection_10m",
    ):
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat:.3f}&longitude={lng:.3f}"
            f"&start_date={start}&end_date={end_date}"
            f"&hourly={params}&timezone=UTC"
        )
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                h = json.loads(r.read().decode())["hourly"]
            spd_key, dir_key = params.split(",")
            return list(zip(h[dir_key], h[spd_key]))
        except Exception:
            continue
    return []


def backtrack(lat, lng, winds, hours):
    """Step a parcel backward through the hourly wind series.

    Wind direction is where the wind comes FROM; the parcel was moving
    toward (dir+180). One hour per step, winds sampled at the obs point.
    """
    path = [[round(lat, 4), round(lng, 4)]]
    clat, clng = lat, lng
    for d, s in reversed(winds[-hours:]):
        if d is None or s is None:
            continue
        toward = (d + 180) % 360
        rad = toward * 3.14159265 / 180
        # km travelled in that hour, then convert to degrees
        dx = s * math.sin(rad) * -1.0
        dy = s * math.cos(rad) * -1.0
        clat += dy / 110.574
        clng += dx / (111.320 * max(0.1, abs(math.cos(clat * 3.14159265 / 180))))
        path.append([round(clat, 4), round(clng, 4)])
    return [path[i] for i in range(0, len(path), STEP_KEEP)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--countries", default=None)
    ap.add_argument("--per-country", type=int, default=12)
    ap.add_argument("--hours", type=int, default=72)
    args = ap.parse_args()

    db = store.connect(args.db)

    # ERA5 archive lags ~5 days; use a conservative 10-day cutoff
    cutoff = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    q = """SELECT DISTINCT country FROM notables WHERE obsDt <= ?"""
    if args.countries:
        ccs = "','".join(args.countries.split(","))
        q += f" AND country IN ('{ccs}')"
    countries = [r[0] for r in db.execute(q, (cutoff,))]

    for cc in countries:
        rows = db.execute(
            """SELECT subId, speciesCode, comName, locName, lat, lng, obsDt
               FROM notables
               WHERE country=? AND obsDt<=? AND lat IS NOT NULL
                 AND (subId, speciesCode) NOT IN
                     (SELECT subId, speciesCode FROM tracks)
               ORDER BY obsDt DESC LIMIT ?""",
            (cc, cutoff, args.per_country * 2),
        ).fetchall()
        done = db.execute(
            "SELECT COUNT(*) FROM tracks WHERE country=?", (cc,)
        ).fetchone()[0]
        n_new = 0
        for subId, sp, com, loc, lat, lng, od in rows:
            if done + n_new >= args.per_country:
                break
            try:
                end = dt.date.fromisoformat(od[:10])
                winds = fetch_level_winds(
                    lat, lng, end, days=args.hours // 24 + 1
                )
            except Exception as e:
                print(f"  {cc} {com}: fetch failed {e}", file=sys.stderr)
                continue
            if not winds:
                continue
            path = backtrack(lat, lng, winds, args.hours)
            if len(path) < 3:
                continue
            db.execute(
                """INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?,?,?)""",
                (subId, sp, cc, com, loc, od, json.dumps(path)),
            )
            n_new += 1
            time.sleep(0.1)
        db.commit()
        print(f"  {cc}: +{n_new} tracks ({done + n_new} total)")


if __name__ == "__main__":
    main()
