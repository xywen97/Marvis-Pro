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
    try: sys.stdin.read()
    finally: server.shutdown(); server.server_close(); runtime.close()
