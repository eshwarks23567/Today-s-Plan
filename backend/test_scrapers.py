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
import time
import urllib.request

import agent
import booktic

# Captured before main() starts monkeypatching booktic.fetch for the parser
# fixtures — test_fetch_retries_challenges needs the real one.
REAL_FETCH = booktic.fetch

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


def test_bms_seat_url(monkeypatch_fetch):
    """The seat-layout deep link is assembled from data BMS publishes on the
    buytickets page — region from the query key, venueCode from the card,
    sessionId from the showtime."""
    datecode = "20260714"
    buy = f"https://in.bookmyshow.com/movies/hyderabad/alpha/buytickets/ET00403805/{datecode}"

    monkeypatch_fetch(bms_buytickets_html(datecode))
    url = booktic.bms_seat_url(buy, "AMB Cinemas: Gachibowli", "07:20 PM")
    check("bms_seat_url builds the exact seat-layout link",
          url == f"https://in.bookmyshow.com/movies/hyd/seat-layout/ET00403805/AMBH/254602/{datecode}")

    monkeypatch_fetch(bms_buytickets_html(datecode))
    check("bms_seat_url matches a venue by its name before the colon",
          booktic.bms_seat_url(buy, "AMB Cinemas", "07:20 PM") == url)

    monkeypatch_fetch(bms_buytickets_html(datecode))
    check("bms_seat_url returns None for a showtime that is not there",
          booktic.bms_seat_url(buy, "AMB Cinemas: Gachibowli", "11:55 PM") is None)

    monkeypatch_fetch(bms_buytickets_html(datecode))
    check("bms_seat_url returns None for a different venue",
          booktic.bms_seat_url(buy, "PVR Nexus", "07:20 PM") is None)

    # the wrong-date trap applies here too: never deep-link into another day
    monkeypatch_fetch(bms_buytickets_html(datecode, served_datecode="20260721"))
    check("bms_seat_url refuses a page BMS served for a different date",
          booktic.bms_seat_url(buy, "AMB Cinemas: Gachibowli", "07:20 PM") is None)


def district_page_html(session_time_utc="2026-07-13T16:00", price=175.0,
                       venue="Roongta Cinemas, Novum, Nampally",
                       mcd="mkm8o9qs7et", enc="1101081-2694-obbkgo-1101081", fmt="2D") -> str:
    """District's session payload carries the two ids its own seat-layout URL is
    built from — mcd names the route, encSessionId identifies the show."""
    cinemas = [{"cinemaInfo": {"name": venue}, "sessions": [{
        "showTime": session_time_utc, "areas": [{"price": price}],
        "scrnFmt": fmt, "mcd": mcd, "encSessionId": enc}]}]
    fragment = ('"nearbyCinemas":' + json.dumps(cinemas)).replace('"', '\\"')
    return f'<script>self.__next_f.push([1,"...pageData {fragment} more..."])</script>'


def test_district_seat_url(monkeypatch_fetch):
    """District gets the same one-hop booking BookMyShow does. The URL is not
    scraped a second time — every part of it is in the session payload, and the
    result is byte-identical to the one District publishes in its JSON-LD offers."""
    book = "https://www.district.in/movies/hanuman-ansh-movie-tickets-in-hyderabad-MV225612"
    expected = ("https://www.district.in/movies/seat-layout/mkm8o9qs7et"
                "?encsessionid=1101081-2694-obbkgo-1101081&freeseating=false"
                "&fromsessions=true&type=MOVIES&contentid=225612")

    monkeypatch_fetch(district_page_html())
    check("district_seat_url builds the exact seat-layout link",
          booktic.district_seat_url(book, venue="Roongta Cinemas", time_str="9:30 PM",
                                    today_iso="2026-07-13") == expected)

    monkeypatch_fetch(district_page_html())
    check("district_seat_url returns None for a showtime that is not there",
          booktic.district_seat_url(book, "Roongta Cinemas", "11:55 PM",
                                    today_iso="2026-07-13") is None)

    monkeypatch_fetch(district_page_html())
    check("district_seat_url returns None for a different venue",
          booktic.district_seat_url(book, "PVR Nexus", "9:30 PM",
                                    today_iso="2026-07-13") is None)

    check("district_seat_url needs a content id in the movie url",
          booktic.district_seat_url("https://www.district.in/movies/no-id", "x", "9:30 PM") is None)

    monkeypatch_fetch(district_page_html())
    mv = {"title": "Hanuman Ansh",
          "url": "https://www.district.in/movies/hanuman-ansh-movie-tickets-MV225612"}
    rows = booktic.district_showtimes(mv, "hyderabad", "2026-07-13")
    check("district listings still parse after the refactor",
          rows and rows[0]["sessions"][0]["min"] == 175.0)
    check("district sessions now carry the screen format too",
          rows and rows[0]["sessions"][0]["attrs"] == "2D")


