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


# Cloudflare answers a challenged request with HTTP 200 and an interstitial, so a
# blocked fetch looks exactly like a successful one until the parser quietly finds
# nothing in it — the silent-zero that left the listings empty for five days once
# already. Only the title is a reliable tell: the challenge-platform script tag
# appears on perfectly good pages too.
_CHALLENGE = re.compile(r"<title>\s*just a moment", re.I)


def fetch(url: str, tries: int = 3) -> str:
    # ponytail: shelling out to curl because Akamai 403s python TLS; swap to curl_cffi if this breaks
    # --proto pins this to https even after a redirect: most urls here come out of
    # scraped JSON-LD, and curl speaks file://, scp:// and more, so an attacker-set
    # link in a listing could otherwise make us read local files into the prompt
    problem = "no attempt"
    for attempt in range(tries):
        r = subprocess.run(
            ["curl", "-s", "-L", "--proto", "=https", "--proto-redir", "=https",
             "--max-time", "30", "-A", UA, url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0 or not r.stdout:
            problem = f"exit {r.returncode}"
        elif _CHALLENGE.search(r.stdout[:2000]):
            problem = "got a Cloudflare interstitial"
        else:
            return r.stdout
        if attempt < tries - 1:
            time.sleep(1.0 + attempt)  # a challenge usually clears on the next try
    raise RuntimeError(f"fetch failed ({problem}) after {tries} tries: {url}")


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
        # keyed by day, so yesterday's entries are dead weight — drop them rather
        # than growing one entry per city per day for as long as the process lives
        for stale in [k for k in _posters if k[1] != key[1]]:
            _posters.pop(stale, None)
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


def _showtime_prices(showtime: dict) -> tuple[list[float], str]:
    """Per-category prices are no longer on the showtime itself — they live in the
    bottom sheet the card opens on double-tap, one widget per seat category
    (layoutId 'seat-category-type-<availability>') with the price as a display
    string like '₹ 1,250.00'. Returns (prices, format) where format is
    e.g. 'Hindi • 2D'."""
    widgets = ((((showtime.get("customGestureCTA") or {}).get("additionalData") or {})
                .get("bottomSheetData") or {}).get("widgets")) or []
    prices, fmt = [], ""
    for w in widgets:
        var = w.get("variableData") or {}
        layout = str(w.get("layoutId", ""))
        if layout == "format-container":
            fmt = var.get("format", "")
        elif layout.startswith("seat-category-type-"):
            digits = re.sub(r"[^\d.]", "", str(var.get("seatCost", "")))
            if digits:
                prices.append(float(digits))
    return prices, fmt


def _bms_page(buy_url: str) -> tuple[str, str, list[dict]]:
    """Parse a buytickets page once, flat. Returns (region code, the date the page
    ACTUALLY served, sessions) — both the listings crawl and the seat-layout deep
    link read from this single traversal.

    BMS moved showtimes out of showtimesByEvent.showDates (now served empty) into
    showtimesFunctionalApi, under a query name that embeds event, date and region
    — so the key is matched by prefix, and the region is read back out of it."""
    try:
        queries = initial_state(fetch(buy_url))["showtimesFunctionalApi"]["queries"]
        key, q = next((k, v) for k, v in queries.items()
                      if k.startswith("fetchPrimaryDynamic") and (v or {}).get("data"))
        dyn = q["data"]["data"]
    except (RuntimeError, KeyError, TypeError, StopIteration):
        return "", "", []
    region = key.rsplit("-", 1)[-1]  # fetchPrimaryDynamic-ET00447840---20260811-HYD
    served = str((dyn.get("additionalData") or {}).get("dateCode") or "")
    sessions = []
    for w in dyn.get("showtimeWidgets", []):
        if w.get("type") != "groupList":
            continue
        for group in w.get("data", []):
            for card in group.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                cad = card.get("additionalData") or {}
                for section in card.get("showtimesSections", []):
                    for st in section.get("showtimes", []):
                        sad = st.get("additionalData") or {}
                        prices, fmt = _showtime_prices(st)
                        if not sad.get("showTime") or not prices:
                            continue
                        sessions.append({
                            "venue": cad.get("venueName") or card.get("id", "?"),
                            "venue_code": cad.get("venueCode", ""),
                            "session_id": str(sad.get("sessionId", "")),
                            "time": sad["showTime"],
                            "min": min(prices), "max": max(prices), "attrs": fmt})
    return region, served, sessions


def bms_showtimes(movie: dict, datecode: str) -> list[dict]:
    """BookMyShow: all venues/sessions/prices for one movie on one date."""
    # movie url: https://in.bookmyshow.com/hyderabad/movies/lenin/ET00441159
    # first path segment is BMS's canonical city slug — reuse it, don't trust user input
    m = re.search(r"bookmyshow\.com/([^/]+)/movies/([^/]+)/(ET\d+)", movie["url"])
    if not m:
        return []
    city, slug, code = m.groups()
    buy_url = f"https://in.bookmyshow.com/movies/{city}/{slug}/buytickets/{code}/{datecode}"
    _, served, sessions = _bms_page(buy_url)
    # A movie with nothing on the requested date is served its NEXT available date
    # instead of an empty page, so without this check next Friday's showtimes get
    # filed under today — silently, and at the right-looking price.
    if served != datecode:
        return []
    rows: dict[str, list] = {}
    for s in sessions:
        rows.setdefault(s["venue"], []).append(
            {"time": s["time"], "min": s["min"], "max": s["max"], "attrs": s["attrs"]})
    if rows:
        movie["book"] = buy_url
    return [{"venue": v, "sessions": ss} for v, ss in rows.items()]


def bms_seat_url(buy_url: str, venue: str, time_str: str) -> str | None:
    """Deep link straight to one show's seat layout, or None if it can't be resolved.

    BMS used to answer a showtime click with a "how many seats" dialog; that dialog
    is gone and the click now just navigates here. Since every part of this URL is
    data BMS already publishes on the buytickets page, the destination can be built
    directly — which also sidesteps the seat layout refusing to render inside an
    automated browser at all."""
    m = re.search(r"/buytickets/(ET\d+)/(\d{8})", buy_url)
    if not m:
        return None
    event, datecode = m.groups()
    region, served, sessions = _bms_page(buy_url)
    if not region or served != datecode:
        return None
    vkey = venue.split(":")[0].strip().lower()  # "AAA Cinemas: Ameerpet" -> "aaa cinemas"
    for s in sessions:
        if s["time"] != time_str or not (s["venue_code"] and s["session_id"]):
            continue
        if vkey and vkey not in s["venue"].lower():
            continue
        return (f"https://in.bookmyshow.com/movies/{region.lower()}/seat-layout/"
                f"{event}/{s['venue_code']}/{s['session_id']}/{datecode}")
    return None


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
    """One block per movie. Language and format go in because in India they are the
    first filter most people apply — "any Telugu shows after 9?" is unanswerable
    without them, and they were being parsed and then dropped here."""
    lines = [f"\n## {mv['title']}  — book: {mv['book']}"]
    for r in rows:
        # A venue almost always runs one movie in a single language and format, so
        # repeating it on every showtime is the same few words 1,100 times over —
        # ~2,000 tokens per request. Hoist it to the venue when it is shared, and
        # only annotate per showtime when a venue genuinely mixes them.
        formats = {_session_format(s) for s in r["sessions"]}
        shared = formats.pop() if len(formats) == 1 else ""
        shows = []
        for s in r["sessions"]:
            price = f"Rs{s['min']:.0f}" + (f"-{s['max']:.0f}" if s["max"] > s["min"] else "")
            fmt = "" if shared else _session_format(s)
            shows.append(f"{s['time']} {price}" + (f" {fmt}" if fmt else ""))
        venue = f"{r['venue']} [{shared}]" if shared else r["venue"]
        lines.append(f"- {venue}: {', '.join(shows)}")
    return lines


def _session_format(s: dict) -> str:
    """'English • 2D | LASER DOLBY ATMOS' -> 'English 2D'. The sound-tech tail costs
    tokens on every request and nobody filters a cinema search on Dolby."""
    fmt = (s.get("attrs") or "").split("|")[0].replace("•", " ").strip()
    return re.sub(r"\s{2,}", " ", fmt)


# Today's showtimes sell out and shift; Friday's 8pm show is still Friday's 8pm
# show an hour later. Refreshing all five days on the fast clock meant four fifths
# of every crawl re-downloading data that had not changed — and from one IP, that
# volume is how a scraper gets blocked rather than rate-limited.
TODAY_TTL = 20 * 60
FUTURE_TTL = 60 * 60
DAYS = 5  # today + next 4: each extra day adds ~10 BMS page fetches to the future part
_refreshing: set = set()   # (city, part) pairs currently being rebuilt
_refresh_lock = threading.Lock()  # guards the check-and-set below against two
                                   # requests racing to both spawn a refresh thread
# Serialises snapshot reads against the swap that replaces them. Every reader is a
# request thread in THIS process, so taking it around the read means no handle is
# open when the swap runs — which is what Windows requires. It deliberately does
# NOT cover the crawl itself: that is seconds of network, and readers must not
# block on it. atomic_swap's retry then only has to survive openers we don't
# control (this project lives in a OneDrive folder, which scans on its own clock).
_snapshot_lock = threading.Lock()


def _part_path(city: str, part: str) -> Path:
    return CACHE / f"{city}_{date.today():%Y%m%d}_{part}.txt"


# Does this turn need the later dates at all? Most do not — "what's on tonight",
# "cheapest seats", "book that one" are all answered by today alone, and the future
# section is a third of a ~26,000-token prompt carried on EVERY request. Getting
# this wrong in the generous direction only costs tokens; getting it wrong the other
# way costs an answer, so the pattern leans towards including.
AHEAD = re.compile(
    r"\b(tomorrow|weekend|next\s+week|this\s+week|later|upcoming|advance|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
    r"|\b\d{1,2}\s*(st|nd|rd|th)\b"
    r"|\b(jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)


def needs_future(question: str, history: list[dict] | None = None) -> bool:
    """True when the question — or the couple of turns it is replying to — refers to
    a day other than today. A follow-up like "book the second one" carries no date of
    its own, so the recent turns are read too."""
    recent = [question]
    for turn in (history or [])[-4:]:
        recent += [p.get("text", "") for p in turn.get("parts", [])]
    return any(AHEAD.search(t or "") for t in recent)


def crawl(city: str, ahead: bool = True) -> str:
    """Current listings; stale-while-revalidate so answers never wait on a crawl.

    Two snapshots on two clocks — today refreshes three times an hour, the rest of
    the week once. `ahead=False` sends only today, which is what most questions
    actually need and roughly a third less prompt on every one of them."""
    _check_city(city)
    CACHE.mkdir(exist_ok=True)
    for old in CACHE.glob(f"{city}_*.txt"):  # yesterday's snapshots
        if not old.name.startswith(f"{city}_{date.today():%Y%m%d}_"):
            old.unlink(missing_ok=True)
    today = _part(city, "today", TODAY_TTL, _build_today)
    if ahead:
        return today + "\n" + _part(city, "future", FUTURE_TTL, _build_future)
    # Say so, or the model reads "only today is here" as "nothing else is playing"
    # and tells the user this is all there is.
    return today + ("\n\n# Later dates\nOnly today's listings are loaded this turn. Shows for "
                    "the next few days DO exist — if the user asks about another day, say you "
                    "can look it up and ask them to name the day.")


def _part(city: str, part: str, ttl: int, build) -> str:
    path = _part_path(city, part)
    if not path.exists():
        return _write_part(city, part, build)
    if time.time() - path.stat().st_mtime > ttl:
        with _refresh_lock:
            if (city, part) not in _refreshing:
                _refreshing.add((city, part))
                threading.Thread(target=_refresh, args=(city, part, build), daemon=True).start()
    with _snapshot_lock:  # no reader holds the file open while the swap runs
        return path.read_text(encoding="utf-8")


def _refresh(city: str, part: str, build):
    try:
        _write_part(city, part, build)
    except Exception as e:
        print(f"  background refresh failed for {city}/{part}: {e}", file=sys.stderr)
    finally:
        _refreshing.discard((city, part))


def _write_part(city: str, part: str, build) -> str:
    text = build(city)
    path = _part_path(city, part)
    tmp = path.with_suffix(".tmp")  # atomic swap so a reader never sees a half-written file
    tmp.write_text(text, encoding="utf-8")
    with _snapshot_lock:
        atomic_swap(tmp, path)
    return text


def _bms_day(city: str, movies: list[dict], offset: int, pool) -> list[str]:
    day = date.today() + timedelta(days=offset)
    daycode = day.strftime("%Y%m%d")
    all_rows = list(pool.map(lambda mv: bms_showtimes(mv, daycode), movies))
    got = [(mv, rows) for mv, rows in zip(movies, all_rows) if rows]
    label = "today" if offset == 0 else day.strftime("%A")
    lines = [f"\n# BookMyShow — {day.isoformat()} ({label}), {len(got)} movies"]
    for mv, rows in got:
        lines += section(mv, rows)  # mv['book'] was just set for THIS date
    print(f"  BMS {day.isoformat()}: {len(got)} movies", file=sys.stderr)
    return lines


def _build_today(city: str) -> str:
    """Everything that actually moves during a day: today's shows, both sources, events."""
    today_iso = date.today().isoformat()
    bms_movies = itemlist(fetch(f"https://in.bookmyshow.com/explore/movies-{city}"))
    dcity = DISTRICT_CITY.get(city, city)
    district_movies = itemlist(fetch(f"https://www.district.in/movies/?city={dcity}"))
    if not bms_movies and not district_movies:
        raise RuntimeError("both sources returned no movies (layout changed or network down)")

    lines = [f"Movie showtimes in {city}, crawled at {datetime.now():%H:%M} (today refreshes "
             f"every ~20 min, later dates hourly). Each section is labelled with its own date (see "
             "headings); District covers today only. The same cinema can appear in both sources "
             "with different prices — treat them as competing ticket sellers."]
    with ThreadPoolExecutor(max_workers=8) as pool:
        lines += _bms_day(city, bms_movies, 0, pool)
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
    return "\n".join(lines)


def _build_future(city: str) -> str:
    """Tomorrow onwards — the bulk of the fetches, and the part that barely changes."""
    movies = itemlist(fetch(f"https://in.bookmyshow.com/explore/movies-{city}"))
    lines = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for offset in range(1, DAYS):
            lines += _bms_day(city, movies, offset, pool)
    return "\n".join(lines)


def atomic_swap(tmp: Path, dest: Path, tries: int = 50) -> None:
    """Move tmp onto dest, retrying while Windows says the destination is busy.

    POSIX replaces a file out from under an open handle happily. Windows refuses
    with PermissionError while ANY handle is open, and Python's read path does not
    ask for FILE_SHARE_DELETE — so a reader that happens to be mid-read fails the
    swap outright. Since _refresh only logs its failure, one lost race would leave
    the snapshot stale for the rest of the day. Reads take microseconds, so a short
    retry wins the race a single attempt loses."""
    for _ in range(tries):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            time.sleep(0.02)
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"could not swap in {dest.name}: the file stayed busy")


def crawled_at(city: str) -> float:
    """Unix mtime of the snapshot answers are coming from; 0 when nothing is cached."""
    f = _part_path(city, "today")
    return f.stat().st_mtime if f.exists() else 0


def _read_stream(resp, on_token) -> dict:
    """streamGenerateContent?alt=sse emits one `data: {...}` line per partial
    candidate. Collapse them back into exactly the shape generateContent returns,
    so there stays one response-parsing path below, calling on_token as prose lands."""
    text, extra = [], {}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        cand = (chunk.get("candidates") or [{}])[0]
        for p in (cand.get("content") or {}).get("parts") or []:
            if "functionCall" in p:
                extra["functionCall"] = p["functionCall"]
            elif p.get("text"):
                text.append(p["text"])
                on_token(p["text"])
        if cand.get("finishReason"):
            extra["finishReason"] = cand["finishReason"]
        if chunk.get("promptFeedback"):
            extra["promptFeedback"] = chunk["promptFeedback"]
    parts = []
    if "functionCall" in extra:
        parts.append({"functionCall": extra["functionCall"]})
    if text:
        parts.append({"text": "".join(text)})
    return {"candidates": [{"content": {"parts": parts},
                            "finishReason": extra.get("finishReason")}],
            "promptFeedback": extra.get("promptFeedback") or {}}


# Which model answers. "gemini" is the default; anything else is spoken to over the
# OpenAI chat-completions shape, which is the lingua franca of open-weight hosting —
# Groq, OpenRouter, Together, and a local Ollama or llama.cpp server all implement
# it. So one extra code path buys every open-source option, cloud or on your own
# machine, rather than one integration per vendor.
PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
LLM_BASE = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
# Applies per socket read, so it bounds a stalled provider rather than the whole
# answer — streaming keeps resetting it. A minute of dead air is not worth waiting
# through when the message at the end is only going to say "it did not respond".
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", 45))


def _openai_tools(tools: list | None) -> list:
    """Gemini declares tools as function_declarations; OpenAI wraps each one in
    {"type": "function", ...}. The JSON Schema in `parameters` is identical, which
    is why one tool definition serves both."""
    out = []
    for t in tools or []:
        for fn in t.get("function_declarations", []):
            out.append({"type": "function", "function": {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {})}})
    return out


def _openai_messages(system: str, history: list[dict], question: str) -> list[dict]:
    """History is stored in Gemini's shape (it is what the browser saves), so it is
    translated on the way out rather than migrating everyone's saved chats."""
    msgs = [{"role": "system", "content": system}]
    for turn in history:
        text = "".join(p.get("text", "") for p in turn.get("parts", []))
        msgs.append({"role": "assistant" if turn.get("role") == "model" else "user",
                     "content": text})
    msgs.append({"role": "user", "content": question})
    return msgs


def _openai_result(message: dict):
    """Reply text, or the same {"name", "args"} shape ask_llm returns for Gemini —
    so agent.py never learns which provider answered."""
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        return {"name": fn.get("name", ""), "args": args}
    return message.get("content") or ""


def _read_openai_stream(resp, on_token):
    """Same job as _read_stream, different wire format: content arrives as
    delta.content, and a tool call's arguments arrive as a JSON string in
    fragments that have to be concatenated before they parse."""
    text, call_name, call_args = [], "", []
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        delta = ((chunk.get("choices") or [{}])[0].get("delta")) or {}
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if fn.get("name"):
                call_name = fn["name"]
            if fn.get("arguments"):
                call_args.append(fn["arguments"])
        piece = delta.get("content")
        if piece:
            text.append(piece)
            on_token(piece)
    return _openai_result({"content": "".join(text)} if not call_name else
                          {"tool_calls": [{"function": {"name": call_name,
                                                        "arguments": "".join(call_args)}}]})


def _ask_openai(system: str, history: list[dict], question: str, tools, on_token):
    key = os.environ.get("LLM_API_KEY", "")
    payload = {"model": LLM_MODEL, "messages": _openai_messages(system, history, question)}
    if tools:
        payload["tools"] = _openai_tools(tools)
    if on_token:
        payload["stream"] = True
    # Cloudflare sits in front of some of these APIs and rejects Python's default
    # User-Agent outright (403, Cloudflare error 1010) — the same fingerprint check
    # that makes fetch() shell out to curl for BookMyShow. Ask like a browser.
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:  # a local Ollama or llama.cpp server needs no key at all
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{LLM_BASE}/chat/completions",
                                 data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
            if on_token:
                return _read_openai_stream(r, on_token)
            out = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"{LLM_MODEL} at {LLM_BASE} returned HTTP {e.code}: {detail}")
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"{LLM_BASE} did not respond within {LLM_TIMEOUT}s "
                           f"({getattr(e, 'reason', e)}). Is it running and reachable?")
    return _openai_result((out.get("choices") or [{}])[0].get("message") or {})


