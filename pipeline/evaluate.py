"""Evaluate forecast skill: did a notable actually get reported?

For predictions whose 7-day horizon has passed, a 'hit' = at least one
notable observation at that hotspot during the window. Writes
data/calibration.json for the frontend.

    python3 evaluate.py --db notables.sqlite --out ../site/data
"""
import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

import store

BUCKETS = [(80, 100), (60, 79), (40, 59), (20, 39), (5, 19)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--out", default="../site/data")
    args = ap.parse_args()

    db = store.connect(args.db)
    today = dt.date.today()

    # mature predictions only (horizon fully elapsed)
    cutoff = (today - dt.timedelta(days=7)).isoformat()
    preds = db.execute(
        """SELECT country, locId, buildDate, idx FROM predictions
           WHERE buildDate <= ?""",
        (cutoff,),
    ).fetchall()
    if not preds:
        print("no mature predictions yet — check back after a week")
        return

    # notable dates per locId, for hit testing
    obs_dates = defaultdict(set)
    for locid, od in db.execute("SELECT locId, obsDt FROM notables"):
        if locid and od:
            obs_dates[locid].add(od[:10])

    hits = 0
    by_bucket = defaultdict(lambda: [0, 0])   # label -> [n, hits]
    by_country = defaultdict(lambda: [0, 0])
    daily = defaultdict(lambda: [0, 0])       # buildDate -> [n, hits]
    for cc, locid, bdate, idx in preds:
        start = dt.date.fromisoformat(bdate)
        window = {
            (start + dt.timedelta(days=i)).isoformat()
            for i in range(1, 8)
        }
        hit = bool(obs_dates.get(locid, set()) & window)
        hits += hit
        label = next(
            f"{lo}–{hi}" for lo, hi in BUCKETS if lo <= idx <= hi
        )
        by_bucket[label][0] += 1
        by_bucket[label][1] += hit
        by_country[cc][0] += 1
        by_country[cc][1] += hit
        daily[bdate][0] += 1
        daily[bdate][1] += hit

    out = {
        "asOf": today.isoformat(),
        "total": len(preds),
        "hits": hits,
        "rate": round(hits / len(preds), 3),
        "buckets": [
            {
                "label": label,
                "n": n,
                "hits": h,
                "rate": round(h / n, 3) if n else 0,
            }
            for label, (n, h) in sorted(
                by_bucket.items(),
                key=lambda kv: -int(kv[0].split("–")[0]),
            )
        ],
        "countries": sorted(
            (
                {
                    "cc": cc, "n": n, "hits": h,
                    "rate": round(h / n, 3),
                }
                for cc, (n, h) in by_country.items() if n >= 20
            ),
            key=lambda c: -c["n"],
        )[:15],
        "daily": [
            {"date": d, "n": n, "hits": h, "rate": round(h / n, 3)}
            for d, (n, h) in sorted(daily.items())
        ][-30:],
    }

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "calibration.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(
        f"{hits}/{len(preds)} hits "
        f"({out['rate'] * 100:.1f}%) — wrote calibration.json"
    )


if __name__ == "__main__":
    main()
