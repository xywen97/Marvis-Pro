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
            result.append(Line(text[:end], row.tone, prefix))
            text = prefix + text[end:]
        result.append(Line(text, row.tone, prefix))
    return result


def node(text, tone='muted', children=None):
    return {'text': text, 'tone': tone, 'children': children or []}


def tree(nodes, prefix=''):
    rows = []
    for i, item in enumerate(nodes):
        last = i == len(nodes) - 1
        stem = prefix + ('   ' if last else '│  ')
        lines = str(item['text']).splitlines() or ['']
        rows.append(Line(prefix + ('└─ ' if last else '├─ ') + lines[0], item['tone'], stem))
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


def process_nodes(task, include_stream=True):
    nodes = []; calls = []
    for event in task.get('events', []):
        kind, data = event['kind'], event['content']
        if kind == 'queued': nodes.append(node('◷ 已加入任务队列'))
        elif kind == 'planning': nodes.append(node('◇ 规划 · ' + data['text'], 'thought'))
        elif kind == 'model_start': nodes.append(node(f"◇ 模型处理中 · 第 {data['step']} 轮 · {data.get('model', '')}", 'thought'))
        elif kind == 'assistant_note': nodes.append(node('◇ 处理说明 · ' + data['text'], 'thought'))
        elif kind == 'assistant_stream' and include_stream:
            nodes.append(node('✦ MarvisXY（生成中）', 'answer', [node(data['text'], 'normal')]))
        elif kind == 'tool_start':
            item = node('→ 调用 ' + data['tool'], 'tool', [node('参数', 'muted', [node(json.dumps(data.get('arguments', {}), ensure_ascii=False, indent=2), 'detail')])])
            nodes.append(item); calls.append((data['tool'], item))
        elif kind in ('tool_result', 'tool_error'):
            parent = next((item for name, item in reversed(calls) if name == data['tool'] and not item.get('done')), None)
            if kind == 'tool_result':
                item = node(f"✓ {data['tool']} 执行完成 · {data.get('elapsed_ms', 0)} ms", 'success', [node('结果', 'muted', [node(preview(data['result']), 'detail')])])
            else: item = node('✗ ' + data['tool'] + ' · ' + data['text'], 'error')
            if parent:
                parent['children'].append(item); parent['done'] = True
            else: nodes.append(item)
        elif kind in ('approval', 'approved', 'rejected'):
            parent = next((item for _, item in reversed(calls) if not item.get('done')), None)
            if kind == 'approval': item = node('◇ 等待确认 · ' + data.get('action', data.get('tool', '')), 'warning')
            elif kind == 'approved': item = node('✓ 已批准执行', 'success')
            else: item = node('! ' + data.get('text', '用户拒绝了操作'), 'warning')
            if parent:
                parent['children'].append(item)
                if kind == 'rejected': parent['done'] = True
            else: nodes.append(item)
        elif kind in ('rejected', 'cancel_requested', 'failed', 'cancelled'):
            nodes.append(node('! ' + data.get('text', kind), 'error' if kind == 'failed' else 'warning'))
        elif kind == 'completed': nodes.append(node('✓ 任务完成', 'success'))
    if task['status'] in ('running', 'queued', 'waiting_approval'):
        status = {'running': '执行中', 'queued': '排队', 'waiting_approval': '待确认'}[task['status']]
        nodes.append(node(f"◷ {status} · 已用时 {int(time.time() - task['created'])} 秒", 'warning'))
    return nodes


def dialogue_rows(messages, tasks, show_trace=True):
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
        nodes = []
        if task and show_trace:
            nodes.append(node('执行过程', 'muted', process_nodes(task, not answer)))
        if answer: nodes.append(node('✦ MarvisXY', 'answer', [node(answer['content'], 'normal')]))
        rows.extend(tree(nodes))
    return rows
