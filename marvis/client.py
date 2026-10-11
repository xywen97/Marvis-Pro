"""Attach additional clients to an existing local engine instead of replaying tasks."""
from __future__ import annotations
import json
import urllib.request
from pathlib import Path


class RemoteRuntime:
    owned = False
    def __init__(self, endpoint):
        self.base = endpoint['base']; self.token = endpoint['token']
        if not self.base.startswith('http://127.0.0.1:'): raise ValueError('无效本机服务地址。')
    def call(self, path, data=None):
        request = urllib.request.Request(self.base + '/api/' + path, headers={'X-Marvis-Token': self.token, 'Content-Type': 'application/json'}, data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise ValueError(json.loads(e.read()).get('error','服务请求失败'))
    def snapshot(self):return self.call('state')
    def settings(self):return self.snapshot()['settings']
    def sessions(self):return self.snapshot()['sessions']
    def messages(self, session_id):return self.call('messages?session='+session_id)
    def image(self, path):
        from urllib.parse import quote
        return self.call('image?path='+quote(path))
    def session_tree(self, session_id):return self.call('tree?session='+session_id)
    def navigate_tree(self, session_id, node_id=None):return self.call('tree-navigate',{'session_id':session_id,'node_id':node_id})
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
    def close(self):pass


def connect(directory):
    try:
        endpoint=json.loads((Path(directory).expanduser().resolve()/'endpoint.json').read_text())
        client=RemoteRuntime(endpoint);client.snapshot();return client
    except (OSError,ValueError,KeyError):return None
