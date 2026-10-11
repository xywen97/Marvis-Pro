import unittest
from unittest.mock import Mock
from marvis.tui import Terminal, COMMANDS


class CompletionTests(unittest.TestCase):
    def setUp(self):
        runtime = Mock()
        runtime.sessions.return_value = [{'id': 'session'}]
        self.terminal = Terminal(runtime)

    def enter(self, text):
        self.terminal.input = text
        self.terminal.cursor = len(text)
        return self.terminal.suggestions()

    def test_unique_prefix_completes_without_executing(self):
        self.assertEqual([c[0] for c in self.enter('/ne')], ['/new'])
        self.assertTrue(self.terminal.complete())
        self.assertEqual(self.terminal.input, '/new ')
        self.terminal.runtime.new_session.assert_not_called()
        self.assertEqual(self.terminal.suggestions(), [])

    def test_selection_and_prefix_change(self):
        matches = self.enter('/work')
        self.assertEqual(len(matches), 4)
        self.terminal.completion_index = 2
        self.terminal.complete()
        self.assertEqual(self.terminal.input, matches[2][0] + ' ')
        self.enter('/mo')
        self.assertEqual(self.terminal.completion_index, 0)
        self.assertEqual([c[0] for c in self.terminal.suggestions()], ['/mode', '/model'])

    def test_no_completion_in_arguments_or_natural_language(self):
        for text in ('你好', '/files /ne', '/nonexistent', '/new '):
            self.assertEqual(self.enter(text), [])
            self.assertFalse(self.terminal.complete())
        self.enter('/ne'); self.terminal.cursor = 2
        self.assertEqual(self.terminal.suggestions(), [])

    def test_dismiss_until_input_changes(self):
        self.enter('/n'); self.terminal.completion_hidden = True
        self.assertEqual(self.terminal.suggestions(), [])
        self.assertEqual([c[0] for c in self.enter('/ne')], ['/new'])

    def test_all_commands_have_usage_and_description(self):
        matches = self.enter('/')
        self.assertEqual(matches, COMMANDS)
        self.assertEqual(len({c[0] for c in matches}), len(matches))
        self.assertTrue(all(c[1] and c[2] for c in matches))
