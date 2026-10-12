"""MarvisXY: native desktop, full-screen terminal and shared local engine."""
from __future__ import annotations
import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from marvis.paths import default_directory, default_config, env_path, bundled


def load_config(path):
    try:
        import yaml
        with open(path, encoding='utf-8') as f: config = yaml.safe_load(f) or {}
        config.setdefault('llm', {}).setdefault('env_file', str(env_path()))
        return config
    except ImportError:
        return {'llm': {'env_file': str(env_path())}}
    except FileNotFoundError:
        return {'llm': {'env_file': str(env_path())}}


def run_once(runtime, text, session_id=None):
    task = runtime.submit(text, session_id)
    seen = set()
    while True:
        state = runtime.snapshot()
        current = next(t for t in state['tasks'] if t['id'] == task['id'])
        if current['approval'] and task['id'] not in seen:
            print('\n执行计划：\n' + json.dumps(current['approval'], ensure_ascii=False, indent=2))
            try: answer = input('确认执行？输入 yes 同意，其余输入拒绝：').strip().lower()
            except EOFError: answer = 'no'
            runtime.approve(task['id'], answer == 'yes'); seen.add(task['id'])
        if current['status'] in ('completed','failed','cancelled','interrupted'):
            print(current['result']);return current
        time.sleep(.1)


def main():
    if sys.version_info < (3, 12):
        raise SystemExit('MarvisXY 需要 Python 3.12+；请重新创建项目 .venv 并安装依赖。')
    parser = argparse.ArgumentParser(description='MarvisXY macOS 桌面 App 与交互式终端')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--app', action='store_true', help='启动 macOS WKWebView 桌面 App')
    modes.add_argument('--web', action='store_true', help='启动并打开本机浏览器工作台')
    modes.add_argument('--serve', action='store_true', help='只启动后台 API 与工作台，输出访问 URL')
    modes.add_argument('--tui', action='store_true', help='启动全屏 TUI（F1–F5 页面）')
    modes.add_argument('--interactive', action='store_true', help='启动逐行交互 CLI')
    parser.add_argument('--config', default=str(default_config()))
    parser.add_argument('--data-dir', default=str(default_directory()), help='持久化数据目录')
    parser.add_argument('--diagnose-bundle', action='store_true', help='检查独立运行组件，不启动服务')
    parser.add_argument('--engine', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=0, help='服务端口，默认自动分配')
    parser.add_argument('--offline', action='store_true', help='禁用模型调用；网络工具仍由运行模式控制')
    parser.add_argument('--routes', action='store_true', help='查看工具与旧版路由规则')
    parser.add_argument('text', nargs='*', help='单轮自然语言或 /tool TOOL JSON 指令')
    args = parser.parse_args(); config = load_config(args.config)
    if args.diagnose_bundle:
        import importlib.util
        checks = {name: importlib.util.find_spec(name) is not None for name in ('webview', 'openviking_cli', 'llama_cpp', 'yaml')}
        checks['embedding_model'] = (ROOT / 'models' / 'bge-small-zh-v1.5-f16.gguf').is_file()
        print(json.dumps({'bundled': bundled(), 'python': sys.executable, 'checks': checks}, ensure_ascii=False))
        return 0 if all(checks.values()) else 1
    if args.routes:
        from agent import MainAgent
        from marvis.capabilities import TOOLS
        print(MainAgent(config).describe_routing())
        print('\n结构化工具：')
        for t in TOOLS: print(t['function']['name'] + '：' + t['function']['description'])
        return 0
    from marvis.client import connect
    from marvis.runtime import Runtime
    from marvis.server import serve
    runtime = connect(args.data_dir)
    if bundled() and not args.engine and not args.serve and runtime is None:
        from marvis.engine_process import start_engine
        try: runtime = start_engine(args.data_dir, args.config, args.offline)
        except RuntimeError as error:
            print(str(error), file=sys.stderr); return 1
    if args.engine and runtime:
        return 0
    if runtime and args.offline and not runtime.settings()['offline']:
        print('已有引擎正在运行且启用了模型。请先退出该引擎，再以 --offline 启动。', file=sys.stderr);return 1
    try:
        runtime = runtime or Runtime(args.data_dir, config, args.offline)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    server = None
    try:
        if args.engine:
            from marvis.engine_process import run_engine
            run_engine(runtime)
        elif args.app:
            if not runtime.owned: runtime.attach()
            from marvis.desktop import launch
            launch(runtime)
        else:
            if not runtime.owned: runtime.attach()
            # Every primary UI exposes the same engine so other clients can attach.
            if runtime.owned or args.web or args.serve:
                server, url = serve(runtime, args.port)
            if args.web or args.serve:
                print('MarvisXY 工作台：' + url, flush=True)
                print('仅监听本机；按 Ctrl+C 停止。', flush=True)
                if args.web:
                    import webbrowser
                    webbrowser.open(url)
                threading.Event().wait()
            elif args.text:
                result=run_once(runtime, ' '.join(args.text))
                return 0 if result['status']=='completed' else 1
            elif args.interactive:
                sid=runtime.new_session();print('MarvisXY · 输入 exit 退出；任务与历史持久化保存。')
                while True:
                    try:text=input('你 > ').strip()
                    except EOFError:break
                    if text in ('exit','quit','退出'):break
                    if text:run_once(runtime,text,sid)
            else:
                from marvis.tui import launch
                launch(runtime)
    except KeyboardInterrupt:pass
    except (RuntimeError, ValueError) as e:
        print(str(e),file=sys.stderr);return 1
    finally:
        if server:server.shutdown();server.server_close()
        runtime.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
