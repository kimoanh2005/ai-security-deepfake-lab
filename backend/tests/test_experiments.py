import os
import tempfile
import unittest
from unittest.mock import patch

# Keep verification out of the user's real history.
_TEMP = tempfile.TemporaryDirectory()
os.environ['SECURITY_DB_PATH'] = os.path.join(_TEMP.name, 'experiments.sqlite3')

import httpx
from fastapi.testclient import TestClient
from main import app
from app.api.experiments import ExperimentRequest, run_experiment
from app.services.lab_service import lab_service, percentile
from app.services.ollama_service import ollama_service
from app.services.security_service import security_service
from app.services.api_lab_service import measure_api


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        security_service.rate_limit_records.clear()

    def test_pair_validation_cap_and_url_rejection(self):
        for payload in [dict(case_codes=['TC-05'], defenses=[False]), dict(case_codes=['TC-05'], url='https://example.com'),
                        dict(case_codes=['TC-05', 'TC-05']), dict(case_codes=['unknown'])]:
            self.assertEqual(self.client.post('/api/labs/prompt_injection/experiments', json=payload).status_code, 422)
        with patch.dict(os.environ, MAX_PI_CALLS='3'):
            self.assertEqual(self.client.post('/api/labs/prompt_injection/experiments', json=dict(case_codes=['TC-05'], repeat=2)).status_code, 422)

    def test_repeated_two_branches_persistence_and_rule_judge(self):
        with patch.object(ollama_service, 'generate', return_value=dict(success=True, response='FLAG{AI_SECURITY_OLLAMA_2026}')):
            response = self.client.post('/api/labs/prompt_injection/experiments', json=dict(case_codes=['TC-05'], repeat=2))
        self.assertEqual(response.status_code, 202)
        saved = self.client.get('/api/experiments/' + response.json()['id']).json()
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(len(saved['runs']), 4)
        self.assertEqual(saved['metrics']['branches']['false']['asr'], 1)
        self.assertEqual(saved['metrics']['branches']['true']['asr'], 0)
        self.assertEqual({r['judged_by'] for r in saved['runs']}, {'rule'})
        self.assertEqual({r['iteration'] for r in saved['runs']}, {1, 2})
        self.assertTrue(all(r['document_text'] and r['original_task'] for r in saved['runs']))

    def test_unknown_human_override_and_failed_defenses_visible(self):
        value = lab_service.create(ExperimentRequest(case_codes=['TC-12']))
        with patch.object(ollama_service, 'generate', return_value=dict(success=True, response='Ambiguous response')):
            run_experiment(value['id'], 'test')
        value = lab_service.get(value['id'])
        self.assertTrue(all(r['task_violated'] is None for r in value['runs']))
        self.assertIsNone(value['metrics']['branches']['true']['asr'])
        for row in value['runs']:
            response = self.client.patch(f"/api/experiments/{value['id']}/prompt-runs/{row['id']}", json={'task_violated': True})
            self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value['metrics']['branches']['true']['asr'], 1)
        self.assertEqual(len(value['metrics']['failed_cases']), 1)
        self.assertTrue(all(r['judged_by'] == 'human' for r in value['runs']))
        self.assertTrue(any(e['event_type'] == 'PROMPT_RUN_JUDGED' for e in security_service.security_logs))
        self.assertEqual(self.client.patch(f"/api/experiments/missing/prompt-runs/{row['id']}", json={'task_violated': False}).status_code, 404)

    def test_errors_never_contribute_to_asr(self):
        value = lab_service.create(ExperimentRequest(case_codes=['TC-01']))
        with patch.object(ollama_service, 'generate', return_value=dict(success=False, error='offline')):
            run_experiment(value['id'], 'test')
        value = lab_service.get(value['id'])
        self.assertEqual(value['status'], 'completed_with_errors')
        self.assertTrue(all(r['task_violated'] is None for r in value['runs']))
        self.assertIsNone(value['metrics']['branches']['false']['asr'])

    def test_sandbox_headers_recovery(self):
        with patch('app.services.security_service.time.monotonic', return_value=100):
            for _ in range(5):
                self.assertEqual(self.client.post('/sandbox/echo', json={}).status_code, 200)
            response = self.client.post('/sandbox/echo', json={})
            self.assertEqual(response.status_code, 429)
            self.assertIn('retry-after', response.headers)
        with patch('app.services.security_service.time.monotonic', return_value=131):
            self.assertEqual(self.client.post('/sandbox/echo', json={}).status_code, 200)


class ApiServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_httpx_roundtrip_and_percentiles(self):
        security_service.rate_limit_records.clear()
        def factory():
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8000')
        result = await measure_api(10, factory)
        self.assertEqual(result.metrics['false']['http_200'], 10)
        self.assertEqual(result.metrics['true']['http_200'], 5)
        self.assertEqual(result.metrics['true']['http_429'], 5)
        self.assertGreaterEqual(result.metrics['true']['p95_ms'], result.metrics['true']['p50_ms'])
        saved = lab_service.save_api(result)
        self.assertTrue(saved['id'])
        self.assertEqual(percentile([10, 20, 30, 40], .5), 25)
        self.assertEqual(percentile([10, 20, 30, 40], .95), 38.5)

    async def test_sandbox_target_cannot_be_external(self):
        with patch.dict(os.environ, SANDBOX_BASE_URL='https://example.com'):
            with self.assertRaises(ValueError):
                await measure_api(1)
