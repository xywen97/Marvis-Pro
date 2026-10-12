"""Independent specialist loops; only main can dispatch or bridge a handoff."""
from __future__ import annotations
import json

from marvis.agent_protocol import DISPATCH_TOOL, REPORT_TOOL, REQUEST_SCHEMA, REPORT_SCHEMA, RESULT_SCHEMA, validate, request_signature
from marvis.capabilities import TOOLS
from marvis.prompts import build_system_prompt, build_worker_prompt
from marvis.store import uid
from marvis.team import TOOL_AGENTS, AGENT_STRATEGIES, tool_message


class Coordinator:
    def __init__(self, runtime, ident, session_id, caps, settings):
        self.runtime, self.ident, self.session_id = runtime, ident, session_id
        self.caps, self.settings = caps, settings
        self.options = runtime.config.get('orchestration') or {}
        self.main_rounds = self.limit('max_rounds', 8, 24)
        self.dispatch_limit = self.limit('max_dispatches', 12, 32)
        self.model_limit = self.limit('max_model_calls', 64, 128)
        self.tool_limit = self.limit('max_tool_calls', 128, 256)
        self.model_calls = 0; self.tool_calls = 0; self.dispatches = 0
        self.signatures = {}; self.failures = {}; self.call_ids = set(); self.completed_requests = set(); self.results = []
        self.available = [t for t in TOOLS if not (settings['mode'] == 'local' and t['function']['name'] in ('browser_read', 'browser_open', 'web_search'))]

    def limit(self, key, default, maximum):
        return max(1, min(maximum, int(self.options.get(key, default))))

    def turn(self, client, messages, tools, actor, request_id=None, final=False):
        self.runtime._check(self.ident)
        if self.model_calls >= self.model_limit: raise ValueError('达到任务共享模型调用上限。')
        self.model_calls += 1
        return self.runtime._model_turn(self.ident, client, messages, self.model_calls,
                                        tool_choice='none' if final else 'auto', tools=tools,
                                        agent=actor, request_id=request_id)

    def normalize(self, result):
        if not result.ok: raise ValueError('模型调用失败：' + result.error)
        if len(result.tool_calls) > 12: raise ValueError('模型单次工具请求超过限制。')
        calls = []
        for raw in result.tool_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get('function'), dict): raise ValueError('模型返回无效工具调用结构。')
            call = {**raw, 'function': dict(raw['function']), 'type': 'function'}
            if not isinstance(call['function'].get('name'), str) or not call['function']['name']:
                raise ValueError('模型工具名称必须是非空字符串。')
            if not isinstance(call.get('id'), str) or not call['id'] or call['id'] in self.call_ids: call['id'] = uid()
            self.call_ids.add(call['id']); calls.append(call)
        return calls

    def remember_ids(self, messages):
        for message in messages:
            for call in message.get('tool_calls', []):
                if call.get('id'): self.call_ids.add(call['id'])

    def physical_tool(self, actor, call, request_id=None):
        self.tool_calls += 1
        if self.tool_calls > self.tool_limit: raise ValueError('达到任务共享工具调用上限。')
        fn = call['function']; name = fn.get('name'); args = json.loads(fn.get('arguments') or '{}')
        # Enforce ownership even if a provider invents an unavailable function.
        if TOOL_AGENTS.get(name) != actor: raise PermissionError('不能调用其他 Agent 的工具：' + str(name))
        if name not in {t['function']['name'] for t in self.available}: raise PermissionError('此运行模式没有提供该工具。')
        signature = json.dumps([name, args], ensure_ascii=False, sort_keys=True)
        if self.failures.get(signature, 0) >= 2: raise ValueError('相同参数已失败两次，停止重复执行；请调整参数或解释失败原因。')
        try:
            out = self.runtime._tool(self.ident, self.caps, name, args, step=self.model_calls,
                                     call_id=call['id'], scope='worker' if actor != 'main' else None,
                                     request_id=request_id)
            self.failures.pop(signature, None)
            return out
        except Exception:
            self.failures[signature] = self.failures.get(signature, 0) + 1
            raise

    def record_error(self, actor, call, error, request_id=None):
        if any(e['kind'] == 'tool_error' and e['content'].get('call_id') == call['id']
               for e in self.runtime.store.events(self.ident)): return
        metadata = {'agent': actor, 'tool': call['function'].get('name'), 'call_id': call['id'], 'step': self.model_calls}
        if actor != 'main': metadata.update(scope='worker', request_id=request_id)
        self.runtime.store.event(self.ident, 'tool_start', {**metadata, 'arguments': call['function'].get('arguments', '')})
        self.runtime.store.event(self.ident, 'tool_error', {**metadata, 'text': str(error)})

    def dispatch(self, request):
        validate(request, REQUEST_SCHEMA)
        actor = request['agent']
        if self.dispatches >= self.dispatch_limit: raise ValueError('达到主控调度上限，停止转接。')
        if request.get('continuation_of') and request['continuation_of'] not in self.completed_requests:
            raise ValueError('continuation_of 必须指向当前任务已返回的请求。')
        owned = [t for t in self.available if TOOL_AGENTS[t['function']['name']] == actor]
        if not owned: raise ValueError('此模式下目标助手没有可用工具。')
        signature = request_signature(request)
        if self.signatures.get(signature, 0) >= 2: raise ValueError('相同助手任务已调度两次，停止循环转接。')
        self.signatures[signature] = self.signatures.get(signature, 0) + 1
        self.dispatches += 1
        request_id = uid()
        self.runtime.store.event(self.ident, 'agent_start', {'agent': actor, 'request_id': request_id, 'request': request})
        rounds_config = self.options.get('agent_rounds') or {}
        rounds = max(1, min(16, int(rounds_config.get(actor, AGENT_STRATEGIES[actor]['rounds']))))
        tools = owned + [REPORT_TOOL]
        system = build_worker_prompt(actor, self.settings, tools, rounds)
        history = []
        for node in self.runtime.store.entry_branch(self.session_id):
            if node['kind'] == 'agent_context' and node['data'].get('agent') == actor:
                history = node['data']['messages']
            if node['kind'] == 'user' and node['task_id'] != self.ident:
                task = self.runtime.store.one('SELECT status FROM tasks WHERE id=?', (node['task_id'],))
                if task and task['status'] == 'queued': break
        messages = [{'role': 'system', 'content': system}] + history + [{'role': 'user', 'content': json.dumps(
            {'version': 1, 'request_id': request_id, **request}, ensure_ascii=False)}]
        self.remember_ids(messages)
        evidence = []
        report = None
        client = self.runtime.model(self.settings)
        try:
            for _ in range(rounds):
                result = self.turn(client, messages, tools, actor, request_id)
                calls = self.normalize(result)
                if not calls:
                    if not result.content.strip(): raise ValueError('助手模型返回空响应。')
                    messages.extend([{'role': 'assistant', 'content': result.content},
                                     {'role': 'user', 'content': '请用 agent_report 提交协议报告，明确证据与未完成部分。'}])
                    continue
                # A terminal report cannot coexist with physical changes in one batch.
                terminal = [c for c in calls if c['function'].get('name') == 'agent_report']
                messages.append({'role': 'assistant', 'content': result.content, 'tool_calls': calls})
                for call in calls:
                    try:
                        if terminal and len(calls) != 1: raise ValueError('agent_report 必须单独调用，当前批次未执行。')
                        if call['function'].get('name') == 'agent_report':
                            candidate = json.loads(call['function'].get('arguments') or '{}')
                            validate(candidate, REPORT_SCHEMA)
                            if any(r['agent'] == actor for r in candidate['requests']): raise ValueError('助手不能请求自己。')
                            if candidate['requests'] and candidate['status'] != 'needs_handoff': raise ValueError('协作请求必须标记 needs_handoff。')
                            if candidate['status'] == 'needs_handoff' and not candidate['requests']: raise ValueError('needs_handoff 必须提供请求。')
                            for request_item in candidate['requests']:
                                if request_item.get('continuation_of') not in (None, request_id): raise ValueError('助手协作只能关联本次 request_id。')
                            report = candidate; out = {'accepted': True}
                        else:
                            out = self.physical_tool(actor, call, request_id)
                            evidence.append({'tool': call['function']['name'], 'call_id': call['id'], 'result': out})
                    except Exception as error:
                        self.runtime._check(self.ident)
                        self.record_error(actor, call, error, request_id)
                        out = {'error': str(error)}
                        evidence.append({'tool': str(call['function'].get('name', '')), 'call_id': call['id'], 'result': out})
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': tool_message(out)})
                if report: break
            if report is None:
                report = {'status': 'partial', 'summary': '助手执行预算已用完，尚未提交有效完成报告；请主控检查已有证据。', 'requests': []}
            if report['status'] == 'completed' and any('error' in e['result'] or e['result'].get('rejected') for e in evidence):
                report['status'] = 'partial'
                report['summary'] += '\n执行记录包含失败或拒绝，不能确认全部完成。'
        except Exception as error:
            self.runtime._check(self.ident)
            report = {'status': 'failed', 'summary': str(error), 'requests': []}
        envelope = {'version': 1, 'request_id': request_id, 'agent': actor, **report, 'evidence': evidence}
        envelope['summary'] = envelope['summary'][:12000]
        validate(envelope, RESULT_SCHEMA)
        # Private provider context is attached to an execution-tree anchor. Branch
        # navigation/forking naturally selects only the corresponding ancestry.
        try: saved = self.runtime.bounded_context(messages)[1:]
        except ValueError:
            saved = [messages[-1]] if messages[-1]['role'] == 'user' else []
            saved.append({'role': 'assistant', 'content': '先前助手上下文超过预算，仅保留统一报告：' + tool_message(envelope)})
        self.runtime.store.event(self.ident, 'agent_context', {'agent': actor, 'request_id': request_id, 'messages': saved})
        self.runtime.store.event(self.ident, 'agent_result', {'agent': actor, 'request_id': request_id, 'result': envelope})
        self.completed_requests.add(request_id)
        self.results.append(envelope)
        return envelope

    def budget_summary(self):
        return '已达到任务共享调用上限，停止执行；任务可能尚未全部完成。\n\n' + '\n'.join(
            f"{r['agent']}（{r['status']}）：{r['summary']}" for r in self.results)

    def run(self):
        client = self.runtime.model(self.settings)
        tools = [t for t in self.available if TOOL_AGENTS[t['function']['name']] == 'main'] + [DISPATCH_TOOL]
        system = build_system_prompt(self.settings, self.runtime.config.get('persona') or {}, tools, self.main_rounds)
        system += f'\n共享预算：调度 {self.dispatch_limit} 次，模型调用 {self.model_limit} 次，实体工具调用 {self.tool_limit} 次。'
        messages = [{'role': 'system', 'content': system}] + self.runtime.context_messages(self.session_id, self.ident)
        self.remember_ids(messages)
        for _ in range(self.main_rounds):
            if self.model_calls >= self.model_limit: return self.budget_summary()
            result = self.turn(client, messages, tools, 'main')
            calls = self.normalize(result)
            if not calls:
                if not result.content.strip(): raise ValueError('模型返回空响应，请重试。')
                return result.content.strip()
            messages.append({'role': 'assistant', 'content': result.content, 'tool_calls': calls})
            for call in calls:
                name = call['function'].get('name')
                if name == 'agent_dispatch':
                    metadata = {'agent': 'main', 'tool': name, 'call_id': call['id'], 'step': self.model_calls}
                    self.runtime.store.event(self.ident, 'tool_start', {**metadata, 'arguments': call['function'].get('arguments', '')})
                    try:
                        out = self.dispatch(json.loads(call['function'].get('arguments') or '{}'))
                        self.runtime.store.event(self.ident, 'tool_result', {**metadata, 'result': out})
                    except Exception as error:
                        self.runtime._check(self.ident); out = {'error': str(error)}
                        self.runtime.store.event(self.ident, 'tool_error', {**metadata, 'text': str(error)})
                else:
                    try: out = self.physical_tool('main', call)
                    except Exception as error:
                        self.runtime._check(self.ident); self.record_error('main', call, error); out = {'error': str(error)}
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': tool_message(out)})
        self.runtime.store.event(self.ident, 'execution_limit', {'agent': 'main', 'text': '主控轮次用完，汇总实际结果。'})
        messages.append({'role': 'system', 'content': '执行预算已用完。仅汇总真实结果和未完成事项，不调用工具。'})
        if self.model_calls >= self.model_limit:
            return self.budget_summary()
        result = self.turn(client, messages, tools, 'main', final=True)
        self.normalize(result)
        if result.tool_calls: raise ValueError('模型在执行预算用完后仍返回工具调用，未执行这些操作。')
        return result.content.strip() or '已达到执行步数上限，请查看执行记录。'
