"""BookTic web app — stdlib HTTP server wrapping booktic.py.

    python server.py [port]         # http://localhost:8765, this machine only
    python server.py --lan          # also reachable from your Wi-Fi
    PORT=8080 python server.py      # hosted mode (see HOSTED below)

Setting $PORT is how every PaaS starts a process, so it doubles as the signal
that this is a shared box on the public internet rather than someone's laptop.
That one flag changes three things: bind every interface, trust the proxy's
X-Forwarded-For for rate limiting, and stop learning preferences — prefs.json
is a single global file, so on a shared host it would blend every visitor's
venues together and feed them back to each other.
"""
import json
import os
import socket
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent
import booktic
import prefs
import ratelimit

FRONTEND = (Path(__file__).parent.parent / "frontend").resolve()
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
         ".json": "application/manifest+json", ".svg": "image/svg+xml",
         ".woff2": "font/woff2"}

HOSTED = bool(os.environ.get("PORT"))

CSP = ("default-src 'self'; "
       "script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline'; "   # the hero animation sets style attributes
       "img-src 'self' data: https:; "        # posters are served from several CDNs
       "connect-src 'self'; font-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# every /api/ask spends Gemini free-tier quota, so one runaway tab or a stuck
# retry loop can burn the day's budget in a minute — 20/min is far above what a
# person types and far below what a loop does
_limiter = ratelimit.RateLimiter(int(os.environ.get("RATE_PER_MIN", 20)))
# The burst limit does nothing against steady use: the free tier is ~1500
# requests a day TOTAL across everyone, so one enthusiastic visitor can spend it
# all inside the per-minute cap. The daily cap is what actually protects the key.
_daily = ratelimit.RateLimiter(int(os.environ.get("RATE_PER_DAY", 60)), 86_400)


def client_ip(handler) -> str:
    """Who to rate-limit. Behind a proxy every connection arrives from the load
    balancer, so limiting on the socket address would put every visitor in ONE
    bucket. Only trust the forwarded header when we know we are behind a proxy —
    otherwise anyone could set it and get a fresh bucket per request."""
    if HOSTED:
        for header in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
            value = handler.headers.get(header)
            if value:
                return value.split(",")[0].strip()  # leftmost = original client
    return handler.client_address[0]


def _log_error():  # full traceback server-side; the client sees only what _safe says
    traceback.print_exc(file=sys.stderr)


def _safe(e: Exception) -> str:
    """What a 500 is allowed to tell the caller. Exception text here can carry local
    filesystem paths, so on a shared host it is replaced by something generic — but
    on your own machine the detail is the whole point of reading the error."""
    return "Something went wrong on the server." if HOSTED else str(e)


def lan_ip() -> str:
    # ponytail: UDP "connect" to a public IP picks the right local NIC without
    # sending any packet (connect on UDP just fills in the routing table entry)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/posters"):
            from urllib.parse import parse_qs, urlparse
            city = parse_qs(urlparse(self.path).query).get("city", ["hyderabad"])[0]
            # The homepage asks for posters in whatever city the browser remembers,
            # which is the first the server hears of it — so use that to warm the
            # right city's crawl, rather than guessing from prefs at boot.
            try:
                if city in booktic.CITIES and not booktic.crawled_at(city):
                    threading.Thread(target=_warm, args=(city,), daemon=True).start()
                return self._json(200, booktic.posters(city))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                _log_error()
                return self._json(500, {"error": _safe(e)})
        name = "index.html" if self.path == "/" else self.path.lstrip("/")
        # Resolve to a canonical absolute path and verify it's still inside FRONTEND,
        # rather than blacklisting "/" and "..": a bare leading backslash (no slash,
        # no dotdot) makes pathlib treat the join as drive-rooted on Windows and walks
        # straight out of FRONTEND — a blacklist of specific substrings always has a
        # gap; containment-checking the resolved path doesn't.
        try:
            file = (FRONTEND / name).resolve()
        except (OSError, ValueError):
            return self.send_error(404)
        if not file.is_relative_to(FRONTEND) or file.suffix not in TYPES or not file.is_file():
            return self.send_error(404)
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{TYPES[file.suffix]}; charset=utf-8")
        if file.suffix == ".html":
            # Defence in depth behind the escaping in md(): even an injected tag
            # could not load a script from anywhere but here, and there are no
            # inline <script> blocks to allow. Posters come from arbitrary CDNs,
            # so img-src stays broad; nothing else needs to reach off-origin.
            self.send_header("Content-Security-Policy", CSP)
        # this app is under active iteration — a stale cached app.js/style.css
        # showing an already-fixed bug wastes more time than never caching does
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    MAX_BODY = 1_000_000  # 1MB is generous for a question + running chat history
    MAX_QUESTION = 4_000  # a typed question; the longest real one is a sentence or two
    MAX_TURNS = 200       # ~100 exchanges, far past where the model stops using them
    MAX_HISTORY_CHARS = 200_000

    def do_POST(self):
        if self.path != "/api/ask":
            return self.send_error(404)
        _limiter.prune()  # a LAN sees a handful of IPs; scanning them beats tracking a timer
        who = client_ip(self)
        burst = _limiter.check(who)
        # only count against the day's allowance if the burst limit let it through,
        # or one runaway tab would spend a visitor's whole day in a few seconds
        wait = burst or _daily.check(who)
        if wait:
            # Drain the body we are never going to parse. Answering while the client
            # is still sending gets the connection reset instead of delivered, so the
            # page reports "connection failed" rather than the retry-in-Ns we wrote.
            # Same reason MAX_BODY drains below; this path just returns sooner.
            try:
                pending = min(int(self.headers.get("Content-Length", 0) or 0), self.MAX_BODY)
                if pending > 0:
                    self.rfile.read(pending)
            except (ValueError, OSError):
                pass
            note = (f"Too many requests — try again in {wait}s." if burst else
                    "That's today's limit for this demo, so the shared API key survives "
                    "until tomorrow. Try again in the morning.")
            return self._json(429, {"error": note}, **{"Retry-After": str(wait)})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                raise ValueError("empty request body")
            if length > self.MAX_BODY:
                # drain a bounded amount so the client gets a clean 400 instead of a
                # connection reset (the socket still has the body in flight) — capped
                # so a client lying about a huge Content-Length can't force us to
                # buffer unbounded data just to reject it
                self.rfile.read(min(length, self.MAX_BODY * 2))
                raise ValueError("request body too large")
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise ValueError("body must be a JSON object")  # else .get() 500s on a list
            question = req.get("question")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("missing 'question'")
            # A 1MB body is within MAX_BODY but is never a real question, and every
            # byte of it gets billed to the Gemini free tier. Cap the parts that
            # reach the prompt, generously enough that real use never notices.
            if len(question) > self.MAX_QUESTION:
                raise ValueError("'question' is too long")
            history = req.get("history", [])
            if not isinstance(history, list):
                raise ValueError("'history' must be a list")
            if len(history) > self.MAX_TURNS:
                raise ValueError("'history' has too many turns")
            # Shape-check every turn before it reaches Gemini: a malformed history
            # otherwise comes back as an opaque 400 from the API, and history is the
            # one input that arrives wholesale from the client on every request.
            for turn in history:
                if (not isinstance(turn, dict) or turn.get("role") not in ("user", "model")
                        or not isinstance(turn.get("parts"), list) or not turn["parts"]
                        or not all(isinstance(p, dict) and isinstance(p.get("text"), str)
                                   for p in turn["parts"])):
                    raise ValueError("malformed 'history' turn")
            if sum(len(p["text"]) for t in history for p in t["parts"]) > self.MAX_HISTORY_CHARS:
                raise ValueError("'history' is too large")
            city = req.get("city", "hyderabad")
            # a non-string city is unhashable, and `city not in CITIES` would raise
            # TypeError rather than the ValueError the 400 path expects
            if not isinstance(city, str):
                raise ValueError("'city' must be a string")
            # Only pay for the later dates when the turn is actually about them
            ahead = booktic.needs_future(question, history)
            listings = booktic.crawl(city, ahead)  # raises ValueError for an unknown city
        except (json.JSONDecodeError, ValueError) as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            _log_error()
            return self._json(500, {"error": _safe(e)})

        # Everything above could still choose a status code. From here the answer
        # streams, so the 200 is already committed and a later failure has to arrive
        # as an error *event* instead.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def send(**event):
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.flush()

        # Through a tunnel the server is not the machine the person is holding, so
        # opening a browser here would pop a window on a desktop nobody is looking
        # at. Any proxy header means the request came from somewhere else; then the
        # link travels back and the page offers it as a button instead.
        remote = HOSTED or any(self.headers.get(h) for h in
                               ("CF-Ray", "CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"))
        try:
            answer, booked, url = agent.handle(
                question, history, listings, city,
                on_token=lambda t: send(type="token", text=t),
                on_status=lambda t: send(type="status", text=t),
                auto_open=not remote)
            send(type="done", answer=answer, history=history, booked=booked, url=url,
                 crawled=booktic.crawled_at(city))
        except (BrokenPipeError, ConnectionError):
            pass  # the tab was closed or Esc aborted mid-answer; nothing to report to
        except Exception as e:
            _log_error()
            try:
                send(type="error", error=_safe(e))
            except (BrokenPipeError, ConnectionError):
                pass

    def _json(self, code: int, payload, **headers):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter console
        pass


def _warm(city: str):
    """Crawl in the background at boot — otherwise the day's first question sits
    inside crawl() waiting on the network with nothing on screen but typing dots.

    Today only: that is 22 fetches rather than 74, and the later dates are built
    lazily the first time someone actually asks about another day. It matters most
    on a free host that sleeps, where every wake pays this again — and a 74-request
    burst is exactly the shape that makes BookMyShow start serving interstitials."""
    try:
        booktic.crawl(city, ahead=False)
        print(f"listings ready for {city}", file=sys.stderr)
    except Exception:
        _log_error()  # the first request will simply crawl again


if __name__ == "__main__":
    # movie titles and venue names are not ASCII, and Windows hands a redirected
    # stderr the cp1252 codec — without this, logging one Telugu title raises
    # UnicodeEncodeError inside the request thread and turns an answer into a 500
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if a != "--lan"]
    port = int(os.environ.get("PORT") or (args[0] if args else 8765))
    prefs.ENABLED = not HOSTED  # one global prefs file must not learn from strangers
    # Booking opens a browser window on THIS machine, so listening on every
    # interface hands that to anyone sharing the Wi-Fi. Loopback by default;
    # --lan is the deliberate opt-in for reaching it from your phone.
    lan = "--lan" in sys.argv

    def listener(host: str, family: int) -> ThreadingHTTPServer:
        cls = type("Server", (ThreadingHTTPServer,),
                   {"address_family": family, "daemon_threads": True})
        return cls((host, port), Handler)

    servers = []
    if HOSTED:
        # the platform terminates TLS and forwards; bind everything it can reach
        servers.append(listener("0.0.0.0", socket.AF_INET))
    elif lan:
        servers.append(listener("0.0.0.0", socket.AF_INET))
    else:
        # "localhost" resolves to ::1 before 127.0.0.1 on Windows, so an IPv4-only
        # loopback socket costs every connection ~2s waiting for the fallback — on
        # the exact URL printed below. Listen on both; keep them loopback so
        # booking still can't be triggered by anyone else on the network.
        servers.append(listener("127.0.0.1", socket.AF_INET))
        try:
            servers.append(listener("::1", socket.AF_INET6))
        except OSError:
            pass  # no IPv6 on this box — 127.0.0.1 alone still serves

    threading.Thread(target=_warm, args=(prefs.load().get("home_city") or "hyderabad",),
                     daemon=True).start()
    model = ("Gemini (gemini-flash-latest)" if booktic.PROVIDER == "gemini"
             else f"{booktic.LLM_MODEL} via {booktic.LLM_BASE}")
    print(f"Today's Plan running at http://localhost:{port}")
    print(f"  model: {model}")
    if lan:
        print(f"On your phone (same Wi-Fi): http://{lan_ip()}:{port}")
        print("  ! open to everyone on this network, with no login — booking opens browser")
        print("    windows on this machine, so only use --lan on a network you trust")
    else:
        print("  this machine only — pass --lan to reach it from your phone")
    print("(Ctrl+C to stop)")
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    servers[0].serve_forever()
