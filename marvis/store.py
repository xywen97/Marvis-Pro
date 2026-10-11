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
        ''')
        # Upgrade linear histories once; preserve all original message IDs and content.
        old_messages = 'parent_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(messages)')}
        for table, name, declaration in [('sessions', 'active_leaf', 'TEXT'), ('messages', 'parent_id', 'TEXT'), ('messages', 'label', "TEXT DEFAULT ''")]:
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
        self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

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
            return ident

    def branch(self, session_id, leaf):
        rows = self.rows('SELECT * FROM messages WHERE session_id=?', (session_id,))
        by_id = {r['id']: r for r in rows}; path = []; seen = set()
        while leaf:
            if leaf in seen or leaf not in by_id: raise ValueError('会话树路径无效。')
            seen.add(leaf); item = by_id[leaf]; path.append(item); leaf = item['parent_id']
        return list(reversed(path))

    def event(self, task_id, kind, content):
        now = time.time()
        ident = self.execute('INSERT INTO events(task_id,kind,content,created) VALUES (?,?,?,?)', (task_id, kind, json.dumps(content, ensure_ascii=False), now))
        self.execute('UPDATE tasks SET updated=? WHERE id=?', (now, task_id))
        return ident

    def events(self, task_id):
        rows = self.rows('SELECT * FROM events WHERE task_id=? ORDER BY id', (task_id,))
        for row in rows:
            row['content'] = json.loads(row['content'])
        return rows

    def close(self):
        with self.lock:
            self.db.close()
