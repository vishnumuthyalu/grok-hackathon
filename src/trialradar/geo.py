"""Distance, hardcoded demo cities, and a rate-limited Nominatim geocoder."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import httpx

from trialradar.paths import geocode_cache_path

# City, ST -> (lat, lon). Used before any network call so the demo is stable.
CITY_COORDS: dict[str, tuple[float, float]] = {
    "austin, tx": (30.2672, -97.7431),
    "round rock, tx": (30.5083, -97.6789),
    "san antonio, tx": (29.4241, -98.4936),
    "houston, tx": (29.7604, -95.3698),
    "dallas, tx": (32.7767, -96.7970),
    "fort worth, tx": (32.7555, -97.3308),
    "el paso, tx": (31.7619, -106.4850),
    "waco, tx": (31.5493, -97.1467),
    "temple, tx": (31.0982, -97.3428),
    "college station, tx": (30.6280, -96.3344),
    "new york, ny": (40.7128, -74.0060),
    "boston, ma": (42.3601, -71.0589),
    "chicago, il": (41.8781, -87.6298),
    "los angeles, ca": (34.0522, -118.2437),
    "san francisco, ca": (37.7749, -122.4194),
    "san diego, ca": (32.7157, -117.1611),
    "seattle, wa": (47.6062, -122.3321),
    "denver, co": (39.7392, -104.9903),
    "lone tree, co": (39.5517, -104.8863),
    "atlanta, ga": (33.7490, -84.3880),
    "miami, fl": (25.7617, -80.1918),
    "tampa, fl": (27.9506, -82.4572),
    "orlando, fl": (28.5383, -81.3792),
    "philadelphia, pa": (39.9526, -75.1652),
    "pittsburgh, pa": (40.4406, -79.9959),
    "phoenix, az": (33.4484, -112.0740),
    "minneapolis, mn": (44.9778, -93.2650),
    "baltimore, md": (39.2904, -76.6122),
    "nashville, tn": (36.1627, -86.7816),
    "charlotte, nc": (35.2271, -80.8431),
    "raleigh, nc": (35.7796, -78.6382),
    "portland, or": (45.5152, -122.6784),
    "detroit, mi": (42.3314, -83.0458),
    "st louis, mo": (38.6270, -90.1994),
    "saint louis, mo": (38.6270, -90.1994),
    "cleveland, oh": (41.4993, -81.6944),
    "columbus, oh": (39.9612, -82.9988),
    "indianapolis, in": (39.7684, -86.1581),
    "kansas city, mo": (39.0997, -94.5786),
    "salt lake city, ut": (40.7608, -111.8910),
    "new orleans, la": (29.9511, -90.0715),
    "sacramento, ca": (38.5816, -121.4944),
    "las vegas, nv": (36.1699, -115.1398),
    "cincinnati, oh": (39.1031, -84.5120),
    "milwaukee, wi": (43.0389, -87.9065),
    "providence, ri": (41.8240, -71.4128),
    "richmond, va": (37.5407, -77.4360),
    "birmingham, al": (33.5207, -86.8025),
    "oklahoma city, ok": (35.4676, -97.5164),
    "memphis, tn": (35.1495, -90.0490),
    "louisville, ky": (38.2527, -85.7585),
    "hartford, ct": (41.7658, -72.6734),
    "buffalo, ny": (42.8864, -78.8784),
    "tucson, az": (32.2226, -110.9747),
    "albuquerque, nm": (35.0844, -106.6504),
    "omaha, ne": (41.2565, -95.9345),
    "jacksonville, fl": (30.3322, -81.6557),
    "washington, dc": (38.9072, -77.0369),
}

_STATE = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "district of columbia": "dc", "florida": "fl", "georgia": "ga", "hawaii": "hi",
    "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy",
}

_lock = threading.Lock()
_last_nominatim = 0.0


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def normalize_place(place: str) -> str:
    text = " ".join(place.lower().replace(".", "").split())
    if "," in text:
        city, region = [part.strip() for part in text.split(",", 1)]
        region = _STATE.get(region, region)
        return f"{city}, {region}"
    return text


def hardcoded_coords(place: str) -> tuple[float, float] | None:
    key = normalize_place(place)
    if key in CITY_COORDS:
        return CITY_COORDS[key]
    # "Austin" alone, when only one hardcoded city has that name.
    matches = [coords for name, coords in CITY_COORDS.items() if name.split(",")[0] == key]
    if len(matches) == 1:
        return matches[0]
    return None


def _read_cache() -> dict:
    path = geocode_cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _write_cache(cache: dict) -> None:
    path = geocode_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


def geocode(place: str, *, allow_network: bool = True) -> tuple[float, float] | None:
    """Resolve a city. Hardcoded demo cities and the disk cache win; Nominatim is last."""
    if not place or not place.strip():
        return None
    hit = hardcoded_coords(place)
    if hit:
        return hit
    key = normalize_place(place)
    with _lock:
        cache = _read_cache()
        if key in cache:
            lat, lon = cache[key]
            return float(lat), float(lon)
    if not allow_network:
        return None
    found = _nominatim(place)
    if not found:
        return None
    with _lock:
        cache = _read_cache()
        cache[key] = [found[0], found[1]]
        _write_cache(cache)
    return found


def _nominatim(place: str) -> tuple[float, float] | None:
    """One request per second, with a descriptive User-Agent, as Nominatim requires."""
    global _last_nominatim
    import os

    ua = os.environ.get(
        "NOMINATIM_USER_AGENT",
        "TrialRadar/0.1 (local hackathon demo)",
    )
    with _lock:
        wait = 1.05 - (time.monotonic() - _last_nominatim)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim = time.monotonic()
    try:
        response = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": ua, "Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None
    if not payload:
        return None
    try:
        return float(payload[0]["lat"]), float(payload[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def place_label(city: str | None, state: str | None) -> str:
    parts = [p for p in (city, state) if p]
    return ", ".join(parts)
