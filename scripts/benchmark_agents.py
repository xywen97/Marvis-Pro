"""Reproducible local capability benchmark; writes only to a temporary folder.

Run: .venv/bin/python scripts/benchmark_agents.py
No model calls, network requests or application launches.
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marvis.runtime import Runtime
from marvis.capabilities import Capabilities


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); work = root/'work'; work.mkdir()
        for index in range(500): (work/f'{index:04d}.md').write_text(f'Document {index}\n' + '本地索引测试。'*1000)
        runtime = Runtime(root/'data', offline=True)
        try:
            runtime.update_settings({'roots': [str(work)]}); caps = Capabilities(runtime.store)
            begin = time.perf_counter(); first = caps.execute('knowledge_index', {'path': str(work)})
            initial_ms = (time.perf_counter() - begin)*1000
            repeats = []
            with patch.object(caps, 'read', wraps=caps.read) as read:
                for _ in range(5):
                    begin = time.perf_counter(); last = caps.execute('knowledge_index', {'path': str(work)})
                    repeats.append((time.perf_counter() - begin)*1000)
                repeat_reads = read.call_count
            with patch.object(runtime.store, 'setting', wraps=runtime.store.setting) as settings:
                caps = Capabilities(runtime.store)
                caps.execute('file_list', {'path': str(work)})
                root_reads = sum(c.args[0] == 'roots' for c in settings.call_args_list)
            median = statistics.median(repeats)
            print(json.dumps({'documents': 500, 'initial_index_ms': round(initial_ms, 2),
                              'unchanged_index_median_ms': round(median, 2), 'repeat_runs': 5,
                              'repeat_document_reads': repeat_reads, 'unchanged_per_run': last['unchanged'],
                              'initial_indexed': first['indexed'], 'root_setting_reads_per_scan': root_reads,
                              'note': '冷启动与未变化重扫的比较，不代表所有工作负载的加速倍数。'}, ensure_ascii=False, indent=2))
        finally: runtime.close()


if __name__ == '__main__': main()
