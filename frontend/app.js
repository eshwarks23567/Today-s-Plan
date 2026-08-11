// BookTic frontend — chat, voice in/out, persistence, hero animation, particles.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
const $ = (id) => document.getElementById(id);
const chat = $("chat"), q = $("q"), form = $("form"), micBtn = $("mic"),
      speakBtn = $("speak"), citySel = $("city"), sendBtn = $("send"),
      chatsSel = $("chats");
const motionOK = !matchMedia("(prefers-reduced-motion: reduce)").matches;
// spoken replies are opt-in (screen readers + public phones must not get surprise audio)
let history = [], speakOn = localStorage.getItem("booktic.speak") === "1", busy = false;
speakBtn.setAttribute("aria-pressed", String(speakOn));
const PLACEHOLDER = innerWidth < 420 ? "Ask anything…" : "Ask about movies, timings or prices…";
// only to word the "still working" note — the server decides what actually happens
const BOOKISH = /\b(book|ticket|tickets|seat|seats|reserve|buy)\b/i;
q.placeholder = PLACEHOLDER;
let inflight = null; // AbortController for the request currently in flight, if any
let currentTypewriter = null; // { finish() } for the reply currently typing out, if any

// pristine markup, captured once, so "new chat" can restore the homepage without a reload
const HERO_HTML = $("hero").outerHTML;
const GALLERY_HTML = $("gallery").outerHTML;
const elFromHTML = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};

// ---- persistence (localStorage; single-user local app needs no DB) ----
const store = JSON.parse(localStorage.getItem("booktic.chats") || "[]");
let chatId = null, msgs = [];

function save() {
  const rec = { id: chatId, title: (msgs.find(m => m.cls === "me")?.text || "New chat").slice(0, 48),
                city: citySel.value, history, msgs };
  const i = store.findIndex(c => c.id === chatId);
  if (i >= 0) store[i] = rec; else store.unshift(rec);
  store.length = Math.min(store.length, 20);
  localStorage.setItem("booktic.chats", JSON.stringify(store));
  localStorage.setItem("booktic.current", String(chatId));
}

function record(cls, text, asHtml) {
  if (!chatId) chatId = Date.now();
  msgs.push({ cls, text, asHtml: !!asHtml });
  save();
}

function refreshChatsMenu() {
  chatsSel.hidden = !store.length;
  chatsSel.innerHTML = '<option value="">Past chats…</option>' +
    store.map(c => `<option value="${c.id}">${c.title.replace(/</g, "&lt;")}</option>`).join("");
  chatsSel.value = chatId ? String(chatId) : "";
}

function bindChips() {
  document.querySelectorAll(".chip").forEach(c => c.onclick = () => ask(c.textContent));
}

// Restore the homepage in place — no navigation, no re-fetching four CDNs, no lost scroll.
function freshChat() {
  if (inflight) inflight.abort();
  speechSynthesis.cancel();
  localStorage.removeItem("booktic.current");
  chatId = null; history = []; msgs = [];
  chat.innerHTML = HERO_HTML;
  chat.classList.add("heroed");
  bindChips();
  revealHero();
  if (!$("gallery")) document.body.insertBefore(elFromHTML(GALLERY_HTML), chat);
  buildGallery();
  startOrbit();
  refreshChatsMenu();
  q.focus();
}

// Swap to a saved conversation in place, with its own history/context.
function loadChat(rec) {
  if (inflight) inflight.abort();
  speechSynthesis.cancel();
  chatId = rec.id; history = rec.history; msgs = rec.msgs;
  citySel.value = rec.city;
  chat.classList.remove("heroed");
  chat.innerHTML = "";
  $("gallery")?.remove();
  msgs.forEach(m => add(m.cls, m.text, m.asHtml));
  refreshChatsMenu();
  q.focus();
}

$("newchat").onclick = freshChat;
chatsSel.onchange = () => {
  const rec = store.find(c => String(c.id) === chatsSel.value);
  if (rec) loadChat(rec);
};

