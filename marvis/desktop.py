"""Native macOS desktop host using WKWebView through pywebview."""
from __future__ import annotations
import importlib.util


def launch(runtime):
    if importlib.util.find_spec('webview') is None:
        raise RuntimeError('桌面 App 需要 pywebview。请运行：python3 -m pip install -r requirements-desktop.txt\n也可先运行 python3 main.py --web 使用相同工作台界面。')
    import webview
    from marvis.server import serve
    from marvis.file_picker import FilePicker
    picker = FilePicker(runtime)
    server, url = serve(runtime, file_picker=picker.choose)
    print('MarvisXY 本机工作台：' + url, flush=True)
    try:
        picker.window = webview.create_window('MarvisXY · 个人 AI 工作台', url, width=1380, height=900, min_size=(900, 650), background_color='#f8fafb')
        webview.start(debug=False)
    finally:
        server.shutdown(); server.server_close()
