# Today's Plan

A free, voice-first AI agent for planning your evening. Ask in plain language — *"what's playing tonight?"*, *"cheapest tickets for Alpha"*, *"any concerts this weekend?"* — and it reads **live listings from BookMyShow and District**, compares prices across every cinema in your city, and can open your browser straight on a specific show's seat map, leaving only payment to you.

Built to cost nothing to run: no paid APIs, browser-native voice, free-tier LLM, and **no third-party Python packages at all** — the backend is standard library only.

## Features

- **Voice in, voice out** — browser-native speech recognition and TTS, with a live transcript while you speak
- **Streaming answers** — replies appear as they are generated, so a long comparison starts reading in 2–4s instead of landing all at once after 15+
- **Two ticket sellers compared** — the same cinema often prices differently on BookMyShow vs District; the agent calls out which is cheaper
- **5-day showtime window** — today plus the next four days, refreshed in the background every ~20 minutes
- **Events & concerts** — gigs, standup, workshops with dates and starting prices
- **One-hop booking** — say *"book 2 tickets for Alpha at INOX Odeon 07:35 PM"* and it resolves that exact show to its seat-map URL and opens it. It asks first whenever it had to infer the venue, time or party size rather than being told.
- **Grounded answers only** — the model answers strictly from crawled listings, and every recommendation carries its booking link
- **Persistent chats** — past conversations and their context survive refreshes, with per-chat delete
- **7 cities** — Hyderabad, Bengaluru, Mumbai, Delhi NCR, Chennai, Pune, Kolkata

## Quick start

Requirements: Python 3.10+, Windows (uses the bundled `curl.exe`). Nothing to `pip install`.

```powershell
# 1. Get a free Gemini API key: https://aistudio.google.com/apikey
setx GEMINI_API_KEY "your-key"     # then open a fresh terminal

# 2. Run
python backend/server.py           # → http://localhost:8765
```

The server listens on loopback only, because booking opens a browser window on the
machine running it. To reach it from your phone:

```powershell
python backend/server.py --lan     # anyone on your Wi-Fi, no login
.\run.ps1                          # or: Cloudflare tunnel, public HTTPS URL
```

`run.ps1` starts the server and a quick tunnel and prints the URL. Reached remotely,
booking returns a tappable link instead of opening a browser on the host — the
server is not the device you are holding.

The core Q&A also works as a plain terminal chat:

```powershell
python backend/booktic.py --city hyderabad
python backend/booktic.py --crawl        # just print the listings snapshot
```

## How it works

```
Browser (voice + chat UI)
        │  POST /api/ask   ← Server-Sent Events back
        ▼
server.py (stdlib HTTP)
        │
        ▼
agent.py ── one Gemini call, with a `book` tool declared
        │
        ├── plain answer  → streamed to the page token by token
        └── tool call     → resolve the exact show's seat-map URL,
                            confirm if anything was inferred, then open it
        ▲
booktic.crawl — 20-min snapshots of:
BookMyShow movies (5 days) · District movies · BMS events
```

There is no planner, router or agent framework. One model call reads the listings,
answers questions, and decides to book — so *"the second one"* resolves against what
it actually said, not against a summary handed to a second call.

Booking used to drive a real browser with Playwright. It no longer does: BookMyShow
removed the seat-count dialog that automation clicked, and its seat layout will not
render inside an automated browser at all. Every part of the seat-map URL is data
BookMyShow already publishes, so the agent builds the link and hands it to your own
browser — faster, far less code, and it actually works.

Scraping notes, hard-earned:

- BookMyShow's CDN blocks Python's TLS fingerprint but not plain `curl` — fetches shell out to `curl.exe`, pinned to https so a hostile link in a listing can't read local files
- Showtimes live in `showtimesFunctionalApi.queries['fetchPrimaryDynamic-…']` inside `__INITIAL_STATE__`. The older `showtimesByEvent.showDates` is still present but is now always empty — parsing it silently yields zero movies
- Per-category prices are not on the showtime. They sit in the bottom sheet each showtime opens on double-tap, as display strings like `₹ 1,250.00`
- **Ask BookMyShow for a date with no shows and it answers with that movie's next available date**, correctly formatted and entirely plausible. The parser compares the served `dateCode` against the requested one and drops mismatches, or next Friday's showtimes end up filed under today
- District serves session times in UTC with no timezone marker (+5:30 to IST)
- Listings snapshots are plain text files with a TTL — stale-while-revalidate, so answers never wait on a crawl

## Tests

Three files, three different jobs:

```powershell
python backend/test_scrapers.py    # fast, offline: parsers, agent logic, concurrency
python backend/test_live.py        # hits the real sites — run this on a schedule
python backend/test_stress.py      # needs a running server; concurrency and hostile input
```

`test_scrapers.py` runs the real parsers against synthetic fixtures, so it catches
regressions in this code. It cannot catch a site changing shape underneath you —
that is exactly how BookMyShow's move went unnoticed while every fixture passed.
`test_live.py` is the other half, and asserts only what must be true on any working
day. `test_stress.py` found six concurrency and input-validation bugs that no
single-threaded test would have.

## Limitations

- Future dates (up to 4 ahead) are BookMyShow-only; District renders only today server-side
- Deep links to a specific seat map are BookMyShow-only — District bookings open the movie page
- Gemini's free tier throttles at ~20 requests/min; the app falls back to the lite model, and the server rate-limits to 20/min per IP
- Voice input needs a browser with the Web Speech API — verify on your own phone before relying on it
- A Cloudflare quick tunnel hands out a **new URL every restart**, and there is no login on it: treat the link as the password
- BookMyShow can change shape any day. `test_live.py` is the early warning

## Disclaimer

Prices come from live public listings and can change at checkout. Booking and payment always complete on BookMyShow or District — this tool never handles payment details. Not affiliated with BookMyShow, District, or Google.
