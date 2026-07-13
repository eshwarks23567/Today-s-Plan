# BookTic — Features Document

Companion to [PRD.md](PRD.md). Priorities: **P0** = launch blocker, **P1** = fast-follow, **P2** = later.

**Standing constraint:** BookTic is free for users and free to build — every feature must be implementable with browser-native APIs, open-source tools, or free-tier services (see the Zero-Cost Constraint table in the PRD).

---

## 1. Voice & Conversation

| Feature | Description | Priority |
|---|---|---|
| Voice query input | Tap-to-talk mic; browser-native Web Speech API (free) | P0 |
| Spoken responses | Browser-native TTS summary of top results ("I found 6 events tonight; the top pick is…") | P0 |
| Text chat fallback | Same agent over a chat box for quiet/noisy environments | P0 |
| Session memory | Follow-ups work: "what about tomorrow?", "only IMAX", "the second one" | P0 |
| Entity confirmation | Agent reads back ambiguous parses: "Did you mean *Dune Part Three*?" | P0 |
| Wake-word / hands-free mode | "Hey BookTic…" continuous listening | P2 |
| Multi-language voice | English, Hindi, Telugu; code-mixed queries ("kal evening ka show") | P1 |

## 2. Agentic Search & Data

| Feature | Description | Priority |
|---|---|---|
| Live web aggregation | Agent tools that search/scrape listing sources at query time | P0 |
| Background catalog crawl | Scheduled ingestion of what's on (movies, concerts, standup, sports, theatre, workshops) | P0 |
| Unified listing schema | One normalized record per event across all sources, deduped | P0 |
| Source attribution | Every result shows where it came from + freshness timestamp | P0 |
| Structured-data parsing | Prefer JSON-LD/schema.org Event markup over brittle HTML scraping | P0 |
| Long-tail discovery | Free search discovery (DuckDuckGo / Google PSE free quota) finds events not on major platforms (college fests, local gigs) | P1 |

## 3. Recommendations & Ranking

| Feature | Description | Priority |
|---|---|---|
| Blended default ranking | Relevance × rating × distance × price fit | P0 |
| Sort controls | By price (asc), start time, distance, popularity — via voice or chips | P0 |
| Constraint filters | Category, budget cap, time window, area — parsed from one utterance | P0 |
| "Tonight" smart defaults | Time-of-day aware: after 6 PM, "today" means this evening | P0 |
| Personalized picks | Learns from clicks/bookings: genres, price band, preferred areas | P1 |
| Occasion mode | "Date night", "family with kids", "team outing" presets | P2 |

## 4. Price Intelligence

| Feature | Description | Priority |
|---|---|---|
| Cheapest-ticket finder | Per-show price comparison across all sources for a named title | P0 |
| Tradeoff narration | "Cheapest is ₹149 but it's 9 AM; cheapest evening show is ₹220" | P0 |
| Price freshness guard | Live re-fetch before asserting any price; show "as of X min ago" | P0 |
| Fee-inclusive totals | Compare on final price incl. convenience fees where visible | P1 |
| Price-drop alerts | "Tell me if this goes under ₹300" → push notification | P1 |
| On-sale alerts | "Ping me when Coldplay tickets open" | P1 |
| Historical price hints | "Weekday matinees for this cinema are usually 40% cheaper" | P2 |

## 5. Booking Handoff

| Feature | Description | Priority |
|---|---|---|
| Deep-link handoff | One tap/voice-confirm opens the exact show/seat page on the source site | P0 |
| Pre-filled context | Carry showtime/venue selection into the link where the source supports it | P1 |
| Booking journal | Track what the user clicked through to; ask "did you book it?" to close the loop | P1 |
| Calendar add | Booked event → Google Calendar with venue + travel-time reminder | P2 |
| In-app checkout | Via official partner APIs only (not automation) | P2 |

## 6. User Profile & Social

| Feature | Description | Priority |
|---|---|---|
| Home city + location | GPS or manual; per-query override ("…in Bangalore") | P0 |
| Preference profile | Languages, genres, usual budget, favorite venues | P1 |
| Group planning | Share a shortlist; agent finds the slot that fits everyone | P2 |
| Watchlist | "Remind me when this releases here" | P1 |

## 7. Platform & Quality

| Feature | Description | Priority |
|---|---|---|
| PWA client | Installable web app; mic permission; works on mobile + desktop | P0 |
| Streaming results | Partial results appear while the agent is still searching | P0 |
| No-hallucination guarantee | Agent answers only from tool results; every claim traceable to a source | P0 |
| Graceful degradation | Source down → say so and answer from remaining sources | P0 |
| Rate-limit & robots.txt compliance | Per-source politeness in all crawlers | P0 |
| Accessibility | Full voice-only operation path; screen-reader-friendly cards | P1 |
| Query analytics | Track failed/reformulated queries to find data gaps | P1 |

---

## Launch cut (Phase 0–1 = all P0s)

Text+voice agent · one city · movies + major events · 2–3 sources · unified catalog · blended ranking + sort · cheapest-ticket comparison · deep-link handoff · session memory · PWA.

Everything else waits until the P0 loop retains users.
