"""BookTic web app — stdlib HTTP server wrapping booktic.py.

    python server.py [port]     # then open http://localhost:8765
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agent
import booktic

FRONTEND = Path(__file__).parent.parent / "frontend"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/posters"):
            from urllib.parse import parse_qs, urlparse
            city = parse_qs(urlparse(self.path).query).get("city", ["hyderabad"])[0]
            body = json.dumps(booktic.posters(city)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return self.wfile.write(body)
        name = "index.html" if self.path == "/" else self.path.lstrip("/")
        file = FRONTEND / name
        # flat frontend dir only — no subpaths, so no traversal to worry about
        if "/" in name or ".." in name or file.suffix not in TYPES or not file.is_file():
            return self.send_error(404)
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{TYPES[file.suffix]}; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/ask":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        try:
            city = req.get("city", "hyderabad")
            listings = booktic.crawl(city)
            history = req.get("history", [])
            answer = agent.handle(req["question"], history, listings, city)
            code, out = 200, {"answer": answer, "history": history}
        except Exception as e:
            code, out = 500, {"error": str(e)}
        body = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter console
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"BookTic running at http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
