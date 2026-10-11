"""Authenticated loopback API serving the bundled desktop UI."""
from __future__ import annotations
import json
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from marvis.capabilities import Capabilities

STATIC = Path(__file__).resolve().parent.parent / 'ui'


def serve(runtime, port=0):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def response(self, code, value, kind='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if kind.startswith('application/json') else value
            self.send_response(code); self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(body)
        def authorized(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected: return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + expected: return False
            return secrets.compare_digest(self.headers.get('X-Marvis-Token', ''), token)
        def live(self):
            # fetch streaming preserves header authentication; tokens never enter URLs.
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Connection', 'close')
            self.end_headers()
            previous = None; deadline = time.monotonic() + 25
            try:
                while time.monotonic() < deadline:
                    snapshot = runtime.snapshot()
                    payload = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
                    if payload != previous:
                        self.wfile.write(('data: ' + payload + '\n\n').encode('utf-8'))
                        self.wfile.flush(); previous = payload
                    time.sleep(.1 if any(t['status'] in ('queued','running','waiting_approval') for t in snapshot['tasks']) else .5)
            except (OSError, ValueError, sqlite3.Error): pass  # Client closed or engine stopped.
            finally: self.close_connection = True
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith('/api/'):
                if not self.authorized(): return self.response(403, {'error': '访问令牌无效。'})
                if parsed.path == '/api/live': return self.live()
                try:
                    if parsed.path == '/api/state': value = runtime.snapshot()
                    elif parsed.path == '/api/messages': value = runtime.messages(parse_qs(parsed.query).get('session', [''])[0])
                    elif parsed.path == '/api/tree': value = runtime.session_tree(parse_qs(parsed.query).get('session', [''])[0])
                    elif parsed.path == '/api/image': value = runtime.image(parse_qs(parsed.query).get('path', [''])[0])
                    elif parsed.path == '/api/knowledge': value = runtime.knowledge(parse_qs(parsed.query).get('q', [''])[0])
                    else: return self.response(404, {'error': '接口不存在。'})
                    return self.response(200, value)
                except Exception as e: return self.response(400, {'error': str(e)})
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}': return self.response(403, {'error': '不允许此 Host。'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            for name in ('markdown.js', 'progress.js', 'office.js', 'vendor/marked.js', 'vendor/purify.js', 'vendor/highlight.js'):
                files['/' + name] = (name, 'text/javascript; charset=utf-8')
            files['/vendor/highlight.css'] = ('vendor/highlight.css', 'text/css; charset=utf-8')
            files['/office.css'] = ('office.css', 'text/css; charset=utf-8')
            if parsed.path not in files: return self.response(404, {'error': '文件不存在。'})
            name, kind = files[parsed.path]
            self.response(200, (STATIC / name).read_bytes(), kind)
        def do_POST(self):
            if not self.authorized(): return self.response(403, {'error': '访问令牌无效。'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 200000: return self.response(413, {'error': '请求大小无效。'})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict): raise ValueError('请求必须是 JSON 对象。')
                path = urlparse(self.path).path
                if path == '/api/sessions': value = {'id': runtime.new_session(data.get('session_id'))}
                elif path == '/api/tree-navigate': value = runtime.navigate_tree(data['session_id'], data.get('node_id'))
                elif path == '/api/tree-label': value = runtime.label_node(data['session_id'], data['node_id'], data.get('label', ''))
                elif path == '/api/tasks': value = runtime.submit(data.get('prompt', ''), data.get('session_id'), data.get('action'))
                elif path == '/api/cancel': value = runtime.cancel(data['id'])
                elif path == '/api/approve': value = runtime.approve(data['id'], data['approved'])
                elif path == '/api/settings': value = runtime.update_settings(data)
                elif path == '/api/workflows': value = runtime.add_workflow(data['name'], data['prompt'], data['interval'])
                elif path == '/api/workflow-action': value = runtime.workflow_action(data['id'], data['action'])
                else: return self.response(404, {'error': '接口不存在。'})
                return self.response(200, value)
            except Exception as e: return self.response(400, {'error': str(e)})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    if runtime.owned:
        import os
        endpoint = runtime.store.directory / 'endpoint.json'
        fd = os.open(str(endpoint), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump({'base': f'http://127.0.0.1:{server.server_port}', 'token': token, 'pid': os.getpid()}, file)
    url = f'http://127.0.0.1:{server.server_port}/#token={token}'
    return server, url
