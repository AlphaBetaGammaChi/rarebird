"""Weekly reference fetcher: hotspots + region hierarchy.

Hotspots give us scoring locations, observer-effort proxies, and
subnational codes for county filtering. Region lists drive the picker.

Usage:
    python3 fetch_reference.py [--db notables.sqlite] [--countries GB,US]
                               [--expand-sub2 GB,US]
"""
import argparse
import sys

import ebird
import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--countries", default=None)
    ap.add_argument("--expand-sub2", default="GB,US",
                    help="countries whose counties get named in the picker")
    args = ap.parse_args()

    db = store.connect(args.db)
    expand = set(args.expand_sub2.split(",")) if args.expand_sub2 else set()

    country_list = ebird.countries()
    for c in country_list:
        db.execute(
            "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
            (c["code"], c["name"], "world", "country"),
        )
    db.commit()

    targets = args.countries.split(",") if args.countries else [
        c["code"] for c in country_list
    ]

    for i, cc in enumerate(targets):
        try:
            # subnational1 list (states/provinces)
            for r in ebird.subregions("subnational1", cc):
                db.execute(
                    "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
                    (r["code"], r["name"], cc, "subnational1"),
                )
                # subnational2 (counties) for expanded countries
                if cc in expand:
                    for r2 in ebird.subregions("subnational2", r["code"]):
                        db.execute(
                            "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
                            (r2["code"], r2["name"], r["code"], "subnational2"),
                        )
            # hotspots
            for h in ebird.hotspots(cc):
                db.execute(
                    """INSERT OR REPLACE INTO hotspots VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        h["locId"],
                        h.get("locName"),
                        h.get("lat"),
                        h.get("lng"),
                        h.get("countryCode") or cc,
                        h.get("subnational1Code"),
                        h.get("subnational2Code"),
                        h.get("numSpeciesAllTime"),
                        h.get("latestDt"),
                    ),
                )
        except SystemExit:
            raise
        except Exception as e:
            print(f"  {cc}: FAILED {e}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            db.commit()
            print(f"  {i + 1}/{len(targets)}")
    db.commit()

    hs = db.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    rg = db.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
    print(f"done: {hs} hotspots, {rg} regions")


if __name__ == "__main__":
    main()
