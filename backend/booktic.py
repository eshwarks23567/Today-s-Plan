"""BookTic backend — movie showtime agent over live BookMyShow + District data.

Usage:
    python booktic.py              # chat loop (crawls on first question, caches for the day)
    python booktic.py --crawl     # just crawl and print the listings
    python booktic.py --city pune # different city slug

Needs GEMINI_API_KEY env var (free tier: https://aistudio.google.com/apikey).
"""
import json, os, re, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CACHE = Path(__file__).parent / "cache"
# matches the <select> in frontend/index.html — also the security boundary for
# city inputs: crawl() builds a filesystem path from this string, so anything
# not on this list must be rejected before it ever reaches Path construction
CITIES = {"hyderabad", "bengaluru", "mumbai", "ncr", "chennai", "pune", "kolkata"}


def _check_city(city: str) -> None:
    if city not in CITIES:
        raise ValueError(f"unknown city {city!r}")


def fetch(url: str) -> str:
    # ponytail: shelling out to curl because Akamai 403s python TLS; swap to curl_cffi if this breaks
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "30", "-A", UA, url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed ({r.returncode}): {url}")
    return r.stdout


def itemlist(html: str) -> list[dict]:
    """Movies from a page's JSON-LD ItemList (BMS and District both publish one)."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(block)
        except json.JSONDecodeError:
            continue
        for it in (d if isinstance(d, list) else [d]):
            if it.get("@type") == "ItemList":
                return [
                    {"title": e["name"].strip(), "url": e["url"], "image": e.get("image")}
                    for e in it["itemListElement"] if e.get("url")
                ]
    return []


_posters: dict = {}


def posters(city: str) -> list[str]:
    """Poster images for the gallery: movies (District JSON-LD) interleaved with
    events/concerts (BMS explore page banners, recropped to portrait via CDN params)."""
    _check_city(city)
    key = (city, date.today())
    if key not in _posters:
        dcity = DISTRICT_CITY.get(city, city)
        try:
            movies = [m["image"] for m in
                      itemlist(fetch(f"https://www.district.in/movies/?city={dcity}")) if m.get("image")]
        except RuntimeError:
            movies = []
        try:
            html = fetch(f"https://in.bookmyshow.com/explore/events-{city}")
            events = [re.sub(r"tr:[^/]+", "tr:w-300,h-426", u) for u in dict.fromkeys(
                re.findall(r'https://assets-in\.bmscdn\.com/discovery-catalog/events[^"\s)]+', html))]
        except RuntimeError:
            events = []
        from itertools import zip_longest
        _posters[key] = [u for pair in zip_longest(movies, events) for u in pair if u]
    return _posters[key]


def initial_state(html: str) -> dict:
    i = html.find("__INITIAL_STATE__")
    if i == -1:
        raise RuntimeError("no __INITIAL_STATE__")
    d, _ = json.JSONDecoder().raw_decode(html[html.find("{", i):])
    return d


def bms_showtimes(movie: dict, datecode: str) -> list[dict]:
    """BookMyShow: all venues/sessions/prices for one movie on one date."""
    # movie url: https://in.bookmyshow.com/hyderabad/movies/lenin/ET00441159
    # first path segment is BMS's canonical city slug — reuse it, don't trust user input
    m = re.search(r"bookmyshow\.com/([^/]+)/movies/([^/]+)/(ET\d+)", movie["url"])
    if not m:
        return []
    city, slug, code = m.groups()
    buy_url = f"https://in.bookmyshow.com/movies/{city}/{slug}/buytickets/{code}/{datecode}"
    try:
        state = initial_state(fetch(buy_url))
        day = state["showtimesByEvent"]["showDates"][datecode]
        venues_meta = day["primaryStatic"]["data"]["venues"]
        widgets = day["dynamic"]["data"]["showtimeWidgets"]
    except (RuntimeError, KeyError):
        return []  # no shows for this movie/date
    rows = []
    for w in widgets:
        if w.get("type") != "groupList":
            continue
        for group in w["data"]:
            for card in group.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                vmeta = venues_meta.get(card.get("id"), {})
                sessions = []
                for st in card.get("showtimes", []):
                    ad = st.get("additionalData", {})
                    prices = [float(c["curPrice"]) for c in ad.get("categories", []) if c.get("curPrice")]
                    if not ad.get("showTime") or not prices:
                        continue
                    sessions.append({"time": ad["showTime"], "min": min(prices), "max": max(prices),
                                     "attrs": ad.get("attributes", "")})
                if sessions:
                    rows.append({"venue": vmeta.get("venueName", card.get("id", "?")), "sessions": sessions})
    if rows:
        movie["book"] = buy_url
    return rows


# District calls Delhi-NCR differently from BMS's "ncr" slug
DISTRICT_CITY = {"ncr": "delhi-ncr"}


def district_showtimes(movie: dict, city: str, today_iso: str) -> list[dict]:
    """District: parse SSR pageData.nearbyCinemas[].sessions[] from the city movie page."""
    dcity = DISTRICT_CITY.get(city, city)
    url = movie["url"].replace("-movie-tickets-MV", f"-movie-tickets-in-{dcity}-MV")
    try:
        # session JSON sits escaped inside Next.js RSC payload; unescape then raw_decode
        txt = fetch(url).replace('\\"', '"')
        i = txt.find('"nearbyCinemas":[')
        if i == -1:
            return []
        cinemas, _ = json.JSONDecoder().raw_decode(txt[txt.find("[", i):])
    except (RuntimeError, json.JSONDecodeError):
        return []
    rows = []
    for c in cinemas:
        sessions = []
        for s in c.get("sessions", []):
            when = s.get("showTime", "")  # "2026-07-13T10:46" — UTC despite no suffix
            prices = [a["price"] for a in s.get("areas", []) if a.get("price")]
            if not when or not prices:
                continue
            t = datetime.fromisoformat(when) + timedelta(hours=5, minutes=30)  # → IST
            if t.date().isoformat() != today_iso:
                continue
            sessions.append({"time": t.strftime("%I:%M %p").lstrip("0"),
                             "min": min(prices), "max": max(prices)})
        if sessions:
            rows.append({"venue": c.get("cinemaInfo", {}).get("name", "?"), "sessions": sessions})
    if rows:
        movie["book"] = url
    return rows


def bms_events(city: str) -> list[str]:
    """Upcoming events/concerts: list from the city explore page, details from each
    event page's embedded state (its JSON-LD is junk — empty venues, fake dates)."""
    try:
        evs = itemlist(fetch(f"https://in.bookmyshow.com/explore/events-{city}"))
    except RuntimeError:
        return []

    def details(ev):
        try:
            html = fetch(ev["url"])
        except RuntimeError:
            return None
        cards = []
        try:
            queries = initial_state(html)["eventsSynopsisApi"]["queries"]
            for v in queries.values():
                data = (v or {}).get("data") or {}
                if isinstance(data, dict) and isinstance(data.get("cards"), list):
                    cards += [c for c in data["cards"] if isinstance(c, dict) and c.get("venue")]
        except (RuntimeError, KeyError):
            pass
        if cards:
            # tours embed one card per city — prefer this city's, else the first
            pick = next((c for c in cards if city.lower() in c.get("venue", "").lower()), cards[0])
            venue, when, price = pick.get("venue", "?"), pick.get("date", "?"), pick.get("price", "")
        else:
            # single events render details in HTML, not state — regex meta/description
            v = re.search(r'happening at ([^"<]{3,60})', html)
            w = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s?\d{1,2}\s?"
                          r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*", html)
            pr = re.search(r"(?:₹|Rs\.?\s?)\s?[\d,]+\s*onwards", html)
            if not (v or w):
                return None
            venue = v.group(1).strip() if v else "?"
            when = w.group(0) if w else "?"
            price = pr.group(0) if pr else ""
        price = (price or "price NA").replace("₹", "Rs ")
        return f"- {ev['title']} @ {venue} | {when} | {price} | book: {ev['url']}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [r for r in pool.map(details, evs) if r]


