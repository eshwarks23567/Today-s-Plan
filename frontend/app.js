// BookTic frontend — chat, voice in/out, persistence, hero animation, particles.
const $ = (id) => document.getElementById(id);
const chat = $("chat"), q = $("q"), form = $("form"), micBtn = $("mic"),
      speakBtn = $("speak"), citySel = $("city"), sendBtn = $("send"),
      chatsSel = $("chats");
const motionOK = !matchMedia("(prefers-reduced-motion: reduce)").matches;
let history = [], speakOn = true, busy = false;

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

$("newchat").onclick = () => { localStorage.removeItem("booktic.current"); location.reload(); };
chatsSel.onchange = () => { localStorage.setItem("booktic.current", chatsSel.value); location.reload(); };

// past-chats dropdown + restore of the current conversation
(function restore() {
  if (store.length) {
    chatsSel.hidden = false;
    chatsSel.innerHTML = '<option value="">Past chats…</option>' +
      store.map(c => `<option value="${c.id}">${c.title.replace(/</g, "&lt;")}</option>`).join("");
  }
  const cur = store.find(c => String(c.id) === localStorage.getItem("booktic.current"));
  if (!cur) { chat.classList.add("heroed"); return; }
  chatId = cur.id; history = cur.history; msgs = cur.msgs;
  citySel.value = cur.city;
  chatsSel.value = String(cur.id);
  $("hero")?.remove();
  $("gallery")?.remove();
  msgs.forEach(m => add(m.cls, m.text, m.asHtml));
})();

// ---- chat ----
document.querySelectorAll(".chip").forEach(c => c.onclick = () => ask(c.textContent));
citySel.onchange = () => {
  history = [];
  buildGallery();
};

function add(cls, text, asHtml) {
  const d = document.createElement("div");
  d.className = cls === "hint" ? "hint" : "msg " + cls;
  if (motionOK) d.classList.add("animate__animated", "animate__fadeInUp", "animate__faster");
  if (asHtml) d.innerHTML = text; else d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

// minimal markdown: links, bold, headings, bullets
const md = (t) => t
  .replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/^#{2,4} (.*)$/gm, "<h4>$1</h4>")
  .replace(/^\s*[*-] (.*)$/gm, "· $1");

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
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ city: citySel.value, question: text, history }),
    });
    const out = await r.json();
    if (out.error) { add("hint", "Something went wrong: " + out.error); return; }
    history = out.history;
    const bubble = add("bot", md(out.answer), true);
    if (motionOK) typeIn(bubble);
    record("bot", md(out.answer), true);
    if (speakOn) say(out.answer);
  } catch (e) {
    add("hint", "Connection failed: " + e.message);
  } finally {
    wait.remove();
    setBusy(false);
    q.focus();
  }
}

form.onsubmit = (e) => {
  e.preventDefault();
  const t = q.value.trim();
  if (t && !busy) { q.value = ""; ask(t); }
};

// typewriter reveal: the bubble already holds the final HTML; empty its text
// nodes, then type them back a few chars per frame, pausing at sentence ends.
function typeIn(el) {
  const nodes = [];
  (function walk(n) {
    for (const c of [...n.childNodes]) {
      if (c.nodeType === 3) { nodes.push([c, c.textContent]); c.textContent = ""; }
      else walk(c);
    }
  })(el);
  let i = 0, j = 0, pause = 0;
  (function step() {
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
    if (i < nodes.length) requestAnimationFrame(step);
  })();
}

// ---- voice out (browser-native) ----
function say(text) {
  speechSynthesis.cancel();
  const plain = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*#_`]/g, "").replace(/Rs\.?\s?/g, "rupees ");
  const u = new SpeechSynthesisUtterance(plain.slice(0, 600));
  u.lang = "en-IN";
  speechSynthesis.speak(u);
}
speakBtn.onclick = () => {
  speakOn = !speakOn;
  speakBtn.setAttribute("aria-pressed", String(speakOn));
  if (!speakOn) speechSynthesis.cancel();
};

// ---- voice in (browser-native, Chrome/Edge) ----
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const rec = new SR();
  rec.lang = "en-IN";
  rec.interimResults = true; // live transcript in the input while speaking
  const stop = () => { micBtn.classList.remove("listening"); q.placeholder = "Ask about movies, timings or prices…"; };
  rec.onresult = (e) => {
    const t = [...e.results].map(r => r[0].transcript).join("");
    q.value = t;
    if (e.results[e.results.length - 1].isFinal) { q.value = ""; ask(t); }
  };
  rec.onend = stop;
  rec.onerror = stop;
  micBtn.onclick = () => {
    if (micBtn.classList.contains("listening")) return rec.stop();
    speechSynthesis.cancel();
    micBtn.classList.add("listening");
    q.placeholder = "Listening…";
    rec.start();
  };
} else {
  micBtn.disabled = true;
  micBtn.title = "Voice input requires Chrome or Edge";
}

// ---- hero reveal (anime.js; skipped entirely when motion is off or CDN failed) ----
if (motionOK && window.anime) {
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

(function orbit() {
  const gallery = $("gallery");
  if (!gallery) return;
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
})();
buildGallery();

// ---- particle backdrop ----
if (motionOK) {
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
