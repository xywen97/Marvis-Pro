"""Local delayed SSE model and UI server for the browser streaming regression test."""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm_client import LLMClient
from marvis.runtime import Runtime
from marvis.server import serve

PARTS = ['## 流式验证\n\n', '这是第一段，', '这是第二段，', '最后一段。\n\n', '```python\n', 'print("逐步显示")\n', '```']
if '--burst-tail' in sys.argv: PARTS.append('\n\n' + '最后的大段内容仍需连续显示。'*200)
MULTI_TOOLS = '--multi-tools' in sys.argv
TEAM_TOOLS = '--team-tools' in sys.argv
EVALUATION = '--evaluation' in sys.argv
WITH_TOOLS = '--tools' in sys.argv or MULTI_TOOLS or TEAM_TOOLS
class Model(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert request['stream'] is True
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        try:
            def send(delta):
                self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':delta}]})+'\n\n').encode());self.wfile.flush();time.sleep(.35)
            if TEAM_TOOLS:
                names = {t['function']['name'] for t in request.get('tools', [])}
                main = 'agent_dispatch' in names
                if EVALUATION and not names:
                    send({'content': json.dumps({'score': 10, 'reason': '任务与证据一致，交付可使用。'}, ensure_ascii=False)})
                elif main and request['messages'][-1]['role'] == 'user':
                    send({'content': '我先安排电脑管家检查磁盘和内存。'})
                    send({'tool_calls': [{'index': 0, 'id': 'dispatch-computer', 'function': {
                        'name': 'agent_dispatch', 'arguments': json.dumps({'agent': 'computer', 'objective': '检查磁盘和内存', 'context': ''})}}]})
                elif not main and request['messages'][-1]['role'] == 'user':
                    for index, section in enumerate(('disk', 'memory')):
                        send({'tool_calls': [{'index': index, 'id': 'check-' + section, 'function': {
                            'name': 'system_info', 'arguments': json.dumps({'section': section})}}]})
                elif not main:
                    send({'tool_calls': [{'index': 0, 'id': 'computer-report', 'function': {
                        'name': 'agent_report', 'arguments': json.dumps({'status': 'completed', 'summary': '磁盘和内存检查完成', 'requests': []})}}]})
                else:
                    for part in PARTS: send({'content': part})
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush(); return
            if WITH_TOOLS and request['messages'][-1]['role']=='user':
                for text in ['我先','检查本机的','磁盘和内存，','随后整理检查结果。']:
                    send({'content':text})
                for index, section in enumerate(('disk','memory')):
                    send({'tool_calls':[{'index':index,'id':f'call-{index}','function':{'name':'system_info','arguments':'{"section":'}}]})
                    send({'tool_calls':[{'index':index,'function':{'arguments':json.dumps(section)+'}'}}]})
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n');self.wfile.flush();return
            last_user = max(i for i, message in enumerate(request['messages']) if message['role']=='user')
            if MULTI_TOOLS and sum(message['role']=='tool' for message in request['messages'][last_user:])==2:
                for text in ['初步检查已完成，','我再复核','电池状态，','然后给出完整总结。']:
                    send({'content':text})
                send({'tool_calls':[{'index':0,'id':'call-battery','function':{'name':'system_info','arguments':'{"section":"battery"}'}}]})
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n');self.wfile.flush();return
            for part in PARTS:
                chunk={'choices':[{'index':0,'delta':{'content':part}}]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode());self.wfile.flush();time.sleep(.4)
            self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');self.wfile.flush()
        except OSError: pass

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        model=ThreadingHTTPServer(('127.0.0.1',0),Model)
        threading.Thread(target=model.serve_forever,daemon=True).start()
        runtime=Runtime(folder, config={'evaluation': {'enabled': EVALUATION}})
        if '--empty-duplicates' in sys.argv:
            runtime.store.session();runtime.store.session()
        if WITH_TOOLS:
            from marvis.capabilities import Capabilities
            original=Capabilities.execute
            def delayed(self, name, arguments, plan=None):
                if name=='system_info':time.sleep(.5)
                return original(self,name,arguments,plan)
            Capabilities.execute=delayed
        runtime.model=lambda settings:LLMClient(base_url=f'http://127.0.0.1:{model.server_port}/v1',api_key='test',model='delayed-test',env_file=Path(folder)/'empty.env')
        server,url=serve(runtime)
        print(json.dumps({'url':url,'expected':''.join(PARTS)}),flush=True)
        try:
            if '--tui' in sys.argv:
                from marvis.tui import launch
                launch(runtime)
            else: sys.stdin.read()
        finally: server.shutdown();server.server_close();runtime.close();model.shutdown();model.server_close()