def section(mv: dict, rows: list[dict]) -> list[str]:
    lines = [f"\n## {mv['title']}  — book: {mv['book']}"]
    for r in rows:
        shows = ", ".join(
            f"{s['time']} Rs{s['min']:.0f}" + (f"-{s['max']:.0f}" if s["max"] > s["min"] else "")
            for s in r["sessions"])
        lines.append(f"- {r['venue']}: {shows}")
    return lines


CRAWL_TTL = 20 * 60  # seconds — listings older than this refresh in the background
DAYS = 5  # today + next 4: each extra day adds ~10 BMS page fetches per crawl
_refreshing: set = set()
_refresh_lock = threading.Lock()  # guards the check-and-set below against two
                                   # requests racing to both spawn a refresh thread


def crawl(city: str) -> str:
    """Current listings snapshot; stale-while-revalidate so answers never wait on a crawl."""
    _check_city(city)
    CACHE.mkdir(exist_ok=True)
    cache_file = CACHE / f"{city}_{date.today():%Y%m%d}.txt"
    if not cache_file.exists():
        return _crawl_now(city, cache_file)
    if time.time() - cache_file.stat().st_mtime > CRAWL_TTL:
        with _refresh_lock:
            if city not in _refreshing:
                _refreshing.add(city)
                threading.Thread(target=_refresh, args=(city, cache_file), daemon=True).start()
    return cache_file.read_text(encoding="utf-8")


def crawled_at(city: str) -> float:
    """Unix mtime of the snapshot answers are coming from; 0 when nothing is cached."""
    f = CACHE / f"{city}_{date.today():%Y%m%d}.txt"
    return f.stat().st_mtime if f.exists() else 0


def _refresh(city: str, cache_file: Path):
    try:
        _crawl_now(city, cache_file)
    except Exception as e:
        print(f"  background refresh failed for {city}: {e}", file=sys.stderr)
    finally:
        _refreshing.discard(city)


