"""Bounded diagnostic for an out-of-scope Core input worker fault. No hardware.
Exit 1 preserves the unmet reconnect expectation; never counted as a PASS test.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('real_backend_fixture.py'))],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    env={**os.environ, 'PYTHONPATH': 'src'})
report = {'backend': 'real Application', 'io': 'synthetic input adapter', 'case': 'iter_pending raises OSError', 'expected': 'input worker reconnects after transient receive fault'}
try:
    ready = json.loads(child.stdout.readline())
    assert ready['ready']
    def health():
        with urllib.request.urlopen(ready['origin'] + '/api/v1/health', timeout=3) as response:
            return json.load(response)
    report['before'] = health()
    child.stdin.write(json.dumps({'id': 1, 'action': 'read_error'}) + '\n')
    child.stdin.flush()
    json.loads(child.stdout.readline())
    time.sleep(5)  # More than two normal reconnect intervals; finite, no retries.
    report['after'] = health()
    report['status'] = 'PASS' if report['after']['midi_input']['connection'] == 'CONNECTED' else 'FAIL'
finally:
    child.stdin.close()
    child.wait(timeout=10)
    report['server_stderr'] = child.stderr.read()
    report['pid'] = child.pid
    print(json.dumps(report, indent=2))
sys.exit(0 if report['status'] == 'PASS' else 1)
