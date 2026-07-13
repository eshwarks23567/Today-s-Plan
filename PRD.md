# BookTic — Product Requirements Document

**Version:** 0.1 (Draft) · **Date:** 13 Jul 2026 · **Owner:** Eshwar Kamurthy Srinivasalu

---

## 1. Overview

BookTic is a **voice-first, agentic event & movie discovery platform**. Instead of browsing a catalog like BookMyShow, the user *talks* to an agent: *"What's happening tonight?"*, *"Find me the cheapest tickets for Dune 3 this weekend."* The agent gathers live data from the web (ticketing sites, event aggregators, cinema listings), reasons over it, and returns ranked, personalized recommendations — sorted by price, time, distance, or preference — with deep links to book.

**One-liner:** *BookMyShow, but you talk to it and it does the searching, comparing, and haggling for you.*

## 2. Problem Statement

Booking an evening out today means:
1. Opening 3–4 apps/sites (BookMyShow, District, Insider, Google) to see what's on.
2. Manually comparing showtimes, venues, and prices across them.
3. No single place answers "what's the cheapest way to watch X tonight near me?"
4. Discovery is browse-driven, not intent-driven — the user does the work, not the platform.

## 3. Goals & Non-Goals

### Goals (v1)
- Answer natural-language (voice + text) queries about movies/events happening in the user's city.
- Aggregate live listing data from the web (search + scraping + APIs where available).
- Rank and sort results by **price, time, distance, rating, and user preference**.
- Find the **cheapest ticket** for a specific movie/event across sources.
- Hand off to the source platform for actual payment/booking (deep link).

### Non-Goals (v1)
- Processing payments or issuing tickets ourselves.
- Automated checkout / auto-purchase on third-party sites (legal + ToS risk; revisit later).
- Covering every city — launch with 1–2 metro cities.
- Native mobile apps — start with a web app (PWA) with mic access.
- **Any revenue** — BookTic is a free service. No ads, no premium tier, no affiliate fees in v1.

### Zero-Cost Constraint

The platform must be **free for users AND free to build/operate**. Every technology choice below must fit in a free tier or be open-source and self-hostable:

| Need | Free choice | Notes |
|---|---|---|
| STT + TTS | **Web Speech API** (browser-native) | Zero cost, zero server load; quality is good enough on Chrome/Edge. Whisper (self-hosted) as fallback if needed |
| LLM | Free-tier APIs (Google Gemini free tier, Groq free tier) or local via Ollama | Rate limits are fine at prototype scale |
| Search layer | DuckDuckGo (no key), direct source crawling | No SerpAPI/Bing paid APIs |
| Scraping | Python + httpx/BeautifulSoup/Playwright | All open-source |
| Database | SQLite (local) → Supabase/Neon free tier when hosted | Listings volume for 1 city fits easily |
| Hosting | Vercel/Render/Fly free tier; PWA is static + one small backend | |
| Maps/geo | OpenStreetMap + Nominatim | No Google Maps billing |

Scale ceiling of the free tiers (LLM rate limits, ~100s of daily users) is accepted — this is a personal/portfolio project first.

## 4. Target Users

| Persona | Need |
|---|---|
| **Spontaneous Sam** (22–30, urban) | "What's fun tonight?" — zero-effort discovery |
| **Budget Priya** (student) | Cheapest tickets, discounts, off-peak shows |
| **Planner Rahul** (family) | Specific movie, specific time window, seats together |
| **Accessibility users** | Voice-first interaction as a genuine access need, not a gimmick |

## 5. Core User Flows

### Flow A — Open discovery
> 🎤 "What are the events happening tonight?"

1. Agent resolves context: location (GPS/profile), date = today evening.
2. Fans out to data sources (cached crawl + live search).
3. Deduplicates and normalizes listings (title, venue, time, price range, category).
4. Responds by voice + visual cards: top 5 ranked, with "sort by price / time / distance" chips.

### Flow B — Cheapest ticket hunt
> 🎤 "Find me the cheapest tickets for F1 the Movie this weekend."

1. Agent identifies the title, expands to all showings Sat–Sun in the user's city.
2. Pulls per-show pricing across all sources/cinema chains.
3. Returns cheapest option(s) with tradeoffs stated aloud ("₹149 at PVR Kukatpally, but it's a 9 AM show; cheapest evening show is ₹220 at AMB").
4. One tap/voice-confirm → deep link to the booking page.

### Flow C — Constrained search
> 🎤 "Any standup comedy under ₹500 near Gachibowli after 8 PM?"

Multi-constraint filtering (category + price + location + time) resolved in one utterance.

### Flow D — Follow-up conversation
> 🎤 "Okay what about tomorrow instead?" / "Only IMAX." / "Book the second one."

Agent maintains dialog state; refinements don't restart the search.

## 6. Functional Requirements

