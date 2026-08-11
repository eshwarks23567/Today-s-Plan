"""BookTic web app — stdlib HTTP server wrapping booktic.py.

    python server.py [port]     # then open http://localhost:8765, or from a
                                 # phone on the same Wi-Fi: http://<lan-ip>:8765
"""
import json
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

# every /api/ask spends Gemini free-tier quota, so one runaway tab or a stuck
# retry loop can burn the day's budget in a minute — 20/min is far above what a
# person types and far below what a loop does
_limiter = ratelimit.RateLimiter(20)


def _log_error():  # full traceback server-side; the client only sees str(e)
    traceback.print_exc(file=sys.stderr)


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
            try:
                code, out = 200, booktic.posters(city)
            except ValueError as e:
                code, out = 400, {"error": str(e)}
            except Exception as e:
                _log_error()
                code, out = 500, {"error": str(e)}
            body = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return self.wfile.write(body)
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
        # this app is under active iteration — a stale cached app.js/style.css
        # showing an already-fixed bug wastes more time than never caching does
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    MAX_BODY = 1_000_000  # 1MB is generous for a question + running chat history

    def do_POST(self):
        if self.path != "/api/ask":
            return self.send_error(404)
        _limiter.prune()  # a LAN sees a handful of IPs; scanning them beats tracking a timer
        wait = _limiter.check(self.client_address[0])
        if wait:
            body = json.dumps({"error": f"Too many requests — try again in {wait}s."}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(wait))
            self.end_headers()
            return self.wfile.write(body)
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
            question = req.get("question")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("missing 'question'")
            history = req.get("history", [])
            if not isinstance(history, list):
                raise ValueError("'history' must be a list")
            city = req.get("city", "hyderabad")
            listings = booktic.crawl(city)  # raises ValueError for an unknown city
            answer, booked = agent.handle(question, history, listings, city)
            code, out = 200, {"answer": answer, "history": history, "booked": booked,
                              "crawled": booktic.crawled_at(city)}
        except (json.JSONDecodeError, ValueError) as e:
            code, out = 400, {"error": str(e)}
        except Exception as e:
            _log_error()
            code, out = 500, {"error": str(e)}
        body = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter console
        pass


def _warm(city: str):
    """Crawl in the background at boot — otherwise the day's first question sits
    inside crawl() for ~a minute with nothing on screen but typing dots."""
    try:
        booktic.crawl(city)
        print(f"listings ready for {city}", file=sys.stderr)
    except Exception:
        _log_error()  # the first request will simply crawl again


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    threading.Thread(target=_warm, args=(prefs.load().get("home_city") or "hyderabad",),
                     daemon=True).start()
    print(f"Today's Plan running at http://localhost:{port}")
    print(f"On your phone (same Wi-Fi): http://{lan_ip()}:{port}  — local network only, no login")
    print("(Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
