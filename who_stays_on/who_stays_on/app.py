"""Web server: serves the screen (static/index.html) and POST /api/roster.

Start:  python app.py          (default port 8000, override with PORT=9000)
Only needs: python 3 + scipy (see requirements.txt). No internet at runtime.
"""
import json
import mimetypes
import os
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from solver import run

ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 2 * 1024 * 1024


def _no_const(name):
    raise ValueError(f"{name} is not allowed in JSON")


def roster_from_bytes(raw):
    """bytes -> (status, dict). Malformed JSON is invalid_input (400)."""
    try:
        data = json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_constant=_no_const)
    except (ValueError, UnicodeDecodeError) as e:
        return 400, {"status": "invalid_input", "field": "(body)",
                     "message": f"(body): not valid JSON ({e})"}
    return run(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "WhoStaysOn/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/roster":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return self._send(400, {"status": "invalid_input", "field": "(body)",
                                    "message": "(body): missing or too large"})
        code, out = roster_from_bytes(self.rfile.read(n))
        self._send(code, out)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        for base in ("static", "examples"):
            full = os.path.normpath(os.path.join(ROOT, base, path.lstrip("/")))
            if full.startswith(os.path.join(ROOT, base)) and os.path.isfile(full):
                with open(full, "rb") as f:
                    return self._send(200, f.read(), (mimetypes.guess_type(full)[0] or "text/plain") + "; charset=utf-8")
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Who Stays On? running at http://localhost:{port}  (POST /api/roster)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
