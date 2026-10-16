"""Daily collector: pull recent notable observations for every country.

One API call per country per day covers the whole world (~200 calls).
Dedupes on (checklist, species) so history accumulates over time.

Usage:
    python3 fetch_notables.py [--db notables.sqlite] [--days 7] [--countries GB,US]
"""
import argparse
import datetime as dt
import json
import sys

import ebird
import store


def harvest(db, country_code, back):
    obs = ebird.recent_notables(country_code, back=back)
    today = dt.date.today().isoformat()
    n = 0
    for o in obs:
        if not o.get("subId") or not o.get("speciesCode"):
            continue
        db.execute(
            """INSERT INTO notables
               (subId, speciesCode, comName, sciName, locId, locName,
                lat, lng, obsDt, howMany, country, sub1, sub2, firstSeen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(subId, speciesCode) DO NOTHING""",
            (
                o["subId"],
                o["speciesCode"],
                o.get("comName"),
                o.get("sciName"),
                o.get("locId"),
                o.get("locName"),
                o.get("lat"),
                o.get("lng"),
                o.get("obsDt"),
                o.get("howMany"),
                country_code,
                o.get("subnational1Code") or country_code,
                o.get("subnational2Code"),
                today,
            ),
        )
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--days", type=int, default=7,
                    help="lookback window (dedupe makes overlap harmless)")
    ap.add_argument("--countries", default=None,
                    help="comma-separated subset, e.g. GB,US (default: all)")
    args = ap.parse_args()

    db = store.connect(args.db)
    if args.countries:
        targets = args.countries.split(",")
        all_codes = {c["code"] for c in ebird.countries()}
        targets = [t for t in targets if t in all_codes]
    else:
        targets = [c["code"] for c in ebird.countries()]

    total = 0
    for i, cc in enumerate(targets):
        try:
            total += harvest(db, cc, args.days)
        except SystemExit:
            raise
        except Exception as e:
            print(f"  {cc}: FAILED {e}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            db.commit()
            print(f"  {i + 1}/{len(targets)} countries, {total} rows")
    db.commit()

    n_total = db.execute("SELECT COUNT(*) FROM notables").fetchone()[0]
    store.set_meta(db, "last_fetch", dt.datetime.utcnow().isoformat() + "Z")
    db.commit()
    print(f"done: +{total} candidate rows, {n_total} total notables in db")


if __name__ == "__main__":
    main()
