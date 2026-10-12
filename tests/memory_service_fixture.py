"""Real disposable subprocess for native-service lifecycle tests."""
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

config = json.load(open(sys.argv[1]))
key = os.environ['MARVIS_MEMORY_SERVICE_KEY']


def guardian():
    sys.stdin.buffer.read()
    os.kill(os.getpid(), signal.SIGTERM)


threading.Thread(target=guardian, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self): self.respond()
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.respond()
    def respond(self):
        if self.path == '/ready': data = {'status': 'ready'}
        elif self.headers.get('X-API-Key') != key:
            self.send_response(403); self.end_headers(); return
        elif self.path.endswith('/find'): data = {'status': 'ok', 'result': {'memories': []}}
        else: data = {'status': 'ok', 'result': []}
        raw = json.dumps(data).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(raw)


HTTPServer(('127.0.0.1', config['server']['port']), Handler).serve_forever()