// past-chats dropdown + restore of the current conversation on first page load
(function restore() {
  refreshChatsMenu();
  const cur = store.find(c => String(c.id) === localStorage.getItem("booktic.current"));
  if (!cur) { chat.classList.add("heroed"); return; }
  loadChat(cur);
})();

// ---- chat ----
bindChips();
citySel.onchange = () => {
  localStorage.setItem("booktic.city", citySel.value);
  if (document.querySelector(".msg")) {
    // mid-conversation city switch would silently desync the visible chat from the
    // model's context — start a fresh chat instead so screen and context agree
    return freshChat();
  }
  history = [];
  buildGallery();
};
if (!localStorage.getItem("booktic.current")) {
  citySel.value = localStorage.getItem("booktic.city") || "hyderabad";
}

function add(cls, text, asHtml) {
  const d = document.createElement("div");
  d.className = cls === "hint" ? "hint" : "msg " + cls;
  if (asHtml) d.innerHTML = text; else d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

// minimal markdown, block-aware: real <ul><li> (not a "· " prefix that strands its
// bullet above a block-level booking link) and "---" becomes an <hr>, not literal dashes.
const inline = (s) => s
  // the URL group is escaped separately — the earlier &/< pass in md() doesn't
  // touch quotes, and an unescaped " here could break out of the href attribute
  .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,
    (_, label, url) => `<a href="${url.replace(/"/g, "&quot;")}" target="_blank" rel="noopener">${label}</a>`)
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");

function md(t) {
  const lines = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").split("\n");
  let html = "", inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (/^-{3,}$/.test(line)) { closeList(); html += "<hr>"; continue; }
    const h = line.match(/^#{2,4}\s+(.*)$/);
    if (h) { closeList(); html += `<h4>${inline(h[1])}</h4>`; continue; }
    const li = line.match(/^[*-]\s+(.*)$/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    closeList();
    if (line) html += `<p>${inline(line)}</p>`;
  }
  closeList();
  return html;
}

function setBusy(on) {
  busy = on;
  sendBtn.disabled = on;
  sendBtn.classList.toggle("loading", on);
  document.querySelectorAll(".chip").forEach(c => c.disabled = on);
}

async function ask(text) {
  if (busy) return;
  setBusy(true);
  speechSynthesis.cancel();
  $("hero")?.remove();
  $("gallery")?.remove();
  chat.classList.remove("heroed");
  add("me", text);
  record("me", text);
  const wait = add("bot", "<i></i><i></i><i></i>", true);
  wait.classList.add("typing");
  inflight = new AbortController();
  // long waits here are normal — the day's first crawl, or a browser being driven.
  // Three silent dots for a minute read as "stuck", so name what's taking the time.
  const slow = setTimeout(() => {
    const note = document.createElement("span");
    note.className = "waitnote";
    note.textContent = BOOKISH.test(text)
      ? "Opening your browser — this takes a moment"
      : "Reading today's listings — the first ask of the day is the slow one";
    wait.appendChild(note);
  }, 5000);
  // a hung server would otherwise leave the dots spinning forever
  let timedOut = false;
  const limit = setTimeout(() => { timedOut = true; inflight?.abort(); }, 120000);
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: citySel.value, question: text, history }),
      signal: inflight.signal,
    });
    const out = await r.json();
    if (out.error) { retryHint("Something went wrong: " + out.error, text); return; }
    history = out.history;
    showFresh(out.crawled);
    const cls = out.booked ? "bot booked" : "bot";
    const bubble = add(cls, md(out.answer), true);
    record(cls, md(out.answer), true);
    if (motionOK) {
      // screen readers get the full answer once via this mirror; the visual bubble
      // is hidden from them while the typewriter churns its text nodes
      const sr = document.createElement("div");
      sr.className = "sr-only";
      sr.textContent = plain(out.answer);
      chat.appendChild(sr);
      bubble.setAttribute("aria-hidden", "true");
      chat.style.scrollBehavior = "auto"; // smooth scrolling fights per-frame typing scroll
      currentTypewriter = typeIn(bubble, () => {
        bubble.removeAttribute("aria-hidden");
        sr.remove();
        chat.style.scrollBehavior = "";
        currentTypewriter = null;
      });
    }
    if (speakOn) say(out.answer);
  } catch (e) {
    if (e.name !== "AbortError") retryHint("Connection failed: " + e.message, text);
    else if (timedOut) retryHint("That took too long — the server may still be crawling listings.", text);
    // otherwise it was Esc: the user cancelled on purpose, nothing to say
  } finally {
    clearTimeout(slow);
    clearTimeout(limit);
    inflight = null;
    wait.remove();
    setBusy(false);
    q.focus();
  }
}

