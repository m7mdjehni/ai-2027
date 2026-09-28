"""Loopback-only demo server, isolated from backend cookies and APIs."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Preview(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"service":"local-project-preview"}')
            return
        super().do_GET()

    def end_headers(self):
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, 'Open a project index.html directly')
        return None


if __name__ == '__main__':
    root = Path(__file__).resolve().parent / 'generated_projects'
    ThreadingHTTPServer(('127.0.0.1', 8050), partial(Preview, directory=str(root))).serve_forever()
