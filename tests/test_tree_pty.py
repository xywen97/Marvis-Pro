"""Exercise actual curses input decoding in an isolated pseudo-terminal."""
import json
import os
import pty
import select
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
import fcntl
import termios

CHILD = '''
import curses,json,sys,tempfile,time
from pathlib import Path
from marvis.runtime import Runtime
from marvis.tui import Terminal
from marvis.capabilities import Capabilities
from llm_client import ChatResult
output=Path(sys.argv[1])
class Model:
 configured=True
 model="tree-fixture"
 def chat(self,messages,**kwargs):
  if messages[-1]['role']=='user':
   output.with_suffix('.context').write_text(json.dumps(messages,ensure_ascii=False))
   return ChatResult.success('公开行动说明：先检查磁盘，再查看内存。',tool_calls=[{'id':'disk','type':'function','function':{'name':'agent_inspect','arguments':'{}'}},{'id':'memory','type':'function','function':{'name':'agent_inspect','arguments':'{}'}}])
  return ChatResult.success('检查完毕。')
class UI(Terminal):
 def select_tree(self,node_id,summarize=False):
  super().select_tree(node_id,summarize)
  path=self.runtime.store.entry_branch(self.session)
  output.write_text(json.dumps({'session':self.session,'input':self.input,'kind':path[-1]['kind'] if path else 'root','nodes':len(self.runtime.session_tree(self.session)['nodes'])},ensure_ascii=False))
with tempfile.TemporaryDirectory() as directory:
 r=Runtime(directory,offline=True,config={'evaluation': {'enabled': False}});r.offline=False;r.model=lambda settings:Model()
 Capabilities.execute=lambda self,name,args,*rest:{'demo':True}
 sid=r.new_session();task=r.submit('执行评估',sid)
 while task['id'] in r.cancels:time.sleep(.01)
 try:curses.wrapper(UI(r).run)
 finally:r.close()
'''

class TreePTYTests(unittest.TestCase):
    def test_arrows_enter_editor_and_same_session_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'selection.json'; master,slave=pty.openpty()
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',30,120,0,0))
            process=subprocess.Popen([str(Path(__file__).parents[1]/'.venv/bin/python'),'-c',CHILD,str(output)],stdin=slave,stdout=slave,stderr=slave,cwd=Path(__file__).parents[1],env={**os.environ,'TERM':'xterm-256color'})
            os.close(slave); captured=bytearray()
            def pump():
                if select.select([master],[],[],.03)[0]:
                    try:captured.extend(os.read(master,65536))
                    except OSError:pass
            def wait(predicate):
                until=time.monotonic()+5
                while time.monotonic()<until:
                    pump()
                    if predicate():return
                self.fail('PTY interaction timed out: '+captured[-500:].decode('utf-8','replace'))
            def write(text):os.write(master,text.encode());time.sleep(.08);pump()
            def selection(kind):
                def ready():
                    try:return json.loads(output.read_text())['kind']==kind
                    except (FileNotFoundError,json.JSONDecodeError):return False
                wait(ready);return json.loads(output.read_text())
            try:
                wait(lambda:'MarvisXY'.encode() in captured)
                write('/tree\r');wait(lambda:'执行步骤树'.encode() in captured)
                wait(lambda:'清空编辑器'.encode() in captured)
                self.assertIn(b'tool call',captured);self.assertIn(b'tool result',captured)
                Path('/tmp/marvis-tree-screen.ansi').write_bytes(captured)
                write('\x1bOA'*3+'\r');result=selection('tool_result')
                self.assertEqual(result['input'],'');sid=result['session']
                write('/tree\r');write('\x1bOA'*2+'\r');thought=selection('thinking')
                self.assertEqual(thought['input'],'')
                write('/tree\r');write('\x1bOA\r');user=selection('root')
                self.assertEqual(user['input'],'执行评估');self.assertEqual(user['session'],sid)
                output.with_suffix('.context').unlink()
                write('（修改）\r');wait(lambda:output.with_suffix('.context').exists())
                history=json.loads(output.with_suffix('.context').read_text())
                self.assertEqual([m['content'] for m in history if m['role']=='user'],['执行评估（修改）'])
                self.assertNotIn('检查完毕',str(history))
                # Wait for completion before reopening the tree.
                time.sleep(.15);write('/tree\r');write('\x1bOA\r')
                latest=selection('tool_result');self.assertEqual(latest['session'],sid)
                self.assertGreater(latest['nodes'],user['nodes'])
                write('/q\r');wait(lambda:process.poll() is not None);self.assertEqual(process.returncode,0)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                os.close(master)
