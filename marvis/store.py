"""SQLite persistence shared by desktop, web and terminal clients."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings_revision = 0
        self.db = sqlite3.connect(str(self.directory / 'marvis.sqlite3'), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, task_id TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, session_id TEXT, prompt TEXT, status TEXT, result TEXT, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, content TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS documents (path TEXT PRIMARY KEY, name TEXT, category TEXT, text TEXT, size INTEGER, modified REAL);
            CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT, prompt TEXT, interval INTEGER, next_run REAL, enabled INTEGER, last_task TEXT);
            CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, session_id TEXT, parent_id TEXT, kind TEXT, role TEXT, content TEXT, data TEXT, task_id TEXT, event_id INTEGER, created REAL, label TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS context_samples (session_id TEXT, entry_id TEXT, agent TEXT, data TEXT, updated REAL, PRIMARY KEY(session_id,entry_id,agent));
            CREATE INDEX IF NOT EXISTS entries_session ON entries(session_id);
            CREATE INDEX IF NOT EXISTS entries_task ON entries(task_id,created);
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
            CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS tasks_session ON tasks(session_id);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id);
            CREATE INDEX IF NOT EXISTS entries_event ON entries(event_id);
        ''')
        # Upgrade linear histories once; preserve all original message IDs and content.
        old_messages = 'parent_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(messages)')}
        new_entries = 'active_entry' not in {r[1] for r in self.db.execute('PRAGMA table_info(sessions)')}
        for table, name, declaration in [('sessions', 'active_entry', 'TEXT'), ('sessions', 'archived', 'INTEGER NOT NULL DEFAULT 0'), ('sessions', 'active_leaf', 'TEXT'), ('messages', 'parent_id', 'TEXT'), ('messages', 'label', "TEXT DEFAULT ''")]:
            if name not in {r[1] for r in self.db.execute('PRAGMA table_info(' + table + ')')}:
                self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        if old_messages:
            for session in self.db.execute('SELECT id FROM sessions').fetchall():
                previous = None
                messages = self.db.execute('SELECT m.id FROM messages m LEFT JOIN tasks t ON m.task_id=t.id WHERE m.session_id=? ORDER BY COALESCE(t.created,m.created),m.created', (session[0],)).fetchall()
                for message in messages:
                    self.db.execute('UPDATE messages SET parent_id=? WHERE id=?', (previous, message[0]))
                    previous = message[0]
                self.db.execute('UPDATE sessions SET active_leaf=? WHERE id=?', (previous, session[0]))
        # In-flight tasks cannot survive a process restart. Never replay mutations.
        self.db.execute("UPDATE tasks SET status='interrupted', result='服务退出，任务已中断；请重新提交。' WHERE status IN ('queued','running','waiting_approval')")
        self.db.commit()
        if new_entries: self._migrate_entries()

    def execute(self, sql, args=()):
        with self.lock:
            cursor = self.db.execute(sql, args)
            self.db.commit()
            return cursor.lastrowid

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def setting(self, key, default=None):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
            self.settings_revision += 1

    def upsert_documents(self, rows):
        """Commit one bounded indexing batch rather than fsync every file."""
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)', rows)

    def sample_context(self, task_id, agent, data):
        """Keep numeric readings at execution anchors without adding tree nodes."""
        with self.lock:
            anchor = self.one('SELECT id,session_id FROM entries WHERE task_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (task_id,))
            if not anchor: return
            self.execute('INSERT OR REPLACE INTO context_samples VALUES (?,?,?,?,?)',
                (anchor['session_id'], anchor['id'], agent, json.dumps(data, ensure_ascii=False), time.time()))

    def context_samples(self):
        # UNION terminates even if corrupt ancestry contains a cycle. Only the
        # selected ancestry contributes; sibling branch readings never leak.
        rows = self.rows('''WITH RECURSIVE path(session_id,id,parent_id) AS (
            SELECT s.id,e.id,e.parent_id FROM sessions s JOIN entries e ON e.id=s.active_entry
            UNION SELECT p.session_id,e.id,e.parent_id FROM path p JOIN entries e ON e.id=p.parent_id AND e.session_id=p.session_id
        ) SELECT c.* FROM context_samples c JOIN path p ON c.session_id=p.session_id AND c.entry_id=p.id
          ORDER BY c.updated,c.rowid''')
        result = {}
        for row in rows:
            result.setdefault(row['session_id'], {})[row['agent']] = json.loads(row['data'])
        return result

    def session(self, title='新对话'):
        ident = uid()
        self.execute('INSERT INTO sessions(id,title,created) VALUES (?,?,?)', (ident, title, time.time()))
        return ident

    def add_message(self, session_id, role, content, task_id=None):
        with self.lock:
            leaf = self.one('SELECT active_leaf FROM sessions WHERE id=?', (session_id,))['active_leaf']
            parent = leaf
            if role == 'assistant' and task_id:
                user = self.one("SELECT id FROM messages WHERE task_id=? AND role='user'", (task_id,))
                if user: parent = user['id']
            ident = uid()
            self.execute('INSERT INTO messages(id,session_id,role,content,task_id,created,parent_id) VALUES (?,?,?,?,?,?,?)', (ident, session_id, role, content, task_id, time.time(), parent))
            if role == 'user' or leaf == parent:
                self.execute('UPDATE sessions SET active_leaf=? WHERE id=?', (ident, session_id))
            if role == 'assistant':
                # Queued turns were reserved before this answer existed; insert it into their path.
                self.execute("UPDATE messages SET parent_id=? WHERE parent_id=? AND role='user' AND id<>?", (ident, parent, ident))
            entry_parent = self.one('SELECT active_entry FROM sessions WHERE id=?', (session_id,))['active_entry']
            if role == 'assistant' and task_id:
                last = self.one('SELECT id FROM entries WHERE task_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (task_id,))
                if last: entry_parent = last['id']
            self.append_entry(session_id, ident, role, role, content, {}, task_id, entry_parent, created=time.time())
            return ident

    def append_entry(self, session_id, ident, kind, role, content, data, task_id, parent, event_id=None, created=None, label=''):
        with self.lock:
            self.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)', (ident,session_id,parent,kind,role,content,json.dumps(data,ensure_ascii=False),task_id,event_id,created or time.time(),label))
            # Insert executing steps before already reserved queued user turns.
            if kind != 'user' and task_id:
                self.execute("UPDATE entries SET parent_id=? WHERE parent_id IS ? AND kind='user' AND id<>? AND session_id=? AND task_id IN (SELECT id FROM tasks WHERE status='queued')", (ident,parent,ident,session_id))
            current = self.one('SELECT active_entry FROM sessions WHERE id=?', (session_id,))['active_entry']
            if kind == 'user' or current == parent:
                self.execute('UPDATE sessions SET active_entry=? WHERE id=?', (ident,session_id))
            return ident

    def record_event_entry(self, task_id, event_id, kind, data, created=None):
        types = {'context_compaction':('context_compaction','meta'), 'agent_context':('agent_context','meta'), 'assistant_note':('thinking','assistant'), 'tool_start':('tool_call','assistant'),
                 'tool_result':('tool_result','tool'), 'tool_error':('tool_result','tool'), 'rejected':('tool_result','tool')}
        if kind not in types: return
        with self.lock:
            if self.one('SELECT id FROM entries WHERE event_id=?', (event_id,)): return
            last = self.one('SELECT * FROM entries WHERE task_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (task_id,))
            if not last: return
            entry_kind, role = types[kind]
            content = data.get('text','') if entry_kind == 'thinking' else json.dumps(data,ensure_ascii=False)
            if entry_kind == 'agent_context': content = '独立助手上下文检查点：' + data['agent']
            if entry_kind == 'context_compaction': content = '上下文压缩检查点：' + data['agent'] + '（' + data['stage'] + '）'
            self.append_entry(last['session_id'],'event-'+str(event_id),entry_kind,role,content,data,task_id,last['id'],event_id,created)

    def entry_branch(self, session_id, leaf=None):
        with self.lock:
            if leaf is None:
                row = self.one('SELECT active_entry FROM sessions WHERE id=?', (session_id,)); leaf = row['active_entry'] if row else None
            nodes = self.rows('SELECT * FROM entries WHERE session_id=?', (session_id,)); by_id = {n['id']:n for n in nodes}
            path = []; seen = set()
            while leaf:
                if leaf in seen or leaf not in by_id: raise ValueError('执行树路径无效。')
                seen.add(leaf); path.append(by_id[leaf]); leaf = by_id[leaf]['parent_id']
            for node in path: node['data'] = json.loads(node['data'])
            return list(reversed(path))

    def _migrate_entries(self):
        # Replay each old task's semantic events between its user and final answer.
        # Original message IDs, labels and all branches remain intact.
        for row in self.rows('SELECT * FROM messages ORDER BY created,rowid'):
            parent = row['parent_id']
            self.execute('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)', (row['id'],row['session_id'],parent,row['role'],row['role'],row['content'],'{}',row['task_id'],None,row['created'],row.get('label') or ''))
            if row['role'] == 'user' and row['task_id']:
                for event in self.events(row['task_id']):
                    self.record_event_entry(row['task_id'],event['id'],event['kind'],event['content'],event['created'])
            elif row['role'] == 'assistant' and row['task_id']:
                last = self.one('SELECT id FROM entries WHERE task_id=? AND id<>? ORDER BY created DESC,rowid DESC LIMIT 1', (row['task_id'],row['id']))
                if last: self.execute('UPDATE entries SET parent_id=? WHERE id=?', (last['id'],row['id']))
        self.execute('UPDATE sessions SET active_entry=active_leaf')

    def branch(self, session_id, leaf):
        rows = self.rows('SELECT * FROM messages WHERE session_id=?', (session_id,))
        by_id = {r['id']: r for r in rows}; path = []; seen = set()
        while leaf:
            if leaf in seen or leaf not in by_id: raise ValueError('会话树路径无效。')
            seen.add(leaf); item = by_id[leaf]; path.append(item); leaf = item['parent_id']
        return list(reversed(path))

    def event(self, task_id, kind, content):
        with self.lock:
            now = time.time()
            ident = self.execute('INSERT INTO events(task_id,kind,content,created) VALUES (?,?,?,?)', (task_id, kind, json.dumps(content, ensure_ascii=False), now))
            self.execute('UPDATE tasks SET updated=? WHERE id=?', (now, task_id))
            self.record_event_entry(task_id,ident,kind,content,now)
            return ident

    def events(self, task_id):
        rows = self.rows('SELECT * FROM events WHERE task_id=? ORDER BY id', (task_id,))
        for row in rows:
            row['content'] = json.loads(row['content'])
        return rows

    def close(self):
        with self.lock:
            self.db.close()
