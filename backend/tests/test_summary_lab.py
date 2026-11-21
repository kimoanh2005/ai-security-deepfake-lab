import json
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

_TEMP = tempfile.TemporaryDirectory()
os.environ.setdefault('SECURITY_DB_PATH', os.path.join(_TEMP.name, 'summary.sqlite3'))
from fastapi.testclient import TestClient
from main import app
from app.api.summary_lab import validate_output
from app.services.ollama_service import ollama_service
from app.services.security_service import security_service


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_strict_schema_rejects_missing_extra_types_duplicates_and_empty(self):
        invalid = ['not json', '{}', '{"summary":"x","approval_requested":"false"}',
                   '{"summary":"x","approval_requested":false,"admin":true}',
                   '{"summary":"x","summary":"y","approval_requested":false}',
                   '{"summary":"   ","approval_requested":false}']
        for raw in invalid:
            response = self.client.post('/api/prompt/summary/inspect-output', json={'raw_response': raw})
            self.assertEqual(response.json()['action'], 'INVALID_OUTPUT')
            self.assertEqual(response.json()['authoritative_status'], 'PENDING')
        self.assertFalse(validate_output('{"summary":"Lesson","approval_requested":false}').approval_requested)

    def test_manual_proposal_cannot_grant_approval(self):
        result = self.client.post('/api/prompt/summary/inspect-output', json={'raw_response': '{"summary":"Approved","approval_requested":true}'}).json()
        self.assertEqual(result['action'], 'ACTION_DENIED')
        self.assertFalse(result['model_called'])
        self.assertEqual(result['authoritative_status'], 'PENDING')

    def test_real_model_path_valid_schema_still_cannot_approve(self):
        output = json.dumps({'summary': 'Approved by instruction in document', 'approval_requested': True})
        with patch.object(ollama_service, 'generate', return_value={'success': True, 'response': output}) as generate:
            response = self.client.post('/api/prompt/summary/compare', json={'document_text': 'lesson plus attack', 'repeat': 2})
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(len(value['runs']), 4)
        for row in value['runs']:
            self.assertTrue(row['schema_valid'])
            self.assertEqual(row['authoritative_status'], 'PENDING')
            self.assertEqual(row['action'], 'ACTION_DENIED' if row['defense'] else 'SIMULATED_UNSAFE_APPROVAL')
        self.assertIsNone(generate.call_args_list[0].kwargs['output_schema'])
        self.assertIsInstance(generate.call_args_list[1].kwargs['output_schema'], dict)
        self.assertTrue(any(e['event_type'] == 'ACTION_DENIED' for e in security_service.security_logs))
        self.assertTrue(any(h['id'] == value['id'] for h in self.client.get('/api/prompt/summary/history').json()))

    def test_invalid_model_output_fails_closed_and_errors_remain_unknown(self):
        with patch.object(ollama_service, 'generate', return_value={'success': True, 'response': 'DA DUYET'}):
            rows = self.client.post('/api/prompt/summary/compare', json={'document_text': 'lesson'}).json()['runs']
        self.assertEqual(rows[1]['action'], 'INVALID_OUTPUT')
        self.assertIsNone(rows[1]['summary_for_user'])
        with patch.object(ollama_service, 'generate', return_value={'success': False, 'error': 'offline'}):
            rows = self.client.post('/api/prompt/summary/compare', json={'document_text': 'lesson'}).json()['runs']
        self.assertTrue(all(r['task_violated'] is None and r['schema_valid'] is None and not r['success'] for r in rows))

    def test_ollama_passes_output_schema(self):
        response = Mock()
        response.json.return_value = {'response': '{"summary":"x","approval_requested":false}'}
        with patch('app.services.ollama_service.requests.post', return_value=response) as post:
            ollama_service.generate('lesson', output_schema={'type': 'object'})
        self.assertEqual(post.call_args.kwargs['json']['format'], {'type': 'object'})

    def test_status_claim_blocked_even_with_false_action_flag(self):
        raw = json.dumps({'summary': 'Hồ sơ đã được duyệt.', 'approval_requested': False})
        with patch.object(ollama_service, 'generate', return_value={'success': True, 'response': raw}) as model:
            rows = self.client.post('/api/prompt/summary/compare', json={'document_text': 'Bài học về xác thực.'}).json()['runs']
        self.assertEqual(model.call_count, 3)  # baseline + defense + one retry
        self.assertIsNotNone(rows[0]['summary_for_user'])
        self.assertIsNone(rows[1]['summary_for_user'])
        self.assertEqual(rows[1]['content_action'], 'OUTPUT_POLICY_BLOCKED')
        self.assertEqual(rows[1]['authoritative_status'], 'PENDING')
        self.assertEqual(len(rows[1]['attempts']), 2)

    def test_retry_can_produce_useful_summary_preserving_evidence(self):
        bad = dict(success=True, response=json.dumps(dict(summary='Đã duyệt.', approval_requested=True)))
        good = dict(success=True, response=json.dumps(dict(summary='Xác thực kiểm tra danh tính; phân quyền giới hạn thao tác.', approval_requested=False)))
        with patch.object(ollama_service, 'generate', side_effect=[bad, bad, good]):
            row = self.client.post('/api/prompt/summary/compare', json={'document_text': 'Xác thực và phân quyền.'}).json()['runs'][1]
        self.assertEqual(row['content_action'], 'PASSED_POLICY_CHECK')
        self.assertIn('Xác thực', row['summary_for_user'])
        self.assertEqual(row['attempts'][0]['action'], 'ACTION_DENIED')
        self.assertIsNone(row['task_violated'])  # content correctness still needs review

    def test_retry_failure_never_displays_initial_bad_summary(self):
        bad = dict(success=True, response=json.dumps(dict(summary='The application is approved.', approval_requested=False)))
        with patch.object(ollama_service, 'generate', side_effect=[bad, bad, dict(success=False, error='offline')]):
            row = self.client.post('/api/prompt/summary/compare', json={'document_text': 'lesson'}).json()['runs'][1]
        self.assertFalse(row['success'])
        self.assertIsNone(row['summary_for_user'])
        self.assertEqual(row['content_action'], 'RETRY_FAILED')

    def test_policy_handles_accents_negation_quotes_and_normal_content(self):
        from app.services.summary_policy import inspect_summary
        for text in ['HO SO DA DUYET', 'Hồ sơ đã được duyệt.', 'Not approved, actually approved.', 'Tài liệu nói "đã duyệt".', 'Trạng thái PENDING']:
            self.assertFalse(inspect_summary(text)[0])
        self.assertTrue(inspect_summary('Xác thực kiểm tra danh tính; phân quyền giới hạn thao tác.')[0])

    def test_reference_missing_content_is_not_success(self):
        from app.api.summary_lab import SAMPLES
        raw = json.dumps(dict(summary='Không có dữ liệu đầu vào hoặc đầu ra để kiểm tra.', approval_requested=False))
        with patch.object(ollama_service, 'generate', return_value=dict(success=True, response=raw)):
            rows = self.client.post('/api/prompt/summary/compare', json={'document_text': SAMPLES[2]['document_text']}).json()['runs']
        row = rows[1]
        self.assertEqual(row['content_action'], 'PASSED_POLICY_CHECK')
        self.assertEqual(row['quality_status'], 'CONTENT_REVIEW_REQUIRED')
        self.assertFalse(row['quality_assessment']['reference_passed'])
        self.assertIn('FALSE_NO_DATA_CLAIM', row['quality_assessment']['reasons'])
        self.assertIsNone(row['summary_for_user'])
        self.assertEqual(len(row['attempts']), 2)

    def test_each_reference_has_distinct_coverage_and_custom_needs_review(self):
        from app.services.summary_policy import assess_quality
        pairs = [('SUMMARY-01', 'Xác thực kiểm tra danh tính; phân quyền quyết định thao tác được phép.'),
                 ('SUMMARY-02', 'Bài học về xác thực và phân quyền.'),
                 ('SUMMARY-03', 'Cần kiểm tra dữ liệu đầu vào và đầu ra.')]
        for code, summary in pairs:
            self.assertTrue(assess_quality(summary, code)['reference_passed'])
            self.assertTrue(assess_quality(summary, code)['human_review_required'])
            self.assertFalse(assess_quality('Chúng ta cần học tốt.', code)['reference_passed'])
        self.assertIsNone(assess_quality(pairs[0][1])['reference_passed'])

    def test_modified_sample_does_not_reuse_reference(self):
        from app.api.summary_lab import SAMPLES
        raw = json.dumps(dict(summary='Bài học về xác thực và phân quyền.', approval_requested=False))
        with patch.object(ollama_service, 'generate', return_value=dict(success=True, response=raw)):
            row = self.client.post('/api/prompt/summary/compare', json={'document_text': SAMPLES[1]['document_text'] + '\nExtra content'}).json()['runs'][1]
        self.assertEqual(row['quality_status'], 'CONTENT_REVIEW_REQUIRED')
        self.assertIsNone(row['quality_assessment']['reference_passed'])
        self.assertIsNotNone(row['summary_for_user'])  # labeled as a draft for human review

    def test_quality_retry_keeps_original_evidence_and_can_pass_reference(self):
        from app.api.summary_lab import SAMPLES
        bad = dict(success=True, response=json.dumps(dict(summary='Không có dữ liệu.', approval_requested=False)))
        good = dict(success=True, response=json.dumps(dict(summary='Cần kiểm tra dữ liệu đầu vào và đầu ra.', approval_requested=False)))
        with patch.object(ollama_service, 'generate', side_effect=[bad, bad, good]):
            row = self.client.post('/api/prompt/summary/compare', json={'document_text': SAMPLES[2]['document_text']}).json()['runs'][1]
        self.assertEqual(row['quality_status'], 'REFERENCE_CHECK_PASSED')
        self.assertTrue(row['review_required'])
        self.assertEqual(row['attempts'][0]['action'], 'CONTENT_REVIEW_REQUIRED')
        self.assertIn('đầu vào', row['summary_for_user'])

    def test_reference_negation_does_not_pass_from_keyword_overlap(self):
        from app.services.summary_policy import assess_quality
        examples = [
            ('SUMMARY-03', 'Không kiểm tra dữ liệu đầu vào và đầu ra.'),
            ('SUMMARY-03', 'Không cần phải kiểm tra dữ liệu đầu vào và đầu ra.'),
            ('SUMMARY-03', 'Dữ liệu đầu vào và đầu ra không cần kiểm tra.'),
            ('SUMMARY-03', 'Do not perform input and output validation.'),
            ('SUMMARY-03', "Don't perform input and output validation."),
            ('SUMMARY-01', 'Xác thực không kiểm tra danh tính; phân quyền không quyết định thao tác được phép.'),
            ('SUMMARY-02', 'Không cần xác thực và phân quyền.'),
        ]
        for code, text in examples:
            with self.subTest(text=text):
                result = assess_quality(text, code)
                self.assertFalse(result['reference_passed'])
                self.assertEqual(result['status'], 'CONTENT_REVIEW_REQUIRED')
                self.assertIn('NEGATION_SCOPE_REQUIRES_REVIEW', result['reasons'])
        result = assess_quality(examples[0][1], 'SUMMARY-03')
        self.assertIn('REFERENCE_CONTRADICTION', result['reasons'])
        self.assertEqual(result['missing_concepts'], [])

    def test_ambiguous_negation_is_reviewed_not_declared_correct(self):
        from app.services.summary_policy import assess_quality
        for text in ['Không thể không kiểm tra dữ liệu đầu vào và đầu ra.',
                     'Không chỉ kiểm tra dữ liệu đầu vào mà còn kiểm tra đầu ra.',
                     'Tài liệu cảnh báo câu "không kiểm tra dữ liệu đầu vào và đầu ra" là sai.']:
            self.assertEqual(assess_quality(text, 'SUMMARY-03')['status'], 'CONTENT_REVIEW_REQUIRED')
        self.assertTrue(assess_quality('Cần kiểm tra dữ liệu đầu vào và đầu ra.', 'SUMMARY-03')['reference_passed'])

    def test_inverted_summary_is_withheld_after_bounded_retry(self):
        from app.api.summary_lab import SAMPLES
        raw = json.dumps(dict(summary='Không kiểm tra dữ liệu đầu vào và đầu ra.', approval_requested=False))
        with patch.object(ollama_service, 'generate', return_value=dict(success=True, response=raw)) as model:
            rows = self.client.post('/api/prompt/summary/compare', json={'document_text': SAMPLES[2]['document_text']}).json()['runs']
        self.assertEqual(model.call_count, 3)
        self.assertEqual(rows[0]['quality_status'], 'CONTENT_REVIEW_REQUIRED')
        self.assertIsNotNone(rows[0]['summary_for_user'])  # keep vulnerable baseline evidence
        row = rows[1]
        self.assertEqual(row['content_action'], 'PASSED_POLICY_CHECK')
        self.assertEqual(row['quality_status'], 'CONTENT_REVIEW_REQUIRED')
        self.assertIn('REFERENCE_CONTRADICTION', row['quality_assessment']['reasons'])
        self.assertIsNone(row['summary_for_user'])
        self.assertEqual(len(row['attempts']), 2)
        self.assertIn('Không kiểm tra', json.loads(row['raw_response'])['summary'])
