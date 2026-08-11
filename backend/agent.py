"""Agentic layer — one Gemini call that either answers or asks to book.

There is no separate planner or router. The same call that reads the listings and
answers questions also decides, through function calling, that the user wants a
browser driven; booking opens a real, visible browser, clicks through to seat
selection, and STOPS — the human completes payment themselves.

Two rules shape the code below:
  * nothing consequential happens off an inference alone — when the model fills in
    a venue or showtime the user never said, we ask first (see CONFIRM_TAIL);
  * no step is reported unless the page was checked to have actually done it
    (see _reacted) — a booking agent that narrates unverified clicks is worse than
    one that admits it stopped early.
"""
import re
import sys
import traceback

import booktic
import prefs

_browsers = []  # keep refs so finished bookings stay open for the user
MAX_OPEN_BROWSERS = 3  # each entry is a live Chrome AND a Playwright driver process

# Ends every confirmation question, and is the only marker that a confirmation is
# outstanding: the client owns the session, so history is the whole of our state.
CONFIRM_TAIL = "Say yes and I'll open it."

BOOK_TOOL = {"function_declarations": [{
    "name": "book",
    "description": ("Open the user's browser on a specific show and click through to seat "
                    "selection. Call this ONLY when the user is asking to book, select or "
                    "open tickets now — never when they are just asking about showtimes. "
                    "Works for movies and for events/concerts."),
    "parameters": {
        "type": "object",
        "properties": {
            "movie": {"type": "string", "description": "title exactly as it appears in the listings"},
            "book_url": {"type": "string",
                         "description": "that title's '— book:' URL, copied from the listings section "
                                        "for the date the user wants"},
            "venue": {"type": "string",
                      "description": "venue exactly as in the listings; omit unless the user named it "
                                     "or the conversation makes it unambiguous"},
            "time": {"type": "string",
                     "description": "showtime exactly as in the listings, e.g. '07:35 PM'; omit for "
                                    "events, and whenever the user has not settled on one"},
            "seats": {"type": "integer", "description": "number of tickets; 2 if unstated"},
            "category": {"type": "string",
                         "description": "seat category, only if the user named one, e.g. PLATINUM"},
            "inferred": {"type": "array", "items": {"type": "string"},
                         "description": "names of the fields above that you filled from context, "
                                        "preferences or your own suggestion rather than from what the "
                                        "user actually said this turn"},
        },
        "required": ["movie", "book_url"],
    },
}]}


def handle(question: str, history: list, listings: str, city: str) -> tuple[str, bool]:
    """Returns (answer, booked) — booked=True only when a browser was really driven."""
    out = booktic.ask_llm(question, listings, history, tools=[BOOK_TOOL])
    if isinstance(out, str):
        return out, False  # a plain answer; ask_llm has already recorded the turn

    call = out.get("args") or {}
    print("book call:", call, file=sys.stderr)
    answer, booked = _book(call, history, city)
    # ask_llm deliberately leaves a tool call out of history, so record the turn here
    # as plain text — the next turn and the client's saved transcript then read back
    # as a conversation rather than as a dangling function call
    history.append({"role": "user", "parts": [{"text": question}]})
    history.append({"role": "model", "parts": [{"text": answer}]})
    return answer, booked


def _book(call: dict, history: list, city: str) -> tuple[str, bool]:
    url = (call.get("book_url") or "").strip()
    if not url:
        return ("I couldn't match that to a specific show or event. Tell me the title, and for "
                "movies the venue and showtime (e.g. \"book 2 tickets for Alpha at INOX Odeon "
                "07:35 PM\")."), False

    # Only fields that were actually filled count — a model listing 'venue' as inferred
    # while leaving it empty would otherwise stall the booking on a question about nothing.
    if [f for f in (call.get("inferred") or []) if call.get(f)] and not _awaiting_confirmation(history):
        return _confirmation(call), False

    try:
        seats = max(1, min(10, int(call.get("seats") or 2)))
    except (TypeError, ValueError):
        seats = 2
    try:
        done, selected = drive_browser(url, call.get("venue") or "", call.get("time") or "",
                                       seats, call.get("category"))
    except Exception as e:
        return (f"I couldn't open a browser to book this ({e}). Today's Plan needs Chrome or "
                "Edge, plus Playwright's browser driver set up — run `playwright install "
                "chromium` in the backend folder and try again."), False
    if selected:
        prefs.remember_booking(city, call.get("venue"))
    # movie page opened without a chosen showtime → tell the user how to go further
    hint = (" Name a venue and showtime (e.g. \"INOX Odeon 07:35 PM\") and I'll select the exact "
            "show for you." if "/buytickets/" in url and not call.get("time") else "")
    return (f"I've opened {call.get('movie')} in your browser and got as far as: "
            f"{' → '.join(done)}.{hint} Finish seat selection and payment there — I never enter "
            "payment details."), True


def _confirmation(call: dict) -> str:
    """The model filled in something the user never said, and the next step opens a
    browser and starts clicking. One sentence back is cheaper than landing on the
    wrong show — and cheap enough that over-asking beats guessing."""
    seats = call.get("seats") or 2
    line = f"{seats} ticket{'' if seats == 1 else 's'} for {call.get('movie')}"
    if call.get("venue"):
        line += f" at {call['venue']}"
    if call.get("time"):
        line += f", {call['time']}"
    if call.get("category"):
        line += f" ({call['category']})"
    return f"Just to check — {line}? {CONFIRM_TAIL}"


