"""Zero-dependency HTTP server for the SHOWDOWN bot."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bot import decide, decision_diagnostics


class Handler(BaseHTTPRequestHandler):
    server_version = "ShowdownBot/2.0"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in ("", "/health"):
            self._json(200, {"status": "ok"})
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
        self.send_header("Allow", "GET, POST, OPTIONS")
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
            try:
                print(
                    json.dumps(
                        decision_diagnostics(payload, action),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception:
                # Observability must never turn a legal move into a failed move.
                pass
            self._json(200, action)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            # Keep diagnostics out of the wire response and avoid crashing a worker.
            self._json(500, {"error": "internal decision error"})

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "5000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"SHOWDOWN bot listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
