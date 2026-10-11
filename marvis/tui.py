"""Curses interactive terminal, including task approvals and persistent sessions."""
from __future__ import annotations
import curses
import json
import locale
import re
import shlex
import time
import unicodedata
from marvis.session_tree import Navigator
from marvis.transcript import Line, dialogue_rows, process_nodes, tree, wrapped

PAGES = ['对话', '任务', '知识库', '自动化', '设置']
STATUS = {'queued': '排队', 'running': '执行中', 'waiting_approval': '待确认', 'completed': '完成', 'failed': '失败', 'cancelled': '停止', 'interrupted': '中断'}
HELP = '''输入自然语言，或使用以下命令：
/new                         新建对话
/tree [NODE_ID]              会话树：历史跳转、编辑重发、分支切换
/sessions                    列出会话 ID
/session ID                  切换历史会话
/files PATH [KEYWORD]        文件名和内容搜索
/read PATH                   读取文档
/write PATH TEXT             创建文件（需确认）
/organize PATH               整理目录（需确认）
/duplicates PATH             查找重复文件
/convert PATH FORMAT         转换文档（需确认）
/index PATH                  建立知识库索引
/search KEYWORD              搜索知识库
/ocr PATH                    提取图片文字
/system [SECTION]            overview/disk/memory/processes/battery
/apps                        列出 macOS 应用
/open-app NAME               打开应用（需确认）
/web URL                     读取网页
/browse URL                  打开网页（需确认）
/web-search QUERY            网络搜索
/mode local|efficiency       切换模式
/model URL MODEL             配置效率模式模型；Key 从 .env 读取
/roots PATH [PATH ...]        设置授权目录，空格路径请加引号
/workflow MINUTES NAME TASK   创建自动化，名称请加引号
/workflow-run ID              立即执行工作流
/workflow-toggle ID           暂停/恢复工作流
/workflow-delete ID           删除工作流
/approve [TASK_ID]            批准待确认操作
/reject [TASK_ID]             拒绝操作
/cancel [TASK_ID]             停止任务
/trace                       展开/收起对话执行过程
/help                        帮助
/quit                        退出
F1–F5 页面 · ↑↓滚动/选择联想 · Tab补全/切换页面 · Esc收起联想 · Ctrl+N新会话 · Ctrl+X停止
'''

# Keep suggestions and /help in sync, including argument hints.
COMMANDS = []
for line in HELP.splitlines():
    if line.startswith('/'):
        usage, description = re.split(r'\s{2,}', line, maxsplit=1)
        COMMANDS.append((usage.split()[0], usage, description))


def width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def fit(text, columns):
    out = ''; used = 0
    for c in str(text):
        if ord(c) < 32: c = ' '
        w = width(c)
        if used + w > columns: break
        out += c; used += w
    return out


def wrap(text, columns):
    result = []
    for paragraph in str(text).splitlines() or ['']:
        line = ''; used = 0
        for c in paragraph:
            if ord(c) < 32: c = ' '
            w = width(c)
            if used + w > columns:
                result.append(line); line = ''; used = 0
            line += c; used += w
        result.append(line)
    return result