// how stale the listings behind the last answer were — the crawl is cached for
// ~20 minutes, so "live prices" deserves a number next to it
function showFresh(unixSeconds) {
  if (!unixSeconds) return;
  const mins = (Date.now() / 1000 - unixSeconds) / 60;
  $("fresh").textContent = (mins < 1 ? "Listings updated just now" :
    `Listings updated ${Math.round(mins)} min ago`) + " · ";
}

form.onsubmit = (e) => {
  e.preventDefault();
  const t = q.value.trim();
  if (t && !busy) { q.value = ""; ask(t); }
};

// failed requests get a one-tap retry instead of a dead-end error line
function retryHint(message, question) {
  const h = add("hint", message + " ");
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = "Retry";
  b.onclick = () => { h.remove(); ask(question); };
  h.appendChild(b);
}

// typewriter reveal: the bubble already holds the final HTML; empty its text
// nodes, then type them back a few chars per frame, pausing at sentence ends.
// Click anywhere on the bubble (except a link) to finish instantly.
//
// Reveals one block at a time (paragraph, list item, rule) rather than typing
// every text node across the whole tree at once — the flat approach left
// not-yet-typed list items and booking-link buttons sitting on screen as
// empty placeholder boxes (an <a> button has padding/background/border, so
// an empty one is a visible gray pill ahead of the cursor). Booking-link
// buttons pop in whole instead of typing letter by letter — "B", "Bo", "Book"
// on something you click reads oddly, since it isn't prose.
function typeIn(el, onDone) {
  const targets = [...el.querySelectorAll(":scope > *, :scope ul > li")];
  const popIn = new Set(targets.filter(t => t.tagName === "HR" ||
    (t.tagName === "LI" && t.childElementCount === 1 && t.firstElementChild.tagName === "A")));
  targets.forEach(t => t.classList.add("reveal-pending"));

  const allText = (root) => {
    const out = [];
    (function walk(n) { for (const c of [...n.childNodes])
      c.nodeType === 3 ? out.push([c, c.textContent]) : walk(c); })(root);
    return out;
  };
  // like allText, but stops at any descendant that is itself a target — so a
  // <ul>'s pass doesn't re-type text its own <li> targets will type themselves
  const ownText = (root) => {
    const out = [];
    (function walk(n) {
      for (const c of [...n.childNodes]) {
        if (c.nodeType === 3) out.push([c, c.textContent]);
        else if (!targets.includes(c)) walk(c);
      }
    })(root);
    return out;
  };

  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    targets.forEach(t => { t.classList.remove("reveal-pending"); t.style.opacity = ""; });
    allText(el).forEach(([n, full]) => n.textContent = full);
    chat.scrollTop = chat.scrollHeight;
    if (onDone) onDone();
  };
  el.addEventListener("click", (e) => { if (!e.target.closest("a")) finish(); });

  let ti = 0;
  const nextTarget = () => {
    if (finished) return;
    if (ti >= targets.length) return finish();
    const t = targets[ti++];
    // reveal-pending is display:none (zero height) so the bubble's real height
    // only grows as content actually appears — otherwise every still-hidden
    // block below would already count toward scrollHeight, and autoscroll
    // would land in blank space past whatever has actually been typed so far.
    // Un-hiding switches display back on; only THEN does the opacity fade run,
    // so the fade never fights the layout math.
    t.classList.remove("reveal-pending");
    t.style.opacity = "0";
    requestAnimationFrame(() => { t.style.opacity = "1"; });
    chat.scrollTop = chat.scrollHeight;
    if (popIn.has(t)) { setTimeout(nextTarget, 90); return; }
    typeNodes(ownText(t), nextTarget);
  };

  function typeNodes(nodes, done) {
    nodes.forEach(([n]) => n.textContent = "");
    let i = 0, j = 0, pause = 0;
    (function step() {
      if (finished) return;
      if (pause-- > 0) { requestAnimationFrame(step); return; }
      let budget = 3;
      while (budget-- > 0 && i < nodes.length) {
        const [node, full] = nodes[i];
        if (j >= full.length) { i++; j = 0; continue; }
        const ch = full[j];
        node.textContent = full.slice(0, ++j);
        if (".!?".includes(ch)) { pause = 8; break; }   // breathe between sentences
        if (ch === ",") { pause = 3; break; }
      }
      chat.scrollTop = chat.scrollHeight;
      if (i < nodes.length) requestAnimationFrame(step); else done();
    })();
  }

  nextTarget();
  return { finish };
}

