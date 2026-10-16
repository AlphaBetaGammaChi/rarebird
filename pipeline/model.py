"""Rarity forecast model.

Scores hotspots for "likelihood of a rarity being reported" over the
forecast horizon. Deliberately simple and explainable — vagrancy is a
sparse, chaotic process, so an honest statistical model beats a black box.

score = base_rate x seasonal x weather x recency_boost, effort-adjusted.
The reported index is normalized to 0-100 within each country.
"""
import datetime as dt
import math
from collections import defaultdict


def parse_obsdt(s):
    """eBird obsDt is 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'."""
    return dt.datetime.strptime(s[:10], "%Y-%m-%d").date()


def iso_week(d):
    return d.isocalendar()[1]


def lat_band(lat):
    return int(math.floor(lat / 10.0)) * 10


def haversine_km(a_lat, a_lng, b_lat, b_lng):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- seasonality

def seasonal_tables(db):
    """Notable counts by (lat_band, iso_week) across all history.

    Hemisphere falls out naturally: southern bands peak in different weeks.
    """
    counts = defaultdict(int)
    for lat, obsdt in db.execute(
        "SELECT lat, obsDt FROM notables WHERE lat IS NOT NULL"
    ):
        counts[(lat_band(lat), iso_week(parse_obsdt(obsdt)))] += 1

    band_totals = defaultdict(int)
    band_weeks = defaultdict(set)
    for (band, week), c in counts.items():
        band_totals[band] += c
        band_weeks[band].add(week)

    mult = {}
    for band in band_totals:
        mean = band_totals[band] / 53.0
        for week in range(1, 54):
            c = counts.get((band, week), 0)
            # smoothed multiplier, damped toward 1 when data is thin
            raw = (c + 1.0) / (mean + 1.0)
            n_years = max(1, band_totals[band] / 200.0)
            confidence = min(1.0, n_years)
            mult[(band, week)] = 1.0 + confidence * min(3.0, raw - 1.0)
    return mult


def seasonal_multiplier(tables, lat, week):
    band = lat_band(lat)
    if (band, week) in tables:
        return tables[(band, week)]
    # nearest populated band
    for delta in range(10, 90, 10):
        for b in (band - delta, band + delta):
            if (b, week) in tables:
                return tables[(b, week)]
    return 1.0


# ------------------------------------------------------------------- weather

DIR_NAMES = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def dir_bin(deg):
    return int(((deg % 360) + 11.25) / 22.5) % 16 if deg is not None else None


def weather_index(daily, week, lat, fingerprint=None):
    """Vagrancy-weather index from Open-Meteo daily forecast.

    Strong sustained winds during a location's peak rarity weeks boost the
    score; heavy storms add a bump. When a learned arrival fingerprint is
    supplied, days whose dominant wind matches a historically favorable
    direction get an additional boost (and opposed winds dampen slightly).
    Returns (mean_index, [per-day notes]).
    """
    speeds = daily.get("windspeed_10m_max") or []
    dirs = daily.get("winddirection_10m_dominant") or []
    precip = daily.get("precipitation_sum") or []
    if not speeds:
        return 1.0, []

    band = lat_band(lat)
    in_peak = seasonal_peak_weeks_cache.get(band, set())
    peak_factor = 1.0 if week in in_peak else 0.4

    idx_days, notes = [], []
    for i, spd in enumerate(speeds):
        wind_boost = min(0.6, (spd or 0) / 60.0) * peak_factor
        if fingerprint and i < len(dirs):
            b = dir_bin(dirs[i])
            if b is not None:
                fp = fingerprint[b]
                wind_boost += max(-0.25, min(0.5, fp * 4.0))
                if fp > 0.04:
                    notes.append(
                        f"favorable {DIR_NAMES[b]} winds day {i + 1}"
                    )
        storm_boost = 0.15 if (precip and (precip[i] or 0) > 15) else 0.0
        idx_days.append(1.0 + wind_boost + storm_boost)
        if wind_boost > 0.3 and not fingerprint:
            notes.append(f"strong winds day {i + 1}")
        if storm_boost:
            notes.append(f"storm day {i + 1}")
    mean = sum(idx_days) / len(idx_days)
    return mean, sorted(set(notes))


seasonal_peak_weeks_cache = {}