class Terminal:
    def __init__(self, runtime):
        self.runtime = runtime; self.page = 0; self.input = ''; self.cursor = 0
        sessions = runtime.sessions(); self.session = sessions[0]['id'] if sessions else runtime.new_session()
        self.scroll = 0; self.notice = '输入 /help 查看全部命令'; self.local_text = ''; self.query = ''; self.show_trace = True
        self.navigator = None
        self.completion_key = None; self.completion_index = 0; self.completion_hidden = False

    def suggestions(self):
        key = (self.input, self.cursor)
        if key != self.completion_key:
            self.completion_key = key
            self.completion_index = 0
            self.completion_hidden = False
        if (self.completion_hidden or not self.input.startswith('/')
                or self.cursor != len(self.input) or any(c.isspace() for c in self.input)):
            return []
        return [entry for entry in COMMANDS if entry[0].startswith(self.input.lower())]

    def complete(self):
        suggestions = self.suggestions()
        if not suggestions: return False
        self.input = suggestions[self.completion_index % len(suggestions)][0] + ' '
        self.cursor = len(self.input)
        return True

    def task(self, tool, arguments, prompt):
        task = self.runtime.submit(prompt, self.session, {'tool': tool, 'arguments': arguments})
        self.session = task['session_id']; self.local_text = ''; self.page = 0; self.scroll = 0

    def command(self, text):
        if not text.startswith('/'):
            task = self.runtime.submit(text, self.session); self.session = task['session_id']; self.local_text = ''; self.page = 0
            return True
        args = shlex.split(text); cmd = args[0]; rest = args[1:]
        tools = {
            '/files': ('file_list', lambda: {'path': rest[0], 'query': ' '.join(rest[1:]), 'content': True}),
            '/read': ('file_read', lambda: {'path': rest[0]}),
            '/write': ('file_write', lambda: {'path': rest[0], 'text': ' '.join(rest[1:])}),
            '/organize': ('file_organize', lambda: {'path': rest[0]}),
            '/duplicates': ('file_duplicates', lambda: {'path': rest[0]}),
            '/convert': ('document_convert', lambda: {'path': rest[0], 'format': rest[1]}),
            '/index': ('knowledge_index', lambda: {'path': rest[0]}),
            '/ocr': ('image_ocr', lambda: {'path': rest[0]}),
            '/system': ('system_info', lambda: {'section': rest[0] if rest else 'overview'}),
            '/apps': ('app_list', lambda: {}),
            '/open-app': ('app_open', lambda: {'name': ' '.join(rest)}),
            '/web': ('browser_read', lambda: {'url': rest[0]}),
            '/browse': ('browser_open', lambda: {'url': rest[0]}),
            '/web-search': ('web_search', lambda: {'query': ' '.join(rest)}),
        }
        if cmd in tools:
            name, build = tools[cmd]; self.task(name, build(), text)
        elif cmd == '/quit': return False
        elif cmd == '/trace': self.show_trace = not self.show_trace
        elif cmd == '/help': self.local_text = HELP; self.page = 0
        elif cmd == '/new': self.session = self.runtime.new_session(); self.local_text = ''; self.page = 0
        elif cmd == '/tree':
            if rest: self.select_tree(rest[0])
            else: self.navigator = Navigator(self.runtime.session_tree(self.session))
        elif cmd == '/sessions': self.local_text = '\n'.join(f"{s['id']}  {s['title']}" for s in self.runtime.sessions()); self.page = 0
        elif cmd == '/session':
            if not any(s['id'] == rest[0] for s in self.runtime.sessions()): raise ValueError('会话不存在。')
            self.session = rest[0]; self.local_text = ''; self.page = 0
        elif cmd == '/search': self.query = ' '.join(rest); self.page = 2
        elif cmd == '/mode': self.runtime.update_settings({'mode': rest[0]}); self.notice = '模式已切换'
        elif cmd == '/model': self.runtime.update_settings({'model': {'base_url': rest[0], 'model': rest[1]}}); self.notice = '模型配置已保存'
        elif cmd == '/roots': self.runtime.update_settings({'roots': rest}); self.notice = '授权目录已保存'
        elif cmd == '/workflow':
            self.runtime.add_workflow(rest[1], ' '.join(rest[2:]), int(rest[0]) * 60); self.page = 3
        elif cmd.startswith('/workflow-'):
            result = self.runtime.workflow_action(rest[0], cmd.removeprefix('/workflow-'))
            if 'session_id' in result: self.session = result['session_id']; self.page = 0
        elif cmd in ('/approve', '/reject', '/cancel'):
            state = self.runtime.snapshot()
            task = next((t for t in state['tasks'] if (t['approval'] if cmd != '/cancel' else t['status'] in ('queued','running','waiting_approval'))), None)
            ident = rest[0] if rest else task['id'] if task else None
            if not ident: raise ValueError('没有可操作的任务。')
            if cmd == '/cancel': self.runtime.cancel(ident)
            else: self.runtime.approve(ident, cmd == '/approve')
        else: raise ValueError('未知命令；输入 /help 查看帮助。')
        self.scroll = 0
        return True

    @staticmethod
    def trace(task, include_stream=True):
        return '\n'.join(row.text for row in tree(process_nodes(task, include_stream)))

    def dialogue(self, snapshot):
        return dialogue_rows(self.runtime.messages(self.session), snapshot.get('tasks', []), self.show_trace)

    @staticmethod
    def line_attr(tone):
        pairs = {'user': 1, 'tool': 2, 'success': 4, 'error': 5,
                 'warning': 3, 'thought': 6, 'muted': 6, 'detail': 6, 'answer': 1}
        attr = curses.color_pair(pairs.get(tone, 0))
        if tone in ('user', 'tool', 'answer', 'error'): attr |= curses.A_BOLD
        if tone in ('thought', 'muted'): attr |= curses.A_DIM
        return attr

    def content(self, snapshot):
        if self.page == 0:
            if self.local_text: return self.local_text
            msgs = self.runtime.messages(self.session)
            if not msgs: return '✦  MarvisXY — 你的个人 AI 工作台\n\n把琐事交给我，专注你想做的事。\n\n/files "目录" 关键词       搜索文件\n/system disk              检查磁盘\n/index "目录"             建立知识库\n/organize "目录"          整理预览与确认\n\n输入任务后，工具在后台执行。F2 查看完整执行记录。\n输入 /help 查看所有命令。'
            return '\n'.join(row.text for row in dialogue_rows(msgs, snapshot.get('tasks', []), self.show_trace))
        if self.page == 1:
            out = []
            for t in snapshot['tasks']:
                out.append(f"[{STATUS[t['status']]}] {t['prompt']}\nID: {t['id']}")
                for e in t['events']:
                    out.append('  ' + e['kind'] + ': ' + json.dumps(e['content'], ensure_ascii=False))
                out.append('')
            return '\n'.join(out) or '暂无任务。'
        if self.page == 2:
            files = self.runtime.knowledge(self.query)['files']
            return '本地知识库 · /index 建立索引 · /search 关键词\n\n' + '\n\n'.join(f"{f['category']}  {f['name']}\n{f['path']}\n{f['preview']}" for f in files) if files else '知识库暂无匹配文件。使用 /index "目录" 建立索引。'
        if self.page == 3:
            return '自动化 · 服务运行期间执行\n/workflow 60 "磁盘巡检" 检查磁盘空间\n\n' + '\n\n'.join(f"{'运行' if w['enabled'] else '暂停'}  {w['name']} · 每 {w['interval']//60} 分钟\nID: {w['id']}\n{w['prompt']}" for w in snapshot['workflows'])
        return '设置 · /mode /model /roots\n\n' + json.dumps(snapshot['settings'], ensure_ascii=False, indent=2) + '\n\n本地模式：Ollama 127.0.0.1:11434 / qwen3:latest\nAPI Key 从 .env 读取，不显示在界面。'

    def select_tree(self, node_id):
        result = self.runtime.navigate_tree(self.session, node_id)
        self.input = result['input']; self.cursor = len(self.input)
        self.navigator = None; self.local_text = ''; self.page = 0; self.scroll = 0
        self.notice = '已切换历史位置；新消息将形成分支，原记录保留。'

    def draw_tree(self, screen):
        h, w = screen.getmaxyx(); screen.erase()
        nav = self.navigator
        def put(y, text, attr=0):
            if 0 <= y < h:
                try: screen.addstr(y, 2, fit(text, max(0, w - 4)), attr)
                except curses.error: pass
        put(0, '会话树 · ' + nav.data['session']['title'], curses.color_pair(1) | curses.A_BOLD)
        put(2, '↑↓选择 · ←折叠/父节点 · →展开 · Enter切换 · Esc返回', curses.color_pair(2))
        put(3, f"Ctrl+O筛选：{nav.filters[nav.filter]} · L书签 · 输入搜索：{nav.query}", curses.color_pair(2))
        rows = nav.rows(); index = next(i for i, r in enumerate(rows) if r['id'] == nav.selected)
        count = max(1, h - 10); start = max(0, index - count + 1)
        active = set(nav.data['path']); leaf = nav.data['session']['active_leaf']
        for offset, row in enumerate(rows[start:start + count]):
            role = {'user': '你', 'assistant': 'MarvisXY', 'root': '起点'}[row['role']]
            marker = '● ' if row['id'] == leaf else '• ' if row['id'] in active else '  '
            fold = '[+] ' if row['id'] in nav.collapsed else ''
            label = ' ★ ' + row['label'] if row.get('label') else ''
            prefix = row['prefix'] if len(row['prefix']) < w // 3 else '… ' + row['prefix'][-w // 3:]
            text = prefix + marker + fold + role + label + ': ' + row['content'].replace('\n', ' ')
            attr = self.line_attr('user' if row['role'] == 'user' else 'answer')
            if row['id'] == nav.selected: attr |= curses.A_REVERSE
            put(5 + offset, text, attr)
        selected = nav.current()
        put(h - 4, '节点 ' + (selected['id'] or 'root') + ' · ●当前节点 · •当前分支', curses.color_pair(2))
        put(h - 3, selected['content'].replace('\n', ' '))
        if nav.label_edit is not None: put(h - 1, '书签名称（Enter保存，留空清除）：' + nav.label_edit, curses.color_pair(3))
        elif nav.error: put(h - 1, nav.error, curses.color_pair(5))
        else: put(h - 1, '选用户消息：回到其父节点并填入编辑器；选助手回复：从此继续。', curses.color_pair(2))
        screen.refresh()

    def tree_key(self, key):
        nav = self.navigator
        if nav.label_edit is not None:
            if key == '\x1b': nav.label_edit = None
            elif key in ('\n', '\r', curses.KEY_ENTER):
                self.runtime.label_node(self.session, nav.selected, nav.label_edit)
                nav.data = self.runtime.session_tree(self.session); nav.label_edit = None
            elif key in ('\x7f', '\b', curses.KEY_BACKSPACE): nav.label_edit = nav.label_edit[:-1]
            elif isinstance(key, str) and key.isprintable(): nav.label_edit += key
        elif key in ('\x1b', '\x03'): self.navigator = None
        elif key in ('\n', '\r', curses.KEY_ENTER): self.select_tree(nav.selected)
        elif key in (curses.KEY_UP, curses.KEY_DOWN): nav.move(-1 if key == curses.KEY_UP else 1)
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE): nav.move(-10 if key == curses.KEY_PPAGE else 10)
        elif key == curses.KEY_LEFT: nav.fold()
        elif key == curses.KEY_RIGHT: nav.unfold()
        elif key == '\x0f': nav.filter = (nav.filter + 1) % len(nav.filters)
        elif key == 'L' and nav.selected: nav.label_edit = nav.current().get('label') or ''
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE): nav.query = nav.query[:-1]
        elif key == '\x15': nav.query = ''
        elif isinstance(key, str) and key.isprintable(): nav.query += key

    def draw(self, screen):
        if self.navigator is not None:
            self.draw_tree(screen); return
        h, w = screen.getmaxyx(); screen.erase()
        def put(y, x, text, attr=0):
            if y < 0 or y >= h or x >= w: return
            try: screen.addstr(y, x, fit(text, max(0,w-x-1)), attr)
            except curses.error: pass
        if h < 15 or w < 60:
            put(0,0,'窗口过小，请调整至至少 60×15；Ctrl+C 退出。'); screen.refresh(); return
        state = self.runtime.snapshot()
        mode = '本地' if state['settings']['mode']=='local' else '效率'
        put(0,2,'m✦  MarvisXY',curses.color_pair(1)|curses.A_BOLD);put(0,23,f'{mode}模式 · 本地工作空间',curses.color_pair(2))
        x=2
        for i,name in enumerate(PAGES):
            label=f' F{i+1} {name} ';put(2,x,label,curses.color_pair(1)|curses.A_REVERSE if i==self.page else curses.color_pair(2));x+=width(label)+2
        put(3,1,'─'*(w-2),curses.color_pair(2))
        pending=next((t for t in state['tasks'] if t['approval']),None)
        suggestions = self.suggestions()
        menu_rows = min(6, max(1, h - 15)) if suggestions else 0
        menu_space = menu_rows + 1 if suggestions else 0
        footer=(8 if pending else 4) + menu_space
        available=h-footer-5
        if self.page == 0 and not self.local_text and self.runtime.messages(self.session):
            lines = wrapped(self.dialogue(state), w - 6)
        else:
            lines = [Line(line) for line in wrap(self.content(state), w - 6)]
        start=max(0,len(lines)-available-self.scroll) if self.page==0 and not self.local_text else self.scroll
        for i,line in enumerate(lines[start:start+available]):put(5+i,3,line.text,self.line_attr(line.tone))
        if pending:
            put(h-8-menu_space,2,'◇ 待确认：'+pending['approval']['action'],curses.color_pair(3)|curses.A_BOLD)
            plan=pending['approval']; details=plan.get('path') or plan.get('target') or plan.get('arguments') or str(plan.get('count',''))+' 个文件'
            put(h-7-menu_space,2,str(details),curses.color_pair(3));put(h-6-menu_space,2,'输入 /approve 确认 · /reject 拒绝 · F2 查看完整计划',curses.color_pair(3))
        if suggestions:
            start = max(0, self.completion_index - menu_rows + 1)
            top = h - 4 - menu_space
            put(top, 2, f'命令联想 · {self.completion_index + 1}/{len(suggestions)} · ↑↓选择 · Tab补全 · Esc收起', curses.color_pair(2))
            for offset, (name, usage, description) in enumerate(suggestions[start:start + menu_rows]):
                selected = start + offset == self.completion_index
                label = ('› ' if selected else '  ') + usage + '  — ' + description
                attr = curses.color_pair(1) | curses.A_REVERSE if selected else curses.color_pair(2)
                put(top + 1 + offset, 2, label, attr)
        put(h-4,1,'─'*(w-2),curses.color_pair(2));put(h-3,2,'› ',curses.color_pair(1))
        # Keep the insertion cursor visible even for long Chinese input.
        left=0
        while width(self.input[left:self.cursor])>w-8:left+=1
        put(h-3,4,self.input[left:]);put(h-1,2,self.notice,curses.color_pair(2))
        try:screen.move(h-3,4+width(self.input[left:self.cursor]))
        except curses.error:pass
        screen.refresh()

    def run(self, screen):
        curses.curs_set(1);screen.keypad(True);screen.timeout(150)
        if curses.has_colors():
            curses.start_color();curses.use_default_colors();curses.init_pair(1,curses.COLOR_CYAN,-1);curses.init_pair(2,curses.COLOR_BLUE,-1);curses.init_pair(3,curses.COLOR_YELLOW,-1)
            curses.init_pair(4, curses.COLOR_GREEN, -1)
            curses.init_pair(5, curses.COLOR_RED, -1)
            curses.init_pair(6, 245 if curses.COLORS >= 256 else curses.COLOR_WHITE, -1)
        active=True
        while active:
            self.draw(screen)
            try:k=screen.get_wch()
            except curses.error:continue
            try:
                if self.navigator is not None:
                    self.tree_key(k); continue
                if k in ('\n','\r',curses.KEY_ENTER):
                    if self.input.strip():
                        text = self.input.strip(); self.input = ''; self.cursor = 0
                        active = self.command(text)
                elif k in ('\x7f','\b',curses.KEY_BACKSPACE):
                    if self.cursor:self.input=self.input[:self.cursor-1]+self.input[self.cursor:];self.cursor-=1
                elif k==curses.KEY_DC:self.input=self.input[:self.cursor]+self.input[self.cursor+1:]
                elif k==curses.KEY_LEFT:self.cursor=max(0,self.cursor-1)
                elif k==curses.KEY_RIGHT:self.cursor=min(len(self.input),self.cursor+1)
                elif k in (curses.KEY_HOME,'\x01'):self.cursor=0
                elif k in (curses.KEY_END,'\x05'):self.cursor=len(self.input)
                elif k=='\x15':self.input='';self.cursor=0
                elif k=='\x03':active=False
                elif k=='\x0e':self.command('/new')
                elif k=='\x18':self.command('/cancel')
                elif k=='\x1b':self.suggestions();self.completion_hidden=True
                elif k=='\t':
                    if not self.complete():self.page=(self.page+1)%len(PAGES);self.scroll=0
                elif k in (curses.KEY_UP, curses.KEY_DOWN) and self.suggestions():
                    direction = -1 if k == curses.KEY_UP else 1
                    self.completion_index = (self.completion_index + direction) % len(self.suggestions())
                elif isinstance(k,int) and curses.KEY_F1<=k<=curses.KEY_F5:self.page=k-curses.KEY_F1;self.scroll=0
                elif k in (curses.KEY_UP,curses.KEY_PPAGE):self.scroll=max(0,self.scroll+(5 if self.page==0 else -5))
                elif k in (curses.KEY_DOWN,curses.KEY_NPAGE):self.scroll=max(0,self.scroll+(-5 if self.page==0 else 5))
                elif isinstance(k,str) and k.isprintable():self.input=self.input[:self.cursor]+k+self.input[self.cursor:];self.cursor+=len(k)
            except (ValueError,KeyError,IndexError,PermissionError) as e:
                self.notice = '错误：' + str(e)
                if self.navigator is not None: self.navigator.error = self.notice


def launch(runtime):
    locale.setlocale(locale.LC_ALL,'')
    curses.wrapper(Terminal(runtime).run)
