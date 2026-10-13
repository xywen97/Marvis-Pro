import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marvis.runtime import Runtime
from marvis.server import serve
with tempfile.TemporaryDirectory() as directory:
    runtime = Runtime(directory, config={'llm': {'env_file': str(Path(directory) / '.env')}, 'evaluation': {'enabled': False}}, offline=True)
    server, url = serve(runtime)
    print(json.dumps({'url': url}), flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command['action'] == 'memory_pending':
                session = runtime.new_session()
                runtime.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', ('memory-ui-task', session, '保存记忆测试', 'completed', '正在保存', 1, 1))
                runtime.store.execute("INSERT INTO memory_outbox(task_id,session_id,remote_session,origin_task,status,updated) VALUES (?,?,?,?,?,?)", ('memory-ui-save', session, 'remote', 'memory-ui-task', 'extracting', 1))
            elif command['action'] == 'memory_saved':
                runtime.store.execute("UPDATE memory_outbox SET status='completed',memory_writes=2,updated=2 WHERE task_id='memory-ui-save'")
    finally: server.shutdown(); server.server_close(); runtime.close()
