import curses
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
from unittest.mock import Mock, patch
import fcntl
import termios

from marvis.tui import Terminal


class MouseScrollTests(unittest.TestCase):
    def setUp(self):
        runtime = Mock()
        runtime.sessions.return_value = [{'id': 's'}]
        self.ui = Terminal(runtime)
        self.ui.scroll_limit = 40

    def wheel(self, buttons):
        with patch('curses.getmouse', return_value=(0, 10, 10, 0, buttons)):
            self.ui.mouse_event()

    def test_history_wheel_direction_bounds_and_editor(self):
        self.ui.input = '尚未发送的草稿'; self.ui.cursor = 4
        up, down = self.ui.wheel_buttons()
        self.ui.result_focus = 'old-tool'
        self.wheel(up); self.assertEqual(self.ui.scroll, 3)
        self.assertIsNone(self.ui.result_focus)
        self.wheel(down); self.assertEqual(self.ui.scroll, 0)
        self.wheel(down); self.assertEqual(self.ui.scroll, 0)
        for _ in range(20): self.wheel(up)
        self.assertEqual(self.ui.scroll, 40)
        self.assertEqual(self.ui.input, '尚未发送的草稿')
        self.assertEqual(self.ui.cursor, 4)

    def test_help_and_other_pages_scroll_from_top(self):
        for page, text in ((0, 'help'), (1, ''), (2, ''), (3, ''), (4, '')):
            self.ui.page = page; self.ui.local_text = text; self.ui.scroll = 0
            up, down = self.ui.wheel_buttons()
            self.wheel(down); self.assertEqual(self.ui.scroll, 3)
            self.wheel(up); self.assertEqual(self.ui.scroll, 0)

    def test_tree_wheel_selects_without_switching_session(self):
        self.ui.navigator = Mock(label_edit=None)
        up, down = self.ui.wheel_buttons()
        self.wheel(up); self.ui.navigator.move.assert_called_with(-3)
        self.wheel(down); self.ui.navigator.move.assert_called_with(3)
        self.ui.runtime.navigate_tree.assert_not_called()

    def test_click_still_expands_tool_result(self):
        self.ui.mouse_rows = {10: 'tool-result'}
        self.wheel(curses.BUTTON1_CLICKED)
        self.assertIn('tool-result', self.ui.expanded_results)

    def test_live_output_keeps_history_position_then_follows_bottom(self):
        self.ui.cached_state = {'settings': {'mode': 'local'}, 'tasks': []}
        self.ui.state_time = time.monotonic()
        self.ui.animator.apply = lambda state, now: state
        self.ui.messages = lambda: [True]
        lines = ['line ' + str(i) for i in range(80)]
        from marvis.transcript import Line
        self.ui.dialogue = lambda state: [Line(line) for line in lines]
        screen = Mock(); screen.getmaxyx.return_value = (30, 120)
        with patch('curses.color_pair', return_value=0), patch('curses.doupdate'):
            self.ui.draw(screen); self.ui.scroll_by(-1)
            self.ui.draw(screen)
            before = self.ui.last_line_count - 21 - self.ui.scroll
            lines.extend(['new output'] * 7)
            self.ui.draw(screen)
            self.assertEqual(self.ui.last_line_count - 21 - self.ui.scroll, before)
            self.ui.scroll_by(1, 1000); self.assertEqual(self.ui.scroll, 0)
            lines.append('latest'); self.ui.draw(screen)
            self.assertEqual(self.ui.scroll, 0)


CHILD = '''
import curses,json,sys,tempfile
from pathlib import Path
from marvis.runtime import Runtime
from marvis.tui import Terminal
output=Path(sys.argv[1])
class UI(Terminal):
 def messages(self):
  return [{'role':'assistant','content':'\\n'.join('history line %03d'%i for i in range(100)),'task_id':None}]
 def draw(self,screen):
  super().draw(screen)
  output.write_text(json.dumps({'scroll':self.scroll,'limit':self.scroll_limit,'first':screen.instr(5,3).decode('utf-8','replace')}))
with tempfile.TemporaryDirectory() as directory:
 r=Runtime(directory,offline=True)
 try:curses.wrapper(UI(r).run)
 finally:r.close()
'''


class MousePTYTests(unittest.TestCase):
    def test_actual_terminal_wheel_reports_in_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'viewport.json'; master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
            root = Path(__file__).parents[1]
            process = subprocess.Popen([str(root / '.venv/bin/python'), '-c', CHILD, str(output)],
                                       stdin=slave, stdout=slave, stderr=slave, cwd=root,
                                       env={**os.environ, 'TERM': 'xterm-256color'})
            os.close(slave); captured = bytearray()
            def wait(predicate):
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    if select.select([master], [], [], .03)[0]:
                        try: captured.extend(os.read(master, 65536))
                        except OSError: pass
                    try:
                        state = json.loads(output.read_text())
                        if predicate(state): return state
                    except (FileNotFoundError, json.JSONDecodeError): pass
                self.fail('Mouse PTY timed out: ' + captured[-500:].decode('utf-8', 'replace'))
            def wheel(button):
                # xterm mode 1000 used by macOS ncurses; up=64, down=65.
                os.write(master, b'\x1b[M' + bytes([32 + button, 43, 43]))
            try:
                initial = wait(lambda state: state['limit'] > 0)
                wheel(64); older = wait(lambda state: state['scroll'] == 3)
                self.assertNotEqual(initial['first'], older['first'])
                wheel(65); bottom = wait(lambda state: state['scroll'] == 0)
                self.assertEqual(initial['first'], bottom['first'])
                os.write(master, b'/q\r')
                wait(lambda state: process.poll() is not None)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
                os.close(master)
