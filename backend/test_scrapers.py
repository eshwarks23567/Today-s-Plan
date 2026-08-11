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

import agent
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
                        time_str="07:20 PM", prices=(295.0, 350.0), has_data=True,
                        served_datecode=None) -> str:
    """Mirrors showtimesFunctionalApi.queries['fetchPrimaryDynamic-<et>---<date>-<region>'],
    where BMS now publishes showtimes. Prices sit one level deeper than they used to:
    inside each showtime's double-tap bottom sheet, as display strings.

    served_datecode fakes BMS answering with a different date from the one asked for,
    which is what it does when the requested date has no shows for that movie."""
    def showtime(t):
        return {
            "title": t,
            "additionalData": {"showTime": t, "sessionId": "254602", "availStatus": "2"},
            "customGestureCTA": {"additionalData": {"bottomSheetData": {"widgets": [
                {"layoutId": "format-container", "type": "utility",
                 "variableData": {"format": "Telugu • 2D"}},
                {"layoutId": "category-price-header-container", "type": "text",
                 "variableData": {"title": "Seat category and price"}},
            ] + [
                {"layoutId": "seat-category-type-available", "type": "text",
                 "variableData": {"seatType": f"CAT{i}", "seatCost": f"₹ {p:,.2f}",
                                  "seatAvalibility": "AVAILABLE"}}
                for i, p in enumerate(prices)
            ]}}},
        }

    dyn = {"data": {"data": {
        "additionalData": {"dateCode": served_datecode or datecode, "eventCode": "ET00403805"},
        "showtimeWidgets": [
            {"type": "adtech", "data": []},
            {"type": "groupList", "data": [{"data": [
                {"type": "venue-card", "id": venue_id,
                 "additionalData": {"venueCode": venue_id, "venueName": venue_name},
                 "showtimesSections": [{"showtimes": [showtime(time_str)]}]},
            ]}]},
        ],
    }}}
    state = {
        # the real page still carries this key, now stripped of its showDates
        "showtimesByEvent": {"additionalData": {}, "currentDateCode": datecode},
        "showtimesFunctionalApi": {"queries": (
            {"fetchStaticShowtimes": {"data": {"data": {"styles": {}}}},
             f"fetchPrimaryDynamic-ET00403805---{datecode}-HYD": dyn} if has_data else {})},
    }
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
    check("bms_showtimes reads the format out of the bottom sheet", s.get("attrs") == "Telugu • 2D")

    monkeypatch_fetch(bms_buytickets_html(datecode, prices=(1250.0,)))
    big = booktic.bms_showtimes(dict(movie), datecode)
    check("bms_showtimes parses a thousands-separated price",
          big and big[0]["sessions"][0]["min"] == 1250.0)

    # BMS answers with the movie's next available date rather than an empty page
    monkeypatch_fetch(bms_buytickets_html(datecode, served_datecode="20260721"))
    check("bms_showtimes drops a page BMS served for a different date",
          booktic.bms_showtimes(dict(movie), datecode) == [])

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


def test_ask_llm_tool_call():
    """A tool call comes back raw and leaves history alone — the caller records the
    turn as plain text once it knows whether the booking actually happened."""
    os.environ.setdefault("GEMINI_API_KEY", "test-key")
    sent, real_urlopen = {}, urllib.request.urlopen

    def capture(req, **k):
        sent.update(json.loads(req.data))
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "book", "args": {"movie": "Alpha", "seats": 3}}}]}}]}).encode())

    history = []
    try:
        urllib.request.urlopen = capture
        out = booktic.ask_llm("book it", "listings", history, tools=[agent.BOOK_TOOL])
    finally:
        urllib.request.urlopen = real_urlopen
    check("tool declarations reach the API", "tools" in sent)
    check("a tool call is returned raw, not stringified",
          isinstance(out, dict) and out.get("args", {}).get("movie") == "Alpha")
    check("a tool call leaves history for the caller to record", history == [])


def test_agent_confirms_before_acting():
    """Nothing consequential off an inference alone: a plan carrying fields the model
    filled in itself must come back as a question, not as a driven browser."""
    drove = []

    def fake_drive(*a, **k):
        drove.append(a)
        return ["opened booking page"], False  # False: never let a test touch prefs.json

    call = {"movie": "Alpha", "book_url": "https://x/buytickets/ET1/20260811",
            "venue": "INOX Odeon", "time": "07:35 PM", "seats": 2, "inferred": ["venue", "time"]}
    real_drive = agent.drive_browser
    try:
        agent.drive_browser = fake_drive
        answer, booked = agent._book(call, [], "hyderabad")
        check("an inferred plan asks before opening a browser", not booked and not drove)
        check("the question names the show it is about to open",
              "INOX Odeon" in answer and "07:35 PM" in answer and agent.CONFIRM_TAIL in answer)

        # the user has now said yes: our own confirmation is the last model turn
        history = [{"role": "user", "parts": [{"text": "book alpha"}]},
                   {"role": "model", "parts": [{"text": agent._confirmation(call)}]}]
        answer, booked = agent._book(call, history, "hyderabad")
        check("confirming acts instead of asking again", booked and len(drove) == 1)

        # a plan the user stated outright should never stall on a question
        stated = dict(call, inferred=[])
        agent._book(stated, [], "hyderabad")
        check("a fully stated plan books straight away", len(drove) == 2)

        # a field named as inferred but left empty is not a reason to stop
        agent._book(dict(call, inferred=["category"]), [], "hyderabad")
        check("an inferred field that was never filled does not block", len(drove) == 3)
    finally:
        agent.drive_browser = real_drive

    answer, booked = agent._book({"movie": "Alpha"}, [], "hyderabad")
    check("a plan with no booking URL neither asks nor acts",
          not booked and agent.CONFIRM_TAIL not in answer)
    check("an unanswered confirmation is detected in history",
          agent._awaiting_confirmation([{"role": "model", "parts": [{"text": agent._confirmation(call)}]}]))
    check("an ordinary reply is not mistaken for a confirmation",
          not agent._awaiting_confirmation([{"role": "model", "parts": [{"text": "Alpha is at 7:35 PM."}]}]))


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
    print("ask_llm tool calls"); test_ask_llm_tool_call()
    print("agent confirmation"); test_agent_confirms_before_acting()

    booktic.fetch = real_fetch
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
