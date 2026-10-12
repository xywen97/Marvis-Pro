"""Delivery artifacts are outputs, while read-only evidence stays bounded and auditable."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from marvis.evaluation import digest, evidence_snapshot


class EvidenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = []
        self.coordinator = SimpleNamespace(
            ident='test', caps=SimpleNamespace(path=lambda p: Path(p)),
            runtime=SimpleNamespace(_check=lambda ident: None,
                                    store=SimpleNamespace(events=lambda ident: self.events)))

    def result(self, tool, value, kind='tool_result'):
        self.events.append({'kind': kind, 'content': {'agent': 'file', 'tool': tool, 'result': value}})

    def test_many_read_only_paths_are_not_delivery_artifacts(self):
        for i in range(30):
            p = self.root / f'source{i}.py'; p.write_text('answer = 42\n' * 1000)
            self.result('directory_list', {'path': str(self.root), 'entries': [{'path': str(p)}]})
            self.result('file_read_lines', {'path': str(p), 'text': p.read_text()})
        snapshot = evidence_snapshot(self.coordinator)
        self.assertEqual(snapshot['artifacts'], [])
        self.assertEqual(len(snapshot['sources']), 8)
        self.assertEqual(snapshot['source_files_total'], 30)
        self.assertTrue(snapshot['source_snapshots_limited'])
        self.assertTrue(all(source['truncated'] for source in snapshot['sources']))
        self.assertLess(len(json.dumps(snapshot, ensure_ascii=False)), 60000)
        record = next(r for r in snapshot['records'] if r['tool'] == 'file_read_lines')
        self.assertTrue(record['result']['truncated'])
        self.assertEqual(record['result_sha256'], digest(self.events[1]['content']['result']))

    def test_successful_edits_and_conversions_count_but_errors_do_not(self):
        edited = self.root / 'edited.py'; edited.write_text('x = 1')
        converted = self.root / 'converted.txt'; converted.write_text('converted')
        self.result('file_edit', {'edited': str(edited)})
        self.result('document_convert', {'created': str(converted)})
        self.result('file_write', {'created': str(self.root / 'failed'), 'error': 'failed'})
        self.result('file_write', {'created': str(self.root / 'rejected')}, 'rejected')
        snapshot = evidence_snapshot(self.coordinator)
        self.assertEqual({a['path'] for a in snapshot['artifacts']}, {str(edited), str(converted)})
        self.assertEqual(snapshot['sources'], [])

    def test_real_output_limit_still_blocks_delivery(self):
        for i in range(21): self.result('file_write', {'created': str(self.root / f'output{i}')})
        with self.assertRaisesRegex(ValueError, '产物超过 20'): evidence_snapshot(self.coordinator)

    def test_source_changes_invalidate_snapshot_digest(self):
        source = self.root / 'source.py'; source.write_text('x=1')
        self.result('code_check', {'path': str(source), 'valid': True})
        original = digest(evidence_snapshot(self.coordinator))
        source.write_text('x=2')
        self.assertNotEqual(original, digest(evidence_snapshot(self.coordinator)))