| # | Requirement | Priority |
|---|---|---|
| F1 | Voice input (speech-to-text) and spoken responses (TTS) | P0 |
| F2 | Text chat fallback (same agent, same pipeline) | P0 |
| F3 | Intent + entity extraction (title, date/time, budget, location, category) | P0 |
| F4 | Web data acquisition: scrapers/search for movie & event listings | P0 |
| F5 | Normalization & dedup of listings across sources into a unified schema | P0 |
| F6 | Ranking engine: sort by price / time / distance / rating; blended default rank | P0 |
| F7 | Cheapest-ticket comparison for a named title across all sources | P0 |
| F8 | Deep links to source booking pages | P0 |
| F9 | Conversational memory within a session (follow-ups, refinements) | P0 |
| F10 | User profile: home city, preferences, language | P1 |
| F11 | Price-drop / availability alerts ("tell me when tickets open") | P1 |
| F12 | Personalized recommendations from booking/click history | P1 |
| F13 | Multi-language voice (English + Hindi + Telugu to start) | P1 |
| F14 | Group planning ("find a slot that works for 4 people") | P2 |
| F15 | Calendar integration (add booked event, check free slots) | P2 |

## 7. Data Sources & Acquisition Strategy

**Order of preference per source (all free):**
1. **Free official APIs** — use wherever offered (some event platforms have free public endpoints).
2. **Structured data on public pages** — JSON-LD / schema.org `Event` markup (Google requires it, so most listing pages have it). Cheap to parse, resilient, free.
3. **Free search discovery** — DuckDuckGo HTML results / Google Programmable Search free quota for long-tail events.
4. **HTML scraping** — per-source adapters (httpx + BeautifulSoup; Playwright only for JS-heavy pages), respect robots.txt and rate limits.

**Freshness model:** background crawl every N hours for catalog (what exists), live fetch at query time for volatile data (seat prices, availability). Cache with short TTL on price data.

**Legal note:** scraping ticketing sites may violate ToS. v1 mitigations: public-data only, no login-walled scraping, no automated purchasing, attribution + traffic referral back to sources (they get the sale — this is the pitch for future partnerships).

## 8. System Architecture (high level)

```
Voice (mic) ──► STT ──► Agent Orchestrator (LLM) ──► TTS ──► Voice out
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
        Search/Scrape    Listings Store    User Profile
        tool adapters    (normalized DB)   & Session Memory
              │               ▲
              └── ingestion ──┘
```

- **Agent Orchestrator:** LLM with tool-use (search, fetch_listings, compare_prices, geo, calendar). Handles intent, multi-step plans, and follow-up context.
- **Ingestion workers:** scheduled crawlers + on-demand fetchers writing to the normalized listings store.
- **Listings store:** unified schema — `{event_id, title, category, venue, geo, datetime, price_min/max, source, deep_link, last_seen}`.
- **Client:** PWA — mic button, streaming voice replies, result cards.

## 9. Success Metrics

| Metric | Target (6 mo post-launch) |
|---|---|
| Query → useful result rate (thumbs-up / no reformulation) | > 80% |
| Voice queries as share of all queries | > 40% |
| Median time from query to booking deep-link click | < 60 s |
| Cheapest-ticket accuracy (vs. manual check) | > 95% |
| Weekly active users retained at week 4 | > 25% |

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Scraping blocked / ToS action from ticketing sites | Data goes dark | Multi-source redundancy; we drive them referral traffic for free |
| Free-tier limits hit (LLM rate limits, hosting quotas) | Service degrades | Aggressive caching of agent answers for common queries; queue + retry; local Ollama as overflow |
| Stale prices → wrong "cheapest" answer | Trust loss | Live fetch at answer time for price claims; show "as of X min ago" |
| STT errors on titles/venues (accents, code-mixing) | Bad results | Fuzzy title matching against known catalog; confirm ambiguous entities aloud |
| LLM hallucinating events | Trust loss | Agent may only answer from tool results, never from model memory; every card carries a source link |
| Latency of multi-source live search | Poor UX | Streamed partial results ("found 3 so far…"); background catalog cache |

## 11. Rollout Plan

- **Phase 0 (4 wks):** Text-only agent, 1 city, movies only, 2–3 sources. Validate the data pipeline + ranking.
- **Phase 1 (4 wks):** Voice in/out, events added, cheapest-ticket comparison, PWA launch.
- **Phase 2:** Alerts, personalization, multi-language, second city.
- **Phase 3:** Partnerships/official APIs, group planning, calendar, explore in-app checkout.

## 12. Open Questions

1. Which launch city? (Data-source coverage should decide.)
2. Which free LLM to standardize on — Gemini free tier (better quality, hard rate limits) vs. Groq free tier (fast) vs. local Ollama (unlimited but needs your machine running)?
3. How far do we go on auto-booking later — browser automation is powerful but legally fragile.
4. If usage ever outgrows free tiers, what gives first — and is "donations/keep it small" the answer, or does a revenue model get revisited?
