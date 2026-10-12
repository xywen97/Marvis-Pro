"""Tree-shaped transcript rows with semantic styles and hanging indentation."""
from __future__ import annotations
from dataclasses import dataclass
import json
import time
import unicodedata


@dataclass
class Line:
    text: str
    tone: str = 'normal'
    continuation: str = ''
    action: str = ''


def columns(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def wrapped(rows, limit):
    """Wrap by terminal cells while retaining style and tree continuation guides."""
    result = []
    for row in rows:
        text = ''.join(c if ord(c) >= 32 else ' ' for c in row.text)
        prefix = row.continuation
        while columns(text) > limit:
            used = 0; end = 0
            for c in text:
                size = columns(c)
                if used + size > limit: break
                used += size; end += 1
            if end <= len(prefix) or not end: break
            result.append(Line(text[:end], row.tone, prefix, row.action))
            text = prefix + text[end:]
        result.append(Line(text, row.tone, prefix, row.action))
    return result


def node(text, tone='muted', children=None, action=''):
    return {'text': text, 'tone': tone, 'children': children or [], 'action': action}


def tree(nodes, prefix=''):
    rows = []
    for i, item in enumerate(nodes):
        last = i == len(nodes) - 1
        stem = prefix + ('   ' if last else '│  ')
        lines = str(item['text']).splitlines() or ['']
        rows.append(Line(prefix + ('└─ ' if last else '├─ ') + lines[0], item['tone'], stem, item.get('action', '')))
        rows.extend(Line(stem + line, item['tone'], stem) for line in lines[1:])
        rows.extend(tree(item['children'], stem))
    return rows


def preview(value):
    # Render real multiline output instead of literal JSON \n escape sequences.
    if isinstance(value, dict) and isinstance(value.get('output'), str):
        text = value['output']
    elif isinstance(value, dict) and isinstance(value.get('text'), str):
        text = value['text']
    else: text = json.dumps(value, ensure_ascii=False, indent=2)
    lines = text.splitlines()
    clipped = len(text) > 1200 or len(lines) > 12
    text = '\n'.join(lines[:12])[:1200]
    return text + ('\n…（F2 查看完整结果）' if clipped else '')


def process_nodes(task, include_stream=True, expanded=None, frame=0):
    expanded = expanded or set(); groups = []; by_step = {}; current = 0
    def group(step):
        if step not in by_step:
            item = node(f'Step {step}' if step else '工具执行', 'muted')
            by_step[step] = item; groups.append(item)
        return by_step[step]
    calls = {}; legacy = []; pending_streams = {}
    for index, event in enumerate(task.get('events', [])):
        kind, data = event['kind'], event['content']
        if kind == 'model_start': current = data['step']
        parent = group(data.get('step', current))
        if kind == 'context_compaction_start':
            parent['children'].append(node(data['text'], 'muted'))
        elif kind == 'context_compaction':
            parent['children'].append(node(f"上下文已压缩 · {data['agent']} · {data['before_tokens']} → {data['after_tokens']} 估算 tokens", 'muted'))
        elif kind == 'context_compaction_error': parent['children'].append(node(data['text'], 'error'))
        elif kind == 'model_start': parent['text'] = f"Step {current} · {data.get('model', '')}"
        elif kind in ('assistant_note', 'assistant_stream'):
            if kind == 'assistant_note' or include_stream:
                parent['children'].append(node('╭ 处理说明' + (' · 生成中' if kind == 'assistant_stream' else ''), 'thought', [node(data['text'], 'thought')]))
        elif kind in ('tool_call_stream', 'tool_start'):
            ident = data.get('call_id') or str(event.get('id', index))
            key = str(task['id']) + ':' + ident
            item = calls.get(key)
            if not item:
                item = node('', 'muted', action=key);calls[key] = item;parent['children'].append(item)
                legacy.append((data.get('tool'), item))
            item['text'] = ('[-] ' if key in expanded else '[+] ') + '→ 调用 ' + (data.get('tool') or '准备工具')
            args = data.get('arguments', {})
            item['arguments'] = args
            item['children'] = [node('参数', 'muted', [node(args if isinstance(args,str) else json.dumps(args,ensure_ascii=False,indent=2), 'detail')])] if key in expanded else []
            if kind == 'tool_call_stream':pending_streams[key] = item
            else: pending_streams.pop(key, None)
        elif kind in ('tool_result','tool_error','approval','approved','rejected'):
            key = str(task['id']) + ':' + str(data.get('call_id'))
            item = calls.get(key) if data.get('call_id') else next((n for name,n in reversed(legacy) if not n.get('done') and (not data.get('tool') or name==data['tool'])),None)
            if kind == 'tool_result':
                detail = node(f"✓ {data['tool']} 执行完成 · {data.get('elapsed_ms',0)} ms",'success')
                if item and item['action'] in expanded:detail['children']=[node('结果','muted',[node(preview(data['result']),'detail')])]
            elif kind == 'tool_error':detail=node('✗ '+data.get('text','工具失败'),'error')
            elif kind == 'approval':detail=node('◇ 等待确认 · '+data.get('action',data.get('tool','')),'warning')
            elif kind == 'approved':detail=node('✓ 已批准执行','success')
            else:detail=node('! '+data.get('text','操作已拒绝'),'warning')
            if item:
                item['children'].append(detail)
                if kind in ('tool_result','tool_error','rejected'):item['done']=True
            else:parent['children'].append(detail)
        elif kind in ('failed','cancelled','cancel_requested','stream_fallback'):
            parent['children'].append(node('! '+data.get('text',kind),'error' if kind=='failed' else 'muted'))
    spinner = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[frame % 10]
    for item in pending_streams.values():item['text'] += ' · ' + spinner + ' 生成调用'
    groups = [g for g in groups if g['children'] or g['text'].startswith('Step')]
    if task['status'] in ('running','queued','waiting_approval'):
        status = {'running':'执行中','queued':'排队','waiting_approval':'待确认'}[task['status']]
        if groups:groups[-1]['text'] += ' · ' + spinner + ' ' + status
        else:groups.append(node(spinner+' '+status,'muted'))
    return groups


class StreamAnimator:
    """Reveal received public text at a steady terminal cadence, without delaying tools."""
    def __init__(self):self.shown = {};self.last = None
    def apply(self, snapshot, now):
        elapsed = max(0, now - self.last) if self.last is not None else .03;self.last = now
        result = {**snapshot, 'tasks': []};live = set()
        for task in snapshot.get('tasks', []):
            events = []
            for index,event in enumerate(task.get('events', [])):
                if event['kind'] == 'assistant_stream' and task['status'] == 'running':
                    key = (task['id'],event.get('id',index));live.add(key)
                    target=event['content']['text']; previous=self.shown.get(key,'')
                    if not target.startswith(previous):previous=''
                    count=max(1,int(elapsed*90),int((len(target)-len(previous))/8))
                    value=target[:len(previous)+count];self.shown[key]=value
                    event={**event,'content':{**event['content'],'text':value}}
                events.append(event)
            result['tasks'].append({**task,'events':events})
        self.shown={key:value for key,value in self.shown.items() if key in live}
        return result

def dialogue_rows(messages, tasks, show_trace=True, expanded=None, frame=0):
    by_id = {t['id']: t for t in tasks}
    answers = {m.get('task_id'): m for m in messages if m['role'] == 'assistant'}
    rows = []
    for message in messages:
        if message['role'] != 'user':
            # Messages without a matching user task remain visible (legacy imports).
            if not any(m['role'] == 'user' and m.get('task_id') == message.get('task_id') for m in messages):
                rows.extend(tree([node('✦ MarvisXY', 'answer', [node(message['content'], 'normal')])]))
            continue
        if rows: rows.append(Line(''))
        parts = message['content'].splitlines() or ['']
        rows.append(Line('你 › ' + parts[0], 'user', '     '))
        rows.extend(Line('     ' + part, 'user', '     ') for part in parts[1:])
        task = by_id.get(message.get('task_id')); answer = answers.get(message.get('task_id'))
        if task and 'event_cutoff' in message and task['status'] not in ('queued','running','waiting_approval'):
            task = {**task,'events':[e for e in task['events'] if e['id']<=message['event_cutoff']]}
        nodes = []
        if task and show_trace:
            nodes.append(node('执行过程', 'muted', process_nodes(task, not answer, expanded, frame)))
        if answer: nodes.append(node('✦ MarvisXY', 'answer', [node(answer['content'], 'normal')]))
        rows.extend(tree(nodes))
    return rows
