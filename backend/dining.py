"""Dine-in restaurants from District, Zomato's going-out app: cost for two, cuisines,
rating, locality, today's offer ("Flat 25% OFF") and table-booking times.

Source rules, both from District's own robots.txt:
- only pages District lists in its dining sitemap, and
- never a url with a query string ("Disallow: /*?").
Swiggy and Zomato's own sites are not read at all: they block automated access.
"""
import gzip
import json
import re
import subprocess
import sys
import time

from booktic import UA, _check_city, fetch

# the app's city keys → District's dining city slugs
DINING_CITY = {"bengaluru": "bangalore"}
SITEMAP = "https://cdn.district.in/sitemap/sitemap-dining-listing-pages.xml.gz"
AREAS_TTL = 24 * 3600          # the sitemap changes rarely: one download a day is plenty

_areas: dict = {"at": 0.0, "by_city": {}}


def _fetch_gz(url: str) -> str:
    """fetch() reads text; the sitemap is a gzip file, so this one is read as bytes."""
    r = subprocess.run(
        ["curl", "-s", "-L", "--proto", "=https", "--proto-redir", "=https",
         "--max-time", "30", "-A", UA, "-w", "%{http_code}", url],
        capture_output=True,
    )
    body, status = r.stdout[:-3], r.stdout[-3:]
    if r.returncode != 0 or status != b"200":
        raise RuntimeError(f"sitemap fetch failed (exit {r.returncode}, HTTP {status.decode(errors='replace')})")
    return gzip.decompress(body).decode("utf-8", "replace")


def parse_areas(sitemap_xml: str) -> dict[str, dict[str, str]]:
    """{dining city: {"banjara hills": url}} from the listing sitemap's area pages
    (/dining/{city}/{zone}/{area}-restaurants)."""
    by_city: dict[str, dict[str, str]] = {}
    for url in re.findall(r"<loc>([^<?]+)</loc>", sitemap_xml):
        m = re.match(r"https://www\.district\.in/dining/([a-z-]+)/[a-z0-9-]+/([a-z0-9-]+)-restaurants$", url)
        if m:
            by_city.setdefault(m.group(1), {})[m.group(2).replace("-", " ")] = url
    return by_city


def areas(city: str) -> dict[str, str]:
    """Neighbourhood name → its District listing page, for one of the app's cities."""
    _check_city(city)
    if time.time() - _areas["at"] > AREAS_TTL:
        try:
            _areas["by_city"], _areas["at"] = parse_areas(_fetch_gz(SITEMAP)), time.time()
        except (RuntimeError, OSError, EOFError) as e:   # keep yesterday's list over none
            print(f"  dining sitemap failed: {e}", file=sys.stderr)
    return _areas["by_city"].get(DINING_CITY.get(city, city), {})


def find_area(city: str, text: str) -> str | None:
    """The neighbourhood a question names, longest name first ("banjara hills" over "hills")."""
    t = text.lower()
    return next((a for a in sorted(areas(city), key=len, reverse=True) if re.search(rf"\b{re.escape(a)}\b", t)), None)


_CARD = re.compile(r'\{"id":"(\d+)","title":\{"text":')


def parse_restaurants(html: str) -> list[dict]:
    """The restaurant cards in a District dining page's embedded data."""
    t = html.replace('\\"', '"')
    dec, seen, out = json.JSONDecoder(), set(), []
    for m in _CARD.finditer(t):
        if m.group(1) in seen:
            continue
        try:
            card, _ = dec.raw_decode(t, m.start())
        except ValueError:
            continue
        tr = card.get("tracking")
        if not isinstance(tr, dict):
            continue                     # a different object that happens to start the same way
        seen.add(m.group(1))
        slots = (card.get("slotBooking") or {}).get("slots") or []
        out.append({
            "name": (card.get("title") or {}).get("text", "").strip(),
            "url": ((card.get("action") or {}).get("url") or "").split("?")[0],
            "rating": tr.get("rating"),
            "cuisines": tr.get("cuisines") or "",
            "cost_for_two": tr.get("costForTwo") if isinstance(tr.get("costForTwo"), int) else None,
            "locality": tr.get("locality") or "",
            "offer": next((g.get("label") for g in card.get("tags") or [] if str(g.get("id", "")).startswith("offer")), None),
            "open": ((card.get("serviceability") or {}).get("type") == "OPEN"),
            "tables": [s.get("title") for s in slots if isinstance(s, dict) and s.get("title")],
        })
    return [r for r in out if r["name"] and r["url"]]


LIST_TTL = 30 * 60             # offers and open/closed move during the day; menus do not
_lists: dict[str, tuple[float, list[dict]]] = {}


def restaurants(city: str, area: str | None = None) -> list[dict]:
    """Restaurants for a city, or for one of its neighbourhoods when that page exists."""
    _check_city(city)
    url = areas(city).get(area or "") or f"https://www.district.in/dining/{DINING_CITY.get(city, city)}/restaurants"
    hit = _lists.get(url)
    if hit and time.time() - hit[0] < LIST_TTL:
        return hit[1]
    rows = parse_restaurants(fetch(url))
    _lists[url] = (time.time(), rows)
    return rows


# ponytail: a keyword test, not a classifier; widen the list when a food question slips past it
_FOOD = re.compile(r"\b(food|eat|eating|dinner|lunch|breakfast|brunch|dine|dining|restaurants?|cafe|caf[eé]s?|"
                   r"biryani|buffet|drinks?|bar|pub|hungry|offers?|cost for two)\b", re.I)


def wants_food(question: str) -> bool:
    return bool(_FOOD.search(question))


def for_question(city: str, question: str) -> str:
    """The dining section to add to the listings for this question, or "" when it is
    not about food. A failure becomes a note, never an error: the movies still answer."""
    if not wants_food(question):
        return ""
    area = find_area(city, question)
    where = f"{area.title()}, {city.title()}" if area else city.title()
    try:
        rows = restaurants(city, area)
    except RuntimeError as e:
        print(f"  dining failed: {e}", file=sys.stderr)
        return f"\n# Dining: District's restaurant listings could not be read just now."
    return dining_section(rows, where)


def dining_section(rows: list[dict], where: str) -> str:
    """Listings text for the model, in the same spirit as the showtime sections."""
    lines = [f"\n# Dining (source: District) — {where}, {len(rows)} restaurants. Prices are cost for two; "
             "offers are District's dine-in offers, applied when booked or paid through District."]
    for r in rows:
        bits = [f"₹{r['cost_for_two']} for two" if r["cost_for_two"] else None, r["cuisines"], r["locality"],
                f"rated {r['rating']}" if r["rating"] else None, f"offer: {r['offer']}" if r["offer"] else None,
                "open now" if r["open"] else "closed now"]
        tables = f"; tables from {r['tables'][0]} to {r['tables'][-1]}" if r["tables"] else ""
        lines.append(f"- {r['name']} — {'; '.join(b for b in bits if b)}{tables} — book: {r['url']}")
    return "\n".join(lines)
