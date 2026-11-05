#!/usr/bin/env python3
"""Manual local-model check using a disposable project and recording writer.

Run from a checkout with models/qwen3.5-2b-Q4_K_M.gguf available. No service,
Jan configuration, or existing project is modified. Results stay in a new
local temporary directory. Never commit that directory or private inputs.
"""
import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prompt_mode
import story_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--start-line', type=int, default=1)
    parser.add_argument('--end-line', type=int)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    source = args.source.read_bytes() if args.source else None
    source_hash = hashlib.sha256(source).hexdigest() if source is not None else None
    cases = [('unrelated', 'Nera has silver eyes. Nera is a navigator. '
              'The Lantern Guild operates in Veyra. Orin owns a brass telescope.')]
    if source is not None:
        text = source.decode('utf-8-sig')
        selected = ''.join(text.splitlines(keepends=True)[args.start_line - 1:args.end_line])
        cases.append(('reference', selected))
    work = Path(tempfile.mkdtemp(prefix='storyteller-dump-results-'))
    print('RESULT_DIRECTORY=' + str(work), flush=True)
    projects = []
    for alias, _ in cases:
        folder = work / alias
        folder.mkdir()
        projects.append(dict(alias=alias, name='Disposable ' + alias, folder=str(folder), model='recording-writer'))
    config = work / 'projects.json'
    config.write_text(json.dumps({'version': 1, 'projects': projects}))
    requests = []
    class Writer(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_error(500, 'Data Dump must never reach this endpoint')
        def log_message(self, *args):
            pass
    writer = ThreadingHTTPServer(('127.0.0.1', 0), Writer)
    thread = threading.Thread(target=writer.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (work / 'gateway.log').open('w')
    process = None
    report = {'cases': [], 'source_sha256': source_hash, 'jan_ui_tested': False}
    def request(method, path, payload=None):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        try:
            conn.request(method, path, json.dumps(payload) if payload is not None else None,
                         {'Content-Type': 'application/json'})
            reply = conn.getresponse()
            return reply.status, reply.read().decode()
        finally:
            conn.close()
    try:
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'storyteller.py'), 'serve',
                                    '--port', str(port), '--upstream', f'http://127.0.0.1:{writer.server_port}'],
                                   env={**os.environ, 'STORYTELLER_CONFIG': str(config)},
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + args.timeout
        while True:
            if process.poll() is not None:
                raise RuntimeError('Gateway exited: see gateway.log')
            try:
                if request('GET', '/health')[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Gateway did not become ready')
            time.sleep(.25)
        for project, (label, text) in zip(projects, cases):
            story_state.advance(project, story_state.build_scene_state(project, 'sentinel', 'The traveler waited at the harbor.'))
            state_dir = story_state.location(project)
            before = {p.name: p.read_bytes() for p in state_dir.glob('story-state*')}
            prompt_mode.arm(project, 'dump')
            payload = dict(model=project['alias'], stream=(label == 'reference'), messages=[dict(role='user', content=text)])
            status, body = request('POST', '/v1/chat/completions', payload)
            assert status == 200 and 'captured' in body, (status, body)
            task = prompt_mode.recent_task(project)
            assert Path(task['source']).read_text() == text
            print(label + ': captured, waiting for extraction', flush=True)
            while task['status'] not in ('complete', 'error', 'paused'):
                if time.monotonic() > deadline:
                    raise TimeoutError('Import timed out: ' + label)
                if process.poll() is not None:
                    raise RuntimeError('Gateway stopped during import')
                time.sleep(.5)
                task = prompt_mode.recent_task(project)
            assert task['status'] == 'complete', task
            assert before == {p.name: p.read_bytes() for p in state_dir.glob('story-state*')}
            memory = Path(project['folder']) / '.storyteller/memory'
            events = memory / 'events.jsonl'
            rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
            for row in rows:
                # Exact quotations can span separately numbered sentences.
                assert all(' '.join(part.split()) in ' '.join(text.split())
                           for part in row['evidence'].splitlines() if part.strip()), row
            if label == 'unrelated':
                expected = {part.strip() for part in text.split('.') if part.strip()}
                assert {row['fact'].rstrip('.') for row in rows} == expected, rows
            assert not (memory / 'recent-events.md').exists()
            prior = events.read_bytes() if events.exists() else b''
            assert request('POST', '/v1/chat/completions', payload)[0] == 200
            assert (events.read_bytes() if events.exists() else b'') == prior
            assert requests == [], requests
            result = dict(case=label, status='PASS', characters=len(text), facts=len(rows), rejected=task.get('rejected'),
                          sections=task.get('sections'), continuation_unchanged=True,
                          creative_requests=len(requests), facts_with_evidence=rows)
            report['cases'].append(result)
            print(json.dumps({k: v for k, v in result.items() if k != 'facts_with_evidence'}), flush=True)
        if source is not None:
            assert hashlib.sha256(args.source.read_bytes()).hexdigest() == source_hash
        report['status'] = 'PASS'
    except BaseException as exc:
        report.update(status='FAIL', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        # Own the complete process group; even a failed assertion must not
        # leave the gateway, import child, or CPU model running unattended.
        if process is not None:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        log.close()
        writer.shutdown()
        writer.server_close()
        thread.join(2)
        report['creative_requests'] = len(requests)
        (work / 'results.json').write_text(json.dumps(report, indent=2))
        print('RESULT=' + report['status'], flush=True)


if __name__ == '__main__':
    main()