def test_section():
    mv = {"title": "Alpha", "book": "https://x/alpha"}
    single = booktic.section(mv, [{"venue": "INOX", "sessions": [{"time": "7:35 PM", "min": 105.0, "max": 105.0}]}])
    check("section formats a flat price without a range", "Rs105" in single[1] and "Rs105-" not in single[1])
    ranged = booktic.section(mv, [{"venue": "INOX", "sessions": [{"time": "7:35 PM", "min": 105.0, "max": 249.0}]}])
    check("section formats a price range when min != max", "Rs105-249" in ranged[1])

    # language and format are what people filter on first; the sound-tech tail is not
    show = lambda t, attrs: {"time": t, "min": 100.0, "max": 100.0, "attrs": attrs}
    same = booktic.section(mv, [{"venue": "INOX", "sessions": [
        show("7:35 PM", "Telugu • 2D | DOLBY ATMOS"), show("10:00 PM", "Telugu • 2D | DOLBY ATMOS")]}])
    check("section keeps language and format", "Telugu 2D" in same[1])
    check("section drops the sound-tech tail", "DOLBY" not in same[1])
    check("section hoists a shared format onto the venue, not every showtime",
          same[1].count("Telugu 2D") == 1 and "INOX [Telugu 2D]" in same[1])

    mixed = booktic.section(mv, [{"venue": "INOX", "sessions": [
        show("7:35 PM", "Telugu • 2D"), show("10:00 PM", "Hindi • IMAX")]}])
    check("section annotates per showtime when a venue mixes formats",
          "[" not in mixed[1] and "Telugu 2D" in mixed[1] and "Hindi IMAX" in mixed[1])

    bare = booktic.section(mv, [{"venue": "INOX", "sessions": [show("7:35 PM", "")]}])
    check("section adds no brackets when there is no format at all", "[" not in bare[1])


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
    filled in itself must come back as a question, not as an opened browser."""
    opened = []

    def fake_open(*a, **k):
        opened.append(a)
        return "https://in.bookmyshow.com/movies/hyd/seat-layout/ET1/AC/1/20260811", False  # False: never let a test touch prefs.json

    call = {"movie": "Alpha", "book_url": "https://in.bookmyshow.com/movies/hyderabad/a/buytickets/ET1/20260811",
            "venue": "INOX Odeon", "time": "07:35 PM", "seats": 2, "inferred": ["venue", "time"]}
    real_open = agent.open_booking
    try:
        agent.open_booking = fake_open
        answer, booked, _ = agent._book(call, [], "hyderabad")
        check("an inferred plan asks before opening a browser", not booked and not opened)
        check("the question names the show it is about to open",
              "INOX Odeon" in answer and "07:35 PM" in answer and agent.CONFIRM_TAIL in answer)

        # the user has now said yes: our own confirmation is the last model turn
        history = [{"role": "user", "parts": [{"text": "book alpha"}]},
                   {"role": "model", "parts": [{"text": agent._confirmation(call)}]}]
        answer, booked, url = agent._book(call, history, "hyderabad")
        check("confirming acts instead of asking again", booked and len(opened) == 1)
        check("the resolved link comes back for the client to open", url == "https://in.bookmyshow.com/movies/hyd/seat-layout/ET1/AC/1/20260811")

        # a plan the user stated outright should never stall on a question
        stated = dict(call, inferred=[])
        agent._book(stated, [], "hyderabad")
        check("a fully stated plan books straight away", len(opened) == 2)

        # a field named as inferred but left empty is not a reason to stop
        agent._book(dict(call, inferred=["category"]), [], "hyderabad")
        check("an inferred field that was never filled does not block", len(opened) == 3)
    finally:
        agent.open_booking = real_open

    answer, booked, _ = agent._book({"movie": "Alpha"}, [], "hyderabad")
    check("a plan with no booking URL neither asks nor acts",
          not booked and agent.CONFIRM_TAIL not in answer)
    check("an unanswered confirmation is detected in history",
          agent._awaiting_confirmation([{"role": "model", "parts": [{"text": agent._confirmation(call)}]}]))
    check("an ordinary reply is not mistaken for a confirmation",
          not agent._awaiting_confirmation([{"role": "model", "parts": [{"text": "Alpha is at 7:35 PM."}]}]))


def test_safe_booking_url():
    """book_url comes from the model, which we tell to copy links out of listings we
    scraped — and BookMyShow event titles are user-submitted. webbrowser.open() is
    os.startfile() on Windows, so an unchecked string there launches whatever the
    shell would. The same value is also rendered into an href on the page."""
    for good in ("https://in.bookmyshow.com/movies/hyd/seat-layout/ET1/AC/1/20260811",
                 "https://www.district.in/movies/x-MV1",
                 "http://in.bookmyshow.com/a"):
        check(f"allows a ticketing link ({good[:34]}…)", agent.safe_booking_url(good) == good)
    for bad, why in [
        ("file:///C:/Windows/System32/calc.exe", "file:// scheme"),
        ("C:\\Windows\\System32\\calc.exe", "a bare executable path"),
        ("javascript:alert(1)", "javascript: (this value is also put in an href)"),
        ("\\\\attacker\\share\\payload.exe", "a UNC path"),
        ("https://evil.com/x", "an unrelated host"),
        ("https://in.bookmyshow.com.evil.com/x", "a lookalike domain"),
        ("", "an empty url"),
    ]:
        check(f"blocks {why}", agent.safe_booking_url(bad) is None)
    check("open_booking refuses to open anything that fails the check",
          _raises(lambda: agent.open_booking("file:///C:/x.exe", "", "", auto_open=False)))


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def _fake_openai_server(responses):
    """A minimal OpenAI-compatible endpoint on a loopback port, so the adapter gets
    tested against the real wire format without a key, a vendor or a network."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            body, stream = responses.pop(0)
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/event-stream" if stream else "application/json")
            self.end_headers()
            if stream:
                for frame in body:
                    self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(json.dumps(body).encode())
            self.wfile.flush()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_openai_provider():
    """Every open-weight host worth using speaks the OpenAI chat shape, so this one
    adapter covers Groq, OpenRouter, Together and a local Ollama alike. What has to
    hold is that agent.py cannot tell which provider answered."""
    text_reply = {"choices": [{"message": {"role": "assistant", "content": "Two films tonight."}}]}
    tool_reply = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "book", "arguments": '{"movie": "Alpha", "seats": 3}'}}]}}]}
    stream_text = [{"choices": [{"delta": {"content": c}}]}
                   for c in ("Two ", "films ", "tonight.")]
    # a real stream splits the arguments JSON across frames — reassembling it is
    # the whole reason this path is not just "read delta.content"
    stream_tool = [
        {"choices": [{"delta": {"tool_calls": [
            {"function": {"name": "book", "arguments": '{"mov'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"function": {"arguments": 'ie": "Alpha"}'}}]}}]},
    ]
    srv, seen = _fake_openai_server([(text_reply, False), (stream_text, True),
                                     (tool_reply, False), (stream_tool, True)])
    host, port = srv.server_address
    saved = (booktic.PROVIDER, booktic.LLM_BASE, booktic.LLM_MODEL)
    booktic.PROVIDER = "openai"
    booktic.LLM_BASE = f"http://{host}:{port}"
    booktic.LLM_MODEL = "some-open-model"
    try:
        h = []
        out = booktic.ask_llm("what is on?", "LISTINGS", h, tools=[agent.BOOK_TOOL])
        check("openai: a plain reply comes back as text", out == "Two films tonight.")
        check("openai: history records the turn", [t["role"] for t in h] == ["user", "model"])
        sent = seen[0]
        check("openai: the system prompt leads the messages",
              sent["messages"][0]["role"] == "system" and "LISTINGS" in sent["messages"][0]["content"])
        check("openai: the model name is sent", sent["model"] == "some-open-model")
        check("openai: the book tool is translated, not dropped",
              sent["tools"][0]["function"]["name"] == "book"
              and "book_url" in sent["tools"][0]["function"]["parameters"]["properties"])

        got = []
        out = booktic.ask_llm("again?", "L", h, on_token=got.append)
        check("openai: streaming yields tokens as they arrive",
              got == ["Two ", "films ", "tonight."])
        check("openai: streaming still returns the whole answer", out == "Two films tonight.")

        h2 = []
        call = booktic.ask_llm("book it", "L", h2, tools=[agent.BOOK_TOOL])
        check("openai: a tool call arrives in the same shape Gemini's does",
              isinstance(call, dict) and call.get("name") == "book"
              and (call.get("args") or {}).get("seats") == 3)
        check("openai: a tool call leaves history for the caller", h2 == [])

        call = booktic.ask_llm("book it", "L", h2, tools=[agent.BOOK_TOOL], on_token=lambda t: None)
        check("openai: streamed tool arguments are reassembled across frames",
              isinstance(call, dict) and call.get("args") == {"movie": "Alpha"})
    finally:
        booktic.PROVIDER, booktic.LLM_BASE, booktic.LLM_MODEL = saved
        srv.shutdown()


def test_fetch_retries_challenges():
    """Cloudflare returns HTTP 200 with an interstitial, so a blocked fetch is
    indistinguishable from a good one until the parser finds nothing — which is how
    the listings sat empty for five days. It has to be detected, not returned."""
    import subprocess
    challenge = "<html><head><title>Just a moment...</title></head><body>x</body></html>"
    real = "<html><head><title>Hyderabad Movie Tickets</title></head></html>"

    class Result:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    calls = []
    queue = [challenge, challenge, real]
    real_run, real_sleep = subprocess.run, booktic.time.sleep
    try:
        booktic.subprocess.run = lambda *a, **k: (calls.append(1), Result(queue[len(calls) - 1]))[1]
        booktic.time.sleep = lambda s: None  # do not actually wait during a test
        out = REAL_FETCH("https://in.bookmyshow.com/x")
        check("fetch retries past an interstitial and returns the real page", out == real)
        check("fetch keeps trying until it gets a usable page", len(calls) == 3)

        calls.clear()
        queue[:] = [challenge, challenge, challenge]
        try:
            REAL_FETCH("https://in.bookmyshow.com/x")
            check("fetch raises when every try is challenged", False)
        except RuntimeError as e:
            check("fetch raises when every try is challenged", "interstitial" in str(e))

        calls.clear()
        queue[:] = [real, real, real]
        REAL_FETCH("https://in.bookmyshow.com/x")
        check("a good page costs exactly one request", len(calls) == 1)
    finally:
        booktic.subprocess.run = real_run
        booktic.time.sleep = real_sleep


def test_gemini_falls_through_a_stalled_model():
    """A model that accepts the connection and never answers must fall through to
    the next one. It used to break the loop instead, so one hung model failed the
    whole request while the next was answering in a second and a half."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []

    def stall():
        while True:
            try:
                held.append(srv.accept()[0])
            except OSError:
                return

    threading.Thread(target=stall, daemon=True).start()
    tried = []
    saved = (booktic.GEMINI_MODELS, booktic.LLM_TIMEOUT, booktic.urllib.request.urlopen)
    os.environ.setdefault("GEMINI_API_KEY", "test-key")
    try:
        booktic.GEMINI_MODELS = ["stalls", "answers"]
        booktic.LLM_TIMEOUT = 1

        def fake_urlopen(req, timeout=None):
            model = req.full_url.split("/models/")[1].split(":")[0]
            tried.append(model)
            if model == "stalls":
                raise TimeoutError("read timed out")
            return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [
                {"text": "the second model answered"}]}}]}).encode())

        booktic.urllib.request.urlopen = fake_urlopen
        out = booktic.ask_llm("hi", "L", [])
        check("a stalled model falls through to the next", out == "the second model answered")
        check("both models were actually tried", tried == ["stalls", "answers"])
    finally:
        booktic.GEMINI_MODELS, booktic.LLM_TIMEOUT, booktic.urllib.request.urlopen = saved
        for c in held:
            c.close()
        srv.close()


def test_needs_future():
    """The later dates are a third of a ~26,000-token prompt carried on every turn.
    Deciding wrongly in the generous direction only costs tokens; deciding wrongly
    the other way costs an answer, so this leans towards including."""
    for q in ("what is playing tonight?", "cheapest tickets", "book 2 seats for Alpha",
              "yes", "any Telugu shows after 9 pm?", "the second one", "shows under Rs200",
              "is that satisfied? maybe"):
        check(f"stays on today: {q[:32]}", not booktic.needs_future(q))
    for q in ("what is on tomorrow?", "anything good this weekend?", "shows on Friday",
              "movies on the 28th", "what about next week", "sat night plans",
              "upcoming concerts"):
        check(f"pulls later dates: {q[:32]}", booktic.needs_future(q))

    # a follow-up carries no date of its own, so the turns it answers are read too
    asked_friday = [{"role": "user", "parts": [{"text": "what is on Friday?"}]},
                    {"role": "model", "parts": [{"text": "Three films on Friday..."}]}]
    check("a follow-up inherits the date from the turns it replies to",
          booktic.needs_future("book the second one", asked_friday))
    later = [{"role": "user", "parts": [{"text": "and the cheapest?"}]},
             {"role": "model", "parts": [{"text": "Rs 150 at AMB"}]}]
    check("an old date scrolls out once the conversation moves on",
          not booktic.needs_future("book the second one", asked_friday + later * 2))


def test_crawl_trims_future(monkeypatch_fetch):
    """Trimming must not read as 'nothing else is playing' — the model has to know
    the later dates exist so it can offer to look them up."""
    real = booktic._part
    # sized like the real sections (future is ~20k chars), or the note that replaces
    # it outweighs what it replaced and the size assertion means nothing
    booktic._part = lambda city, part, ttl, build: (
        "[TODAY SECTION]" + "t" * 37_000 if part == "today" else "[FUTURE SECTION]" + "f" * 20_000)
    try:
        full = booktic.crawl("hyderabad", True)
        trimmed = booktic.crawl("hyderabad", False)
        check("full crawl carries both parts",
              "[TODAY SECTION]" in full and "[FUTURE SECTION]" in full)
        check("trimmed crawl drops the future part",
              "[TODAY SECTION]" in trimmed and "[FUTURE SECTION]" not in trimmed)
        check("trimmed crawl says the later dates still exist",
              "DO exist" in trimmed and "ask them to name the day" in trimmed)
        check("trimming actually makes the prompt smaller", len(trimmed) < len(full))
    finally:
        booktic._part = real


def test_provider_stall():
    """A provider that accepts the connection and never answers is not hypothetical —
    it is what Gemini did from this machine for hours. It has to come back as a
    sentence someone can act on, not a raw socket timeout, and not after a minute."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []

    def accept_and_stall():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)  # keep it open, never write a response

    threading.Thread(target=accept_and_stall, daemon=True).start()
    saved = (booktic.PROVIDER, booktic.LLM_BASE, booktic.LLM_TIMEOUT)
    booktic.PROVIDER = "openai"
    booktic.LLM_BASE = "http://127.0.0.1:%d" % srv.getsockname()[1]
    booktic.LLM_TIMEOUT = 1
    try:
        started = time.time()
        try:
            booktic.ask_llm("hello?", "L", [])
            check("a stalled provider raises rather than hanging", False)
        except RuntimeError as e:
            check("a stalled provider raises rather than hanging", True)
            check("the error says the provider did not respond", "did not respond" in str(e))
            check("it gives up near the timeout, not a minute later", time.time() - started < 15)
        except Exception as e:
            check(f"a stalled provider raises RuntimeError, not {type(e).__name__}", False)
    finally:
        booktic.PROVIDER, booktic.LLM_BASE, booktic.LLM_TIMEOUT = saved
        for c in held:
            c.close()
        srv.close()


