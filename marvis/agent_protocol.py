"""One versioned request/result contract for every specialist."""
from __future__ import annotations
import json

WORKERS = ['file', 'computer', 'app', 'browser', 'search']
REQUEST_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'agent': {'type': 'string', 'enum': WORKERS, 'description': '目标助手 ID。'},
    'objective': {'type': 'string', 'minLength': 1, 'maxLength': 10000, 'description': '明确的子任务目标和完成条件。'},
    'context': {'type': 'string', 'maxLength': 20000, 'description': '必要输入、路径、前序证据或约束；不传完整用户聊天。'},
    'continuation_of': {'type': 'string', 'maxLength': 64, 'description': '可选，当前任务已返回的 request_id，用于关联主控转接。'},
}, 'required': ['agent', 'objective', 'context']}
REPORT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'status': {'type': 'string', 'enum': ['completed', 'partial', 'needs_handoff', 'failed']},
    'summary': {'type': 'string', 'minLength': 1, 'maxLength': 12000, 'description': '基于实际证据的结果、限制及未完成事项。'},
    'requests': {'type': 'array', 'maxItems': 3, 'items': REQUEST_SCHEMA,
                 'description': '需要其他助手完成的子任务，只返回主控，不能自行执行。'},
}, 'required': ['status', 'summary', 'requests']}
TASK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'version': {'type': 'integer', 'enum': [1]}, 'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 64},
    **REQUEST_SCHEMA['properties'],
}, 'required': ['version', 'request_id', *REQUEST_SCHEMA['required']]}
RESULT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'version': {'type': 'integer', 'enum': [1]},
    'request_id': {'type': 'string'}, 'agent': {'type': 'string', 'enum': WORKERS},
    **REPORT_SCHEMA['properties'],
    'evidence': {'type': 'array', 'maxItems': 256, 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
        'tool': {'type': 'string'}, 'call_id': {'type': 'string'}, 'result': {'type': 'object'},
    }, 'required': ['tool', 'call_id', 'result']}},
}, 'required': ['version', 'request_id', 'agent', 'status', 'summary', 'requests', 'evidence']}


def validate(value, schema):
    kind = schema['type']
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    if type(value) is not types[kind]: raise ValueError('Agent 协议类型错误：' + kind)
    if 'enum' in schema and value not in schema['enum']: raise ValueError('Agent 协议枚举值无效。')
    if kind == 'object':
        fields = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value): raise ValueError('Agent 协议缺少必填字段。')
        if schema.get('additionalProperties') is False and set(value) - set(fields): raise ValueError('Agent 协议包含未知字段。')
        for key, item in value.items():
            if key in fields: validate(item, fields[key])
    elif kind == 'array':
        if len(value) > schema.get('maxItems', 128): raise ValueError('Agent 协议列表超过上限。')
        for item in value: validate(item, schema['items'])
    elif kind == 'string':
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 24000): raise ValueError('Agent 协议字符串长度无效。')
        if schema.get('minLength') and not value.strip(): raise ValueError('Agent 协议内容不能为空。')


def control_tool(name, description, parameters):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': parameters}}


DISPATCH_TOOL = control_tool('agent_dispatch', '主控专用：将子任务交给一个独立助手；返回统一结果和证据。助手协作请求必须由主控审核后通过此工具转接，不递归自动转发。', REQUEST_SCHEMA)
REPORT_TOOL = control_tool('agent_report', '助手专用：向主控返回统一报告并结束本次执行。需要其他助手时返回 needs_handoff 与 requests，不能直接调用其他助手。证据由执行器附加。', REPORT_SCHEMA)


def request_signature(request):
    return json.dumps([request['agent'], request['objective'], request['context']], ensure_ascii=False, sort_keys=True)
