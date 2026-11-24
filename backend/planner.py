"""The day planner: a place to visit, a meal and a movie, fitted into the time and
budget someone gives, for the number of people going.

Times, table slots, opening hours, travel and money are all worked out here in code.
The model is not asked to do any of it: asked to, it paired a 10 PM show with a
restaurant that stops seating at 10:30. The plan is written from what this file
computed, so every time and price in it is one the sources actually gave.
"""
import re
from datetime import datetime

import dining
import places
from booktic import _clock

PEOPLE_Q = "How many people are coming, including you?"
MOVIE_MIN = 165     # ponytail: no runtimes in the listings; 2h45 covers a feature plus ads, fetch runtimes if plans run long
MEAL_MIN = 90
VISIT_MIN = 120
NEAR_MIN, FAR_MIN = 15, 40   # ponytail: travel by "same neighbourhood or not"; swap in a routing service if plans miss connections
DAY_END = 23 * 60 + 30

# "plan my/our/a ...", not any "plan": "I plan to watch Ramayana at 9" is a showtime question
_PLAN = re.compile(r"\b(plan (?:my|our|a|an|the|me|us|for|out)|itinerary|day out|outing|things to do|"
                   r"what (?:should|can) (?:we|i) do)\b", re.I)
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


# ---- reading the request ----

def _asked_people(history: list) -> bool:
    last = history[-1] if history else None
    return bool(last and last.get("role") == "model" and last["parts"][0]["text"].startswith(PEOPLE_Q))


def wants_plan(question: str, history: list) -> bool:
    """A plan request, or the answer to this planner's own "how many people" question."""
    return bool(_PLAN.search(question)) or _asked_people(history)


def people(text: str) -> int | None:
    t = text.lower()
    if re.search(r"\b(just me|by myself|alone|solo)\b", t):
        return 1
    n = r"(\d{1,2}|" + "|".join(_NUMWORDS) + r")"
    m = (re.search(rf"\b{n}\s*(?:people|persons|adults|friends|of us|pax|ppl|members|guests)\b", t)
         or re.search(rf"\b(?:we are|we're|group of|party of|for)\s+{n}\b(?!\s*(?:pm|am|:|hours|hrs|rs|₹))", t)
         or re.fullmatch(rf"\s*{n}\s*", t))
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else _NUMWORDS[v]
    if re.search(r"\b(couple|my (?:wife|husband|girlfriend|boyfriend|partner)|date night|the two of us)\b", t):
        return 2
    return None


def _time(m, assume_pm: bool) -> int | None:
    return _clock(m.group(1), m.group(2), m.group(3), assume_pm) if m else None


_AT = r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\b"


def read_request(city: str, text: str, now: datetime | None = None) -> dict:
    """Everything the scheduler needs, from the words of the request."""
    now = now or datetime.now()
    t = text.lower()
    n = people(t)
    budget = re.search(r"(?:₹|\brs\.?\s*|\binr\s*|budget(?: of| is)?\s*|under\s*|within\s*|up ?to\s*|max\s*)(\d[\d,]{2,6})", t)
    amount = int(budget.group(1).replace(",", "")) if budget else None
    each = bool(re.search(r"\b(each|per (?:person|head)|a head|pp)\b", t))
    start = _time(re.search(r"\b(?:from|after|starting(?: at)?|leave at|start at)\s+" + _AT, t), True)
    end = _time(re.search(r"\b(?:till|until|by|before|back by|home by|end by)\s+" + _AT, t), True)
    soonest = (now.hour * 60 + now.minute + 14) // 15 * 15          # the next quarter hour
    for word, at in (("morning", 9 * 60), ("afternoon", 13 * 60), ("evening", 17 * 60), ("night", 19 * 60)):
        if start is None and word in t:
            start = at
    start = max(start or soonest, soonest)
    return {
        "people": n,
        "budget": amount * n if amount and each and n else amount,
        "budget_each": each,
        "start": start,
        "end": end or DAY_END,
        "area": dining.find_area(city, t),
        "movie": not re.search(r"\b(no|skip the|without a?) ?movies?\b", t),
        "visit": bool(re.search(r"\b(mall|shopping|sightsee\w*|tour\w*|visit|museum|park|explore|sights?|heritage|lake)\b", t)),
        "tomorrow": bool(re.search(r"\b(tomorrow|weekend|saturday|sunday|next week)\b", t)),
    }


# ---- the pieces a plan is made of ----

_SECTION = re.compile(r"^# (BookMyShow|District) — \d{4}-\d{2}-\d{2} \((today|[A-Za-z]+)\)")
_SHOW = re.compile(r"(\d{1,2}):(\d{2}) ([AP])M Rs(\d+)(?:-(\d+))?")


