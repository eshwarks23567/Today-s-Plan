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


def remember_booking(city: str, venue: str | None) -> None:
    p = load()
    p["home_city"] = p.get("home_city") or city
    if venue:
        p["venues"][venue] = p["venues"].get(venue, 0) + 1
    PATH.write_text(json.dumps(p, indent=2), encoding="utf-8")


def summary() -> str:
    """One line for a system prompt; empty once nothing has been learned yet."""
    p = load()
    if not p["venues"]:
        return ""
    top = sorted(p["venues"], key=p["venues"].get, reverse=True)[:3]
    return (f"This user has previously booked at: {', '.join(top)}. Prefer these when "
            "they say 'my usual place' or don't name a venue.")
