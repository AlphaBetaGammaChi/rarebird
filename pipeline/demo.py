"""Seed the db with plausible synthetic data for a no-key demo run.

Creates a fake country 'XX' ("Demoland") with hotspots on a coastline,
rarity magnets, seasonal vagrancy pulses in spring/autumn, and recent
sightings so the forecast has signal to show.

    python3 demo.py --db notables.sqlite
"""
import argparse
import datetime as dt
import math
import random

import store

RARITY_NAMES = [
    ("Yellow-browed Warbler", "phyhum"),
    ("Pallas's Warbler", "palwar1"),
    ("Red-flanked Bluetail", "refblu"),
    ("Rustic Bunting", "rusbun"),
    ("Hoopoe", "hoopoe"),
    ("Wryneck", "eurwry"),
    ("Great Grey Shrike", "gryshr"),
    ("Sabine's Gull", "sabgul"),
    ("Long-billed Dowitcher", "lobdow"),
    ("American Golden Plover", "amgplo"),
    ("Pectoral Sandpiper", "pecsan"),
    ("Richard's Pipit", "ricpip1"),
    ("Radde's Warbler", "radwar"),
    ("Little Bunting", "litbun"),
    ("Rose-coloured Starling", "rossta1"),
]

HS_NAMES = [
    "North Cape Point", "Seawatch Head", "Old Marsh Reserve",
    "Willow Fen", "Dunwick Cliffs", "Eastmere Lagoons", "Gorse Common",
    "Harbour Point", "Lighthouse Fields", "The Plantation", "Fen Drove",
    "Saltwick Bay", "Moor Edge", "Birchwood Pit", "Kestrel Down",
    "Sandpiper Pool", "Crow Hill", "Meander Estuary", "Windmill Ridge",
    "South Spit", "Alder Carr", "Falcon Tor", "Reedbed Hide Trail",
    "Chapel Pits", "Bracken Edge", "Long Dunes", "Quarry Pools",
    "Mistley Flats", "Heather Bank", "Stonechat Way",
]

random.seed(42)


def seasonal_weight(d):
    """Vagrancy peaks: Apr-May and Sep-Nov."""
    doy = d.timetuple().tm_yday
    spring = math.exp(-((doy - 125) / 28) ** 2)
    autumn = math.exp(-((doy - 290) / 45) ** 2)
    return 0.15 + spring + 1.4 * autumn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="notables.sqlite")
    ap.add_argument("--country", default="XX")
    args = ap.parse_args()
    db = store.connect(args.db)

    db.execute(
        "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
        (args.country, "Demoland", "world", "country"),
    )
    db.execute(
        "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
        (f"{args.country}-EA", "Eastern Province", args.country, "subnational1"),
    )
    db.execute(
        "INSERT OR REPLACE INTO regions VALUES (?,?,?,?)",
        (f"{args.country}-WE", "Western Province", args.country, "subnational1"),
    )

    # hotspots: denser along east coast, a few rarity magnets
    hotspots = []
    for i, name in enumerate(HS_NAMES):
        coastal = i < 14
        lat = random.uniform(50.0, 55.5) if coastal else random.uniform(50.5, 54.5)
        lng = random.uniform(0.5, 2.5) if coastal else random.uniform(-3.0, -0.5)
        magnet = i in (0, 3, 7, 20)
        nsp = int(random.uniform(120, 320) * (1.8 if magnet else 1.0))
        sub1 = f"{args.country}-EA" if lng > 0 else f"{args.country}-WE"
        locid = f"L{i:05d}"
        db.execute(
            "INSERT OR REPLACE INTO hotspots VALUES (?,?,?,?,?,?,?,?,?)",
            (locid, name, round(lat, 4), round(lng, 4), args.country,
             sub1, f"{sub1}-C{i % 4}", nsp, dt.date.today().isoformat()),
        )
        hotspots.append((locid, name, lat, lng, magnet))

    # ~2.5 years of notables, seasonal pulses, magnets get 4x rate
    start = dt.date.today() - dt.timedelta(days=900)
    sub_n = 0
    for day_i in range(900):
        d = start + dt.timedelta(days=day_i)
        w = seasonal_weight(d)
        for locid, name, lat, lng, magnet in hotspots:
            p = 0.02 * w * (4.0 if magnet else 1.0)
            if random.random() < p:
                sp = random.choice(RARITY_NAMES)
                jitter_lat = lat + random.uniform(-0.02, 0.02)
                jitter_lng = lng + random.uniform(-0.02, 0.02)
                db.execute(
                    """INSERT OR IGNORE INTO notables
                       (subId, speciesCode, comName, sciName, locId, locName,
                        lat, lng, obsDt, howMany, country, sub1, sub2,
                        firstSeen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"S{sub_n:07d}", sp[1], sp[0], "Demo birdus",
                        locid, name, round(jitter_lat, 4),
                        round(jitter_lng, 4), d.isoformat(), 1,
                        args.country, "XX-EA" if lng > 0 else "XX-WE",
                        None, dt.date.today().isoformat(),
                    ),
                )
                sub_n += 1
    db.commit()
    n = db.execute(
        "SELECT COUNT(*) FROM notables WHERE country=?", (args.country,)
    ).fetchone()[0]
    print(f"seeded {n} synthetic notables across {len(hotspots)} hotspots")


if __name__ == "__main__":
    main()
