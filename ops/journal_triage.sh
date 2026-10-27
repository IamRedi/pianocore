#!/bin/bash
echo "=== the 30 matching journal lines (classes) ==="
journalctl -u piano-core.service --since "-25 min" --no-pager 2>/dev/null | grep -iE "error|traceback" | sed 's/.*piano-core\[[0-9]*\]: //' | sort | uniq -c | sort -rn | head -8
echo "=== playback stop line with session events ==="
journalctl -u piano-core.service --since "-25 min" --no-pager 2>/dev/null | grep "playback stopped" | tail -2
echo "=== broken pipe / client abort lines ==="
journalctl -u piano-core.service --since "-25 min" --no-pager 2>/dev/null | grep -c "BrokenPipeError\|ConnectionResetError" || true
echo "=== client addresses of those lines ==="
journalctl -u piano-core.service --since "-25 min" --no-pager 2>/dev/null | grep -B1 "BrokenPipeError" | grep -oE "from 192\.168\.178\.[0-9]+" | sort | uniq -c | head -4
