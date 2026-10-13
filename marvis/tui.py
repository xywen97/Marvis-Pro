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
from marvis.transcript import Line, StreamAnimator, dialogue_rows, process_nodes, tree, wrapped

PAGES = ['对话', '任务', '知识库', '自动化', '设置']
STATUS = {'queued': '排队', 'running': '执行中', 'waiting_approval': '待确认', 'completed': '完成', 'failed': '失败', 'cancelled': '停止', 'interrupted': '中断'}
HELP = '''输入自然语言，或使用以下命令：
/new                         新建对话
/tree                        执行步骤树：历史跳转、编辑重发、分支切换
/sessions                    列出会话 ID
/archives                    列出已归档会话
/archive [ID]                归档指定或当前对话
/restore ID                  恢复已归档对话
/delete ID --confirm         永久删除对话及执行记录
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
/evaluate [JSON]             唤醒评测助手；提交对象与要求后由主控汇总
/mode local|efficiency       切换模式
/model URL MODEL             配置效率模式模型
/key                         隐藏输入并保存模型 API Key
/roots PATH [PATH ...]        设置授权目录，空格路径请加引号
/workflow MINUTES NAME TASK   创建自动化，名称请加引号
/workflow-run ID              立即执行工作流
/workflow-toggle ID           暂停/恢复工作流
/workflow-delete ID           删除工作流
/approve [TASK_ID] [--task]   批准操作；--task 同时授权该文件的本任务验证
/reject [TASK_ID]             拒绝操作
/cancel [TASK_ID]             停止任务
/trace                       展开/收起对话执行过程
/result [N]                  展开/折叠第 N 个工具的参数与结果
/help                        帮助
/quit                        退出
F1–F5 页面 · ↑↓滚动/选择联想 · Tab补全/切换页面 · Ctrl+R工具结果 · Ctrl+N新会话 · Ctrl+X停止
'''

# Exact aliases only: ambiguous prefixes such as /n never execute a command.
# Already short names remain unchanged and still appear in parentheses.
SHORT_COMMANDS = {
    '/evaluate':'eval',
    '/new':'new', '/tree':'t', '/sessions':'ls', '/session':'ss',
    '/archives':'las', '/archive':'ar', '/restore':'rs', '/delete':'del',
    '/files':'f', '/read':'r', '/write':'w', '/organize':'org',
    '/duplicates':'dup', '/convert':'cv', '/index':'i', '/search':'s',
    '/ocr':'ocr', '/system':'sys', '/apps':'apps', '/open-app':'oa',
    '/web':'web', '/browse':'b', '/web-search':'ws', '/mode':'mode',
    '/model':'mdl', '/key':'ky', '/roots':'rt', '/workflow':'wf', '/workflow-run':'wfr',
    '/workflow-toggle':'wft', '/workflow-delete':'wfd', '/approve':'a',
    '/reject':'j', '/cancel':'c', '/trace':'tr', '/result':'res',
    '/help':'h', '/quit':'q',
}
ALIASES = {'/' + short: name for name, short in SHORT_COMMANDS.items()}
ALIASES['/评测'] = '/evaluate'
# Keep suggestions, aliases and /help in sync, including argument hints.
COMMANDS = []
help_lines = []
for line in HELP.splitlines():
    if line.startswith('/'):
        usage, description = re.split(r'\s{2,}', line, maxsplit=1)
        name, _, arguments = usage.partition(' ')
        usage = f'{name} ({SHORT_COMMANDS[name]})' + (' ' + arguments if arguments else '')
        COMMANDS.append((name, usage, description))
        line = f'{usage:<38}  {description}'
    help_lines.append(line)
