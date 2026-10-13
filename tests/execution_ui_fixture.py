"""Deterministic real file/command flow for approval UI regression checks."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marvis.runtime import Runtime
from marvis.server import serve

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    runtime = Runtime(root / 'data', offline=True, config={'evaluation': {'enabled': False}})
    runtime.update_settings({'roots': [str(root)]})
    def chat(ident, session, prompt, caps, settings):
        target = root / ('single.py' if 'single' in prompt else 'scoped.py')
        runtime._tool(ident, caps, 'file_write', {'path': str(target), 'text': 'assert 1 + 1 == 2\nprint("verified")\n'})
        runtime._tool(ident, caps, 'code_execute', {'argv': ['python3', str(target)], 'cwd': str(root)})
        return 'saved and verified'
    runtime._chat = chat
    server, url = serve(runtime)
    print(json.dumps({'url': url}), flush=True)
    try: sys.stdin.read()
    finally:
        server.shutdown(); server.server_close(); runtime.close()
