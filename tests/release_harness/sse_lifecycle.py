"""Owner-authorized candidate-to-same-candidate helper lifecycle with real SSE.

Never used for the initial inherited runtime transition. The caller retains B0
as failure rollback target; this operation's B1 is recorded, not substituted.
"""
import datetime
import http.client
import json
import subprocess
import urllib.error
import time
from pathlib import Path

HOST = '192.168.1.50'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'pianopi@'+HOST]

from bounded_http_json import fetch
from bounded_sse import Peer

def health(timeout=10):
    result = fetch('http://'+HOST+':8788/api/v1/health',timeout=timeout,include_headers=True)
    assert result['status']==200
    headers={key.lower():value for key,value in result['headers'].items()}
    data=result['data'];data['pid']=int(headers['x-piano-process-pid']);data['fds']=int(headers['x-piano-process-fds'])
    return data

def initial_snapshot(response, timeout=10, max_bytes=65536):
    """Bound the entire first frame, including trickling peers, by one deadline."""
    deadline = time.monotonic() + timeout
    frame = b''
    while b'\n\n' not in frame:
        remaining = deadline - time.monotonic()
        assert remaining > 0, 'SSE initial snapshot deadline'
        response.fp.raw._sock.settimeout(remaining)
        chunk = response.read1(min(4096, max_bytes + 1 - len(frame)))
        assert chunk, 'SSE closed before initial snapshot'
        frame += chunk
        assert len(frame) <= max_bytes, 'SSE initial snapshot byte limit'
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith(b'data: ')))

def run(root, wheel, execute):
    root = Path(root)
    proof = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'before': health(), 'streams': [], 'complete': False}
    peers = []
    try:
        for index in range(3):
            peer=Peer('http://'+HOST+':8788/api/v1/live-notes/stream')
            peers.append(peer)
            row=dict(peer.snapshot,index=index,eof=False)
            proof['streams'].append(row)
        # All three real streams have received frames before helper SIGTERM.
        def before_helper():
            assert all(peer.child.poll() is None for peer in peers),'SSE peer exited before actual helper boundary'
            proof['active_peers_at_helper_boundary']=len(peers)
            proof['helper_started_monotonic'] = time.monotonic()
        execute('helper-candidate-sse-lifecycle', SSH + [
            'sudo -n /usr/local/sbin/piano-core-deploy deploy /var/lib/piano-core-incoming/'
            + wheel['wheel']+' '+wheel['sha256']],before_helper)
        assert proof.get('active_peers_at_helper_boundary')==3,'missing actual helper boundary acknowledgment'
        proof['helper_finished_monotonic'] = time.monotonic()
        for peer,row in zip(peers,proof['streams']):
            row.update(peer.eof(timeout=2))
        assert all(row['eof'] and 'error' not in row for row in proof['streams']), proof
        assert all(row['ended_monotonic'] >= proof['helper_started_monotonic'] for row in proof['streams'])
        deadline = time.monotonic() + 15
        proof['readiness_observations'] = []
        while True:
            try:
                remaining=deadline-time.monotonic()
                assert remaining>0, 'new candidate did not become ready'
                observed = health(timeout=remaining)
                assert time.monotonic()<deadline, 'new candidate READY after deadline'
                proof['readiness_observations'].append(observed)
                if observed['status'] == 'ok' and observed['midi_input']['connection'] == 'CONNECTED':
                    proof['after'] = observed
                    break
            except (ConnectionRefusedError, ConnectionResetError, TimeoutError, urllib.error.URLError) as exc:
                if isinstance(exc,urllib.error.URLError):
                    assert not isinstance(exc,urllib.error.HTTPError) and isinstance(exc.reason,(ConnectionRefusedError,ConnectionResetError,TimeoutError)),repr(exc)
                proof['readiness_observations'].append({'not_ready': repr(exc)})
            assert time.monotonic() < deadline, 'new candidate did not become ready'
            time.sleep(.05)
        assert proof['before']['pid'] != proof['after']['pid']
        assert proof['after']['status']=='ok' and proof['after']['midi_input']['connection']=='CONNECTED'
        # The new runtime must support a fresh stream, not merely READY health.
        peer=Peer('http://'+HOST+':8788/api/v1/live-notes/stream')
        peers.append(peer)
        proof['fresh_epoch'] = peer.snapshot['initial_epoch']
        assert all(row['initial_epoch'] != proof['fresh_epoch'] for row in proof['streams'])
        proof['complete'] = True
        return proof
    finally:
        cleanup_errors=[]
        for peer in peers:
            try:peer.close()
            except BaseException as error:cleanup_errors.append(repr(error))
        proof['owned_readers_stopped'] = all(peer.closed and peer.child.poll() is not None for peer in peers)
        proof['owned_peer_pids']=[peer.child.pid for peer in peers]
        proof['owned_cleanup_errors']=cleanup_errors
        (root/'sse-live-lifecycle-proof.json').write_text(json.dumps(proof, indent=2)+'\n')
