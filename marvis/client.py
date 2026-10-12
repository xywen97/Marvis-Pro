"""Attach additional clients to an existing local engine instead of replaying tasks."""
from __future__ import annotations
import json
import threading
import uuid
import urllib.request
import urllib.parse
from pathlib import Path


class RemoteRuntime:
    owned = False
    def __init__(self, endpoint):
        self.base = endpoint['base']; self.token = endpoint['token']
        self.lease_id = None
        self.lease_stop = threading.Event()
        self.lease_thread = None
        parsed = urllib.parse.urlsplit(self.base)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('无效本机服务地址。')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(self, path, data=None):
        request = urllib.request.Request(self.base + '/api/' + path, headers={'X-Marvis-Token': self.token, 'Content-Type': 'application/json'}, data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None)
        try:
            with self.opener.open(request,timeout=5) as response:return json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise ValueError(json.loads(e.read()).get('error','服务请求失败'))
    def snapshot(self):return self.call('state')
    def settings(self):return self.snapshot()['settings']
    def sessions(self, archived=False):return self.snapshot()['archived_sessions' if archived else 'sessions']
    def session_action(self, session_id, action):return self.call('session-action',{'session_id':session_id,'action':action})
    def fork_session(self, session_id, node_id):return self.call('session-fork', {'session_id': session_id, 'node_id': node_id})
    def command(self, text, session_id):return self.call('command', {'text': text, 'session_id': session_id})
    def messages(self, session_id):return self.call('messages?session='+session_id)
    def image(self, path):
        from urllib.parse import quote
        return self.call('image?path='+quote(path))
    def session_tree(self, session_id):return self.call('tree?session='+session_id)
    def navigate_tree(self, session_id, node_id=None, summarize=False, custom_instructions=None):return self.call('tree-navigate',{'session_id':session_id,'node_id':node_id,'summarize':summarize,'custom_instructions':custom_instructions})
    def label_node(self, session_id, node_id, label):return self.call('tree-label',{'session_id':session_id,'node_id':node_id,'label':label})
    def new_session(self, session_id=None):return self.call('sessions',{'session_id':session_id})['id']
    def submit(self,prompt,session_id=None,action=None):return self.call('tasks',{'prompt':prompt,'session_id':session_id,'action':action})
    def cancel(self,ident):return self.call('cancel',{'id':ident})
    def approve(self,ident,approved):return self.call('approve',{'id':ident,'approved':approved})
    def update_settings(self,values):return self.call('settings',values)
    def knowledge(self,query=''):
        from urllib.parse import quote
        return self.call('knowledge?q='+quote(query))
    def add_workflow(self,name,prompt,interval):return self.call('workflows',{'name':name,'prompt':prompt,'interval':interval})
    def workflow_action(self,ident,action):return self.call('workflow-action',{'id':ident,'action':action})
    def client_lease(self, data):return self.call('engine-client', data)
    def attach(self):
        if self.lease_id: return
        ident = uuid.uuid4().hex
        if not self.client_lease({'id': ident}).get('managed'): return
        self.lease_id = ident
        def renew():
            while not self.lease_stop.wait(5):
                try: self.client_lease({'id': ident})
                except (OSError, ValueError): break
        self.lease_thread = threading.Thread(target=renew, daemon=True, name='marvis-client-lease')
        self.lease_thread.start()
    def close(self):
        self.lease_stop.set()
        if self.lease_thread: self.lease_thread.join(timeout=6)
        if self.lease_id:
            try: self.client_lease({'id': self.lease_id, 'release': True})
            except (OSError, ValueError): pass
            self.lease_id = None


def connect(directory):
    try:
        endpoint=json.loads((Path(directory).expanduser().resolve()/'endpoint.json').read_text())
        client=RemoteRuntime(endpoint);client.snapshot();return client
    except (OSError,ValueError,KeyError):return None
