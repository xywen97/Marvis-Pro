import unittest
from unittest.mock import Mock
from marvis.tui import Terminal, COMMANDS, SHORT_COMMANDS, ALIASES, HELP


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

    def test_short_command_completion_displays_canonical_usage(self):
        for text, expected in [('/q','/quit'),('/ls','/sessions'),('/oa','/open-app'),('/wfr','/workflow-run'),('/s','/search')]:
            matches = self.enter(text)
            self.assertEqual(matches[0][0], expected)
            self.assertIn('('+SHORT_COMMANDS[expected]+')', matches[0][1])
            self.terminal.complete()
            self.assertEqual(self.terminal.input, expected+' ')
        self.assertIn('/quit (q)', HELP)
        self.assertIn('/new (new)', HELP)
        self.assertNotIn('/n', ALIASES)
        with self.assertRaisesRegex(ValueError, '未知命令'): self.terminal.command('/n')

    def test_short_commands_preserve_arguments_and_confirmation(self):
        runtime = self.terminal.runtime
        runtime.submit.return_value = {'session_id':'session'}
        runtime.workflow_action.return_value = {}
        self.terminal.command('/f "/tmp/有 空格" 搜索词')
        self.assertEqual(runtime.submit.call_args.args[2], {'tool':'file_list','arguments':{'path':'/tmp/有 空格','query':'搜索词','content':True}})
        self.terminal.command('/wfr workflow-id')
        runtime.workflow_action.assert_called_once_with('workflow-id', 'run')
        with self.assertRaisesRegex(ValueError, 'confirm'): self.terminal.command('/del other')
        runtime.session_action.assert_not_called()
        self.terminal.command('/del other --confirm')
        runtime.session_action.assert_called_once_with('other','delete')
        self.assertFalse(self.terminal.command('/q'))
        self.assertFalse(self.terminal.command('/quit'))

    def test_tree_opens_selector_without_node_argument(self):
        runtime = self.terminal.runtime
        runtime.session_tree.return_value = {'session': {'active_leaf': None}, 'nodes': [], 'path': []}
        for command in ('/tree', '/t'):
            self.assertEqual(self.enter(command)[0][1], '/tree (t)')
            self.terminal.command(command)
            self.assertIsNotNone(self.terminal.navigator)
            self.assertEqual(self.terminal.session, 'session')
        self.assertNotIn('[NODE_ID]', HELP)
        runtime.navigate_tree.assert_not_called()

    def test_every_command_has_one_unambiguous_registered_short_form(self):
        names = {entry[0] for entry in COMMANDS}
        self.assertEqual(set(SHORT_COMMANDS), names)
        self.assertEqual(len(set(SHORT_COMMANDS.values())),len(names))
        for name, short in SHORT_COMMANDS.items():
            self.assertEqual(ALIASES['/'+short], name)
            if '/'+short in names: self.assertEqual('/'+short, name)
