"""Agentic layer — LangGraph routes each message to chat or booking.

chat    -> existing grounded Q&A (booktic.ask_llm)
book    -> Playwright drives a real, visible browser: opens the movie's booking
           page, clicks the chosen showtime, sets seat count and category, then
           STOPS at seat selection — the human completes payment themselves.
"""
import os
import re
from typing import Optional, TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

import booktic
import prefs

os.environ.setdefault("GOOGLE_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
# lite: planning is field extraction, and lite has its own (larger) free-tier quota
llm = ChatGoogleGenerativeAI(model="gemini-flash-lite-latest", temperature=0)

_browsers = []  # keep refs so finished bookings stay open for the user
MAX_OPEN_BROWSERS = 3  # each entry is a live Chrome AND a Playwright driver process

# The planner is a second LLM call carrying the entire listings blob. It only earns
# that round trip when booking is actually on the table — a message with no booking
# word in it is a question, so route it to chat without asking.
BOOKISH = re.compile(r"\b(book|booking|ticket|tickets|seat|seats|reserve|reserving|buy|"
                     r"purchase|get me|go ahead|do it|open it|proceed)\b", re.I)
PLAN_LISTINGS_CHARS = 200_000  # 5 days of listings blow past 40k; flash-lite holds 1M


class Plan(BaseModel):
    intent: str = Field(description="'book' only if the user asks to book/select/open tickets NOW; otherwise 'chat'")
    movie: Optional[str] = Field(None, description="movie title exactly as in listings")
    venue: Optional[str] = Field(None, description="venue name exactly as in listings")
    time: Optional[str] = Field(None, description="showtime exactly as in listings, e.g. '07:35 PM'")
    seats: int = Field(2, description="number of tickets requested, default 2")
    category: Optional[str] = Field(None, description="seat category if the user named one, e.g. PLATINUM")
    book_url: Optional[str] = Field(None, description="that movie's BookMyShow '— book:' URL from the listings")


class State(TypedDict):
    question: str
    history: list
    listings: str
    city: str
    plan: dict
    answer: str


def plan_node(state: State):
    if not BOOKISH.search(state["question"]):
        return {"plan": {"intent": "chat"}}
    recent = [h["parts"][0]["text"] for h in state["history"][-6:]]
    pref_line = prefs.summary()
    p = llm.with_structured_output(Plan).invoke(
        f"Listings:\n{state['listings'][:PLAN_LISTINGS_CHARS]}\n\n"
        + (f"{pref_line}\n\n" if pref_line else "")
        + f"Recent conversation: {recent}\n"
        f"User message: {state['question']}\n\n"
        "Is the user asking to actually book/select/open tickets NOW (intent=book), "
        "or just asking a question (intent=chat)? Booking works for movies AND events/concerts. "
        "Rules for booking plans:\n"
        "1. Positional references ('the second one', 'that one') refer to the ordering in the "
        "MOST RECENT model reply in the conversation above - NOT the order of the listings.\n"
        "2. NEVER invent a venue or showtime. Fill venue/time only if the user named them, said "
        "'my usual place' and a preferred venue is listed above, or the conversation makes them "
        "unambiguous; otherwise leave them null.\n"
        "3. Use EXACT text from the listings for venue/time/book_url; prefer the BookMyShow URL.\n"
        "4. For events, set movie=event title and book_url; leave venue/time null.\n"
        "5. book_url must ALWAYS be filled for intent=book once you know the title: copy the "
        "movie's '— book:' URL from the listings."
    )
    import sys
    print("plan:", p.model_dump(), file=sys.stderr)
    return {"plan": p.model_dump()}


def chat_node(state: State):
    return {"answer": booktic.ask_llm(state["question"], state["listings"], state["history"])}


def book_node(state: State):
    p = state["plan"]
    if not p.get("book_url"):
        answer = ("I couldn't match that to a specific show or event. Tell me the title, and for "
                  "movies the venue and showtime (e.g. \"book 2 tickets for Alpha at INOX Odeon 07:35 PM\").")
    else:
        try:
            done = drive_browser(p["book_url"], p.get("venue") or "", p.get("time") or "",
                                 max(1, min(10, p.get("seats") or 2)), p.get("category"))
        except Exception as e:
            answer = (f"I couldn't open a browser to book this ({e}). Today's Plan needs Chrome "
                      "or Edge, plus Playwright's browser driver set up — run "
                      "`playwright install chromium` in the backend folder and try again.")
        else:
            if any(step.startswith("selected the") for step in done):
                prefs.remember_booking(state["city"], p.get("venue"))
            # movie page opened without a chosen showtime → tell the user how to go further
            hint = (" Name a venue and showtime (e.g. \"INOX Odeon 07:35 PM\") and I'll select the "
                    "exact show for you." if "/buytickets/" in p["book_url"] and not p.get("time") else "")
            answer = (f"I've opened {p.get('movie')} in your browser and got as far as: "
                      f"{' → '.join(done)}.{hint} Finish seat selection and payment there — "
                      "I never enter payment details.")
    state["history"].append({"role": "user", "parts": [{"text": state["question"]}]})
    state["history"].append({"role": "model", "parts": [{"text": answer}]})
    return {"answer": answer}


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


def drive_browser(url: str, venue: str, time_str: str, seats: int, category: str | None) -> list[str]:
    """Best-effort clickthrough; every step is optional — whatever fails, the human continues.
    ponytail: text-based locators, not per-site selector maps; revisit if BMS markup churns."""
    from playwright.sync_api import sync_playwright

    done = []
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
        return done
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
        done.append(f"selected the {time_str} show at {venue}")
        # quantity dialog: BMS gives the count buttons stable ids (#quantity-N).
        # an overlay intercepts normal clicks, so fall back to dispatching the event.
        def tap(loc):
            try:
                loc.click(timeout=4000)
            except Exception:
                loc.dispatch_event("click")
        tap(page.locator(f"#quantity-{seats}"))
        done.append(f"set {seats} tickets")
        if category:
            modal = page.locator("div:has-text('How many seats'):has-text('Select Seats')").last
            tap(modal.get_by_text(category, exact=False).first)
            done.append(f"picked {category}")
        tap(page.get_by_text(re.compile("Select Seats", re.I)).first)
        done.append("reached the seat layout — pick your seats")
    except Exception as e:
        import sys, traceback
        traceback.print_exc(file=sys.stderr)  # server console; user just continues by hand
    try:
        page.screenshot(path=str(booktic.CACHE / "last_booking.png"))
    except Exception:
        pass
    return done


def _route(state: State):
    return "book" if state["plan"].get("intent") == "book" else "chat"


g = StateGraph(State)
g.add_node("planner", plan_node)
g.add_node("chat", chat_node)
g.add_node("book", book_node)
g.set_entry_point("planner")
g.add_conditional_edges("planner", _route, {"chat": "chat", "book": "book"})
g.add_edge("chat", END)
g.add_edge("book", END)
graph = g.compile()


def handle(question: str, history: list, listings: str, city: str) -> tuple[str, bool]:
    """Returns (answer, booked) — booked=True when the browser-driving route ran."""
    try:
        out = graph.invoke({"question": question, "history": history,
                            "listings": listings, "city": city, "plan": {}, "answer": ""})
        return out["answer"], out.get("plan", {}).get("intent") == "book"
    except Exception:
        # planner/browser trouble must never take chat down — answer directly
        import sys, traceback
        traceback.print_exc(file=sys.stderr)
        return booktic.ask_llm(question, listings, history), False
