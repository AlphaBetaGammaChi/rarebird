"""Minimal eBird API v2 client (stdlib only)."""
import json
import os
import time
import urllib.request
import urllib.error

BASE = "https://api.ebird.org/v2"


def api_key():
    key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "EBIRD_API_KEY not set. Get a free key at https://ebird.org/api/keygen"
        )
    return key


def get(path, params=None, retries=3):
    url = BASE + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if qs:
            url += "?" + qs
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"X-eBirdApiToken": api_key()}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            if e.code == 404:
                return []
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt * 2)
    return []


def countries():
    """List of {code, name} for every country eBird covers."""
    return get("/ref/region/list/country/world")


def subregions(region_type, parent_code):
    """subnational1 under a country, or subnational2 under a subnational1."""
    return get(f"/ref/region/list/{region_type}/{parent_code}")


def recent_notables(region_code, back=7):
    """Recent notable (rare) observations in a region. back: 1-30 days."""
    return get(
        f"/data/obs/{region_code}/recent/notable",
        params={"back": min(back, 30), "detail": "simple", "maxResults": 10000},
    )


def hotspots(region_code):
    """All hotspots in a region."""
    return get(f"/ref/hotspot/{region_code}", params={"fmt": "json"})