def test_error_detail_is_scoped():
    """Exception text can carry local filesystem paths. On your own machine that
    detail is the point; once strangers can reach it, it is an information leak."""
    import server
    boom = RuntimeError(r"failed reading C:\Users\someone\secret\prefs.json")
    was = server.HOSTED
    try:
        server.HOSTED = False
        check("locally, the real error reaches you", "prefs.json" in server._safe(boom))
        server.HOSTED = True
        check("hosted, the path is not handed to the caller", "prefs.json" not in server._safe(boom))
    finally:
        server.HOSTED = was


def test_prefs_concurrency():
    """summary() reads prefs.json on EVERY request while remember_booking rewrites it.
    Unlocked, write_text truncates before writing, so a reader lands on zero bytes and
    raises JSONDecodeError — which took down the whole answer, not just the lookup."""
    import shutil
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    import prefs
    real, tmpdir = prefs.PATH, tempfile.mkdtemp()
    try:
        prefs.PATH = Path(tmpdir) / "prefs.json"
        errs = []

        def write(i):
            try:
                prefs.remember_booking("hyderabad", f"V{i % 3}", 2)
            except Exception as e:
                errs.append(repr(e))

        def read(_):
            try:
                prefs.summary()
            except Exception as e:
                errs.append(repr(e))

        with ThreadPoolExecutor(max_workers=16) as pool:
            jobs = [pool.submit(write, i) for i in range(60)]
            jobs += [pool.submit(read, i) for i in range(120)]
            [j.result() for j in jobs]
        check("concurrent prefs reads never see a truncated file", not errs)
        check("no booking is lost to the read-modify-write race",
              sum(prefs.load()["venues"].values()) == 60)

        prefs.PATH.write_text("", encoding="utf-8")
        check("an empty prefs file loads as defaults instead of raising",
              prefs.load()["venues"] == {})
        prefs.PATH.write_text("[1,2,3]", encoding="utf-8")
        check("a prefs file of the wrong shape loads as defaults",
              prefs.load()["venues"] == {} and prefs.load()["home_city"] is None)
    finally:
        prefs.PATH = real
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_atomic_swap():
    """Windows refuses to replace a file while any handle has it open, so the snapshot
    swap failed outright whenever a request was mid-read — and _refresh only logs, so
    the listings would have stayed stale for the rest of the day."""
    import shutil
    import tempfile
    import threading
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    try:
        dest, tmp = d / "snap.txt", d / "snap.tmp"
        dest.write_text("old", encoding="utf-8")
        tmp.write_text("new", encoding="utf-8")
        booktic.atomic_swap(tmp, dest)
        check("atomic_swap replaces the destination", dest.read_text(encoding="utf-8") == "new")
        check("atomic_swap consumes the temp file", not tmp.exists())

        # a handle held briefly must not lose the swap (a no-op on POSIX, which
        # replaces happily under an open handle — this is the Windows regression)
        tmp.write_text("newer", encoding="utf-8")
        fh = open(dest, encoding="utf-8")
        threading.Timer(0.15, fh.close).start()
        booktic.atomic_swap(tmp, dest)
        check("atomic_swap retries past a reader briefly holding the file",
              dest.read_text(encoding="utf-8") == "newer")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    real_fetch = booktic.fetch

    def install(response):
        # response is either a fixed HTML string or a url -> html router function
        booktic.fetch = response if callable(response) else (lambda url: response)

    print("itemlist / initial_state"); test_itemlist(); test_initial_state()
    print("bms_showtimes"); test_bms_showtimes(install)
    print("district_showtimes"); test_district_showtimes(install)
    print("bms_events (single-event regex fallback)"); test_bms_events_single_event_fallback(install)
    print("bms_seat_url"); test_bms_seat_url(install)
    print("district deep link"); test_district_seat_url(install)
    print("section"); test_section()
    print("ask_llm history"); test_ask_llm_history()
    print("ask_llm tool calls"); test_ask_llm_tool_call()
    print("agent confirmation"); test_agent_confirms_before_acting()
    print("openai-compatible provider"); test_openai_provider()
    print("booking url allowlist"); test_safe_booking_url()
    print("fetch resilience"); test_fetch_retries_challenges()
    print("model fallback"); test_gemini_falls_through_a_stalled_model()
    print("prompt scoping"); test_needs_future(); test_crawl_trims_future(install)
    print("provider resilience"); test_provider_stall(); test_error_detail_is_scoped()
    print("concurrency"); test_prefs_concurrency(); test_atomic_swap()

    booktic.fetch = real_fetch
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
