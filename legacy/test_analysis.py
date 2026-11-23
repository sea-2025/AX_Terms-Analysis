import unittest
from unittest.mock import patch
import app


class AnalysisTests(unittest.TestCase):
    def test_offsets_and_chunk_coverage(self):
        text = '첫 문단\r\n\r\n' + '가' * 7000 + '\n마지막'
        items = app.paragraphs(text)
        self.assertTrue(all(text[p['start']:p['end']] == p['text'] for p in items))
        self.assertEqual([p['id'] for p in items], [p['id'] for group in app.chunks(items) for p in group])

    def test_rejects_fabricated_quote_and_preserves_valid_offset(self):
        group = app.paragraphs('이메일을 동의 철회 시까지 보유합니다.\n')
        good = {'category': '보유·파기', 'summary': '동의 철회 시까지 보유합니다.', 'evidence': [{'paragraph_id': 'P0001', 'quote': '동의 철회 시까지'}], 'review': ''}
        bad = dict(good, evidence=[{'paragraph_id': 'P0001', 'quote': '10년간 보유'}])
        accepted, failures = app.validate_findings({'findings': [good, bad]}, group)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(failures), 1)
        ev = accepted[0]['evidence'][0]
        self.assertEqual(group[0]['text'][ev['start']:ev['end']], ev['quote'])

    def test_worker_marks_failed_coverage_unassessed(self):
        job_id = 'test'; groups = app.chunks(app.paragraphs('테스트 문단'))
        app.jobs[job_id] = {'created': 0, 'job_status': 'queued', 'findings': [], 'validation_failures': [], 'coverage': {'processed_paragraph_ids': [], 'failed_paragraph_ids': []}, 'categories': []}
        app.slots.acquire()
        with patch('app.time.time', return_value=1), patch('app.call_model', side_effect=ValueError()):
            app.worker(job_id, groups)
        job = app.jobs.pop(job_id)
        self.assertEqual(job['job_status'], 'failed')
        self.assertEqual(job['coverage']['failed_paragraph_ids'], ['P0001'])
        self.assertTrue(all(c['status'] == 'unassessed' for c in job['categories']))


if __name__ == '__main__': unittest.main()
