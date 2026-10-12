import json
import unittest

from marvis.failure_evidence import failure_evidence


class FailureEvidenceTests(unittest.TestCase):
    def test_large_nested_logs_keep_status_errors_paths_and_unverified_tail(self):
        reports = {'coding': {'status': 'partial',
                             'summary': '已创建文件。' + '日志' * 50000 + '构建尚未验证。',
                             'evidence': [{'result': {'output': 'x' * 100000}}] * 30}}
        physical = [
            {'kind': 'tool_result', 'content': {'agent': 'coding', 'tool': 'file_write',
             'result': {'content': 'x' * 100000, 'created': '/tmp/result.py'}}},
            {'kind': 'tool_error', 'content': {'agent': 'coding', 'tool': 'code_execute', 'text': '构建失败：缺少依赖'}},
            {'kind': 'tool_result', 'content': {'agent': 'coding', 'tool': 'code_check',
             'result': {'output': 'x' * 100000, 'nested': {'path': '/tmp/result.py', 'passed': True}}}},
            {'kind': 'rejected', 'content': {'agent': 'file', 'tool': 'file_edit', 'result': {'rejected': True}}},
        ]
        raw = failure_evidence('创建并验证代码', '评测缺少 score', reports, physical)
        self.assertLessEqual(len(raw), 18000)
        payload = json.loads(raw)
        self.assertEqual(payload['reports']['coding']['status'], 'partial')
        self.assertIn('构建尚未验证', payload['reports']['coding']['summary'])
        self.assertEqual(payload['reports']['coding']['evidence_count'], 30)
        self.assertEqual(payload['execution']['failed_records'], 2)
        self.assertIn('/tmp/result.py', raw)
        self.assertIn('构建失败：缺少依赖', raw)
        self.assertEqual(len(payload['tool_records']), 4)
        self.assertTrue(payload['tool_records'][0]['truncated'])

    def test_many_records_keep_latest_success_recent_failure_and_omission_counts(self):
        physical = [{'kind': 'tool_result', 'content': {'agent': 'coding', 'tool': 'code_execute',
                     'result': {'output': 'x' * 10000, 'status': 'completed'}}} for _ in range(100)]
        physical[-2] = {'kind': 'tool_error', 'content': {'agent': 'coding', 'tool': 'code_execute', 'text': '旧检查失败'}}
        payload = json.loads(failure_evidence('验证', '评分无效', {}, physical))
        self.assertEqual(payload['execution']['total_records'], 100)
        self.assertEqual(payload['execution']['omitted_records'], 98)
        self.assertEqual([r['record_index'] for r in payload['tool_records']], [98, 99])
        self.assertEqual([r['has_error'] for r in payload['tool_records']], [True, False])

    def test_all_role_and_tool_fields_fit_budget_without_notice_only_fallback(self):
        reports = {str(i): {'status': 'partial', 'summary': 's' * 100000} for i in range(8)}
        physical = [{'kind': 'tool_result', 'content': {'agent': 'coding', 'tool': 'tool-' + str(i),
                    'result': {key: 'x' * 100000 for key in ('path', 'created', 'edited', 'target', 'source',
                               'error', 'status', 'notice', 'passed', 'truncated', 'success', 'exit_code')}}}
                    for i in range(24)]
        raw = failure_evidence('t' * 100000, 'f' * 100000, reports, physical)
        self.assertLessEqual(len(raw), 18000)
        payload = json.loads(raw)
        self.assertEqual(len(payload['reports']), 8)
        self.assertEqual(len(payload['tool_records']), 24)
