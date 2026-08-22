"""Zero-dependency HTTP server for the SHOWDOWN Phase 3 bot."""

from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bot import decide, decision_diagnostics


class Handler(BaseHTTPRequestHandler):
    server_version = "ShowdownBot/3.0"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("", "/health"):
            self._json(200, {"status": "ok", "phase": 3})
        else:
            self._json(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        status = 200 if self.path.rstrip("/") in ("", "/health") else 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/move":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            action = decide(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "internal decision error"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # The coordinator's five-second deadline applies to the response, so
        # diagnostics must never sit on its critical path.
        try:
            self._json(200, action)
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            print(json.dumps(decision_diagnostics(payload, action),
                             separators=(",", ":"), sort_keys=True), flush=True)
        except Exception:
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "5000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"SHOWDOWN Phase 3 bot listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
