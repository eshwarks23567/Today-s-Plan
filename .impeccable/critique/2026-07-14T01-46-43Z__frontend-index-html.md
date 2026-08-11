---
target: frontend (index.html + style.css + app.js)
total_score: 22
p0_count: 0
p1_count: 4
timestamp: 2026-07-14T01-46-43Z
slug: frontend-index-html
---
Method: dual-agent (A: design review · B: detector + browser evidence)

# Design Critique — Today's Plan, frontend/index.html

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Great in-chat feedback; booking handoff ("I've opened X in your browser") changes nothing in-UI |
| 2 | Match System / Real World | 2 | CSS specificity bug renders chat as full-width centered blocks, not left/right bubbles |
| 3 | User Control and Freedom | 1 | Auto-TTS on by default; typewriter unskippable; no cancel-in-flight; no Esc-to-stop speech |
| 4 | Consistency and Standards | 3 | Token system disciplined; three-font stack and bubble bug undercut it |
| 5 | Error Prevention | 2 | City switch silently wipes LLM context while stale messages stay on screen |
| 6 | Recognition Rather Than Recall | 3 | Chips + persisted chats good; chips gone forever after first message |
| 7 | Flexibility and Efficiency | 2 | No keyboard accelerators; typewriter throttles every reply |
| 8 | Aesthetic and Minimalist Design | 2 | Composer is Raycast-grade; homepage is particles + orbit + choreography fighting one input |
| 9 | Error Recovery | 2 | Raw backend strings as cold gray hints; no retry affordance |
| 10 | Help and Documentation | 2 | Only guidance is the least-readable text on the page (disclaimer, ~4.0:1 at 10.5px) |
| **Total** | | **22/40** | **Acceptable — engine better than its chrome** |

## Anti-Patterns Verdict

**LLM assessment:** the chat surface itself passes the product bar; the homepage decoration is what says "AI made this." Earned: dark theme, domain-native red, chips, typing dots, spinner. Tells (product-register bans): particle canvas (decorative motion conveying no state), orchestrated load sequence (overline → char-stagger → paragraph → chips), display/label fonts in UI microcopy. Half-earned: the poster orbit uses real inventory (good instinct) but is a non-clickable landing-page carousel grafted onto a tool.

**Deterministic scan (CLI):** 2 findings — `overused-font` (Space Grotesk, true positive but it's only the tertiary label face) and `single-font` (**proven false positive**: B verified Clash Display 600/700, Satoshi 400/500, and Space Grotesk all load and render; the static scanner can't read Fontshare links).

**Browser detector (headless injection; no user-visible overlay in this environment):** 4 hits the static scan missed — `all-caps-body` (37 chars uppercase), `hero-eyebrow-chip` (the tracked-caps overline above the h1), `line-length` (~137ch), `tiny-text` (10.5px disclaimer).

**Where A and B agree:** the 10.5px/low-contrast disclaimer (A computed ≈4.0:1, B flagged tiny-text), the three-font stack, and touch targets (A flagged header buttons; B measured **7 of 8 buttons fail 44×44** — only the send button passes).

**Mechanical clean bill:** no horizontal overflow at either viewport, zero console errors, all 20 gallery images have alt, all three font families confirmed loading.

## Overall Impression

A trustworthy engine wearing a demo's clothes. The conversation surface — composer, feedback states, token discipline — is genuinely product-grade. The homepage spends that trust on spectacle, and four objective bugs (bubble layout, mobile bleed-through, live-region mutation, contrast) drag an otherwise-good UI to mid-pack.

## What's Working

1. **The token system** — 8pt spacing, two radii, one accent + one semantic green, one easing. The foundation is a designer's.
2. **In-chat state honesty** — typing dots, arrow→spinner swap, disabled chips, reduced-motion respected everywhere, graceful mic degradation.
3. **The composer** — restrained, legible, correctly sized send, `:focus-within` accent. This is the bar the rest should meet.

## Priority Issues

- **[P1] Chat bubbles render full-width** — `#chat > *` (specificity 1,0,0) beats `.msg` (0,1,0), so user/bot messages are centered banners, not bubbles; the chat metaphor collapses. **Fix:** `#chat > .msg { max-width: min(85%, 560px); }` + width:auto; let `.me`/`.bot` margins align. *Command: /impeccable polish*
- **[P1] Mobile hero illegible** — posters orbit through the headline and chips at 375px; the radial scrim is too small. **Fix:** full-viewport scrim under the hero or hide the orbit ≤600px. *Command: /impeccable adapt (fold into polish)*
- **[P1] Auto-TTS defaults ON** — narrates over screen readers (double audio) and surprises public mobile users. **Fix:** default off, opt-in stays one tap. *Command: /impeccable polish*
- **[P1] Typewriter mutates an aria-live region and can't be skipped** — announcement spam risk for SR users; throttles power users. **Fix:** announce once (aria-live on a visually-hidden mirror or insert final text for SR), click-to-complete the visual effect. *Command: /impeccable polish*
- **[P2] Disclaimer fails AA** — ≈4.0:1 at 10.5px mono. **Fix:** full-opacity color, ≥12px, body font. *Command: /impeccable polish*
- **[P2] Touch targets** — 7 of 8 buttons under 44px (newchat 34, mic/speak 40, chips 39 tall). **Fix:** min 44px hit areas on mobile. *Command: /impeccable polish*
- **[P2] Booking handoff has no in-UI confirmation** — the highest-stakes agentic moment produces a plain text bubble. **Fix:** a distinct confirmation card (venue · time · seats · "continue in the opened tab"). *Command: /impeccable delight or clarify*

## Persona Red Flags

**Alex (power user):** no `/` or ⌘K focus shortcut; New chat is a full page reload; typewriter unskippable; chips need 7 tab stops.
**Sam (screen reader):** double audio from default TTS; typeIn churns text nodes inside `role="log" aria-live="polite"`; grab-cursor gallery looks interactive but is aria-hidden; `.me` bubble white-on-red ≈4.3:1 (marginal).
**Casey (mobile one-handed):** posters over text at 375px; mic/speak 40px with 4px gap (fat-finger pair); phone speaks aloud in public without consent; placeholder truncates.

## Minor Observations

- Four CDNs (Google Fonts, Fontshare, animate.css cdnjs, anime.js cdnjs) — FOUT/failure surface; `--font-label` lists a monospace fallback for a proportional face
- City switch wipes `history` but keeps stale messages on screen (invisible context desync)
- `scroll-behavior: smooth` fights per-frame scrollTop writes during typewriter (jitter on long replies)
- Raw backend error strings surface verbatim in hints; no retry button
- Persisted bot HTML re-injected from localStorage (md() escapes first — safe today, watch it)
- Gallery hides at no breakpoint but its cards shrink to 64px — consider hiding entirely on small screens

## Questions to Consider

1. Who is the 2-second choreographed reveal for — the user or the demo? What would loading straight into the task look like?
2. The orbit shows real, current inventory and none of it is clickable. Should posters *be* the interface (tap → showtimes) instead of decoration?
3. Why does the agent's boldest act — opening a browser and clicking through a booking — produce the quietest pixel response in the app?
