import json
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs
from marvis.runtime import Runtime
from marvis.file_picker import FilePicker
from marvis.server import serve


class FilePickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.work = self.root/'work'; self.work.mkdir()
        self.file = self.work/'文件 with spaces.txt'; self.file.write_text('content')
        self.runtime = Runtime(self.root/'data', offline=True)
        self.runtime.update_settings({'roots': [str(self.work)]})
        self.picker = FilePicker(self.runtime); self.picker.window = Mock()
        self.webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=10))
        self.module_patch = patch.dict('sys.modules', {'webview': self.webview}); self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop(); self.runtime.close(); self.tmp.cleanup()

    def test_native_dialog_selects_multiple_and_preserves_real_paths(self):
        other = self.work/'second.md'; other.write_text('two')
        self.picker.window.create_file_dialog.return_value = [str(self.file), str(other), str(self.file)]
        self.assertEqual(self.picker.choose()['paths'], [str(self.file), str(other)])
        self.picker.window.create_file_dialog.assert_called_once_with(10, directory=str(self.work), allow_multiple=True)

    def test_cancel_and_duplicate_dialog(self):
        self.picker.window.create_file_dialog.return_value = None
        self.assertEqual(self.picker.choose(), {'paths': []})
        self.picker.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, '已经打开'): self.picker.choose()
        finally: self.picker.lock.release()

    def test_outside_sensitive_and_symlink_files_are_rejected(self):
        outside = self.root/'outside.txt'; outside.write_text('private')
        secret = self.work/'.env'; secret.write_text('secret')
        link = self.work/'link.txt'; link.symlink_to(outside)
        for p in (outside, secret, link):
            self.picker.window.create_file_dialog.return_value = [str(p)]
            with self.subTest(path=p), self.assertRaises(ValueError): self.picker.choose()

    def test_settings_are_rechecked_after_dialog(self):
        other = self.root/'other'; other.mkdir()
        def choose(*args, **kwargs):
            self.runtime.update_settings({'roots': [str(other)]})
            return [str(self.file)]
        self.picker.window.create_file_dialog.side_effect = choose
        with self.assertRaisesRegex(ValueError, '授权目录'): self.picker.choose()

    def test_browser_host_uses_native_macos_dialog(self):
        self.picker.window = None
        with patch('marvis.file_picker.sys.platform', 'darwin'), patch('marvis.file_picker.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps([str(self.file)]))) as run:
            self.assertEqual(self.picker.choose()['paths'], [str(self.file)])
            self.assertEqual(run.call_args.args[0][:3], ['osascript', '-l', 'JavaScript'])

    def test_picker_api_requires_auth_before_opening_dialog(self):
        self.picker.window.create_file_dialog.return_value = [str(self.file)]
        server, url = serve(self.runtime, file_picker=self.picker.choose)
        parsed = urlparse(url); token = parse_qs(parsed.fragment)['token'][0]
        try:
            for key in ('wrong', token):
                request = urllib.request.Request('http://'+parsed.netloc+'/api/pick-files', data=b'{}', headers={'X-Marvis-Token': key, 'Content-Type': 'application/json'})
                if key == 'wrong':
                    with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(request)
                    self.picker.window.create_file_dialog.assert_not_called()
                else:
                    with urllib.request.urlopen(request) as response: self.assertEqual(json.load(response)['paths'], [str(self.file)])
        finally: server.shutdown(); server.server_close()
