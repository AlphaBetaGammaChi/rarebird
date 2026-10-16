"""Learn arrival wind fingerprints per region.

For each (country, latitude band) with enough notable history, fetch
hourly 10m winds from the Open-Meteo archive for the 7 days ending at
each past arrival. The 48h before the sighting is the *signal* window;
the 5 days before that is the local *baseline*. The residual direction
histogram (signal share minus baseline share) is the region's "arrival
wind fingerprint" — which wind directions actually preceded rarities.

    python3 fingerprints.py --db notables.sqlite [--countries GB,US]
                            [--min-arrivals 25] [--max-samples 60]

ERA5 archive data lags ~5 days, so arrivals newer than a week ago are
skipped automatically.
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.request

import store

SIGNAL_H = 48          # hours before sighting
BASELINE_H = 120       # preceding hours used as local climatology
BINS = 16
MIN_SAMPLES = 15


def archive_winds(lat, lng, end_date, days=7):
    """Hourly (direction, speed) for `days` ending at end_date."""
    start = end_date - dt.timedelta(days=days - 1)
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat:.3f}&longitude={lng:.3f}"
        f"&start_date={start}&end_date={end_date}"
        "&hourly=windspeed_10m,winddirection_10m&timezone=UTC"
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                h = json.loads(r.read().decode())["hourly"]
            return list(
                zip(h["winddirection_10m"], h["windspeed_10m"])
            )
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return []


def dir_bin(deg):
    if deg is None:
        return None
    return int(((deg % 360) + 11.25) / 22.5) % BINS


def sample_arrivals(db, country, band, max_samples):
    """Deduped arrivals: one sample per (site-cell, date)."""
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    rows = db.execute(
        """SELECT lat, lng, obsDt FROM notables
           WHERE country=? AND lat IS NOT NULL AND obsDt <= ?
           ORDER BY obsDt DESC""",
        (country, cutoff),
    ).fetchall()
    seen = set()
    out = []
    lo, hi = band, band + 10
    for lat, lng, od in rows:
        if not (lo <= lat < hi):
            continue
        key = (round(lat * 2) / 2, round(lng * 2) / 2, od[:10])
        if key in seen:
            continue
        seen.add(key)
        out.append((lat, lng, od[:10]))
        if len(out) >= max_samples:
            break
    return out


def build_fingerprint(db, country, band, max_samples):
    """Return (bins[16], mean_speed, n) or None."""
    samples = sample_arrivals(db, country, band, max_samples)
    if len(samples) < MIN_SAMPLES:
        return None

    sig = [0] * BINS
    base = [0] * BINS
    speeds = []
    used = 0
    for lat, lng, od in samples:
        try:
            end = dt.date.fromisoformat(od)
            ws = archive_winds(lat, lng, end)
        except Exception as e:
            print(f"    archive fetch failed {od}: {e}", file=sys.stderr)
            continue
        if len(ws) < SIGNAL_H + 24:
            continue
        used += 1
        for i, (d, s) in enumerate(ws):
            b = dir_bin(d)
            if b is None:
                continue
            if i >= len(ws) - SIGNAL_H:
                sig[b] += 1
                if s:
                    speeds.append(s)
            elif i >= len(ws) - SIGNAL_H - BASELINE_H:
                base[b] += 1
    if not used:
        return None

    ns, nb = sum(sig) or 1, sum(base) or 1
    bins = [round(sig[i] / ns - base[i] / nb, 4) for i in range(BINS)]
    return bins, (
        round(sum(speeds) / len(speeds), 1) if speeds else None
    ), used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--countries", default=None)
    ap.add_argument("--min-arrivals", type=int, default=25)
    ap.add_argument("--max-samples", type=int, default=60)
    args = ap.parse_args()

    db = store.connect(args.db)

    q = """SELECT country, CAST(FLOOR(lat/10)*10 AS INT) band, COUNT(*)
           FROM notables WHERE lat IS NOT NULL
           GROUP BY country, band HAVING COUNT(*) >= ?"""
    params = [args.min_arrivals]
    if args.countries:
        ccs = args.countries.split(",")
        q = q.replace("GROUP BY", f"AND country IN "
                      f"({','.join('?' for _ in ccs)}) GROUP BY")
        params = ccs + [args.min_arrivals]

    regions = db.execute(q, params).fetchall()
    print(f"{len(regions)} country×band regions qualify")
    for country, band, n in regions:
        key = f"{country}:{band}"
        fp = build_fingerprint(db, country, band, args.max_samples)
        if fp is None:
            print(f"  {key}: skipped (<{MIN_SAMPLES} usable samples)")
            continue
        bins, mean_speed, used = fp
        db.execute(
            """INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?,?,?)""",
            (key, country, band, json.dumps(bins), mean_speed, used,
             dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )
        peak = max(range(BINS), key=lambda i: bins[i])
        dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        print(
            f"  {key}: {used} samples, peak dir {dirs[peak]} "
            f"(+{bins[peak]:.3f}), mean speed {mean_speed} km/h"
        )
    db.commit()


if __name__ == "__main__":
    main()
