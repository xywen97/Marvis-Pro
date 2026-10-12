"""Bound failure reports by field; never replace the whole input with a notice."""
from __future__ import annotations
import json


def bounded_text(value, limit):
    text = str(value)
    if len(text) <= limit: return text
    half = max(1, (limit - 30) // 2)
    return text[:half] + '…[内容截断，完整内容见执行记录]…' + text[-half:]


def failed_record(event):
    data = event['content']; result = data.get('result')
    return event['kind'] in ('tool_error', 'rejected') or (
        isinstance(result, dict) and bool(result.get('error') or result.get('rejected')))


def result_facts(value, limit):
    """Keep status/error/path fields even when a sibling contains a huge log."""
    fields = []
    keys = ('error', 'rejected', 'path', 'created', 'edited', 'target', 'status', 'passed',
            'success', 'exit_code', 'returncode', 'source', 'truncated', 'notice')
    def walk(item, prefix='', depth=0):
        if depth > 6 or len(fields) >= 12: return
        if isinstance(item, dict):
            # Inspect important fields before descending into bulky result containers.
            for key in keys:
                if key not in item: continue
                child = item[key]
                if not isinstance(child, (dict, list)) and len(fields) < 12:
                    fields.append({'field': prefix + key,
                                   'value': bounded_text(child, limit) if isinstance(child, str) else child})
            for key, child in item.items():
                if isinstance(child, (dict, list)): walk(child, prefix + str(key) + '.', depth + 1)
        elif isinstance(item, list):
            for index, child in enumerate(item[:12]): walk(child, prefix + str(index) + '.', depth + 1)
    walk(value)
    return fields


def failure_evidence(task, failure, reports, physical, budget=18000):
    # Keep latest results plus recent errors. Old failures do not erase later success.
    latest = {}
    for index, event in enumerate(physical):
        data = event['content']
        latest[(data.get('agent'), data.get('tool'))] = index
    errors = [i for i, event in enumerate(physical) if failed_record(event)]
    indices = sorted(set(list(latest.values())[-24:] + errors[-8:]))
    for limit in (600, 300, 150, 75, 32):
        payload = {
            'task': bounded_text(task, 2000), 'failure': bounded_text(failure, 2000),
            'reports': {actor: {'status': report['status'],
                                'summary': bounded_text(report['summary'], limit * 2),
                                'summary_truncated': len(report['summary']) > limit * 2,
                                'evidence_count': len(report.get('evidence', []))}
                        for actor, report in reports.items()},
            'execution': {'total_records': len(physical), 'failed_records': len(errors),
                          'included_records': len(indices), 'omitted_records': len(physical) - len(indices)},
            'tool_records': [],
            'notice': '仅汇总保留的证据；截断及省略内容尚未验证，完整内容保存在执行记录。',
        }
        for index in indices:
            event = physical[index]; data = event['content']
            value = data.get('result', data.get('text', ''))
            raw = json.dumps(value, ensure_ascii=False)
            payload['tool_records'].append({
                'record_index': index, 'agent': data.get('agent'), 'tool': data.get('tool'),
                'kind': event['kind'], 'has_error': failed_record(event),
                'facts': result_facts(value, limit)[:4 if limit <= 75 else 12],
                'excerpt': bounded_text(raw, limit), 'truncated': len(raw) > limit,
            })
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) <= budget: return encoded
    # This is an internal contract violation, not a valid evidence-free model input.
    raise ValueError('关键失败证据超过汇总预算，使用执行器报告。')
