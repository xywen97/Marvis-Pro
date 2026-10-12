import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marvis.runtime import Runtime
from marvis.engine_process import run_engine
runtime = Runtime(sys.argv[1], offline=True, config={'evaluation': {'enabled': False}})
try: run_engine(runtime, idle_seconds=1)
finally: runtime.close()
