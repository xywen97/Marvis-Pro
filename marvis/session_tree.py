"""Navigation over persistent execution entries, with compact branch gutters."""
from __future__ import annotations


class Navigator:
    filters = ('默认', '用户', '书签', '无工具', '全部')

    def __init__(self, data):
        self.data = data; self.query = ''; self.filter = 0; self.collapsed = set()
        self.selected = data['session']['active_leaf']; self.label_edit = None; self.error = ''

    def rows(self):
        nodes = self.data['nodes']; by_id = {n['id']:n for n in nodes}
        visible = set()
        tokens = self.query.casefold().split()
        for n in nodes:
            kind = n.get('kind',n['role'])
            match = all(token in (n['content']+' '+(n.get('label') or '')+' '+kind).casefold() for token in tokens)
            match = match and (self.filter in (0,4) or self.filter==1 and kind=='user' or self.filter==2 and bool(n.get('label')) or self.filter==3 and kind not in ('tool_call','tool_result'))
            if match: visible.add(n['id'])
        # Hidden intermediate entries attach to the nearest visible ancestor.
        children = {None:[]}; parents = {}
        for n in nodes:
            if n['id'] not in visible: continue
            parent = n['parent_id']
            while parent and parent not in visible: parent = by_id[parent]['parent_id']
            parents[n['id']] = parent; children.setdefault(parent,[]).append(n)
        active = set(self.data['path'])
        for siblings in children.values(): siblings.sort(key=lambda n:(n['id'] not in active,n['created'],n['id']))
        rows = [{'id':None,'parent_id':None,'prefix':'','role':'root','kind':'root','content':'会话起点','label':'','has_children':bool(nodes)}]
        stack = [(n,'',i==len(children[None])-1,len(children[None])>1) for i,n in reversed(list(enumerate(children[None])))]
        if None in self.collapsed and not self.query and not self.filter: stack=[]
        while stack:
            item,stem,last,branching = stack.pop()
            branch = children.get(item['id'],[])
            prefix = stem+('└─ ' if last else '├─ ') if branching else stem
            rows.append({**item,'visible_parent':parents[item['id']],'prefix':prefix,'has_children':bool(branch)})
            if item['id'] in self.collapsed and not self.query and not self.filter: continue
            next_stem = stem+('   ' if last else '│  ') if branching else stem
            stack.extend((child,next_stem,i==len(branch)-1,len(branch)>1) for i,child in reversed(list(enumerate(branch))))
        allowed = {r['id'] for r in rows}
        while self.selected and self.selected not in allowed: self.selected=by_id[self.selected]['parent_id']
        return rows

    def move(self, direction):
        rows = self.rows(); index = next(i for i,r in enumerate(rows) if r['id']==self.selected)
        self.selected = rows[max(0,min(len(rows)-1,index+direction))]['id']

    def current(self): return next(r for r in self.rows() if r['id']==self.selected)

    def fold(self):
        item = self.current()
        if item['has_children'] and item['id'] not in self.collapsed: self.collapsed.add(item['id'])
        elif item['id']: self.selected=item.get('visible_parent',item['parent_id'])

    def unfold(self):
        if self.selected in self.collapsed: self.collapsed.remove(self.selected)
        elif self.current()['has_children']: self.move(1)
