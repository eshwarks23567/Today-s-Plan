"""Regression test for the BMS/District parsers — the most fragile code here,
since either site can change markup any day with zero warning.

Runs the REAL parsing functions against synthetic HTML fixtures that mirror
the exact shapes reverse-engineered from the live sites (JSON-LD ItemList,
BMS's __INITIAL_STATE__ blob, District's escaped nearbyCinemas payload).
booktic.fetch is monkeypatched so nothing touches the network; every other
line of the parsers runs unmodified. If a site changes shape, this fails
fast with a specific assertion instead of a silent "no movies found".

    python test_scrapers.py
"""
import io
import json
import os
import sys
import urllib.request

import booktic

FAILURES = []


def check(name, cond):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}")
        FAILURES.append(name)


# ---- fixtures: built to match the real, already-reverse-engineered shapes ----

def ldjson_page(item_list_movies: list[dict]) -> str:
    """A page with several JSON-LD blocks, mimicking real explore pages where
    ItemList is NOT always the first block (Organization/BreadcrumbList precede it)."""
    org = '<script type="application/ld+json">{"@type": "Organization", "name": "X"}</script>'
    items = '<script type="application/ld+json">{"@type": "ItemList", "itemListElement": ' + \
        json.dumps([{"name": m["title"], "url": m["url"], "image": m.get("image")} for m in item_list_movies]) + \
        '}</script>'
    return f"<html><head>{org}{items}</head></html>"


def bms_buytickets_html(datecode: str, venue_id="AMBH", venue_name="AMB Cinemas: Gachibowli",
                        time_str="07:20 PM", prices=(295.0, 350.0), has_data=True) -> str:
    """Mirrors showtimesByEvent.showDates[date].{primaryStatic.data.venues, dynamic.data.showtimeWidgets}."""
    day = {
        "primaryStatic": {"data": {"venues": {venue_id: {"venueName": venue_name}}}},
        "dynamic": {"data": {"showtimeWidgets": [
            {"type": "adtech", "data": []},
            {"type": "groupList", "data": [{"data": [
                {"type": "venue-card", "id": venue_id, "showtimes": [
                    {"additionalData": {
                        "showTime": time_str,
                        "categories": [{"curPrice": f"{p:.2f}"} for p in prices],
                        "attributes": "DOLBY ATMOS",
                    }},
                ]},
            ]}]},
        ]}},
    } if has_data else {}
    state = {"showtimesByEvent": {"showDates": {datecode: day}}}
    # real pages trail more JS after the object; raw_decode must stop at the matching brace
    return f'<script>window.__INITIAL_STATE__ = {json.dumps(state)}; window.__NEXT__ = 1;</script>'


def district_movie_html(session_time_utc: str, price: float, venue="Cinepolis: Lulu Mall, Hyderabad") -> str:
    """District's session JSON sits escaped inside a Next.js RSC payload — every
    quote is backslash-escaped in the raw page; district_showtimes() unescapes first."""
    cinemas = [{"cinemaInfo": {"name": venue},
                "sessions": [{"showTime": session_time_utc, "areas": [{"price": price}]}]}]
    fragment = ('"nearbyCinemas":' + json.dumps(cinemas)).replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"...pageData {fragment} more..."])</script>'


def bms_event_synopsis_html(venue: str, when: str, price_line: str) -> str:
    """Single (non-tour) event page: real BMS event JSON-LD is junk, so the parser
    falls back to regexing the og:description meta and a day-month date pattern."""
    return (f'<html><head><meta name="description" content="Book online tickets for X in Y '
            f'on BookMyShow which is a music-shows event happening at {venue}"></head>'
            f'<body>{when}<br>{price_line}</body></html>')


# ---- tests ----

def test_itemlist():
    movies = [{"title": "Alpha", "url": "https://x/alpha", "image": "https://x/alpha.jpg"},
              {"title": "No URL", "url": None}]
    html = ldjson_page([m for m in movies if m["url"]] + [{"title": "Skip", "url": ""}])
    out = booktic.itemlist(html)
    check("itemlist finds ItemList among multiple ld+json blocks", len(out) == 1)
    check("itemlist keeps title/url/image", out and out[0] == movies[0])
    check("itemlist returns [] when no ItemList present", booktic.itemlist("<html></html>") == [])


def test_initial_state():
    html = bms_buytickets_html("20260714")
    state = booktic.initial_state(html)
    check("initial_state locates and decodes the JSON blob",
          "showtimesByEvent" in state)
    try:
        booktic.initial_state("<html>no marker here</html>")
        check("initial_state raises when __INITIAL_STATE__ is absent", False)
    except RuntimeError:
        check("initial_state raises when __INITIAL_STATE__ is absent", True)


def test_bms_showtimes(monkeypatch_fetch):
    datecode = "20260714"
    movie = {"title": "Alpha", "url": "https://in.bookmyshow.com/hyderabad/movies/alpha/ET00403805"}
    monkeypatch_fetch(bms_buytickets_html(datecode, prices=(295.0, 350.0)))
    rows = booktic.bms_showtimes(dict(movie), datecode)
    check("bms_showtimes extracts one venue", len(rows) == 1)
    check("bms_showtimes extracts venue name", rows and rows[0]["venue"] == "AMB Cinemas: Gachibowli")
    s = rows[0]["sessions"][0] if rows else {}
    check("bms_showtimes extracts showtime", s.get("time") == "07:20 PM")
    check("bms_showtimes takes min/max across price categories", s.get("min") == 295.0 and s.get("max") == 350.0)

    m2 = dict(movie)
    monkeypatch_fetch(bms_buytickets_html(datecode, prices=(295.0, 350.0)))
    booktic.bms_showtimes(m2, datecode)
    check("bms_showtimes sets movie['book'] to the buytickets URL for this date",
          m2.get("book", "").endswith(f"/buytickets/ET00403805/{datecode}"))

    monkeypatch_fetch(bms_buytickets_html(datecode, has_data=False))
    empty = booktic.bms_showtimes(dict(movie), datecode)
    check("bms_showtimes returns [] when the date has no listings (no crash)", empty == [])


