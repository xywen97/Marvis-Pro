"""App adapter for the same slash-command registry and dispatcher as the TUI."""
import shlex
from marvis.tui import Terminal, COMMANDS, ALIASES


def catalog():
    return [{'name':name,'usage':usage,'description':description,
             'aliases':[alias for alias,target in ALIASES.items() if target==name]}
            for name,usage,description in COMMANDS]


def execute(runtime, text, session_id):
    args = shlex.split(text)
    if not args or not text.startswith('/'): raise ValueError('请输入斜杠命令。')
    name = ALIASES.get(args[0],args[0])
    if name not in {c[0] for c in COMMANDS}: raise ValueError('未知命令；输入 /help 查看帮助。')
    if session_id and not runtime.store.one('SELECT id FROM sessions WHERE id=?',(session_id,)):
        raise ValueError('会话不存在。')
    ui = Terminal(runtime)
    if session_id: ui.session = session_id
    if name in ('/tree','/trace','/result','/quit'):
        return {'session_id':ui.session,'view':name[1:],'arguments':args[1:]}
    ui.command(text)
    return {'session_id':ui.session,'page':ui.page,'text':ui.local_text,'query':ui.query,
            'notice':ui.notice if ui.notice!='滚轮 / 触控板查看历史 · 输入 /help 查看全部命令' else ''}