// ---- voice out (browser-native) ----
const plain = (text) => text
  .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
  .replace(/https?:\/\/\S+/g, "the booking link")  // else it spells the whole URL out, character by character
  .replace(/[*#_`]/g, "")
  .replace(/Rs\.?\s?/g, "rupees ");

// Pick a real voice instead of leaving it to the lang tag: most Windows installs
// have no en-IN voice at all, and an unmatched lang silently falls back to the
// system default with the wrong prosody.
let voice = null;
const pickVoice = () => {
  const vs = speechSynthesis.getVoices();
  voice = vs.find(v => v.lang === "en-IN") || vs.find(v => v.lang === "en-GB")
       || vs.find(v => v.lang.startsWith("en")) || null;
};
pickVoice();
speechSynthesis.addEventListener("voiceschanged", pickVoice); // empty on the first call in Chrome

// Chrome cuts any single utterance off after ~15 seconds of speech, so the old
// 600-character utterance was being silenced roughly two thirds of the way in.
// Split into short pieces and queue them: each survives, and the seams land
// where a person would pause anyway.
function chunks(text, max = 180) {
  const out = [];
  let t = text.trim();
  while (t.length > max) {
    const head = t.slice(0, max), min = max / 4;
    // sentence end first, then a comma, then any space — never a chunk so short
    // that the gaps between utterances start to sound like a stutter
    let at = Math.max(head.lastIndexOf(". "), head.lastIndexOf("! "), head.lastIndexOf("? "));
    if (at < min) at = head.lastIndexOf(", ");
    if (at < min) at = head.lastIndexOf(" ");
    if (at < min) at = max - 1;  // one unbroken run of characters
    out.push(t.slice(0, at + 1).trim());
    t = t.slice(at + 1).trim();
  }
  if (t) out.push(t);
  return out;
}

function say(text) {
  speechSynthesis.cancel();
  for (const part of chunks(plain(text)).slice(0, 14)) {
    const u = new SpeechSynthesisUtterance(part);
    u.lang = "en-IN";
    if (voice) u.voice = voice;
    speechSynthesis.speak(u);
  }
}

if (location.hash === "#selftest") {  // open /#selftest in the browser console
  const c = chunks("a. b. c.", 5);
  console.assert(c.join(" ") === "a. b. c.", "chunks: lossy", c);
  console.assert(c.every(s => s.length <= 5), "chunks: over max", c);
  console.assert(chunks("x".repeat(400)).length === 3, "chunks: unbroken run");
  console.assert(plain("see [Book](https://x.com/a) or https://y.in/b") ===
    "see Book or the booking link", "plain: url leaked");
}
speakBtn.onclick = () => {
  speakOn = !speakOn;
  localStorage.setItem("booktic.speak", speakOn ? "1" : "0");
  speakBtn.setAttribute("aria-pressed", String(speakOn));
  if (!speakOn) speechSynthesis.cancel();
};

// keyboard accelerators: "/" focuses the input; Esc stops speech, skips an
// in-progress typewriter, or cancels a request that hasn't answered yet
addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); q.focus(); }
  if (e.key !== "Escape") return;
  speechSynthesis.cancel();
  if (currentTypewriter) currentTypewriter.finish();
  else if (inflight) inflight.abort();
});

