"""Match retries to the operation they actually verify, retaining failure history."""
from __future__ import annotations

import json


def failed(item):
    result = item['result']
    return bool(result.get('error') or result.get('rejected') or result.get('valid') is False
                or result.get('timed_out') or result.get('exit_code', 0) != 0)


def operation(item):
    result = item['result']; args = item.get('arguments', {})
    if item['tool'] == 'code_execute' and result.get('verification'):
        # A different interpreter may recover startup failure, but it must run
        # the identical check against the same immutable script in the same cwd.
        return ('code_execute', result.get('cwd'), json.dumps(result['verification'], sort_keys=True))
    if item['tool'] == 'code_check': return ('code_check', result.get('path') or args.get('path'))
    return (item['tool'], json.dumps(args, sort_keys=True, ensure_ascii=False))


def unresolved_failures(evidence):
    pending = {}
    rejected = []
    for item in evidence:
        if item['result'].get('rejected'):
            rejected.append(item['call_id'])
            continue
        key = operation(item)
        if failed(item): pending.setdefault(key, []).append(item['call_id'])
        else: pending.pop(key, None)
    return rejected + [call for calls in pending.values() for call in calls]
