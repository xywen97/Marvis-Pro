"""Interactive navigation state for the persistent conversation tree."""
from __future__ import annotations


class Navigator:
    filters = ('全部', '用户', '书签')

    def __init__(self, data):
        self.data = data; self.query = ''; self.filter = 0; self.collapsed = set()
        self.selected = data['session']['active_leaf']; self.label_edit = None; self.error = ''

    def rows(self):
        nodes = self.data['nodes']; by_id = {n['id']: n for n in nodes}
        children = {None: []}
        for item in nodes: children.setdefault(item['parent_id'], []).append(item)
        visible = set(by_id)
        if self.query or self.filter:
            visible = set()
            for n in nodes:
                match = self.query.casefold() in (n['content'] + ' ' + (n.get('label') or '')).casefold()
                kind_match = not self.filter or (self.filter == 1 and n['role'] == 'user') or (self.filter == 2 and bool(n.get('label')))
                match = match and kind_match
                if match:
                    ident = n['id']
                    while ident and ident not in visible:
                        visible.add(ident); ident = by_id[ident]['parent_id']
        rows = [{'id': None, 'prefix': '', 'role': 'root', 'content': '会话起点', 'label': '', 'has_children': bool(nodes)}]
        if None in self.collapsed and not self.query and not self.filter:
            self.selected = None
            return rows
        stack = [(n, '', i == len(children.get(None, [])) - 1) for i, n in reversed(list(enumerate(children.get(None, []))))]
        while stack:
            item, prefix, last = stack.pop()
            if item['id'] not in visible: continue
            branch = [c for c in children.get(item['id'], []) if c['id'] in visible]
            rows.append({**item, 'prefix': prefix + ('└─ ' if last else '├─ '), 'has_children': bool(branch)})
            if item['id'] in self.collapsed and not self.query and not self.filter: continue
            stem = prefix + ('   ' if last else '│  ')
            stack.extend((child, stem, i == len(branch) - 1) for i, child in reversed(list(enumerate(branch))))
        if self.selected not in {r['id'] for r in rows}: self.selected = rows[0]['id']
        return rows

    def move(self, direction):
        rows = self.rows(); index = next(i for i, r in enumerate(rows) if r['id'] == self.selected)
        self.selected = rows[max(0, min(len(rows) - 1, index + direction))]['id']

    def current(self):return next(r for r in self.rows() if r['id'] == self.selected)

    def fold(self):
        item = self.current()
        if item['has_children'] and item['id'] not in self.collapsed: self.collapsed.add(item['id'])
        elif item['id']:
            self.selected = item['parent_id']

    def unfold(self):
        if self.selected in self.collapsed: self.collapsed.remove(self.selected)
        elif self.current()['has_children']: self.move(1)
