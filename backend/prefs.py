"""Per-user preference memory — a JSON file, not a database (one local user).

Learned passively from successful bookings: which venues and cities keep
coming up, so "book my usual" and city-less recommendations have something
to lean on. No explicit settings UI; the file is the whole interface.
"""
import json
from pathlib import Path

PATH = Path(__file__).parent / "prefs.json"


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {"home_city": None, "venues": {}}


def remember_booking(city: str, venue: str | None, seats: int | None = None) -> None:
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