def parse_shows(listings: str) -> list[dict]:
    """Today's sessions from the listings text booktic writes (section() format)."""
    out, source, movie, book = [], None, None, None
    for line in listings.splitlines():
        if m := _SECTION.match(line):
            source = m.group(1) if m.group(2) == "today" else None
        elif line.startswith("#") and not line.startswith("##"):
            source = None                           # dining, events and later dates are not shows
        elif source and (m := re.match(r"^## (.+?)\s+— book: (\S+)", line)):
            movie, book = m.group(1), m.group(2)
        elif source and movie and (m := re.match(r"^- (.+?)(?: \[([^\]]+)\])?: (.+)$", line)):
            venue, fmt = m.group(1), m.group(2) or ""
            for s in _SHOW.finditer(m.group(3)):
                lo, hi = int(s.group(4)), int(s.group(5) or s.group(4))
                out.append({"movie": movie, "venue": venue, "format": fmt, "book": book, "source": source,
                            "time": f"{int(s.group(1))}:{s.group(2)} {s.group(3)}M",
                            "start": _clock(s.group(1), s.group(2), s.group(3), False),
                            "price": lo, "prices": f"Rs{lo}" + (f"-{hi}" if hi > lo else "")})
    return out


def meal_cost(r: dict, n: int) -> tuple[int, str]:
    """Bill for n from cost for two, after District's offer. Taxes and drinks are on top."""
    base = (r["cost_for_two"] or 0) / 2 * n
    offer = r.get("offer") or ""
    if m := re.search(r"(\d+)%\s*off", offer, re.I):
        return round(base * (100 - int(m.group(1))) / 100), offer
    if m := re.search(r"₹\s*(\d+)\s*off", offer, re.I):
        return round(max(0, base - int(m.group(1)))), offer
    return round(base), ""


def _minutes(slot: str) -> int | None:
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP])M", slot or "")
    return _clock(m.group(1), m.group(2), m.group(3), False) if m else None


_GENERIC = {"hyderabad", "bengaluru", "bangalore", "mumbai", "delhi", "chennai", "pune", "kolkata", "cinemas",
            "cinema", "multiplex", "dolby", "atmos", "laser", "restaurant", "road", "central", "city", "mall"}


def travel(a: str, b: str) -> int:
    """Minutes between two places: short when they share a neighbourhood word."""
    words = lambda s: {w for w in re.findall(r"[a-z]{5,}", s.lower())} - _GENERIC
    return NEAR_MIN if words(a) & words(b) else FAR_MIN


def visit_cost(p: dict, n: int) -> int:
    m = re.search(r"₹\s*(\d+)", p.get("price") or "")
    return int(m.group(1)) * n if m else 0


# ---- fitting them together ----

