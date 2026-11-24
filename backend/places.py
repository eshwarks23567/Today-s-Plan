"""Sights and malls for the day planner, from Wikivoyage, plus neighbourhood locations
from OpenStreetMap's Nominatim.

Wikivoyage, not OpenStreetMap, for the places themselves: OSM's Indian city data tags
clothing showrooms as malls, leaves parks unnamed and rarely has opening hours, while
Wikivoyage's listings are curated and often carry hours, a price and coordinates.
Both are free APIs meant for automated use; each asks clients to name themselves and
go easy, so everything here is cached on disk and fetched about once a week.
"""
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from booktic import _check_city

AGENT = {"User-Agent": "TodaysPlan/1.0 (personal day planner)"}
CACHE = Path(__file__).with_name("cache")
TTL = 7 * 24 * 3600
WIKIVOYAGE = {"hyderabad": "Hyderabad", "bengaluru": "Bangalore", "mumbai": "Mumbai", "ncr": "Delhi",
              "chennai": "Chennai", "pune": "Pune", "kolkata": "Kolkata"}
MAX_SUBPAGES = 10      # a city's district articles; one request each, a second apart


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=25) as r:
        return json.load(r)


def _cached(name: str, build):
    """A JSON file in cache/, rebuilt when older than TTL; a failed rebuild keeps the old copy."""
    path = CACHE / name
    if path.exists() and time.time() - path.stat().st_mtime < TTL:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = build()
    except (OSError, ValueError, KeyError) as e:    # network, bad JSON, odd payload
        print(f"  {name} failed: {e}", file=sys.stderr)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _wikitext(title: str) -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "wikitext", "format": "json",
                                "formatversion": 2, "redirects": 1})
    return _get_json("https://en.wikivoyage.org/w/api.php?" + q)["parse"]["wikitext"]


def parse_listings(wikitext: str, district: str) -> list[dict]:
    """{{see|...}}, {{do|...}} and {{buy|...}} templates as places. Templates nest
    ({{IATA|..}} inside a description), so the end is found by counting braces."""
    out = []
    for m in re.finditer(r"\{\{\s*(see|do|buy)\s*\|", wikitext, re.I):
        depth, i = 0, m.start()
        while i < len(wikitext):
            if wikitext.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif wikitext.startswith("}}", i):
                depth, i = depth - 1, i + 2
                if depth == 0:
                    break
            else:
                i += 1
        body = wikitext[m.end():i - 2]
        f = {k.strip().lower(): v.strip() for k, v in re.findall(r"(?:^|\|)\s*([a-zA-Z]+)\s*=\s*([^|]*)", body)}
        name = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", f.get("name", "")).strip()
        if not name:
            continue
        try:
            lat, lon = float(f.get("lat", "")), float(f.get("long", ""))
        except ValueError:
            lat = lon = None
        out.append({"name": name, "kind": m.group(1).lower(), "district": district,
                    "hours": f.get("hours", ""), "price": f.get("price", ""), "lat": lat, "lon": lon})
    return out


def city_places(city: str) -> list[dict]:
    """Every see/do/buy listing in the city's Wikivoyage article and its district articles."""
    _check_city(city)
    title = WIKIVOYAGE[city]

    def build():
        main = _wikitext(title)
        found = parse_listings(main, title)
        subs = list(dict.fromkeys(re.findall(rf"\[\[({re.escape(title)}/[^\]|#]+)", main)))[:MAX_SUBPAGES]
        for sub in subs:
            time.sleep(1)               # Wikimedia asks for serial, unhurried requests
            try:
                found += parse_listings(_wikitext(sub), sub.split("/", 1)[1])
            except (OSError, ValueError, KeyError) as e:
                print(f"  wikivoyage {sub} failed: {e}", file=sys.stderr)
        return found
    return _cached(f"places_{city}.json", build) or []


def geocode(area: str, city: str) -> tuple[float, float] | None:
    """Where a neighbourhood is, once per neighbourhood for good."""
    def build():
        q = urllib.parse.urlencode({"q": f"{area}, {WIKIVOYAGE[city]}, India", "format": "json", "limit": 1})
        hit = _get_json("https://nominatim.openstreetmap.org/search?" + q)
        return [float(hit[0]["lat"]), float(hit[0]["lon"])] if hit else None
    got = _cached(f"geo_{city}_{re.sub(r'[^a-z0-9]+', '-', area.lower())}.json", build)
    return (got[0], got[1]) if got else None


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = map(lambda p: (math.radians(p[0]), math.radians(p[1])), (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


_RANGE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\.?\s*[-–to]+\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?\s*m?\b", re.I)


def parse_hours(text: str) -> tuple[int, int] | None:
    """Opening and closing minute of the first time range in a Wikivoyage hours field,
    or None when it has none. ponytail: ignores weekday rules ("closed Fri"), so the
    plan always tells people to check the hours itself."""
    if re.search(r"24\s*h(ou)?rs?|open 24", text, re.I):
        return 0, 24 * 60
    m = _RANGE.search(text)
    if not m:
        return None
    def at(h, mi, mer):
        h, mi = int(h), int(mi or 0)
        if mer:
            h = h % 12 + (12 if mer.lower() == "p" else 0)
        return h * 60 + mi
    o, c = at(m.group(1), m.group(2), m.group(3)), at(m.group(4), m.group(5), m.group(6) or m.group(3))
    if not m.group(3) and m.group(6):
        shared = at(m.group(1), m.group(2), m.group(6))
        if shared < c:
            o = shared                     # "2-8:30PM" means 2 PM to 8:30 PM
    if not m.group(6) and not m.group(3) and c <= o:
        c += 12 * 60                       # "11-8" means 11 AM to 8 PM
    return (o, c) if c > o else None


def near(city: str, area: str | None, limit: int = 12) -> list[dict]:
    """Sights and malls, nearest first when there is an area to be near."""
    rows = [p for p in city_places(city) if p["name"]]
    centre = geocode(area, city) if area else None
    for p in rows:
        p["km"] = round(km(centre, (p["lat"], p["lon"])), 1) if centre and p["lat"] is not None else None
        p["open_close"] = parse_hours(p["hours"])
    if centre:
        rows = sorted([p for p in rows if p["km"] is not None and p["km"] <= 12], key=lambda p: p["km"])
    else:  # no area: places with known hours first, they are the ones a plan can rely on
        rows = sorted(rows, key=lambda p: p["open_close"] is None)
    return rows[:limit]