def prime_peak_weeks(db):
    """Weeks in the top quartile of notable activity per lat band."""
    counts = defaultdict(int)
    for lat, obsdt in db.execute(
        "SELECT lat, obsDt FROM notables WHERE lat IS NOT NULL"
    ):
        counts[(lat_band(lat), iso_week(parse_obsdt(obsdt)))] += 1
    bands = defaultdict(dict)
    for (band, week), c in counts.items():
        bands[band][week] = c
    for band, wc in bands.items():
        if not wc:
            continue
        thresh = sorted(wc.values())[int(len(wc) * 0.75)]
        seasonal_peak_weeks_cache[band] = {w for w, c in wc.items() if c >= thresh}


# ------------------------------------------------------------------- scoring

def candidate_hotspots(db, country, limit=2500):
    """Hotspots worth scoring: rarity history, or most-birded sites."""
    rows = db.execute(
        """SELECT h.locId, h.name, h.lat, h.lng, h.sub1, h.sub2,
                  h.numSpeciesAllTime, h.latestDt,
                  COUNT(n.subId) AS notable_n,
                  MAX(n.obsDt) AS last_notable
           FROM hotspots h
           LEFT JOIN notables n ON n.locId = h.locId
           WHERE h.country = ?
           GROUP BY h.locId""",
        (country,),
    ).fetchall()
    have_history = [r for r in rows if r[8] > 0]
    rest = sorted(
        (r for r in rows if r[8] == 0),
        key=lambda r: (r[6] or 0),
        reverse=True,
    )
    return have_history + rest[: max(0, limit - len(have_history))]


def recent_notables_near(db, country, days=14):
    """Recent notables in-country, indexed by 1-degree grid cells."""
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    grid = defaultdict(list)
    rows = db.execute(
        """SELECT lat, lng, comName, obsDt FROM notables
           WHERE country = ? AND obsDt >= ?""",
        (country, cutoff),
    ).fetchall()
    for lat, lng, com, od in rows:
        if lat is None:
            continue
        grid[(int(lat), int(lng))].append((lat, lng, com, od))
    return grid


def grid_count(grid, lat, lng, radius_km):
    """Count recent notables within radius of a point."""
    cell_lat = int(lat)
    cell_lng = int(lng)
    span = 2  # 2-degree margin covers 50km+ lookups at most latitudes
    n = 0
    for cl in range(cell_lat - span, cell_lat + span + 1):
        for cg in range(cell_lng - span, cell_lng + span + 1):
            for rlat, rlng, _, _ in grid.get((cl, cg), ()):
                if haversine_km(lat, lng, rlat, rlng) <= radius_km:
                    n += 1
    return n


def score_country(db, country, week, weather_for_point):
    """Score every candidate hotspot in a country.

    weather_for_point(lat, lng) -> (index, notes)
    Returns list of dicts sorted by score desc.
    """
    tables = seasonal_tables(db)
    prime_peak_weeks(db)
    grid = recent_notables_near(db, country)

    rows = candidate_hotspots(db, country)
    if not rows:
        return []

    # country base rate: notable reports per hotspot-week, smoothed
    total_notables = db.execute(
        "SELECT COUNT(*) FROM notables WHERE country=?", (country,)
    ).fetchone()[0]
    first = db.execute(
        "SELECT MIN(obsDt) FROM notables WHERE country=?", (country,)
    ).fetchone()[0]
    span_weeks = max(
        2.0,
        (dt.date.today() - parse_obsdt(first)).days / 7.0 if first else 2.0,
    )
    country_rate = total_notables / max(1, len(rows)) / span_weeks

    max_effort = max((r[6] or 0) for r in rows) or 1
    today = dt.date.today()
    out = []
    for r in rows:
        (locid, name, lat, lng, sub1, sub2, nsp, latest, notable_n,
         last_notable) = r
        if lat is None:
            continue

        base = notable_n / span_weeks if notable_n else country_rate * 0.3
        seas = seasonal_multiplier(tables, lat, week)
        widx, wnotes = weather_for_point(lat, lng)
        rec = min(2.0, 1.0 + 0.15 * grid_count(grid, lat, lng, 50))
        effort = 0.3 + 0.7 * (math.log1p(nsp or 0) / math.log1p(max_effort))

        # days since last notable at site (a site 'due' gets mild boost)
        due = 1.0
        if last_notable:
            days_since = (today - parse_obsdt(last_notable)).days
            due = 1.1 if days_since > 21 else 1.0

        expected = base * seas * widx * rec * effort * due
        out.append(
            {
                "locId": locid,
                "name": name,
                "lat": round(lat, 4),
                "lng": round(lng, 4),
                "sub1": sub1,
                "sub2": sub2,
                "expected": expected,
                "notables": notable_n,
                "lastNotable": last_notable,
                "numSpecies": nsp or 0,
                "weather": wnotes[:2],
                "factors": {
                    "seasonal": round(seas, 2),
                    "weather": round(widx, 2),
                    "recent": round(rec, 2),
                },
            }
        )

    top = max(o["expected"] for o in out) or 1.0
    for o in out:
        o["index"] = round(100 * o["expected"] / top)
        o["expected"] = round(o["expected"], 3)
    out.sort(key=lambda o: -o["index"])
    return out


