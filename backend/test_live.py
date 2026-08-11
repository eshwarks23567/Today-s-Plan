"""Live smoke test — hits the real BookMyShow and District, no fixtures.

test_scrapers.py runs the parsers against synthetic HTML, so it catches a
regression in our code and nothing at all when a site changes shape underneath
us. That is exactly how BMS moving its showtimes out of showtimesByEvent went
unnoticed: every fixture still passed while the live crawl quietly returned zero
movies for five days running.

This is the other half. It asserts only what must be true of any working day —
some movies, some venues, some prices, a resolvable seat link — so it stays quiet
unless something is genuinely broken.

    python test_live.py            # exits 1 on failure, for a scheduled run

Deliberately NOT part of test_scrapers.py: it needs the network, it is slow, and
it can fail for reasons that are not your fault (BMS down, no shows on a holiday).
Run it on a schedule, not on every save.
"""
import sys
from datetime import date

import booktic

CITY = "hyderabad"
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  <- ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def main():
    print(f"live smoke test — {CITY}, {date.today()}")

    movies = booktic.itemlist(booktic.fetch(f"https://in.bookmyshow.com/explore/movies-{CITY}"))
    check("BMS lists movies", len(movies) >= 3, f"got {len(movies)}")

    # Scan a few movies rather than trusting the first: any single title can
    # legitimately have no shows today (advance booking, ended run, festival slot).
    datecode = date.today().strftime("%Y%m%d")
    venues = sessions = 0
    sample = None
    for mv in movies[:6]:
        m = dict(mv)  # bms_showtimes sets m['book'] as a side effect — keep THAT dict,
        rows = booktic.bms_showtimes(m, datecode)  # rather than re-crawling for it later
        venues += len(rows)
        sessions += sum(len(r["sessions"]) for r in rows)
        if rows and sample is None:
            sample = (m, rows[0])
    check("BMS returns showtimes today", sessions > 0, f"{venues} venues, {sessions} sessions")

    if sample:
        mv, row = sample
        # the LAST session of the day, not the first: bms_seat_url re-fetches the page,
        # and near a showtime the earliest listing can vanish between the two calls —
        # which fails the check for a reason that has nothing to do with the link
        s = row["sessions"][-1]
        check("BMS sessions carry a sane price", 20 <= s["min"] <= 5000, s)
        check("BMS sessions carry a showtime", bool(s.get("time")), s)
        # the deep link is what booking hands the user — if it stops resolving,
        # booking silently degrades to "here is the movie page, good luck"
        deep = booktic.bms_seat_url(mv.get("book", ""), row["venue"], s["time"])
        check("seat-layout deep link resolves", bool(deep and "/seat-layout/" in deep), deep)

    dmovies = booktic.itemlist(booktic.fetch(
        f"https://www.district.in/movies/?city={booktic.DISTRICT_CITY.get(CITY, CITY)}"))
    check("District lists movies", len(dmovies) >= 3, f"got {len(dmovies)}")
    drows = []
    for mv in dmovies[:4]:
        drows = booktic.district_showtimes(dict(mv), CITY, date.today().isoformat())
        if drows:
            break
    check("District returns showtimes today", bool(drows), f"{len(drows)} venues")

    check("events are found", len(booktic.bms_events(CITY)) > 0)

    if FAILURES:
        print(f"\n{len(FAILURES)} live check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("\nlive sources all healthy")


if __name__ == "__main__":
    main()
