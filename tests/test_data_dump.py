"""Data Dump boundaries: capture, HTTP routing, extraction and resumable storage."""
import http.client
from concurrent.futures import ThreadPoolExecutor
import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

import import_modes
import memory_worker
import prompt_mode
import story_state
import storyteller

LORE = 'Nera has silver eyes. The Lantern Guild operates in Veyra.'


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.project = dict(alias='test-dump', name='Disposable', folder=str(self.folder), model='recording-writer')
        storyteller.ensure_memory(self.folder)
        story_state.advance(self.project, story_state.build_scene_state(self.project, 'baseline', 'Nera waited at the harbor.'))
        self.before = {p.name: p.read_bytes() for p in story_state.location(self.project).glob('story-state*')}

    def assert_position_unchanged(self):
        self.assertEqual(self.before, {p.name: p.read_bytes() for p in story_state.location(self.project).glob('story-state*')})


class CaptureTests(ProjectCase):
    def test_capture_preserves_exact_source_and_rejects_short_input(self):
        prompt_mode.arm(self.project, 'dump')
        self.assertIn('error', prompt_mode.capture(self.project, '?'))
        self.assertTrue(prompt_mode.read_state(self.project)['armed'])
        task = prompt_mode.capture(self.project, LORE)
        self.assertEqual(Path(task['source']).read_text(), LORE)
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(prompt_mode.capture(self.project, LORE)['token'], task['token'])
        self.assert_position_unchanged()

    def test_retry_after_expiry_and_after_another_capture_stays_local(self):
        prompt_mode.arm(self.project, 'dump')
        first = prompt_mode.capture(self.project, LORE)
        prompt_mode.arm(self.project, 'dump')
        prompt_mode.capture(self.project, 'Orin owns a brass telescope.')
        with patch('prompt_mode.time.time', return_value=2_000_000_000):
            retry = prompt_mode.capture(self.project, LORE)
        self.assertIsNotNone(retry, 'A persisted import must never become creative input on retry')
        self.assertEqual(retry['token'], first['token'])

    def test_malformed_state_fails_closed(self):
        prompt_mode.arm(self.project, 'dump')
        (prompt_mode.root(self.project) / 'next-prompt.json').write_text('{')
        with self.assertRaises(ValueError):
            prompt_mode.capture(self.project, LORE)

    def test_concurrent_duplicate_capture_creates_one_durable_task(self):
        prompt_mode.arm(self.project, 'dump')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: prompt_mode.capture(self.project, LORE), range(12)))
        self.assertEqual(len({result['token'] for result in results}), 1)
        self.assertEqual(len(prompt_mode.pending_tasks(self.project)), 1)
        self.assertEqual(sum(not result.get('duplicate', False) for result in results), 1)

    def test_cancel_and_project_isolation(self):
        other = {**self.project, 'alias': 'other-project'}
        prompt_mode.arm(self.project, 'dump')
        self.assertIsNone(prompt_mode.capture(other, LORE))
        self.assertTrue(prompt_mode.cancel(self.project))
        self.assertIsNone(prompt_mode.capture(self.project, LORE))
        prompt_mode.arm(self.project, 'dump')
        prompt_mode.capture(self.project, LORE)
        self.assertIsNone(prompt_mode.capture(other, LORE))
        self.assertEqual(prompt_mode.imported_prompt_hashes(other), set())

    def test_malformed_receipt_blocks_routing(self):
        prompt_mode.arm(self.project, 'dump')
        task = prompt_mode.capture(self.project, LORE)
        prompt_mode.task_path(self.project, task['token']).write_text('[]')
        with self.assertRaises(ValueError):
            prompt_mode.capture(self.project, 'A different ordinary message')
        with self.assertRaises(ValueError):
            storyteller.without_imported_history(self.project, [dict(role='user', content=LORE)])


class GatewayTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.requests = []
        requests = self.requests
        class RecordingWriter(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body = json.dumps({'choices': [{'message': {'content': 'Acknowledged.'}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        writer = self.server(RecordingWriter)
        project = self.project
        class Gateway(storyteller.Gateway):
            upstream = urlsplit('http://127.0.0.1:' + str(writer.server_port))
            @staticmethod
            def get_projects():
                return {project['alias']: project}
            def log_message(self, *args):
                pass
            def log(self, *args):
                pass
        self.gateway = self.server(Gateway)

    def server(self, handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        return server

    def post(self, messages, stream=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        try:
            conn.request('POST', '/v1/chat/completions', json.dumps(dict(model=self.project['alias'], messages=messages, stream=stream)), {'Content-Type': 'application/json'})
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def test_capture_http_streamed_and_nonstreamed_never_contacts_writer(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                prompt_mode.arm(self.project, 'dump')
                status, body = self.post([dict(role='user', content=LORE)], stream)
                self.assertEqual(status, 200)
                self.assertIn('captured', body)
                if stream:
                    self.assertIn('data: [DONE]', body)
        self.assertEqual(self.requests, [])
        self.assert_position_unchanged()

    def test_expired_retry_makes_zero_creative_requests(self):
        prompt_mode.arm(self.project, 'dump')
        self.post([dict(role='user', content=LORE)])
        with patch('prompt_mode.time.time', return_value=2_000_000_000):
            status, _ = self.post([dict(role='user', content=LORE)])
        self.assertEqual(status, 200)
        self.assertEqual(self.requests, [])

    def test_history_without_ack_does_not_leak_capture(self):
        prompt_mode.arm(self.project, 'dump')
        self.post([dict(role='user', content=LORE)])
        for ack in ([], [dict(role='assistant', content='Edited acknowledgement')]):
            status, _ = self.post([dict(role='user', content=LORE)] + ack + [dict(role='user', content='Hello, are you ready?')])
            self.assertEqual(status, 200)
            self.assertNotIn(LORE, json.dumps(self.requests[-1]))
        self.assertEqual(len(self.requests), 2)

    def test_corrupt_capture_state_never_contacts_writer(self):
        prompt_mode.arm(self.project, 'dump')
        (prompt_mode.root(self.project) / 'next-prompt.json').write_text('{')
        status, _ = self.post([dict(role='user', content=LORE)])
        self.assertGreaterEqual(status, 400)
        self.assertEqual(self.requests, [])


class ImportTests(ProjectCase):
    @staticmethod
    def extraction(*args, **kwargs):
        return {'changes': [{'entity': 'Nera', 'category': 'appearance', 'fact': 'Nera has silver eyes', 'evidence_numbers': [1], 'confidence': 'high'}], 'summary': ''}

    def test_incoming_validation_rejects_unsupported_relationship(self):
        raw = self.extraction()
        raw['changes'].append(dict(entity='Nera', category='relationship', fact='Nera is married to Orin', evidence_numbers=[1], confidence='high'))
        valid, rejected, _ = import_modes.validate_records(raw, LORE, LORE, 'dump')
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(valid[0]['evidence'], 'Nera has silver eyes.')

    def test_causal_paraphrase_and_reversed_negation_are_rejected(self):
        source = 'The Beacon glows red. The sky darkens. The Beacon cannot open gates.'
        raw = {'changes': [
            dict(entity='Beacon', category='technology', fact='The Beacon glows red, causing the sky to darken', evidence_numbers=[1, 2]),
            dict(entity='Beacon', category='technology', fact='The Beacon can open gates', evidence_numbers=[3])
        ]}
        valid, rejected, _ = import_modes.validate_records(raw, source, source, 'dump')
        self.assertEqual(valid, [])
        self.assertEqual(len(rejected), 2)

    def test_heading_scoped_reference_fact_keeps_source_attribution(self):
        source = '### Nera\n\nAppearance\n\n- Has silver eyes.\n\n### Orin\n\n- Has blue eyes.'
        sentences = memory_worker.split_sentences(source)
        silver = next(i for i, sentence in enumerate(sentences, 1) if 'Has silver eyes' in sentence)
        blue = next(i for i, sentence in enumerate(sentences, 1) if 'Has blue eyes' in sentence)
        raw = {'changes': [
            dict(entity='Nera', category='appearance', fact='Has silver eyes.', evidence_numbers=[silver]),
            dict(entity='Nera', category='appearance', fact='Has blue eyes.', evidence_numbers=[blue]),
        ]}
        valid, rejected, _ = import_modes.validate_records(raw, source, source, 'dump')
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]['fact'], 'Has silver eyes.')
        self.assertIn('Nera', valid[0]['evidence'])
        self.assertEqual(len(rejected), 1)

    def test_statement_selection_copies_source_and_rejects_invalid_indexes(self):
        source = 'Nera cannot open gates. Orin owns a telescope.'
        raw = {'changes': [dict(entity='Nera', category='status', source_statement=1),
                           dict(entity='Nera', category='status', source_statement=True),
                           dict(entity='Orin', category='possession', source_statement=99)]}
        valid, rejected, _ = import_modes.validate_records(raw, source, source, 'dump')
        self.assertEqual([row['fact'] for row in valid], ['Nera cannot open gates.'])
        self.assertEqual(len(rejected), 2)

    def test_memory_transport_budget_and_truncated_output(self):
        response = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]})
        with patch.object(import_modes, 'urlopen', return_value=io.StringIO(response)) as endpoint:
            with self.assertRaisesRegex(ValueError, 'output limit'):
                import_modes.request_model(import_modes.DUMP_MEMORY_PROMPT, '[1] Nera has silver eyes.')
        sent = json.loads(endpoint.call_args.args[0].data)
        self.assertEqual(sent['max_tokens'], 3072)
        self.assertEqual(sent['model'], memory_worker.MODEL_NAME)
        self.assertEqual(sent['response_format']['schema']['properties']['changes']['maxItems'], 12)
        self.assertIn('[1] Nera has silver eyes.', sent['messages'][1]['content'])

    def test_concurrent_memory_writers_do_not_lose_facts(self):
        def save(number):
            fact = dict(entity='Beacon', category='technology', fact=f'The Beacon has socket number {number}.',
                        evidence=f'The Beacon has socket number {number}.', confidence='high')
            memory_worker.apply_changes(self.project, f'import:dump:job-{number}', [fact], timeline=False)
            import_modes.append_dump_global_facts(self.project, str(number), [fact], 'source')
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(save, range(16)))
        memory = self.folder / '.storyteller/memory'
        self.assertEqual(len((memory / 'events.jsonl').read_text().splitlines()), 16)
        for number in range(16):
            text = f'The Beacon has socket number {number}.'
            self.assertEqual((memory / 'world/beacon.md').read_text().count(text), 1)
            self.assertEqual((memory / 'canon.md').read_text().count(text), 1)
        self.assert_position_unchanged()

    def test_empty_update_completes_without_canon_changes(self):
        prompt_mode.arm(self.project, 'dump')
        task = prompt_mode.capture(self.project, 'Does Nera have silver eyes?')
        with patch.object(memory_worker, 'is_healthy', return_value=True), patch.object(import_modes, 'request_model', return_value={'changes': [], 'summary': ''}):
            result = import_modes.run_import(self.project, 'dump', task['source'])
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['facts'], 0)
        self.assertFalse((self.folder / '.storyteller/memory/events.jsonl').exists())
        self.assert_position_unchanged()

    def test_rearmed_import_does_not_duplicate_world_canon(self):
        raw = {'changes': [dict(entity='Beacon', category='technology',
                fact='The Beacon cannot open gates.', evidence_numbers=[1])]}
        with patch.object(memory_worker, 'is_healthy', return_value=True), patch.object(import_modes, 'request_model', return_value=raw):
            for _ in range(2):
                prompt_mode.arm(self.project, 'dump')
                task = prompt_mode.capture(self.project, 'The Beacon cannot open gates.')
                import_modes.run_import(self.project, 'dump', task['source'])
        memory = self.folder / '.storyteller/memory'
        self.assertEqual(len((memory / 'events.jsonl').read_text().splitlines()), 1)
        self.assertEqual((memory / 'canon.md').read_text().count('The Beacon cannot open gates.'), 1)
        self.assert_position_unchanged()

    def test_outgoing_persistence_retry_and_resume(self):
        prompt_mode.arm(self.project, 'dump')
        task = prompt_mode.capture(self.project, LORE + '\n\n' + 'Orin owns a brass telescope. ' * 110)
        plan = import_modes.prepare(self.project, 'dump', task['source'])
        with patch.object(memory_worker, 'is_healthy', return_value=True), patch.object(import_modes, 'request_model', side_effect=[self.extraction(), RuntimeError('interrupted')]):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                import_modes.run_import(self.project, 'dump', task['source'], plan)
        self.assertEqual(json.loads(import_modes.status_path(self.project, plan['job_id']).read_text())['completed'], 1)
        with patch.object(memory_worker, 'is_healthy', return_value=True), patch.object(import_modes, 'request_model', return_value={'changes': [], 'summary': ''}) as model:
            result = import_modes.run_import(self.project, 'dump', task['source'])
            self.assertEqual(model.call_count, plan['sections'] - 1)
            self.assertEqual(result['state'], 'complete')
            self.assertIsNone(result.get('error'))
            import_modes.run_import(self.project, 'dump', task['source'])
            self.assertEqual(model.call_count, plan['sections'] - 1)
        memory = self.folder / '.storyteller/memory'
        self.assertIn('Nera has silver eyes', (memory / 'characters/nera.md').read_text())
        events = [json.loads(line) for line in (memory / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['evidence'], 'Nera has silver eyes.')
        self.assertFalse((memory / 'recent-events.md').exists())
        self.assertEqual(len(list((memory / 'reference-notes').rglob('section-*.md'))), plan['sections'])
        self.assert_position_unchanged()


if __name__ == '__main__':
    unittest.main()
