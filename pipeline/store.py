"""SQLite store for accumulated rare-bird data."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS notables (
    subId TEXT NOT NULL,
    speciesCode TEXT NOT NULL,
    comName TEXT,
    sciName TEXT,
    locId TEXT,
    locName TEXT,
    lat REAL,
    lng REAL,
    obsDt TEXT,
    howMany INTEGER,
    country TEXT,
    sub1 TEXT,
    sub2 TEXT,
    firstSeen TEXT,
    PRIMARY KEY (subId, speciesCode)
);
CREATE INDEX IF NOT EXISTS idx_notables_country ON notables(country, obsDt);
CREATE INDEX IF NOT EXISTS idx_notables_loc ON notables(locId, obsDt);
CREATE INDEX IF NOT EXISTS idx_notables_species ON notables(country, speciesCode);

CREATE TABLE IF NOT EXISTS hotspots (
    locId TEXT PRIMARY KEY,
    name TEXT,
    lat REAL,
    lng REAL,
    country TEXT,
    sub1 TEXT,
    sub2 TEXT,
    numSpeciesAllTime INTEGER,
    latestDt TEXT
);
CREATE INDEX IF NOT EXISTS idx_hotspots_country ON hotspots(country);

CREATE TABLE IF NOT EXISTS regions (
    code TEXT PRIMARY KEY,
    name TEXT,
    parent TEXT,
    rtype TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    country TEXT NOT NULL,
    locId TEXT NOT NULL,
    buildDate TEXT NOT NULL,
    horizonDays INTEGER,
    idx INTEGER,
    expected REAL,
    PRIMARY KEY (country, locId, buildDate)
);

CREATE TABLE IF NOT EXISTS fingerprints (
    region_key TEXT PRIMARY KEY,
    country TEXT,
    band INTEGER,
    bins TEXT,
    mean_speed REAL,
    n_samples INTEGER,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    subId TEXT NOT NULL,
    speciesCode TEXT NOT NULL,
    country TEXT,
    comName TEXT,
    locName TEXT,
    obsDt TEXT,
    path TEXT,
    PRIMARY KEY (subId, speciesCode)
);

CREATE TABLE IF NOT EXISTS frequencies (
    region TEXT NOT NULL,
    species TEXT NOT NULL,
    week INTEGER NOT NULL,
    freq REAL,
    PRIMARY KEY (region, species, week)
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def connect(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def get_meta(db, key, default=None):
    row = db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute(
        "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, str(value)),
    )
