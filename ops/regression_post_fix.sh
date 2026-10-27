#!/bin/bash
# Post-fix regression battery (run ON the appliance via ssh).
set -u
echo "=== gold1000 library ==="
curl -s --max-time 8 http://127.0.0.1:8788/api/v1/library; echo
echo "=== services ==="
systemctl is-active piano-core.service pluto-piano-server.service
echo "=== learning idle ==="
curl -s --max-time 8 http://127.0.0.1:8788/api/v1/learning | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state"))'
echo "=== journal errors since fix deploy ==="
journalctl -u piano-core.service --since "-20 min" --no-pager 2>/dev/null | grep -ciE "error|traceback" || true
echo "=== play/stop regression ==="
SID=$(curl -s "http://127.0.0.1:8788/api/v1/library/songs?limit=1&status=parsed" | python3 -c 'import json,sys; print(json.load(sys.stdin)["songs"][0]["song_id"])')
curl -s --max-time 10 -X POST -H "Content-Type: application/json" -d "{\"song_id\":\"$SID\"}" http://127.0.0.1:8788/api/v1/playback/play | python3 -c 'import json,sys; print("play:", json.load(sys.stdin).get("state"))'
sleep 2
curl -s --max-time 10 -X POST http://127.0.0.1:8788/api/v1/playback/stop | python3 -c 'import json,sys; d=json.load(sys.stdin); print("stop:", d.get("stopped"))'
curl -s --max-time 8 http://127.0.0.1:8788/api/v1/playback | python3 -c 'import json,sys; d=json.load(sys.stdin); print("final:", d["state"], "| events:", d["events_sent"])'
curl -s --max-time 8 http://127.0.0.1:8788/api/v1/state | python3 -c 'import json,sys; print("midi:", json.load(sys.stdin)["midi_connection"])'
echo "=== learn live (post-fix) ==="
curl -s --max-time 10 http://127.0.0.1:8788/api/v1/learn | python3 -c 'import json,sys; d=json.load(sys.stdin); print("lessons:", d["lesson_count"], "| courses:", d["course_count"])'
