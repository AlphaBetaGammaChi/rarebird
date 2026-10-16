"""Optional: pull eBird barchart frequencies for configured regions.

eBird's barchart endpoint is undocumented but public — it's what the
website itself uses. If it errors or the format changes, we skip
silently; rarity tiers still work from our own history.

Weekly bins (48/year). Stored as frequencies(region, species, week).

    python3 frequency.py --db notables.sqlite --regions GB-ENG-NFK,US-NY
"""
import argparse
import sys
import urllib.request

import store


def fetch_barchart(region):
    url = (
        f"https://ebird.org/barchartData?r={region}"
        "&bmo=1&emo=12&byr=1900&eyr=2100&fmt=tsv"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def parse(text):
    """Tolerant parser: 'Name;<48 freq bins>' per line (or tab-sep)."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = line.replace("\t", ";").split(";")
        if len(cells) < 49:
            continue
        name = cells[0].strip().strip('"')
        try:
            bins = [float(c) if c.strip() else 0.0 for c in cells[1:49]]
        except ValueError:
            continue
        if name and max(bins) > 0:
            out.append((name, bins))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--regions", required=True,
                    help="comma-separated eBird region codes")
    args = ap.parse_args()
    db = store.connect(args.db)

    for region in args.regions.split(","):
        try:
            rows = parse(fetch_barchart(region))
        except Exception as e:
            print(f"  {region}: fetch failed {e}", file=sys.stderr)
            continue
        if not rows:
            print(f"  {region}: no rows parsed (format changed?)")
            continue
        db.execute("DELETE FROM frequencies WHERE region=?", (region,))
        for name, bins in rows:
            for wk, f in enumerate(bins, 1):
                if f:
                    db.execute(
                        "INSERT OR REPLACE INTO frequencies VALUES (?,?,?,?)",
                        (region, name, wk, f),
                    )
        db.commit()
        print(f"  {region}: {len(rows)} species")


if __name__ == "__main__":
    main()
