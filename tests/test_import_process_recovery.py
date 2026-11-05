"""Crash/restart the actual gateway and importer with isolated local HTTP models."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import prompt_mode
import story_state

ROOT = Path(__file__).resolve().parents[1]


class ProcessRecoveryTests(unittest.TestCase):
    def test_killed_gateway_resumes_committed_sections_without_creative_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            project = dict(alias='recovery-test', name='Recovery test', folder=str(work / 'story'), model='writer')
            Path(project['folder']).mkdir()
            config = work / 'projects.json'
            config.write_text(json.dumps({'version': 1, 'projects': [project]}))
            story_state.advance(project, story_state.build_scene_state(project, 'before', 'The traveler waited at the harbor.'))
            before = {p.name: p.read_bytes() for p in story_state.location(project).glob('story-state*')}
            source = 'Nera has silver eyes.\n\n' + 'The archive stays quiet.\n' * 110 + '\nOrin owns a brass telescope.'
            calls, creative_calls = [], []
            interrupted, release = threading.Event(), threading.Event()
            process = None
            servers, threads = [], []
            log = (work / 'gateway.log').open('w')

            class LocalModel(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.reply({'status': 'ok'})
                def do_POST(self):
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    calls.append(payload)
                    if len(calls) == 2:
                        interrupted.set()
                        release.wait(15)
                    numbered = payload['messages'][1]['content'].split('SOURCE SECTION (only these numbered sentences may be cited):\n', 1)[1]
                    changes = []
                    for entity, category, statement in [('Nera', 'appearance', 'Nera has silver eyes.'),
                                                       ('Orin', 'possession', 'Orin owns a brass telescope.')]:
                        match = re.search(r'^\[(\d+)\] ' + re.escape(statement), numbered, re.M)
                        if match:
                            changes.append(dict(entity=entity, category=category, source_statement=int(match[1]), confidence='high'))
                    self.reply({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'changes': changes})}}]})
                def reply(self, data):
                    raw = json.dumps(data).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                def log_message(self, *args):
                    pass

            class Writer(LocalModel):
                def do_POST(self):
                    creative_calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                    self.reply({'error': 'unexpected creative request'})

            def serve(handler):
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                servers.append(server)
                threads.append(thread)
                return server.server_port

            memory_port, writer_port = serve(LocalModel), serve(Writer)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                gateway_port = sock.getsockname()[1]

            def request_http(method, path, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', gateway_port, timeout=2)
                try:
                    connection.request(method, path, json.dumps(body) if body is not None else None,
                                       {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def start():
                nonlocal process
                process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'storyteller.py'), 'serve',
                    '--no-analysis', '--port', str(gateway_port), '--upstream', f'http://127.0.0.1:{writer_port}'],
                    env={**os.environ, 'STORYTELLER_CONFIG': str(config), 'STORYTELLER_MEMORY_PORT': str(memory_port)},
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                until = time.monotonic() + 8
                while time.monotonic() < until:
                    if process.poll() is not None:
                        self.fail('Gateway exited: ' + (work / 'gateway.log').read_text())
                    try:
                        if request_http('GET', '/health')[0] == 200:
                            return
                    except OSError:
                        pass
                    time.sleep(.05)
                self.fail('Gateway did not become ready')

            def kill():
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

            try:
                start()
                prompt_mode.arm(project, 'dump')
                status, body = request_http('POST', '/v1/chat/completions', dict(model=project['alias'], stream=False,
                                    messages=[dict(role='user', content=source)]))
                self.assertEqual(status, 200)
                self.assertIn(b'captured', body)
                self.assertTrue(interrupted.wait(8), (work / 'gateway.log').read_text())
                task = prompt_mode.recent_task(project)
                self.assertEqual(Path(task['source']).read_text(), source)
                memory = Path(project['folder']) / '.storyteller/memory'
                self.assertIn('silver eyes', (memory / 'characters/nera.md').read_text())
                kill()
                release.set()
                start()
                until = time.monotonic() + 10
                while time.monotonic() < until:
                    task = prompt_mode.recent_task(project)
                    if task['status'] in ('complete', 'error', 'paused'):
                        break
                    time.sleep(.05)
                self.assertEqual(task['status'], 'complete', (task, (work / 'gateway.log').read_text()))
                self.assertEqual(len(calls), 3, 'First committed section must not be analyzed again')
                rows = [json.loads(line) for line in (memory / 'events.jsonl').read_text().splitlines()]
                self.assertEqual({row['fact'] for row in rows}, {'Nera has silver eyes.', 'Orin owns a brass telescope.'})
                self.assertEqual(len(rows), 2)
                self.assertEqual(creative_calls, [])
                self.assertEqual(before, {p.name: p.read_bytes() for p in story_state.location(project).glob('story-state*')})
            finally:
                release.set()
                kill()
                log.close()
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(2)


if __name__ == '__main__':
    unittest.main()
