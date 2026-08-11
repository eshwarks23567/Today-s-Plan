"""Agentic layer — one Gemini call that either answers or asks to book.

There is no separate planner or router. The same call that reads the listings and
answers questions also decides, through function calling, that the user wants to
book; booking opens the user's own browser on the exact show's seat map and STOPS
— the human picks seats and pays themselves.

Nothing consequential happens off an inference alone: when the model fills in a
venue or showtime the user never actually said, we ask first (see CONFIRM_TAIL).
"""
import sys
import traceback
import webbrowser

import booktic
import prefs

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


def handle(question: str, history: list, listings: str, city: str, on_token=None,
           on_status=None, auto_open: bool = True) -> tuple[str, bool, str | None]:
    """Returns (answer, booked, url) — url is the page booking resolved to, if any.

    on_token streams the prose of a plain answer as it arrives; on_status reports
    the booking path, which produces no prose to stream but does take a second or
    two resolving the show. auto_open=False returns the link without opening it
    here, for when the server isn't the machine the person is looking at."""
    out = booktic.ask_llm(question, listings, history, tools=[BOOK_TOOL], on_token=on_token)
    if isinstance(out, str):
        return out, False, None  # a plain answer; ask_llm has already recorded the turn

    call = out.get("args") or {}
    print("book call:", call, file=sys.stderr)
    if on_status:
        on_status("Finding that show…" if call.get("time") else "Looking that up…")
    answer, booked, url = _book(call, history, city, auto_open)
    # ask_llm deliberately leaves a tool call out of history, so record the turn here
    # as plain text — the next turn and the client's saved transcript then read back
    # as a conversation rather than as a dangling function call
    history.append({"role": "user", "parts": [{"text": question}]})
    history.append({"role": "model", "parts": [{"text": answer}]})
    return answer, booked, url


def _book(call: dict, history: list, city: str, auto_open: bool = True) -> tuple[str, bool, str | None]:
    url = (call.get("book_url") or "").strip()
    if not url:
        return ("I couldn't match that to a specific show or event. Tell me the title, and for "
                "movies the venue and showtime (e.g. \"book 2 tickets for Alpha at INOX Odeon "
                "07:35 PM\")."), False, None

    # Only fields that were actually filled count — a model listing 'venue' as inferred
    # while leaving it empty would otherwise stall the booking on a question about nothing.
    if [f for f in (call.get("inferred") or []) if call.get(f)] and not _awaiting_confirmation(history):
        return _confirmation(call), False, None

    try:
        seats = max(1, min(10, int(call.get("seats") or 2)))
    except (TypeError, ValueError):
        seats = 2
    venue, when = call.get("venue") or "", call.get("time") or ""
    try:
        target, exact = open_booking(url, venue, when, auto_open)
    except Exception as e:
        return f"I couldn't work out the booking link ({e}). The movie page is {url}", False, url

    opened = "Opened" if auto_open else "Here's"
    if exact:
        prefs.remember_booking(city, venue, seats)
        return (f"{opened} {call.get('movie')} — {venue}, {when} — straight on its seat map. "
                f"Pick your {seats} seat{'' if seats == 1 else 's'} and pay there; I never "
                "enter payment details."), True, target
    # couldn't pin it to one show, so the user lands on the showtime list instead
    why = ("" if not when else
           f" I couldn't pin down {when}{' at ' + venue if venue else ''} on that page, so "
           "you'll need to pick the show yourself.")
    return (f"{opened} {call.get('movie')}.{why} Choose your show and seats there — I never "
            "enter payment details."), True, target


def open_booking(url: str, venue: str, time_str: str, auto_open: bool = True) -> tuple[str, bool]:
    """Resolve the most specific page we can, and open it when we are the machine
    the person is actually sitting at.

    This used to drive a Playwright browser through BMS's booking UI. That UI no
    longer has the dialog it clicked, and the seat layout will not render inside an
    automated browser at all — it sits on "Please wait, while we load the seats"
    indefinitely. Handing the user's own browser a deep link is both the thing that
    works and a great deal less code.

    auto_open is False when the request arrived through a tunnel or proxy: opening
    a browser here would pop a window on a desktop nobody is looking at, so the
    link goes back to the client to open instead.

    Returns (url resolved, whether it was the exact show's seat map)."""
    deep = None
    if venue and time_str and "/buytickets/" in url:
        try:
            deep = booktic.bms_seat_url(url, venue, time_str)
        except Exception:
            traceback.print_exc(file=sys.stderr)  # fall back to the movie page
    if auto_open:
        webbrowser.open(deep or url)
    return deep or url, bool(deep)


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