def schedule(req: dict, shows: list[dict], restaurants: list[dict], sights: list[dict]) -> list[dict]:
    """Every plan that fits the window and the budget, best first."""
    n, start, end, area = req["people"], req["start"], req["end"], req["area"] or ""
    shows = [s for s in shows if s["start"] and s["start"] >= start and s["start"] + MOVIE_MIN <= end] if req["movie"] else []
    plans = []

    def add(stops):
        cost = sum(s["cost"] for s in stops)
        if req["budget"] and cost > req["budget"]:
            return
        meal = next((s for s in stops if s["kind"] == "meal"), None)
        idle = sum(b["at"] - (a["at"] + a["len"]) for a, b in zip(stops, stops[1:]))
        near = sum(8 for s in stops if area and area in s["where"].lower())
        visited = any(s["kind"] == "visit" for s in stops)
        # stops close together beat a cross-town trip, most of all when no area was named
        close = sum(10 for a, b in zip(stops, stops[1:]) if travel(a["where"], b["where"]) == NEAR_MIN)
        score = (float(meal["obj"]["rating"] or 3.5) * 10 if meal else 0) + near + close - idle / 10 \
            + (25 if visited and req["visit"] else 10 if visited else 0)
        if meal and (m := re.search(r"(\d+)%", meal["obj"].get("offer") or "")):
            score += int(m.group(1)) / 3
        if meal and meal["at"] >= 16 * 60:
            score -= abs(meal["at"] - 20 * 60) / 20      # an evening meal leans towards 8 PM, not the first free table
        plans.append({"stops": stops, "cost": cost, "score": score})

    def meal_stop(r, at):
        cost, offer = meal_cost(r, n)
        return {"kind": "meal", "at": at, "len": MEAL_MIN, "obj": r, "cost": cost, "where": r["locality"], "offer": offer}

    def show_stop(s):
        return {"kind": "movie", "at": s["start"], "len": MOVIE_MIN, "obj": s, "cost": s["price"] * n, "where": s["venue"]}

    def with_visit(stops):
        """A sight or mall before the first stop, when there is time and one is open."""
        if not req["visit"] and stops[0]["at"] - start < VISIT_MIN + FAR_MIN + 60:
            return stops
        for p in sights:
            hours = p.get("open_close")
            if not hours:
                continue
            leave = stops[0]["at"] - travel(p["name"] + " " + p["district"], stops[0]["where"])
            at = max(start, hours[0])
            if at + VISIT_MIN <= min(leave, hours[1]):
                return [{"kind": "visit", "at": at, "len": VISIT_MIN, "obj": p, "cost": visit_cost(p, n),
                         "where": p["name"] + " " + p["district"]}] + stops
        return stops

    tables = [(r, [t for t in map(_minutes, r["tables"]) if t is not None]) for r in restaurants if r["cost_for_two"]]
    for s in shows:
        for r, slots in tables:
            gap = travel(r["locality"], s["venue"])
            before = [t for t in slots if t >= start and t + MEAL_MIN + gap <= s["start"]]
            if before:
                add(with_visit([meal_stop(r, max(before)), show_stop(s)]))
            after = [t for t in slots if t >= s["start"] + MOVIE_MIN + gap and t + MEAL_MIN <= end]
            if after:
                add(with_visit([show_stop(s), meal_stop(r, min(after))]))
    if not shows:                   # no movie wanted, or none fits: a meal (and a visit) still makes a plan
        for r, slots in tables:
            # every table, not just the first: a later one leaves room for the visit before it
            for t in slots:
                if t >= start and t + MEAL_MIN <= end:
                    add(with_visit([meal_stop(r, t)]))
    return sorted(plans, key=lambda p: -p["score"])


def pick(plans: list[dict], k: int = 3) -> list[dict]:
    """The best plan and up to two alternatives that differ in both the meal and the show."""
    out = []
    key = lambda p: {(s["kind"], s["obj"].get("name") or s["obj"].get("movie")) for s in p["stops"]}
    for p in plans:
        if all(not (key(p) & key(q)) for q in out):
            out.append(p)
        if len(out) == k:
            break
    return out


# ---- writing it ----

def _clock_text(m: int) -> str:
    m %= 24 * 60
    return f"{(m // 60 - 1) % 12 + 1}:{m % 60:02d} {'AM' if m < 720 else 'PM'}"


def _stop_lines(s: dict, n: int) -> list[str]:
    o, at = s["obj"], _clock_text(s["at"])
    if s["kind"] == "visit":
        extra = "; ".join(b for b in (o["hours"] and f"hours: {o['hours']}", o["price"] and f"entry: {o['price']}") if b)
        return [f"- **{at} · {o['name']}** ({o['district']}){' — ' + extra if extra else ''}"]
    if s["kind"] == "meal":
        offer = f", {s['offer']}" if s["offer"] else ""
        meal = "Lunch" if s["at"] < 15 * 60 else "Early dinner" if s["at"] < 18 * 60 + 30 else "Dinner"
        # the page draws a link as a button, so it goes at the end of the line, not mid-sentence
        book = "Book this table on District" + (" for the offer" if s["offer"] else "")
        return [f"- **{at} · {meal} at {o['name']}** — {o['cuisines']}; ₹{o['cost_for_two']} for two{offer}, "
                f"about ₹{s['cost']:,} for {n}. [{book}]({o['url']})"]
    bms, dis = (o["prices"], "-") if o["source"] == "BookMyShow" else ("-", o["prices"])
    fmt = f" ({o['format']})" if o["format"] else ""
    return [f"- **{at} · {o['movie']}**{fmt} at {o['venue']} — ₹{o['price']} × {n} = ₹{s['cost']:,}",
            f"[[show|{o['movie']}|{o['venue']}|{o['time']}|{bms}|{dis}]]"]


def _notes(req: dict, best: dict) -> list[str]:
    n = req["people"]
    notes = ["meal prices are District's cost for two scaled to your group, before taxes and drinks",
             "travel times are rough (15 min within a neighbourhood, 40 across town)"]
    if req["budget"]:
        notes.insert(0, f"I read ₹{req['budget']:,} as the budget for all {n}" + (" (your per-person figure × the group)" if req["budget_each"] else ""))
    if any(s["kind"] == "visit" for s in best["stops"]):
        notes.append("opening hours come from Wikivoyage and skip weekday closures, so check before you go")
    return notes


