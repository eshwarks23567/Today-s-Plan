"""Per-user preference memory — a JSON file, not a database (one local user).

Learned passively from successful bookings: which venues and cities keep
coming up, so "book my usual" and city-less recommendations have something
to lean on. No explicit settings UI; the file is the whole interface.

Every request runs on its own thread and summary() reads this file on all of
them, while remember_booking() rewrites it. Both go through one reentrant lock:
without it a reader lands mid-write and parses a truncated file, and two
bookings read-modify-write over each other. It is reentrant because
remember_booking calls load() while already holding it.
"""
import json
import threading
from pathlib import Path

PATH = Path(__file__).parent / "prefs.json"
_lock = threading.RLock()


def load() -> dict:
    """Never raises. Preferences are a nicety; a missing or half-written file must
    not take a request down with it."""
    with _lock:
        try:
            p = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            p = {}
    if not isinstance(p, dict):
        p = {}
    p.setdefault("home_city", None)
    p.setdefault("venues", {})
    if not isinstance(p["venues"], dict):
        p["venues"] = {}
    return p


def remember_booking(city: str, venue: str | None, seats: int | None = None) -> None:
    with _lock:
        p = load()
        p["home_city"] = p.get("home_city") or city
        if venue:
            p["venues"][venue] = p["venues"].get(venue, 0) + 1
        if seats:
            # ponytail: last party size wins, not a histogram — who you go with changes,
            # and the most recent booking is the better guess for the next one
            p["seats"] = seats
        PATH.write_text(json.dumps(p, indent=2), encoding="utf-8")


def summary() -> str:
    """One line for a system prompt; empty until something has actually been learned."""
    p = load()
    bits = []
    if p.get("venues"):
        top = sorted(p["venues"], key=p["venues"].get, reverse=True)[:3]
        bits.append(f"This user has previously booked at: {', '.join(top)}. Prefer these when "
                    "they say 'my usual place' or don't name a venue.")
    if p.get("seats"):
        bits.append(f"They last booked {p['seats']} tickets — assume that party size when they "
                    "don't say how many, and list it under `inferred` when you do.")
    return " ".join(bits)