# ------------------------------------------------------------- species picks

def species_picks(db, country, week, limit=12):
    """Species most likely to turn up: recent activity + historic timing."""
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=21)).isoformat()

    recent = defaultdict(lambda: [0, None])
    for sp, com, od in db.execute(
        """SELECT speciesCode, comName, obsDt FROM notables
           WHERE country=? AND obsDt >= ?""",
        (country, cutoff),
    ):
        d = parse_obsdt(od)
        decay = math.exp(-(today - d).days / 10.0)
        recent[sp][0] += decay
        if recent[sp][1] is None or od > recent[sp][1]:
            recent[sp][1] = od

    names = {}
    seasonal = defaultdict(int)
    weeks = {(week - 2 - 1) % 53 + 1, (week - 1 - 1) % 53 + 1, week,
             (week + 1 - 1) % 53 + 1, (week + 2 - 1) % 53 + 1}
    for sp, com, od in db.execute(
        "SELECT speciesCode, comName, obsDt FROM notables WHERE country=?",
        (country,),
    ):
        names[sp] = com
        if iso_week(parse_obsdt(od)) in weeks:
            seasonal[sp] += 1

    picks = []
    for sp in set(recent) | set(seasonal):
        r = recent.get(sp, [0, None])[0]
        s = seasonal.get(sp, 0)
        score = 3 * r + s
        if score <= 0:
            continue
        reasons = []
        if recent.get(sp, [0])[0] > 0.3:
            reasons.append(f"reported recently (last: {recent[sp][1]})")
        if s:
            reasons.append(f"{s} historic record(s) around this week")
        picks.append(
            {
                "species": names.get(sp, sp),
                "code": sp,
                "score": round(score, 2),
                "reason": "; ".join(reasons) or "seasonal possibility",
            }
        )
    picks.sort(key=lambda p: -p["score"])
    return picks[:limit]


# ----------------------------------------------------- windows & missing

def week_label(week):
    d = dt.date(2001, 1, 1) + dt.timedelta(weeks=week - 1)
    return d.strftime("%b %d")


