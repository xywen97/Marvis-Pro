import json
from llm_client import ChatResult


def tool_reply(name, args, text=''):
    return ChatResult.success(text, tool_calls=[{'id': name, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}])


def dispatch_reply(agent='computer', objective='检查指标', context='', text=''):
    return tool_reply('agent_dispatch', {'agent': agent, 'objective': objective, 'context': context}, text)


def report_reply(summary='已检查', status='completed', requests=None):
    return tool_reply('agent_report', {'status': status, 'summary': summary, 'requests': requests or []})
