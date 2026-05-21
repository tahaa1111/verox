"""
Minimal HTTP health server for Cloud Run startup/liveness probes.
Runs in background alongside the Celery worker process.
Responds 200 OK to any GET request on $PORT (default 8080).
"""
import http.server
import os


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, fmt, *args):  # suppress access logs
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    server = http.server.HTTPServer(("", port), _HealthHandler)
    print(f"[health_server] listening on port {port}", flush=True)
    server.serve_forever()
