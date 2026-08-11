---
target: frontend (index.html + style.css + app.js)
total_score: 27
p0_count: 0
p1_count: 3
timestamp: 2026-07-15T03-30-08Z
slug: frontend-index-html
---
Method: dual-agent (A: design review rerun · B: detector + browser evidence rerun)

# Design Critique Rerun — Today's Plan, frontend/index.html

## Design Health Score: 27/40 (was 22)

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Visual states strong; busy/"Listening…" never announced to screen readers |
| 2 | Match System / Real World | 3 | Raw `---` separators and orphaned `·` bullets leak into replies |
| 3 | User Control and Freedom | 2 | No cancel for in-flight requests; typewriter skips on click only, not keyboard |
| 4 | Consistency and Standards | 3 | aria-pressed drove mic dimming (regression, fixed post-assessment); stale aria-pressed="true" in HTML source |
| 5 | Error Prevention | 3 | Busy-guard + city-switch fork are good; voice auto-submits with no confirm |
| 6 | Recognition Rather Than Recall | 3 | Chips gone after first message, no persistent capability hints |
| 7 | Flexibility and Efficiency | 3 | `/` shortcut + Retry good; reload-based navigation, no palette |
| 8 | Aesthetic and Minimalist Design | 3 | Long answers are an unbroken wall; desktop right half unused |
| 9 | Error Recovery | 3 | One-tap Retry chip genuinely good; raw exception text leaks |
| 10 | Help and Documentation | 1 | No onboarding; agentic-booking safety explained only by an 11px eyebrow |

## Verdict

**Not AI-slop.** A's words: "coherent, disciplined product surface." Decoration is properly gated (removed at first message, aria-hidden, dimmed on mobile, reduced-motion respected, CDN-failure safe). The typewriter's accessibility plumbing was singled out as a genuine strength — hidden churning bubble + one-shot sr-mirror + focus return is "careful, correct work most teams skip."

**Detector deltas vs baseline:** browser detector hits fell 4 → 2 (`line-length` and `tiny-text` eliminated by the disclaimer fix; remaining: `all-caps-body` + `hero-eyebrow-chip`, both the intentional overline). Touch targets now pass **0 failures under coarse pointer** (was 7 of 8). No overflow, zero console errors, all three font families proven loading. CLI `single-font` false positive root-caused: the detector's font parser only recognizes Google Fonts URLs, so Fontshare's Clash Display + Satoshi are invisible to it.

## Fixed since baseline (verified by this rerun)

Bubbles left/right correctly; mobile hero legible (gallery dimmed .28); TTS opt-in (aria-pressed=false on load); typewriter SR-safe + click-skip; disclaimer 12px Satoshi full-contrast; coarse-pointer targets 44px; booked receipt card ("Agent action · payment stays with you") praised as trust framing; Retry chip praised.

## New / Remaining Priority Issues

- **[P1] Mic renders at 50% opacity at rest** — `.iconbtn[aria-pressed="false"]` caught the mic once it gained aria-pressed. The flagship control of a voice-first app looked disabled. **Fixed immediately post-assessment** (rule scoped to #speak).
- **[P1] `.me` bubble 4.32:1 contrast** — white on #E23744 fails AA at 15px. **Fixed immediately post-assessment** (deeper #C42F3A bg, ~5.5:1).
- **[P1] Full-page reloads** for new chat / past chat / mid-chat city switch — white flash, CDN re-downloads, hero re-runs. Fix: re-render in place from stored msgs.
- **[P2] Reply payload rendering** — orphaned `·` above link-buttons, raw `---`, wall-of-text. Fix: real list rendering, hr conversion, suppress bullets on link-only lines.
- **[P2] No cancel/keyboard-stop** — Esc should finish typewriter and abort the fetch (AbortController).
- **[P2] Selects were 33px on touch** — **fixed post-assessment** (min-height 44px coarse).
- **[P3] Stale `aria-pressed="true"` on #speak in HTML source; dead `.circular-gallery:focus-visible` rule (no tabindex).**

## Persona Notes

Alex: reloads kill flow; no Cmd-K; no multiline input. Sam: SR journey now largely correct; busy state unannounced; voice auto-submit has no confirm. Casey: input field 161px wide on 375 (mic+speak crowd it); city control top-right outside thumb zone.

## Questions

1. Voice-first, but the mic was the dimmest control and text has autofocus — which modality is actually first?
2. The .booked receipt is post-hoc — where does the user approve the agent's action *before* it runs?
3. The best visual craft lives in the 3 seconds before engagement and is deleted at first message — is the investment aimed at the least important part?
