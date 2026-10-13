"""Local browsing plus durable, workspace-scoped OpenViking resource indexing."""
from __future__ import annotations
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from marvis.capabilities import Capabilities
from marvis.memory import MemoryError


class KnowledgeEngine:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.scope = hashlib.sha256(str(self.store.directory).encode()).hexdigest()[:16]
        self.prefix = 'viking://resources/marvis-knowledge-' + self.scope
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.RLock()
        self.last_error = ''
        self.connection_failed = False
        self._visible_count = 0
        self._count_checked = 0
        self._count_revision = -1
        self.store.execute('''CREATE TABLE IF NOT EXISTS knowledge_resources (
            path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, uri TEXT NOT NULL,
            task TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
            updated REAL NOT NULL)''')
        self.store.execute('CREATE TABLE IF NOT EXISTS knowledge_garbage (uri TEXT PRIMARY KEY)')
        self.worker = threading.Thread(target=self._work, daemon=True, name='marvis-knowledge')
        self.worker.start()

    def caps(self):
        # Browsing and retrieval always honor roots, even in full access mode.
        return Capabilities(self.store, approval_policy='auto')

    def visible_count(self):
        # SSE snapshots are frequent; count metadata at most once per two seconds.
        revision = self.store.settings_revision
        if revision == self._count_revision and time.monotonic()-self._count_checked < 2:
            return self._visible_count
        caps = self.caps(); count = 0
        for row in self.store.rows('SELECT path FROM documents'):
            try: count += caps.path(row['path']).is_file()
            except (OSError, PermissionError, ValueError): continue
        self._visible_count = count
        self._count_revision = revision
        self._count_checked = time.monotonic()
        return count

    def available(self):
        memory = self.runtime.memory
        return bool(memory.client and memory.allowed(self.runtime.settings()) and
                    (not memory.service or memory.service.status()['state'] == 'ready'))

    def documents(self):
        caps = self.caps()
        rows = []
        for row in self.store.rows('SELECT path,name,category,size,modified,text FROM documents ORDER BY modified DESC,path'):
            try:
                p = caps.path(row['path'])
                if not p.is_file(): continue
                st = p.stat()
                row['stale'] = st.st_size != row['size'] or st.st_mtime != row['modified']
                rows.append(row)
            except (OSError, PermissionError, ValueError): continue
        self._visible_count = len(rows)
        self._count_revision = self.store.settings_revision
        self._count_checked = time.monotonic()
        return rows

    @staticmethod
    def fingerprint(row):
        return hashlib.sha256((row['path'] + '\0' + str(row['modified']) + '\0' + str(row['size']) + '\0' + row['text']).encode()).hexdigest()

    def status(self, docs=None):
        docs = self.documents() if docs is None else docs
        resources = {r['path']: r for r in self.store.rows('SELECT * FROM knowledge_resources')}
        eligible = [d for d in docs if d['text'].strip() and not d['stale']]
        ready = processing = failed = 0
        for d in eligible:
            r = resources.get(d['path'])
            if not r or r['fingerprint'] != self.fingerprint(d): continue
            ready += r['status'] == 'ready'
            processing += r['status'] in ('processing', 'submitting')
            failed += r['status'] == 'failed'
        available = self.available() and not self.connection_failed
        return {'available': available, 'total': len(docs), 'eligible': len(eligible), 'ready': ready,
                'processing': processing, 'failed': failed, 'pending': len(eligible)-ready-processing-failed,
                'stale': sum(d['stale'] for d in docs), 'error': self.last_error,
                'message': 'OpenViking 已连接' if available else ('OpenViking 连接暂时不可用，将自动重试。' if self.connection_failed else 'OpenViking 尚未就绪；请在设置中启用长期记忆服务并配置模型。')}

    def search(self, query='', mode='keyword', category='', offset=0, limit=24):
        if not isinstance(query, str) or len(query) > 4000: raise ValueError('搜索内容最多 4000 字符。')
        if mode not in ('keyword', 'semantic'): raise ValueError('无效搜索方式。')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100: raise ValueError('无效分页参数。')
        docs = self.documents()
        sync = self.status(docs)
        resources = {r['path']: r for r in self.store.rows('SELECT * FROM knowledge_resources')}
        for d in docs:
            r = resources.get(d['path'])
            d['semantic_status'] = 'stale' if d['stale'] else 'metadata' if not d['text'].strip() else (
                r['status'] if r and r['fingerprint'] == self.fingerprint(d) else 'pending')
            if d['semantic_status'] == 'submitting': d['semantic_status'] = 'processing'
        query = query.strip()
        hits = docs
        notice = ''
        if query and mode == 'semantic':
            if not sync['available']:
                return {'files': [], 'total': 0, 'has_more': False, 'sync': sync, 'status': 'unavailable', 'notice': sync['message']}
            try:
                result = self.runtime.memory.client.request('POST', '/api/v1/search/find', {
                    'query': query, 'target_uri': self.prefix, 'context_type': 'resource', 'limit': 100})
            except MemoryError:
                self.connection_failed = True
                sync = self.status(docs)
                return {'files': [], 'total': 0, 'has_more': False, 'sync': sync, 'status': 'unavailable', 'notice': sync['message']}
            if not isinstance(result, dict) or not isinstance(result.get('resources'), list): raise MemoryError('OpenViking 检索响应无效。')
            hits = []; seen = set()
            for match in result['resources']:
                uri = match.get('uri', '')
                if not isinstance(uri, str): continue
                for d in docs:
                    r = resources.get(d['path'])
                    if not r or d['semantic_status'] != 'ready' or d['path'] in seen: continue
                    if uri == r['uri'] or uri.startswith(r['uri'] + '/'):
                        hits.append({**d, 'score': match.get('score'), 'preview': str(match.get('abstract') or '')[:600]})
                        seen.add(d['path']); break
            notice = '仅检索已完成同步且原文件未变化的文档；最多召回 100 条候选。'
        elif query:
            needle = query.casefold()
            hits = [d for d in docs if needle in d['name'].casefold() or needle in d['text'].casefold()]
            hits.sort(key=lambda d: needle not in d['name'].casefold())
        if category: hits = [d for d in hits if d['category'] == category]
        total = len(hits)
        files = []
        for d in hits[offset:offset+limit]:
            pos = max(0, d['text'].casefold().find(query.casefold())-80) if query else 0
            files.append({k: v for k, v in {**d, 'preview': d.get('preview', d['text'][pos:pos+600])}.items() if k != 'text'})
        return {'files': files, 'total': total, 'has_more': offset+limit < total, 'offset': offset,
                'sync': sync, 'status': 'ok', 'notice': notice, 'source': 'OpenViking 语义检索' if query and mode == 'semantic' else '本地知识索引'}

    def retry(self):
        self.store.execute("UPDATE knowledge_resources SET updated=0 WHERE status='failed'")
        self.wake.set()
        return self.status()

    def upload(self, text):
        client = self.runtime.memory.client
        boundary = 'marvis-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="document.md"\r\nContent-Type: text/markdown; charset=utf-8\r\n\r\n'.encode()
                + text.encode() + f'\r\n--{boundary}--\r\n'.encode())
        headers = {**client.headers, 'Content-Type': 'multipart/form-data; boundary=' + boundary}
        request = urllib.request.Request(client.url + '/api/v1/resources/temp_upload', data=body, headers=headers, method='POST')
        try:
            with client.opener.open(request, timeout=client.timeout) as response: data = json.loads(response.read(2000001))
            if data.get('status') != 'ok' or not data.get('result', {}).get('temp_file_id'): raise MemoryError('OpenViking 上传响应无效。')
            return data['result']['temp_file_id']
        except (OSError, ValueError): raise MemoryError('OpenViking 文档上传失败。') from None

    def retire(self, uri):
        self.store.execute('INSERT OR IGNORE INTO knowledge_garbage VALUES (?)', (uri,))

    def recover_task(self, row):
        """Resolve a submission whose HTTP response was lost after acceptance."""
        if row['task']:
            try:
                return self.runtime.memory.client.request('GET', '/api/v1/tasks/' + urllib.parse.quote(row['task'], safe=''))
            except MemoryError as e:
                if e.http_status != 404: raise
                return None
        tasks = self.runtime.memory.client.request('GET', '/api/v1/tasks', query={'resource_id': row['uri'], 'limit': 1})
        if not isinstance(tasks, list): raise MemoryError('OpenViking 文档任务列表无效。')
        return tasks[0] if tasks else None

    def sync_once(self):
        if not self.available(): return
        with self.lock:
            client = self.runtime.memory.client
            docs = {d['path']: d for d in self.documents() if d['text'].strip() and not d['stale']}
            for r in self.store.rows('SELECT * FROM knowledge_resources'):
                if self.stop.is_set() or not self.available(): return
                d = docs.get(r['path'])
                if d is None or self.fingerprint(d) != r['fingerprint']:
                    # Finish in-flight ingestion before deletion so it cannot resurrect a removed resource.
                    if r['status'] == 'processing' or not r['task']:
                        task = self.recover_task(r)
                        if task and task.get('status') not in ('completed', 'failed', 'cancelled', 'expired'): continue
                    self.retire(r['uri'])
                    self.store.execute('DELETE FROM knowledge_resources WHERE path=?', (r['path'],))
                elif r['status'] == 'processing':
                    task = self.recover_task(r)
                    state = task.get('status') if task else 'expired'
                    if state in ('completed', 'failed', 'cancelled', 'expired'):
                        self.store.execute('UPDATE knowledge_resources SET status=?,error=?,updated=? WHERE path=?',
                                           ('ready' if state == 'completed' else 'failed', '' if state == 'completed' else '文档处理失败，请重试。', time.time(), r['path']))
                elif r['status'] == 'submitting':
                    # A previous process stopped between persisting the intent and receiving the task ID.
                    self.store.execute("UPDATE knowledge_resources SET status='failed',updated=0 WHERE path=?", (r['path'],))
            for garbage in self.store.rows('SELECT uri FROM knowledge_garbage LIMIT 20'):
                if self.stop.is_set() or not self.available(): return
                try: client.request('DELETE', '/api/v1/fs', query={'uri': garbage['uri'], 'recursive': 'true'})
                except MemoryError as e:
                    if e.http_status != 404: raise
                self.store.execute('DELETE FROM knowledge_garbage WHERE uri=?', (garbage['uri'],))
            count = 0
            for d in docs.values():
                if self.stop.is_set() or not self.available() or count >= 8: break
                r = self.store.one('SELECT * FROM knowledge_resources WHERE path=?', (d['path'],))
                if r and (r['status'] != 'failed' or time.time()-r['updated'] < 60): continue
                count += 1
                fingerprint = self.fingerprint(d)
                uri = self.prefix + '/' + hashlib.sha256(d['path'].encode()).hexdigest()[:24] + '-' + fingerprint[:16] + '.md'
                self.store.execute('INSERT OR REPLACE INTO knowledge_resources VALUES (?,?,?,?,?,?,?)', (d['path'], fingerprint, uri, '', 'submitting', '', time.time()))
                try:
                    if r:
                        task = self.recover_task(r)
                        if task and task.get('status') not in ('failed', 'cancelled', 'expired'):
                            task_id = task.get('task_id') or task.get('id') or r['task']
                            if not isinstance(task_id, str) or not task_id: raise MemoryError('OpenViking 文档任务 ID 无效。')
                            self.store.execute('UPDATE knowledge_resources SET task=?,status=?,error=? WHERE path=?',
                                               (task_id, 'ready' if task.get('status') == 'completed' else 'processing', '', d['path']))
                            continue
                        try: client.request('DELETE', '/api/v1/fs', query={'uri': uri, 'recursive': 'true'})
                        except MemoryError as e:
                            if e.http_status != 404: raise
                    # Re-check authorization and file freshness immediately before exporting the indexed snapshot.
                    p = self.caps().path(d['path']); st = p.stat()
                    if st.st_size != d['size'] or st.st_mtime != d['modified']: continue
                    upload = self.upload('# ' + d['name'] + '\n\n' + d['text'])
                    if not self.available(): continue
                    result = client.request('POST', '/api/v1/resources', {
                        'temp_file_id': upload, 'to': uri, 'create_parent': True, 'wait': False,
                        'reason': 'MarvisXY 文档知识库', 'source_name': 'document.md'})
                    task = result.get('task_id')
                    if not isinstance(task, str) or not task: raise MemoryError('OpenViking 未返回文档处理任务。')
                    actual_uri = result.get('root_uri', uri)
                    if actual_uri != uri: raise MemoryError('OpenViking 文档目标 URI 不一致。')
                    self.store.execute("UPDATE knowledge_resources SET task=?,status='processing',error='',updated=? WHERE path=?", (task, time.time(), d['path']))
                except (MemoryError, OSError, ValueError) as e:
                    self.store.execute("UPDATE knowledge_resources SET status='failed',error=?,updated=? WHERE path=?", ('文档同步失败，请检查服务后重试。', time.time(), d['path']))
                    self.last_error = '部分文档同步失败，请重试。'
            if not self.store.one("SELECT path FROM knowledge_resources WHERE status='failed'"): self.last_error = ''

    def _work(self):
        while not self.stop.is_set():
            try:
                self.sync_once()
                self.connection_failed = False
            except Exception:
                self.connection_failed = True
                self.last_error = 'OpenViking 同步暂时不可用，将自动重试。'
            self.wake.wait(3); self.wake.clear()

    def close(self):
        self.stop.set(); self.wake.set()
        # Requests are bounded; do not close SQLite while the worker still owns it.
        self.worker.join()