def _ask_gemini(system: str, history: list[dict], question: str, tools, on_token):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)")
    payload = {"system_instruction": {"parts": [{"text": system}]},
               "contents": history + [{"role": "user", "parts": [{"text": question}]}]}
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload).encode()
    out, last, stalled = None, None, False
    # 429 = this model's free quota is spent, 500/503 = Gemini itself is wobbling.
    # Both are worth trying the other model for; anything else is our own bug and
    # should surface immediately rather than being retried into a vaguer message.
    RETRYABLE = (429, 500, 503)
    verb = "streamGenerateContent?alt=sse" if on_token else "generateContent"
    for model in ("gemini-flash-latest", "gemini-flash-lite-latest"):  # lite = separate free quota
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{verb}",
            data=body, headers={"Content-Type": "application/json",
                                "User-Agent": UA, "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
                out = _read_stream(r, on_token) if on_token else json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code not in RETRYABLE:
                raise
            last = e.code
        except (TimeoutError, urllib.error.URLError, OSError):
            # Not the same as an error response: the request went out and nothing
            # came back. Trying the second model would just cost another wait, so
            # stop and say what actually happened.
            stalled = True
            break
    if out is None:
        if stalled:
            raise RuntimeError(f"Gemini did not respond within {LLM_TIMEOUT}s. It may be down, or "
                               "this network may be blocked — set LLM_PROVIDER=openai to use an "
                               "open-weight model instead.")
        raise RuntimeError("Gemini free-tier quota exhausted on both models — try again in a minute."
                           if last == 429 else
                           f"Gemini is unavailable right now (HTTP {last}) — try again in a moment.")
    candidates = out.get("candidates") or []
    if not candidates:
        reason = out.get("promptFeedback", {}).get("blockReason", "no response")
        raise RuntimeError(f"Gemini returned no answer ({reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    for p in parts:
        if "functionCall" in p:
            return p["functionCall"]
    answer = "".join(p.get("text", "") for p in parts)
    if not answer.strip():
        raise RuntimeError(f"Gemini returned an empty answer ({candidates[0].get('finishReason')})")
    return answer


def ask_llm(question: str, listings: str, history: list[dict], tools: list | None = None,
            on_token=None):
    """Answer grounded in the listings. Returns the reply text — or, when tools are
    offered and the model calls one, a {"name", "args"} dict, in which case history
    is left untouched for the caller to record once it knows what actually happened.

    Pass on_token to stream: it is called with each chunk of text as it arrives, and
    the full text is still returned at the end. A tool call streams nothing — there
    is no prose to show, and the caller needs the whole call before it can act.

    Which provider answers is decided by PROVIDER; both return the same two shapes,
    so nothing above this function knows or cares."""
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
    )
    if tools:
        # These rules used to live in a separate planner prompt. Folded in here, the
        # one model that reads the listings and its own earlier replies is also the
        # one deciding to act — so "the second one" resolves against what it actually
        # said, instead of against a six-message excerpt handed to a second call.
        system += (
            "\n\nYou can also open the user's browser on a specific show by calling the book "
            "tool. Call it ONLY when they ask to book, select or open tickets now — not when "
            "they are asking about showtimes. Never invent a venue or a showtime: fill those "
            "only when the user named them, said 'my usual place' and a preferred venue is "
            "known, or the conversation makes them unambiguous. Copy venue, time and book_url "
            "verbatim from the listings. List in `inferred` every field you filled from context "
            "or your own suggestion rather than from the user's own words this turn — but leave "
            "it empty when they are simply confirming a plan you already proposed."
        )
    system += f"\n\n{listings}"

    ask = _ask_gemini if PROVIDER == "gemini" else _ask_openai
    out = ask(system, history, question, tools, on_token)
    if isinstance(out, dict):
        return out  # a tool call: the caller records the turn once it knows the outcome
    if not out.strip():
        raise RuntimeError(f"{PROVIDER} returned an empty answer")
    # Only now is history touched. If the call above raised, the caller retries with
    # the SAME list, and a pre-appended question would be sent twice — two consecutive
    # user turns, saved to the browser's localStorage for good.
    history.append({"role": "user", "parts": [{"text": question}]})
    history.append({"role": "model", "parts": [{"text": out}]})
    return out


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