def _awaiting_confirmation(history: list) -> bool:
    """Was the last thing we said a confirmation question? Then this turn is the
    answer to it, and asking again would loop forever."""
    for turn in reversed(history):
        if turn.get("role") == "model":
            return CONFIRM_TAIL in turn["parts"][0]["text"]
    return False


def _xpath_str(s: str) -> str:
    """XPath 1.0 has no escape character for quotes inside a string literal —
    the standard workaround is to pick whichever quote char isn't present, or
    fall back to concat() when the value contains both (e.g. a venue name like
    Prasad's "IMAX" would otherwise break the expression outright)."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in s.split("'")) + ")"


def _reacted(page, probe: str, timeout: int = 6000) -> bool:
    """Did the page actually respond to what we just did? Every step below is
    reported only once this says yes."""
    try:
        page.locator(probe).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def drive_browser(url: str, venue: str, time_str: str, seats: int,
                  category: str | None) -> tuple[list[str], bool]:
    """Best-effort clickthrough; every step is optional — whatever fails, the human
    continues. Returns (steps actually verified, showtime selection confirmed).
    ponytail: text-based locators, not per-site selector maps; revisit if BMS markup churns."""
    from playwright.sync_api import sync_playwright

    done: list[str] = []
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=False)
    except Exception:
        browser = pw.chromium.launch(channel="msedge", headless=False)
    _browsers.append((pw, browser))
    while len(_browsers) > MAX_OPEN_BROWSERS:  # oldest booking window closes itself
        old_pw, old_browser = _browsers.pop(0)
        for shut in (old_browser.close, old_pw.stop):
            try:
                shut()
            except Exception:
                pass
    page = browser.new_page()
    page.goto(url, timeout=60000)
    done.append("opened booking page")
    if not time_str:  # event handoff — no showtime grid to click through
        return done, False
    vkey = venue.split(":")[0].strip() or venue

    def click_show(timeout):
        # wait for the venue to render (networkidle never fires here — analytics beacons),
        # then click the time nearest after its name
        anchor = page.get_by_text(vkey, exact=False).first
        anchor.wait_for(timeout=timeout)
        anchor.scroll_into_view_if_needed(timeout=4000)
        show = page.locator(
            f"xpath=(//*[contains(normalize-space(),{_xpath_str(vkey)})]"
            f"/following::*[normalize-space()={_xpath_str(time_str)}])[1]")
        if not show.count():
            show = page.get_by_text(time_str, exact=True).first
        show.click(timeout=4000)

    # an overlay intercepts normal clicks, so fall back to dispatching the event
    def tap(loc):
        try:
            loc.click(timeout=4000)
        except Exception:
            loc.dispatch_event("click")

    selected = False
    try:
        try:
            click_show(15000)
        except Exception:
            # the show may live on another language/format tab ("Telugu · 2D") — try each
            for tab in page.get_by_text(re.compile(r"^[A-Za-z+ ]+ · .+")).all()[:8]:
                try:
                    label = tab.inner_text(timeout=1000).strip()
                    tab.click(timeout=2000)
                    page.wait_for_timeout(1200)
                    click_show(4000)
                    done.append(f"switched to the {label} tab")
                    break
                except Exception:
                    continue
            else:
                raise  # no tab had it — leave the page open for the human

        # The click is only a real selection once BMS answers it with the quantity
        # dialog. Without this probe, a click that landed on a neighbouring element
        # still got reported as "selected the 07:35 PM show".
        if not _reacted(page, "text=/how many seats/i"):
            done.append(f"clicked {time_str} at {venue}, but the seat-count dialog never opened")
            return done, False
        done.append(f"selected the {time_str} show at {venue}")
        selected = True

        # BMS gives the count buttons stable ids (#quantity-N)
        qty = page.locator(f"#quantity-{seats}")
        if not qty.count():
            done.append(f"couldn't find a {seats}-ticket button — set the count yourself")
            return done, selected
        tap(qty)
        done.append(f"set {seats} tickets")

        if category:
            modal = page.locator("div:has-text('How many seats'):has-text('Select Seats')").last
            cat = modal.get_by_text(category, exact=False).first
            if cat.count():
                tap(cat)
                done.append(f"picked {category}")
            else:
                done.append(f"no {category} seats on this show — pick a category yourself")

        # leaving the showtimes URL is the one unambiguous sign the flow moved on
        before = page.url
        tap(page.get_by_text(re.compile("Select Seats", re.I)).first)
        try:
            page.wait_for_url(lambda u: u != before, timeout=10000)
            done.append("reached the seat layout — pick your seats")
        except Exception:
            done.append("pressed Select Seats but the layout didn't load — carry on in the browser")
    except Exception:
        traceback.print_exc(file=sys.stderr)  # server console; user just continues by hand
    try:
        page.screenshot(path=str(booktic.CACHE / "last_booking.png"))
    except Exception:
        pass
    return done, selected