// ---- voice in (browser-native, Chrome/Edge) ----
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const rec = new SR();
  rec.lang = "en-IN";
  rec.interimResults = true; // live transcript in the input while speaking
  rec.continuous = true;     // keep listening through pauses — see onend
  const stop = () => {
    micBtn.classList.remove("listening");
    micBtn.setAttribute("aria-pressed", "false");
    q.placeholder = PLACEHOLDER;
  };
  rec.onresult = (e) => {
    q.value = [...e.results].map(r => r[0].transcript).join("");
  };
  // Send when recognition actually ends — the mic tapped off, or Chrome's own
  // silence timeout. The old code sent on the first isFinal, which Chrome fires
  // at every pause, so drawing breath mid-question submitted half of it.
  rec.onend = () => {
    stop();
    const t = q.value.trim();
    if (t && !busy) { q.value = ""; ask(t); }
  };
  rec.onerror = (e) => {
    stop();
    if (e.error === "not-allowed")
      add("hint", "Microphone blocked. Allow it from the icon in your address bar, then tap the mic again.");
  };
  micBtn.onclick = () => {
    if (micBtn.classList.contains("listening")) return rec.stop();
    speechSynthesis.cancel();
    micBtn.classList.add("listening");
    micBtn.setAttribute("aria-pressed", "true");
    q.placeholder = "Listening — tap the mic when you're done";
    rec.start();
  };
} else {
  micBtn.disabled = true;
  micBtn.title = "Voice input requires Chrome or Edge";
}

// ---- hero reveal (anime.js; skipped entirely when motion is off or CDN failed) ----
// Callable more than once: "new chat" restores a pristine hero and replays this.
function revealHero() {
  if (!motionOK || !window.anime) return;
  const h1 = $("heroTitle");
  // split into words (nowrap) of chars so lines never break mid-word
  const split = (node) => [...node.childNodes].forEach(n => {
    if (n.nodeType === 1) return split(n);
    const frag = document.createDocumentFragment();
    for (const word of n.textContent.split(/(\s+)/)) {
      if (!word.trim()) { frag.appendChild(document.createTextNode(" ")); continue; }
      const w = document.createElement("span");
      w.style.cssText = "display:inline-block;white-space:nowrap";
      for (const ch of word) {
        const s = document.createElement("span");
        s.className = "ch"; s.textContent = ch;
        s.style.cssText = "display:inline-block;opacity:0";
        w.appendChild(s);
      }
      frag.appendChild(w);
    }
    node.replaceChild(frag, n);
  });
  split(h1);
  anime({ targets: "#hero .overline", opacity: [0, 1],
          easing: "easeOutExpo", duration: 500 });
  anime({ targets: ".ch", translateY: [22, 0], opacity: [0, 1],
          easing: "easeOutExpo", duration: 600, delay: anime.stagger(14, { start: 100 }) });
  anime({ targets: "#hero p", opacity: [0, 1], translateY: [10, 0],
          easing: "easeOutExpo", duration: 500, delay: 450 });
  anime({ targets: ".chip", opacity: [0, 1], translateY: [10, 0],
          easing: "easeOutExpo", duration: 400, delay: anime.stagger(50, { start: 600 }) });
}
if ($("hero")) revealHero();