def test_district_showtimes(monkeypatch_fetch):
    movie = {"title": "Alpha", "url": "https://www.district.in/movies/alpha-movie-tickets-MV175697"}

    # UTC 05:16 -> IST 10:46, no leading-zero stripped (starts with "1")
    monkeypatch_fetch(district_movie_html("2026-07-14T05:16", 150.0))
    rows = booktic.district_showtimes(dict(movie), "hyderabad", "2026-07-14")
    check("district_showtimes shifts UTC to IST (+5:30)",
          rows and rows[0]["sessions"][0]["time"] == "10:46 AM")
    check("district_showtimes extracts price", rows and rows[0]["sessions"][0]["min"] == 150.0)

    # UTC 03:35 -> IST 09:05, lstrip("0") must turn "09:05 AM" into "9:05 AM"
    monkeypatch_fetch(district_movie_html("2026-07-14T03:35", 150.0))
    rows = booktic.district_showtimes(dict(movie), "hyderabad", "2026-07-14")
    check("district_showtimes strips a leading zero from single-digit hours",
          rows and rows[0]["sessions"][0]["time"] == "9:05 AM")

    # UTC 19:00 on the 13th -> IST 00:30 on the 14th: a real day-rollover bug this
    # timezone math must get right, in both directions.
    monkeypatch_fetch(district_movie_html("2026-07-13T19:00", 150.0))
    same_day = booktic.district_showtimes(dict(movie), "hyderabad", "2026-07-14")
    check("district_showtimes includes a session that rolls into today after the IST shift",
          len(same_day) == 1)
    monkeypatch_fetch(district_movie_html("2026-07-13T19:00", 150.0))
    other_day = booktic.district_showtimes(dict(movie), "hyderabad", "2026-07-13")
    check("district_showtimes excludes that same session when asking for the day before",
          other_day == [])


def test_bms_events_single_event_fallback(monkeypatch_fetch):
    """BMS's own JSON-LD for single events is junk (empty venues/fake dates), so the
    parser must fall back to the description meta + regexed date/price."""
    html = ldjson_page([{"title": "DJ Chetas", "url": "https://x/dj-chetas"}])

    def fetch_router(url):
        return html if "explore/events" in url else bms_event_synopsis_html(
            "Quake Arena: Hyderabad", "Sat 18 Jul", "₹799 onwards")
    monkeypatch_fetch(fetch_router)

    out = booktic.bms_events("hyderabad")
    check("bms_events falls back to regex when JSON-LD/state has no venue cards", len(out) == 1)
    line = out[0] if out else ""
    check("bms_events fallback extracts venue", "Quake Arena" in line)
    check("bms_events fallback extracts date", "Sat 18 Jul" in line)
    check("bms_events fallback extracts and normalizes price", "Rs 799" in line)


def test_section():
    mv = {"title": "Alpha", "book": "https://x/alpha"}
    single = booktic.section(mv, [{"venue": "INOX", "sessions": [{"time": "7:35 PM", "min": 105.0, "max": 105.0}]}])
    check("section formats a flat price without a range", "Rs105" in single[1] and "Rs105-" not in single[1])
    ranged = booktic.section(mv, [{"venue": "INOX", "sessions": [{"time": "7:35 PM", "min": 105.0, "max": 249.0}]}])
    check("section formats a price range when min != max", "Rs105-249" in ranged[1])


def test_ask_llm_history():
    """ask_llm mutates the caller's history list in place, and agent.handle retries
    with that same list when the graph blows up. Appending the question before the
    HTTP call meant a failure left it stranded there, and the retry appended it a
    second time — two user turns in a row, saved to the client's localStorage."""
    os.environ.setdefault("GEMINI_API_KEY", "test-key")
    real_urlopen = urllib.request.urlopen

    def fail(*a, **k):
        raise OSError("network down")

    def ok(*a, **k):
        return io.BytesIO(json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "sure"}]}}]}).encode())

    history = [{"role": "user", "parts": [{"text": "hi"}]},
               {"role": "model", "parts": [{"text": "hello"}]}]
    try:
        urllib.request.urlopen = fail
        try:
            booktic.ask_llm("what's on?", "listings", history)
        except Exception:
            pass
        check("a failed ask_llm leaves history untouched", len(history) == 2)

        urllib.request.urlopen = ok
        booktic.ask_llm("what's on?", "listings", history)
    finally:
        urllib.request.urlopen = real_urlopen
    check("a successful ask_llm appends exactly the user turn and the reply",
          len(history) == 4 and [h["role"] for h in history] == ["user", "model", "user", "model"])
    check("the retry after a failure does not duplicate the question",
          history[2]["parts"][0]["text"] == "what's on?")


def main():
    real_fetch = booktic.fetch

    def install(response):
        # response is either a fixed HTML string or a url -> html router function
        booktic.fetch = response if callable(response) else (lambda url: response)

    print("itemlist / initial_state"); test_itemlist(); test_initial_state()
    print("bms_showtimes"); test_bms_showtimes(install)
    print("district_showtimes"); test_district_showtimes(install)
    print("bms_events (single-event regex fallback)"); test_bms_events_single_event_fallback(install)
    print("section"); test_section()
    print("ask_llm history"); test_ask_llm_history()

    booktic.fetch = real_fetch
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
