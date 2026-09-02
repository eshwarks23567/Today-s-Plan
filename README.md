# Today's Plan

A free, voice-first AI agent for planning your evening. Ask in plain language — *"what's playing tonight?"*, *"cheapest tickets for Alpha"*, *"any concerts this weekend?"* — and it reads **live listings from BookMyShow and District**, compares prices across every cinema in your city, and can open your browser straight on a specific show's seat map, leaving only payment to you.

Built to cost nothing to run: no paid APIs, browser-native voice, free-tier LLM, and **no third-party Python packages at all** — the backend is standard library only.

## Features

- **Voice in, voice out** — browser-native speech recognition and TTS, with a live transcript while you speak
- **Streaming answers** — replies appear as they are generated, so a long comparison starts reading in 2–4s instead of landing all at once after 15+
- **Language and format** — every showtime carries its language and screen format, so "any Telugu shows after 9?" is answerable
- **Two ticket sellers compared** — the same cinema often prices differently on BookMyShow vs District; the agent calls out which is cheaper
- **5-day showtime window** — today plus the next four days. Today refreshes every ~20 minutes; later dates hourly, because Friday's 8pm show is still Friday's 8pm show an hour later
- **Events & concerts** — gigs, standup, workshops with dates and starting prices
- **One-hop booking** — say *"book 2 tickets for Alpha at INOX Odeon 07:35 PM"* and it resolves that exact show to its seat-map URL and opens it. It asks first whenever it had to infer the venue, time or party size rather than being told.
- **Grounded answers only** — the model answers strictly from crawled listings, and every recommendation carries its booking link
- **Bring your own model** — Gemini by default, or any open-weight model via Groq, OpenRouter or a local Ollama; see [Choosing a model](#choosing-a-model)
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

## Choosing a model

Gemini's free tier is the default. Any open-weight model works too — Groq,
OpenRouter, Together and a local Ollama or llama.cpp server all speak the OpenAI
chat-completions shape, so one adapter covers all of them:

```powershell
# open weights, hosted (Groq's free tier is fast and generous)
$env:LLM_PROVIDER = "openai"
$env:LLM_API_KEY  = "gsk_..."
$env:LLM_MODEL    = "openai/gpt-oss-120b"

# open weights, entirely on your own machine — no key, no vendor, no quota
$env:LLM_PROVIDER = "openai"
$env:LLM_BASE_URL = "http://localhost:11434/v1"   # ollama serve
$env:LLM_MODEL    = "llama3.2"
```

The server prints which model it is using at startup. `LLM_BASE_URL` defaults to
Groq; `LLM_API_KEY` is optional, because a local server does not want one.

**Mind the token budget.** Counted by the providers themselves — not estimated —
one city is ~13,000 tokens for today alone and roughly double with the later dates. Turns
that do not name another day send the smaller one, but every turn still carries the
whole of that day. Character-count estimates run about half the real figure here:
venue names and prices tokenise badly. Gemini's free tier swallows that; Groq's free tier caps at
8,000 tokens per minute, so it returns HTTP 413 on a large city no matter which
model you pick — fine for a small city or a paid tier. A local Ollama has no such
limit, only your own hardware.

Some of these APIs sit behind Cloudflare, which rejects Python's default
`User-Agent` outright with a 403 (error 1010), so requests go out with a browser
one. The same check is why `fetch()` shells out to curl for BookMyShow.

Two things the model must support, whichever you pick: **streaming**, or answers
arrive all at once after fifteen seconds, and **tool calling**, or booking stops
working and every request becomes a chat reply. Most instruction-tuned open models
above ~8B handle both; very small ones tend to answer *about* the tool instead of
calling it.

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
agent.py ── one LLM call, with a `book` tool declared
        │            (Gemini, or any OpenAI-compatible open-weight model)
        │
        ├── plain answer  → streamed to the page token by token
        └── tool call     → resolve the exact show's seat-map URL,
                            confirm if anything was inferred, then open it
        ▲
booktic.crawl — two snapshots on two clocks:
  today  (~20 min): BMS today · District · events
  future (hourly) : BMS tomorrow..+4
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
- **Cloudflare answers a challenged request with HTTP 200 and a "Just a moment…" page**, so a blocked fetch is indistinguishable from a good one until the parser finds nothing in it. `fetch()` detects the interstitial by title and retries; the `challenge-platform` script tag is NOT a tell, it is on every page including good ones
- The LLM APIs fingerprint too: Groq answers a default-User-Agent request with 403 (Cloudflare error 1010) and Google's endpoint just swallows it, so model calls send a browser UA as well
- Showtimes live in `showtimesFunctionalApi.queries['fetchPrimaryDynamic-…']` inside `__INITIAL_STATE__`. The older `showtimesByEvent.showDates` is still present but is now always empty — parsing it silently yields zero movies
- Per-category prices are not on the showtime. They sit in the bottom sheet each showtime opens on double-tap, as display strings like `₹ 1,250.00`
- **Ask BookMyShow for a date with no shows and it answers with that movie's next available date**, correctly formatted and entirely plausible. The parser compares the served `dateCode` against the requested one and drops mismatches, or next Friday's showtimes end up filed under today
- District serves session times in UTC with no timezone marker (+5:30 to IST)
- Listings snapshots are plain text files with a TTL — stale-while-revalidate, so answers never wait on a crawl

## Metrics

Measured on one Windows 11 laptop against the live sites — medians of a few runs,
not a benchmark suite. Anything touching Gemini varies with their load.

**Footprint and startup**

| | before | now |
|---|---|---|
| third-party Python packages | 3 (langgraph, langchain-google-genai, playwright) | **0** |
| `import agent` | 15.3s | **0.36s** |
| server boot → first HTTP 200 | ~20s | **0.83s** |

The old numbers were the cost of a framework doing one field-extraction call.
Replacing it with a single Gemini call and a tool declaration removed both LLM
dependencies; dropping Playwright for a deep link removed the last one.

**Answering**

| | |
|---|---|
| time to first token | **2.1s** over the tunnel, 3.6s local |
| full answer (13–15k chars) | 16–21s |
| dead air removed by streaming | **12.8–19.3s** |
| LLM calls per booking turn | 1 (was 2) |
| prompt carried per turn | 13,126 tokens today-only (counted by the API) |
| turns that need later dates | only those naming one — the rest send 35% less |
| language/format annotation | 3,900 chars (11,300 before hoisting it per venue) |

Time-to-first-token is roughly flat regardless of answer length — that is the model
reading the listings. Everything after it streams, so the longer the answer, the
more streaming wins.

**Serving**

| | before | now |
|---|---|---|
| `http://localhost:8765` p50 | 2050ms | **10ms** |
| static assets, 40 concurrent | 19 req/s | **130+ req/s** |

Both numbers were one bug: `localhost` resolves to `::1` before `127.0.0.1` on
Windows, and the server was listening on IPv4 only, so every connection paid a
~2s fallback. It now listens on both.

**Data and booking**

| | |
|---|---|
| cold crawl, both parts | 74 fetches in **5s** |
| routine refresh (today only) | **22 fetches** |
| fetches per hour, one city | 219 → **118** |
| listings snapshot | 52,000 chars across 7 sections, 5 days |
| BookMyShow coverage, one city | ~150 venues / ~240 sessions today |
| booking, question → seat map open | **4.7s** (was ~60s of browser driving, and broken) |

**Code**

| | |
|---|---|
| backend | 1,103 lines across 5 files |
| tests | 930 lines across 3 files |
| frontend | 658 JS + 305 CSS lines |
| checks | 63 offline, 8 live |

## Docker

```powershell
docker build -t todaysplan .
docker run -p 8765:8765 -e GEMINI_API_KEY=$env:GEMINI_API_KEY todaysplan
```

195MB, and no `pip install` step — the image is a base image plus the source. It
does need `curl`, which `python:slim` does not ship: `fetch()` shells out to it,
and without it every crawl dies with `fetch failed (exit 127)`, which reads like
an application bug rather than a missing binary.

Setting `$PORT` is what puts the server in hosted mode, so the Dockerfile
defaults it — otherwise the server would bind loopback *inside* the container and
be reachable by nothing. Runs as a non-root user; the crawl snapshots under
`backend/cache` are the only thing written at runtime.

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