HELP = '\n'.join(help_lines)


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
        self.key_entry = False
        sessions = runtime.sessions(); self.session = sessions[0]['id'] if sessions else runtime.new_session()
        self.scroll = 0; self.notice = '滚轮 / 触控板查看历史 · 输入 /help 查看全部命令'; self.local_text = ''; self.query = ''; self.show_trace = True
        self.navigator = None
        self.expanded_results = set(); self.result_focus = None; self.result_actions = []; self.mouse_rows = {}
        self.animator = StreamAnimator(); self.cached_state = None; self.state_time = 0; self.cached_messages = []; self.messages_time = 0; self.messages_session = None
        self.rows_context=None;self.last_line_count=0
        self.scroll_limit = 0
        self.completion_key = None; self.completion_index = 0; self.completion_hidden = False

    def suggestions(self):
        if self.key_entry: return []
        key = (self.input, self.cursor)
        if key != self.completion_key:
            self.completion_key = key
            self.completion_index = 0
            self.completion_hidden = False
        if (self.completion_hidden or not self.input.startswith('/')
                or self.cursor != len(self.input) or any(c.isspace() for c in self.input)):
            return []
        prefix = self.input.lower()
        entries = [entry for entry in COMMANDS
                   if entry[0].startswith(prefix) or ('/' + SHORT_COMMANDS[entry[0]]).startswith(prefix)]
        # A complete short command should be the first suggestion, even when
        # other full commands share the prefix (e.g. /s -> /search).
        if prefix in ALIASES:
            entries.sort(key=lambda entry: entry[0] != ALIASES[prefix])
        return entries

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
        self.state_time=0;self.messages_time=0
        if not text.startswith('/'):
            task = self.runtime.submit(text, self.session); self.session = task['session_id']; self.local_text = ''; self.page = 0
            return True
        args = shlex.split(text); cmd = ALIASES.get(args[0], args[0]); rest = args[1:]
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
        if cmd == '/evaluate':
            if rest:
                self.task('delivery_evaluate', json.loads(' '.join(rest)), '用户主动评测')
            else:
                from marvis.evaluation import EVALUATION_INTAKE
                self.local_text = EVALUATION_INTAKE; self.page = 0
        elif cmd in tools:
            name, build = tools[cmd]; self.task(name, build(), text)
        elif cmd == '/quit': return False
        elif cmd == '/trace': self.show_trace = not self.show_trace
        elif cmd == '/result':
            self.dialogue(self.runtime.snapshot())
            if not self.result_actions: raise ValueError('当前对话没有工具结果。')
            index = int(rest[0]) - 1 if rest else len(self.result_actions) - 1
            if not 0 <= index < len(self.result_actions): raise ValueError('工具序号超出范围。')
            self.toggle_result(self.result_actions[index])
        elif cmd == '/help': self.local_text = HELP; self.page = 0
        elif cmd == '/new': self.session = self.runtime.new_session(self.session); self.local_text = ''; self.page = 0
        elif cmd == '/tree':
            self.navigator = Navigator(self.runtime.session_tree(self.session))
        elif cmd == '/sessions': self.local_text = '\n'.join(f"{s['id']}  {s['title']}" for s in self.runtime.sessions()); self.page = 0
        elif cmd == '/archives': self.local_text = '\n'.join(f"{s['id']}  {s['title']}" for s in self.runtime.sessions(True)) or '没有已归档的对话'; self.page = 0
        elif cmd in ('/archive', '/restore', '/delete'):
            target = rest[0] if rest else self.session
            if cmd == '/delete' and (len(rest) != 2 or rest[1] != '--confirm'):
                raise ValueError('永久删除会清理全部分支和执行记录；确认请使用 /delete ID --confirm。')
            self.runtime.session_action(target, {'/archive':'archive','/restore':'restore','/delete':'delete'}[cmd])
            if cmd == '/restore': self.session = target
            elif target == self.session:
                available = self.runtime.sessions(); self.session = available[0]['id'] if available else self.runtime.new_session()
            self.local_text = ''; self.page = 0; self.navigator = None; self.notice = '对话已' + {'/archive':'归档','/restore':'恢复','/delete':'删除'}[cmd]
        elif cmd == '/session':
            if not any(s['id'] == rest[0] for s in self.runtime.sessions()): raise ValueError('会话不存在。')
            self.session = rest[0]; self.local_text = ''; self.page = 0
        elif cmd == '/search': self.query = ' '.join(rest); self.page = 2
        elif cmd == '/mode': self.runtime.update_settings({'mode': rest[0]}); self.notice = '模式已切换'
        elif cmd == '/model': self.runtime.update_settings({'model': {'base_url': rest[0], 'model': rest[1]}}); self.notice = '模型配置已保存'
        elif cmd == '/key':
            if rest: raise ValueError('/key 不接受明文参数，请使用隐藏输入。')
            self.key_entry = True; self.input = ''; self.cursor = 0
            self.notice = '输入模型 API Key（内容隐藏）· Enter 保存 · Esc 取消'
        elif cmd == '/roots': self.runtime.update_settings({'roots': rest}); self.notice = '授权目录已保存'
        elif cmd == '/workflow':
            self.runtime.add_workflow(rest[1], ' '.join(rest[2:]), int(rest[0]) * 60); self.page = 3
        elif cmd.startswith('/workflow-'):
            result = self.runtime.workflow_action(rest[0], cmd.removeprefix('/workflow-'))
            if 'session_id' in result: self.session = result['session_id']; self.page = 0
        elif cmd in ('/approve', '/reject', '/cancel'):
            state = self.runtime.snapshot()
            task = next((t for t in state['tasks'] if (t['approval'] if cmd != '/cancel' else t['status'] in ('queued','running','waiting_approval'))), None)
            ids = [arg for arg in rest if arg != '--task']
            ident = ids[0] if ids else task['id'] if task else None
            if not ident: raise ValueError('没有可操作的任务。')
            if cmd == '/cancel': self.runtime.cancel(ident)
            elif '--task' in rest: self.runtime.approve(ident, cmd == '/approve', task_scope=True)
            else: self.runtime.approve(ident, cmd == '/approve')
        else: raise ValueError('未知命令；输入 /help 查看帮助。')
        self.scroll = 0
        return True

    @staticmethod
    def trace(task, include_stream=True):
        return '\n'.join(row.text for row in tree(process_nodes(task, include_stream)))

    def dialogue(self, snapshot):
        rows = dialogue_rows(self.messages(), snapshot.get('tasks', []), self.show_trace, self.expanded_results, int(time.monotonic()*10))
        self.result_actions = list(dict.fromkeys(row.action for row in rows if row.action))
        return rows

    def messages(self):
        now=time.monotonic()
        if self.messages_session != self.session or now-self.messages_time >= .1:
            self.cached_messages=self.runtime.messages(self.session);self.messages_time=now;self.messages_session=self.session
        return self.cached_messages

    def toggle_result(self, key):
        if key in self.expanded_results:self.expanded_results.remove(key)
        else:self.expanded_results.add(key)

    def result_key(self, key):
        if key in ('\x1b','\x12'):self.result_focus=None;return
        if not self.result_actions:self.result_focus=None;return
        index=self.result_actions.index(self.result_focus) if self.result_focus in self.result_actions else 0
        if key in (curses.KEY_UP,curses.KEY_DOWN):
            index=max(0,min(len(self.result_actions)-1,index+(-1 if key==curses.KEY_UP else 1)))
            self.result_focus=self.result_actions[index]
        elif key in ('\n','\r',curses.KEY_ENTER):self.toggle_result(self.result_focus)
        elif key==curses.KEY_RIGHT:self.expanded_results.add(self.result_focus)
        elif key==curses.KEY_LEFT:self.expanded_results.discard(self.result_focus)

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
            msgs = self.messages()
            if not msgs: return '✦  MarvisXY — 你的个人 AI 工作台\n\n把琐事交给我，专注你想做的事。\n\n/files "目录" 关键词       搜索文件\n/system disk              检查磁盘\n/index "目录"             建立知识库\n/organize "目录"          整理预览与确认\n\n输入任务后，工具在后台执行。F2 查看完整执行记录。\n输入 /help 查看所有命令。'
            return '\n'.join(row.text for row in self.dialogue(snapshot))
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
        service = (snapshot.get('memory') or {}).get('service') or {}
        names = {'ready': '已就绪', 'starting': '正在启动', 'restarting': '正在恢复', 'failed': '暂不可用', 'stopped': '已停止', 'disabled': '已停用', 'external': '外部服务'}
        memory = '长期记忆：' + names.get(service.get('state'), '未启用')
        counts = (snapshot.get('memory') or {}).get('deliveries', {})
        pending = counts.get('pending', 0) + counts.get('extracting', 0)
        if (snapshot.get('memory') or {}).get('active') and pending: memory += f'；正在保存 {pending} 条记忆'
        if service.get('error'): memory += '；' + service['error']
        return '设置 · /mode /model /key /roots\n\n' + json.dumps(snapshot['settings'], ensure_ascii=False, indent=2) + '\n\n' + memory + '\n本地模式：Ollama 127.0.0.1:11434 / qwen3:latest\n/key 隐藏输入模型密钥，不写入对话记录。'

    def select_tree(self, node_id, summarize=False):
        result = self.runtime.navigate_tree(self.session, node_id, summarize=summarize)
        self.input = result['input']; self.cursor = len(self.input)
        self.navigator = None; self.local_text = ''; self.page = 0; self.scroll = 0
        self.state_time = 0; self.messages_time = 0; self.messages_session = None; self.rows_context = None
        self.notice = '已切换历史位置；新消息将形成分支，原记录保留。'

    def draw_tree(self, screen):
        h, w = screen.getmaxyx(); screen.erase()
        nav = self.navigator
        def put(y, text, attr=0):
            if 0 <= y < h:
                try: screen.addstr(y, 2, fit(text, max(0, w - 4)), attr)
                except curses.error: pass
        put(0, '执行步骤树 · ' + nav.data['session']['title'], curses.color_pair(1) | curses.A_BOLD)
        put(2, '↑↓选择 · ←折叠/父节点 · →展开 · Enter切换 · Ctrl+S摘要后切换 · Esc返回', curses.color_pair(2))
        put(3, f"Ctrl+O筛选：{nav.filters[nav.filter]} · L书签 · 输入搜索：{nav.query}", curses.color_pair(2))
        rows = nav.rows(); index = next(i for i, r in enumerate(rows) if r['id'] == nav.selected)
        count = max(1, h - 10); start = max(0, index - count + 1)
        active = set(nav.data['path']); leaf = nav.data['session']['active_leaf']
        for offset, row in enumerate(rows[start:start + count]):
            kind = row.get('kind',row['role'])
            role = {'user':'user','assistant':'assistant','root':'system','thinking':'thinking','tool_call':'tool call','tool_result':'tool result','branch_summary':'branch summary'}.get(kind,kind)
            marker = '● ' if row['id'] == leaf else '• ' if row['id'] in active else '  '
            fold = '[+] ' if row['id'] in nav.collapsed else ''
            label = ' ★ ' + row['label'] if row.get('label') else ''
            prefix = row['prefix'] if len(row['prefix']) < w // 3 else '… ' + row['prefix'][-w // 3:]
            preview = row['content'].replace('\n',' ')
            if kind in ('tool_call','tool_result'):
                data = row.get('data',{})
                value = data.get('arguments',{}) if kind=='tool_call' else data.get('result',data.get('text',''))
                preview = data.get('tool','')+' '+json.dumps(value,ensure_ascii=False)
            text = prefix + marker + fold + role + label + ': ' + preview
            attr = self.line_attr({'user':'user','thinking':'muted','tool_call':'tool','tool_result':'success','branch_summary':'muted','root':'muted'}.get(kind,'answer'))
            if row['id'] == nav.selected: attr |= curses.A_REVERSE
            put(5 + offset, text, attr)
        selected = nav.current()
        put(h - 4, '节点 ' + (selected['id'] or 'root') + ' · ●当前节点 · •当前分支', curses.color_pair(2))
        put(h - 3, selected['content'].replace('\n', ' '))
        if nav.label_edit is not None: put(h - 1, '书签名称（Enter保存，留空清除）：' + nav.label_edit, curses.color_pair(3))
        elif nav.error: put(h - 1, nav.error, curses.color_pair(5))
        else: put(h - 1, 'user：回到父节点并填入编辑器；其他步骤：从该步继续，清空编辑器。', curses.color_pair(2))
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
        elif key == '\x13': self.select_tree(nav.selected, summarize=True)
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
        now=time.monotonic()
        if self.cached_state is None or now-self.state_time>=.1:
            self.cached_state=self.runtime.snapshot();self.state_time=now
        state = self.animator.apply(self.cached_state,now)
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
        if self.page == 0 and not self.local_text and self.messages():
            lines = wrapped(self.dialogue(state), w - 6)
        else:
            lines = [Line(line) for line in wrap(self.content(state), w - 6)]
        context=(self.session,self.page,self.local_text,w)
        if self.page==0 and not self.local_text and context==self.rows_context and self.scroll>0 and self.result_focus is None:
            self.scroll=max(0,self.scroll+len(lines)-self.last_line_count)
        self.rows_context=context;self.last_line_count=len(lines)
        self.scroll_limit = max(0, len(lines) - available)
        self.scroll = min(self.scroll_limit, max(0, self.scroll))
        start=max(0,len(lines)-available-self.scroll) if self.page==0 and not self.local_text else self.scroll
        if self.result_focus:
            index=next((i for i,line in enumerate(lines) if line.action==self.result_focus),None)
            if index is not None and not start<=index<start+available:
                start=max(0,index-available//2);self.scroll=max(0,len(lines)-available-start)
        self.mouse_rows = {}
        for i,line in enumerate(lines[start:start+available]):
            attr=self.line_attr(line.tone)
            if line.action:
                self.mouse_rows[5+i]=line.action
                if line.action==self.result_focus:attr|=curses.A_REVERSE
            put(5+i,3,line.text,attr)
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
        display = '*' * len(self.input) if self.key_entry else self.input
        left=0
        while width(display[left:self.cursor])>w-8:left+=1
        put(h-3,4,display[left:]);put(h-1,2,'工具结果：↑↓选择 · Enter切换 · ←折叠 · →展开 · Esc返回' if self.result_focus else self.notice,curses.color_pair(2))
        try:screen.move(h-3,4+width(display[left:self.cursor]))
        except curses.error:pass
        screen.noutrefresh();curses.doupdate()

    def scroll_by(self, direction, amount=3):
        """Negative direction goes toward older/top rows, positive toward bottom."""
        if self.navigator is not None:
            if self.navigator.label_edit is None: self.navigator.move(direction * amount)
            return
        self.result_focus = None
        delta = -direction if self.page == 0 and not self.local_text else direction
        self.scroll = min(self.scroll_limit, max(0, self.scroll + delta * amount))

    @staticmethod
    def wheel_buttons():
        # macOS's mouse ABI v1 reports button 5 as REPORT_MOUSE_POSITION.
        # Request button tracking only (mode 1000), not pointer-motion tracking.
        return (getattr(curses, 'BUTTON4_PRESSED', 0),
                getattr(curses, 'BUTTON5_PRESSED', curses.REPORT_MOUSE_POSITION))

    def mouse_event(self):
        try: _, _, y, _, buttons = curses.getmouse()
        except curses.error: return
        up, down = self.wheel_buttons()
        if buttons & up: self.scroll_by(-1)
        elif buttons & down: self.scroll_by(1)
        elif self.navigator is None and buttons & curses.BUTTON1_CLICKED and y in self.mouse_rows:
            self.toggle_result(self.mouse_rows[y])

    def run(self, screen):
        curses.curs_set(1);screen.keypad(True);screen.timeout(30)
        up, down = self.wheel_buttons()
        try:curses.mousemask(curses.BUTTON1_CLICKED | up | down)
        except curses.error:pass
        if curses.has_colors():
            curses.start_color();curses.use_default_colors();curses.init_pair(1,curses.COLOR_CYAN,-1);curses.init_pair(2,curses.COLOR_BLUE,-1);curses.init_pair(3,curses.COLOR_YELLOW,-1)
            curses.init_pair(4, curses.COLOR_GREEN, -1)
            curses.init_pair(5, curses.COLOR_RED, -1)
            curses.init_pair(6, 250 if curses.COLORS >= 256 else curses.COLOR_WHITE, -1)
        active=True
        while active:
            screen.timeout(30 if self.page==0 and not self.local_text and self.navigator is None else 150)
            self.draw(screen)
            try:k=screen.get_wch()
            except curses.error:continue
            try:
                if self.key_entry:
                    self.key_input(k); continue
                if k==curses.KEY_MOUSE:
                    self.mouse_event(); continue
                if self.navigator is not None:
                    self.tree_key(k); continue
                if self.result_focus is not None:
                    self.result_key(k);continue
                if k=='\x12':
                    if self.page==0 and self.result_actions:self.result_focus=self.result_actions[-1]
                    else:self.notice='当前对话没有工具结果。'
                    continue
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
                elif k in (curses.KEY_UP,curses.KEY_PPAGE):self.scroll_by(-1,5)
                elif k in (curses.KEY_DOWN,curses.KEY_NPAGE):self.scroll_by(1,5)
                elif isinstance(k,str) and k.isprintable():self.input=self.input[:self.cursor]+k+self.input[self.cursor:];self.cursor+=len(k)
            except (ValueError,KeyError,IndexError,PermissionError) as e:
                self.notice = '错误：' + str(e)
                if self.navigator is not None: self.navigator.error = self.notice

    def key_input(self, key):
        if key in ('\x1b', '\x03'):
            self.input = ''; self.cursor = 0; self.key_entry = False; self.notice = '已取消密钥输入'
        elif key in ('\n', '\r', curses.KEY_ENTER):
            secret = self.input.strip()
            self.input = ''; self.cursor = 0; self.key_entry = False
            if secret:
                model = self.runtime.settings()['model']
                self.runtime.update_settings({'model': {'base_url': model['base_url'], 'model': model['model'], 'api_key': secret}})
                self.state_time = 0; self.notice = '模型密钥已保存'
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            self.input = self.input[:-1]; self.cursor = len(self.input)
        elif key == '\x15': self.input = ''; self.cursor = 0
        elif isinstance(key, str) and key.isprintable():
            self.input += key; self.cursor = len(self.input)


def launch(runtime):
    locale.setlocale(locale.LC_ALL,'')
    curses.wrapper(Terminal(runtime).run)