def render(req: dict, chosen: list[dict], city: str) -> str:
    """The plan as chat text: what the conversation history keeps and the voice reads."""
    n = req["people"]
    where = (req["area"] or city).title()
    best = chosen[0]
    budget = f" of your ₹{req['budget']:,}" if req["budget"] else ""
    lines = [f"**Plan for {n} · {where} · about ₹{best['cost']:,}{budget}**", ""]
    for s in best["stops"]:
        lines += _stop_lines(s, n)
    last = best["stops"][-1]
    lines += ["", f"Done around {_clock_text(last['at'] + last['len'])}."]
    if len(chosen) > 1:
        lines += ["", "**Other ways to spend it**"]
        for p in chosen[1:]:
            parts = [f"{_clock_text(s['at'])} {s['obj'].get('name') or s['obj'].get('movie')}" for s in p["stops"]]
            lines.append(f"- {' → '.join(parts)} — about ₹{p['cost']:,}")
    lines += ["", "Notes: " + "; ".join(_notes(req, best)) + "."]
    return "\n".join(lines)


def _stop_card(s: dict, n: int) -> dict:
    """One stop as data for the page's plan card."""
    o = s["obj"]
    stop = {"kind": s["kind"], "at": _clock_text(s["at"]), "until": _clock_text(s["at"] + s["len"]), "cost": s["cost"]}
    if s["kind"] == "visit":
        stop.update(label="Visit", title=o["name"], detail=" · ".join(b for b in (o["district"], o["hours"] and f"open {o['hours']}",
                                                                                   o["price"] and f"entry {o['price']}") if b))
    elif s["kind"] == "meal":
        stop.update(label="Lunch" if s["at"] < 15 * 60 else "Early dinner" if s["at"] < 18 * 60 + 30 else "Dinner",
                    title=o["name"], offer=s["offer"] or None,
                    detail=f"{o['cuisines']} · ₹{o['cost_for_two']} for two · {o['locality']}",
                    link={"label": "Book table", "url": o["url"]})
    else:
        stop.update(label="Movie", title=o["movie"],
                    detail=f"{o['venue']}{' · ' + o['format'] if o['format'] else ''} · ₹{o['price']} × {n}",
                    show={"movie": o["movie"], "venue": o["venue"], "time": o["time"], "source": o["source"], "seats": n})
    return stop


def plan_data(req: dict, chosen: list[dict], city: str) -> dict:
    """The same plan as structured data, so the page can draw it as a timeline and
    swap in an alternative without asking again."""
    def option(p):
        last = p["stops"][-1]
        return {"cost": p["cost"], "ends": _clock_text(last["at"] + last["len"]), "stops": [_stop_card(s, req["people"]) for s in p["stops"]]}
    return {"people": req["people"], "where": (req["area"] or city).title(), "budget": req["budget"],
            "options": [option(p) for p in chosen], "notes": _notes(req, chosen[0])}


PEOPLE_CHOICES = ["Just me", "2", "3", "4", "5", "6"]


def respond(city: str, question: str, history: list, listings: str, now: datetime | None = None) -> dict:
    """The planner's whole reply: {"text"} always, plus "choices" (quick replies) when it
    asks a question back, or "plan" (structured, for the page's plan card) when it plans."""
    text = question
    if _asked_people(history) and len(history) >= 2:
        # the reply answers our question ("2", "four of us"): read the size from it alone,
        # then plan the request it answers
        n = people(question)
        text = history[-2]["parts"][0]["text"] + " " + (f"for {n} people" if n else question)
    req = read_request(city, text, now)
    if req["people"] is None:
        return {"text": PEOPLE_Q, "choices": PEOPLE_CHOICES}
    if req["tomorrow"]:
        return {"text": "I can only plan today: District, the source the plans are built from, lists today's shows "
                        "and tables only. Ask me in the morning and I'll plan the day."}
    if req["start"] + 60 > req["end"]:
        return {"text": "There isn't enough of today left in that window to plan anything. Try a later end time."}
    try:
        rows = dining.restaurants(city, req["area"])
    except RuntimeError:
        rows = []
    sights = places.near(city, req["area"]) if (req["visit"] or req["end"] - req["start"] >= 6 * 60) else []
    plans = schedule(req, parse_shows(listings), rows, sights)
    if not plans:
        why = f"within ₹{req['budget']:,}" if req["budget"] else "in that time window"
        return {"text": f"I couldn't fit a plan for {req['people']} {why} with today's shows and open tables"
                        f"{' near ' + req['area'].title() if req['area'] else ''}. Try a wider window, a higher budget or another area."}
    chosen = pick(plans)
    return {"text": render(req, chosen, city), "plan": plan_data(req, chosen, city)}