// ---- circular poster gallery (homepage only; removed once a chat starts) ----
async function buildGallery() {
  const gallery = $("gallery");
  if (!gallery || !$("hero")) return;
  try {
    const imgs = await (await fetch("/api/posters?city=" + citySel.value)).json();
    if (!imgs.length) return;
    gallery.innerHTML = "";
    const N = Math.max(12, imgs.length);
    for (let i = 0; i < N; i++) {
      const im = document.createElement("img");
      im.src = imgs[i % imgs.length];
      im.alt = ""; im.draggable = false;
      gallery.appendChild(im);
    }
  } catch {} // decorative — fail silently
}

// Callable more than once: binds drag listeners to whatever #gallery exists
// right now and runs its own tick loop, which self-stops once that element is
// removed — so "new chat" can spin up a fresh orbit for a NEW gallery element.
// Guarded against the same element being started twice (e.g. clicking "new
// chat" while already on the homepage, before the old gallery was ever
// removed) — without this, each call stacks another independent tick loop
// and another set of pointer listeners on the one element.
function startOrbit() {
  const gallery = $("gallery");
  if (!gallery || gallery.dataset.orbiting) return;
  gallery.dataset.orbiting = "1";
  let angle = 0, vel = 0, dragging = false, lastX = 0;
  gallery.addEventListener("pointerdown", (e) => {
    dragging = true; lastX = e.clientX;
    gallery.setPointerCapture(e.pointerId);
  });
  gallery.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    vel = (e.clientX - lastX) * .004;
    angle += vel; lastX = e.clientX;
  });
  gallery.addEventListener("pointerup", () => dragging = false);
  (function tick() {
    if (!document.getElementById("gallery")) return; // gallery gone → stop the loop
    const cards = gallery.children, N = cards.length;
    if (N) {
      if (!dragging) { angle += (motionOK ? .0016 : 0) + vel; vel *= .95; }
      const rx = Math.min(innerWidth * .44, 640), ry = Math.min(innerHeight * .34, 300);
      const cx = innerWidth / 2, cy = innerHeight / 2;
      for (let i = 0; i < N; i++) {
        const a = angle + i * 2 * Math.PI / N;
        const depth = (Math.sin(a) + 1) / 2; // 0 = far/top, 1 = near/bottom
        cards[i].style.transform =
          `translate(${cx + rx * Math.cos(a)}px, ${cy + ry * Math.sin(a)}px) ` +
          `translate(-50%,-50%) scale(${.7 + .3 * depth}) rotate(${Math.cos(a) * 8}deg)`;
        cards[i].style.opacity = .4 + .6 * depth;
      }
    }
    requestAnimationFrame(tick);
  })();
}
buildGallery();
startOrbit();

// ---- particle backdrop (desktop only: a per-frame canvas repaint next to the
// orbiting gallery is a real battery cost on a phone, for decoration) ----
if (motionOK && innerWidth > 700) {
  const cv = $("particles"), ctx = cv.getContext("2d");
  let dots = [];
  const resize = () => {
    cv.width = innerWidth; cv.height = innerHeight;
    dots = Array.from({ length: Math.min(40, innerWidth / 32) }, () => ({
      x: Math.random() * cv.width, y: Math.random() * cv.height,
      r: Math.random() * 1.3 + .4,
      vx: (Math.random() - .5) * .15, vy: (Math.random() - .5) * .15,
      a: Math.random() * .18 + .04,
      red: Math.random() < .12,
    }));
  };
  resize();
  addEventListener("resize", resize);
  (function tick() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (const d of dots) {
      d.x = (d.x + d.vx + cv.width) % cv.width;
      d.y = (d.y + d.vy + cv.height) % cv.height;
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.r, 0, 7);
      ctx.fillStyle = d.red ? `rgba(226,55,68,${d.a})` : `rgba(255,255,255,${d.a * .7})`;
      ctx.fill();
    }
    requestAnimationFrame(tick);
  })();
}