def _crawl_now(city: str, cache_file: Path) -> str:
    """Pull all sources fresh (BMS movies, District movies, BMS events) and write the snapshot."""
    today_iso = date.today().isoformat()
    for old in CACHE.glob(f"{city}_*.txt"):  # drop yesterday's snapshots
        if old != cache_file:
            old.unlink(missing_ok=True)

    bms_movies = itemlist(fetch(f"https://in.bookmyshow.com/explore/movies-{city}"))
    dcity = DISTRICT_CITY.get(city, city)
    district_movies = itemlist(fetch(f"https://www.district.in/movies/?city={dcity}"))
    if not bms_movies and not district_movies:
        raise RuntimeError("both sources returned no movies (layout changed or network down)")

    lines = [f"Movie showtimes in {city}, crawled at {datetime.now():%H:%M} (auto-refresh ~20 min). "
             f"BookMyShow sections cover {DAYS} dates (see headings); District covers today only. "
             "The same cinema can appear in both sources with different prices — treat them as "
             "competing ticket sellers."]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(DAYS):
            day = date.today() + timedelta(days=i)
            daycode = day.strftime("%Y%m%d")
            all_rows = list(pool.map(lambda mv: bms_showtimes(mv, daycode), bms_movies))
            got = [(mv, rows) for mv, rows in zip(bms_movies, all_rows) if rows]
            label = "today" if i == 0 else day.strftime("%A")
            lines.append(f"\n# BookMyShow — {day.isoformat()} ({label}), {len(got)} movies")
            for mv, rows in got:
                lines += section(mv, rows)  # mv['book'] was just set for THIS date
            print(f"  BMS {day.isoformat()}: {len(got)} movies", file=sys.stderr)
        dis_rows = list(pool.map(lambda mv: district_showtimes(mv, city, today_iso), district_movies))
    got = [(mv, rows) for mv, rows in zip(district_movies, dis_rows) if rows]
    lines.append(f"\n# District — {today_iso} (today), {len(got)} movies")
    for mv, rows in got:
        print(f"  District: {mv['title']} ({len(rows)} venues)", file=sys.stderr)
        lines += section(mv, rows)
    events = bms_events(city)
    print(f"  events: {len(events)}", file=sys.stderr)
    lines.append("\n# Events & concerts (source: BookMyShow; dates shown per event, not only today)")
    lines += events or ["- none found"]
    text = "\n".join(lines)
    tmp = cache_file.with_suffix(".tmp")  # atomic swap so a reader never sees a half-written file
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, cache_file)
    return text


def ask_llm(question: str, listings: str, history: list[dict]) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
    import prefs
    pref_line = prefs.summary()
    system = (
        "You are BookTic, a movie-ticket assistant. Answer ONLY from the listings below - never invent "
        "movies, venues, times or prices. Prices are per-ticket in INR (Rs). When asked for cheapest, "
        "compare across ALL venues AND both sources (BookMyShow and District sell tickets for the same "
        "cinemas at sometimes different prices - point out when one is cheaper). State tradeoffs "
        "(e.g. cheapest is a morning show). Include the booking link of whichever source you recommend "
        "- each date section has its own booking links, so use the link from the date the user wants. "
        "Movie showtimes cover the dates shown in the section headings; for dates beyond them, say "
        "you only see that far ahead. The events/concerts section lists upcoming events with their "
        f"own dates. Today is {date.today().isoformat()}."
        + (f"\n\n{pref_line}" if pref_line else "")
        + f"\n\n{listings}"
    )
    # build the turn without mutating history yet: if the call fails, the caller
    # retries with the SAME list, and a pre-appended question would be sent twice
    # (Gemini then sees two consecutive user turns, and the client saves that
    # malformed history to localStorage for good)
    msg = {"role": "user", "parts": [{"text": question}]}
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": history + [msg],
    }).encode()
    out = None
    for model in ("gemini-flash-latest", "gemini-flash-lite-latest"):  # lite = separate free quota
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    if out is None:
        raise RuntimeError("Gemini free-tier quota exhausted on both models — try again in a minute.")
    candidates = out.get("candidates") or []
    if not candidates:
        reason = out.get("promptFeedback", {}).get("blockReason", "no response")
        raise RuntimeError(f"Gemini returned no answer ({reason})")
    answer = candidates[0]["content"]["parts"][0]["text"]
    history.append(msg)
    history.append({"role": "model", "parts": [{"text": answer}]})
    return answer


def main():
    args = sys.argv[1:]
    city = args[args.index("--city") + 1] if "--city" in args else "hyderabad"
    print(f"BookTic — {city}. Crawling today's listings (first run takes ~a minute)...")
    listings = crawl(city)
    if "--crawl" in args:
        print(listings)
        return
    print("Ready. Ask away (Ctrl+C to quit).\n")
    history = []  # ponytail: in-memory session only, persist when multi-session memory matters
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q:
            print("\nbooktic>", ask_llm(q, listings, history), "\n")


if __name__ == "__main__":
    main()
