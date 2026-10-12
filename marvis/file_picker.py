"""Native local file selection for desktop and browser clients."""
from __future__ import annotations
import json
import subprocess
import sys
import threading
from pathlib import Path
from marvis.capabilities import SKIP

PICK_SCRIPT = '''
const app = Application.currentApplication();
app.includeStandardAdditions = true;
try {
    const files = app.chooseFile({withPrompt: '选择要添加到对话的文件', multipleSelectionsAllowed: true});
    JSON.stringify(files.map(file => file.toString()));
} catch (error) {
    if (error.errorNumber === -128) JSON.stringify([]);
    else throw error;
}
'''


class FilePicker:
    def __init__(self, runtime):
        self.runtime = runtime; self.window = None; self.lock = threading.Lock()

    def choose(self):
        if not self.lock.acquire(blocking=False): raise ValueError('文件选择窗口已经打开。')
        try:
            settings = self.runtime.settings()
            if self.window is not None:
                import webview
                selected = self.window.create_file_dialog(webview.FileDialog.OPEN,
                    directory=settings['roots'][0], allow_multiple=True)
            else:
                if sys.platform != 'darwin': raise ValueError('系统文件选择目前仅支持 macOS。')
                result = subprocess.run(['osascript', '-l', 'JavaScript', '-e', PICK_SCRIPT],
                                        capture_output=True, text=True, timeout=180, shell=False)
                if result.returncode: raise ValueError('无法打开系统文件选择窗口，请检查 macOS 权限。')
                selected = json.loads(result.stdout or '[]')
            if not selected: return {'paths': []}
            if not isinstance(selected, (list, tuple)) or len(selected) > 100: raise ValueError('一次最多选择 100 个文件。')
            # Re-read settings after a potentially long-running dialog.
            settings = self.runtime.settings()
            roots = [Path(root).expanduser().resolve() for root in settings['roots']]
            data_dir = Path(settings['data_dir']).resolve(); paths = []
            for value in selected:
                p = Path(value).expanduser().resolve()
                if not p.is_file(): raise ValueError('所选文件已不存在：' + str(p))
                if not any(root == p or root in p.parents for root in roots):
                    raise ValueError('所选文件不在授权目录内，请先在设置中添加其文件夹：' + str(p.parent))
                if data_dir == p or data_dir in p.parents or any(part in SKIP or part.startswith('.env') for part in p.parts):
                    raise ValueError('不能添加凭据或助手内部文件。')
                if str(p) not in paths: paths.append(str(p))
            return {'paths': paths}
        except subprocess.TimeoutExpired:
            raise ValueError('文件选择已超时，请重新选择。')
        finally: self.lock.release()
