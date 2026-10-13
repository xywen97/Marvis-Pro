"""Execution clocks derived from audit events, independent of background delivery."""
from __future__ import annotations

import time


def task_timing(task, now=None):
    now = time.time() if now is None else now
    terminal = next((e['created'] for e in task['events']
                     if e['kind'] in ('completed', 'failed', 'cancelled', 'interrupted')), None)
    active = task['status'] in ('queued', 'running', 'waiting_approval')
    end = terminal or (now if active else task['updated'])
    result = {'finished_at': None if active else end,
              'total_ms': round(max(0, end - task['created']) * 1000),
              'approval_ms': 0, 'model_ms': 0, 'tool_ms': 0,
              'background_ms': 0}
    approvals = {}; models = {}
    explicit_ends = {(e['content'].get('agent'), e['content'].get('request_id'), e['content'].get('step'))
                     for e in task['events'] if e['kind'] == 'model_end'}
    for event in task['events']:
        kind, data, stamp = event['kind'], event['content'], event['created']
        if terminal and stamp > terminal:
            if kind.startswith('memory_'):
                result['background_ms'] = max(result['background_ms'], round((stamp - terminal) * 1000))
            continue
        call = data.get('call_id')
        key = (data.get('agent'), data.get('request_id'))
        if kind == 'approval': approvals[call] = stamp
        elif kind in ('approved', 'rejected') and call in approvals:
            result['approval_ms'] += round(max(0, stamp - approvals.pop(call)) * 1000)
        elif kind == 'model_start': models[key] = (stamp, data.get('step'))
        elif kind == 'model_end' and key in models:
            result['model_ms'] += round(max(0, stamp - models.pop(key)[0]) * 1000)
        elif kind == 'context_usage' and key in models:
            # Legacy runs have no model_end; context_usage after the turn closes it.
            if (*key, models[key][1]) not in explicit_ends:
                result['model_ms'] += round(max(0, stamp - models.pop(key)[0]) * 1000)
        elif kind in ('tool_result', 'tool_error'):
            result['tool_ms'] += data.get('elapsed_ms', 0) or 0
    result['approval_ms'] += sum(round(max(0, end - start) * 1000) for start in approvals.values())
    result['model_ms'] += sum(round(max(0, end - start[0]) * 1000) for start in models.values())
    return result