def species_windows(db, country, week, limit=15):
    """Per-species expected arrival windows + 'missing birds' detector.

    A species is 'due/overdue' when this week is inside its historic
    window (or past its median first-arrival week) but nothing has been
    reported in the country this calendar year.
    """
    this_year = dt.date.today().year
    hist = defaultdict(list)   # sp -> [(year, week)]
    names = {}
    for sp, com, od in db.execute(
        """SELECT speciesCode, comName, obsDt FROM notables
           WHERE country=?""",
        (country,),
    ):
        d = parse_obsdt(od)
        hist[sp].append((d.year, iso_week(d)))
        names[sp] = com

    out = []
    for sp, recs in hist.items():
        if len(recs) < 3:
            continue
        wc = defaultdict(int)
        firsts = {}
        for y, w in recs:
            wc[w] += 1
            firsts[y] = min(firsts.get(y, 99), w)
        peak = max(wc.values())
        win = sorted(w for w, c in wc.items() if c >= peak * 0.3)
        if not win:
            continue
        prior_years = [w for y, w in firsts.items() if y < this_year]
        med_first = (
            sorted(prior_years)[len(prior_years) // 2] if prior_years
            else None
        )
        seen_this_year = any(y == this_year for y, _ in recs)
        in_window = win[0] <= week <= win[-1]
        overdue = bool(
            med_first and week >= med_first and not seen_this_year
        )
        due_soon = not in_window and win[0] - week in range(1, 4)
        if not (in_window or overdue or due_soon):
            continue
        out.append(
            {
                "species": names[sp],
                "code": sp,
                "window": f"{week_label(win[0])} – {week_label(win[-1])}",
                "medianFirst": week_label(med_first) if med_first else None,
                "overdue": overdue,
                "status": (
                    "overdue" if overdue
                    else "due now" if in_window
                    else "due soon"
                ),
                "records": len(recs),
            }
        )
    out.sort(key=lambda x: (not x["overdue"], x["status"] != "due now",
                            -x["records"]))
    return out[:limit]


# ------------------------------------------------------------- discovery

def drought_board(db, country, limit=12):
    """Days since the last notable per sub-region. Longest first."""
    today = dt.date.today()
    names = {
        r[0]: r[1]
        for r in db.execute("SELECT code, name FROM regions")
    }
    # bare columns beside MAX() come from the max row (SQLite feature)
    rows = db.execute(
        """SELECT COALESCE(sub2, sub1) AS rg, comName, MAX(obsDt)
           FROM notables WHERE country=? GROUP BY rg""",
        (country,),
    ).fetchall()
    rows = [(r[0], r[2], r[1]) for r in rows]  # rg, last, species
    # also include sub-regions that have NEVER had a notable (from hotspots)
    ever = {r[0] for r in rows}
    for (rg,) in db.execute(
        """SELECT DISTINCT COALESCE(sub2, sub1) FROM hotspots
           WHERE country=? AND COALESCE(sub2, sub1) IS NOT NULL""",
        (country,),
    ):
        if rg and rg not in ever:
            rows.append((rg, None, None))

    board = []
    for rg, last, sp in rows:
        if not rg:
            continue
        days = (
            (today - parse_obsdt(last)).days if last else None
        )
        board.append(
            {
                "code": rg,
                "name": names.get(rg, rg),
                "days": days,
                "lastSpecies": sp,
                "lastDate": last,
            }
        )
    board.sort(key=lambda b: (b["days"] is None, -(b["days"] or 10 ** 6)))
    return board[:limit]


def hidden_gems(db, country, limit=10):
    """Sites that look like they should produce rarities (many notables
    nearby) but are lightly watched (low effort proxy)."""
    rows = candidate_hotspots(db, country, limit=4000)
    all_n = db.execute(
        "SELECT lat, lng, locId FROM notables WHERE country=? AND lat IS NOT NULL",
        (country,),
    ).fetchall()
    grid = defaultdict(list)
    for lat, lng, locid in all_n:
        grid[(int(lat), int(lng))].append((lat, lng, locid))

    max_eff = max((r[6] or 0) for r in rows) or 1
    gems = []
    for r in rows:
        (locid, name, lat, lng, sub1, sub2, nsp, latest, notable_n,
         last) = r
        if lat is None:
            continue
        nearby = 0
        own = 0
        for cl in range(int(lat) - 1, int(lat) + 2):
            for cg in range(int(lng) - 1, int(lng) + 2):
                for rlat, rlng, rloc in grid.get((cl, cg), ()):
                    d = haversine_km(lat, lng, rlat, rlng)
                    if d <= 50:
                        nearby += 1
                        if rloc == locid:
                            own += 1
        neighbor = nearby - own
        if neighbor < 3:
            continue
        eff = math.log1p(nsp or 0) / math.log1p(max_eff)
        score = math.log1p(neighbor) * (1.0 - 0.85 * eff)
        gems.append(
            {
                "locId": locid, "name": name,
                "lat": round(lat, 4), "lng": round(lng, 4),
                "nearby": neighbor, "numSpecies": nsp or 0,
                "own": own, "score": round(score, 2),
                "reason": f"{neighbor} rarities within 50km but only "
                          f"{nsp or 0} species recorded here",
            }
        )
    gems.sort(key=lambda g: -g["score"])
    return gems[:limit]


# --------------------------------------------------------------- tiers

def rarity_tiers(db, country):
    """Species -> reports/year tier from our own notable history:
    mega <1/yr, rare <4/yr, scarce <15/yr, else regular."""
    first = db.execute(
        "SELECT MIN(obsDt) FROM notables WHERE country=?", (country,)
    ).fetchone()[0]
    years = max(
        0.5,
        (dt.date.today() - parse_obsdt(first)).days / 365.25
        if first else 0.5,
    )
    tiers = {}
    for sp, n in db.execute(
        """SELECT speciesCode, COUNT(*) FROM notables
           WHERE country=? GROUP BY speciesCode""",
        (country,),
    ):
        rpy = n / years
        tiers[sp] = (
            "mega" if rpy < 1
            else "rare" if rpy < 4
            else "scarce" if rpy < 15
            else "regular"
        )
    return tiers
