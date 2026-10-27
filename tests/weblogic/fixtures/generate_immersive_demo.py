"""Local preview fixture through the unchanged canonical score/fingering pipeline."""
import json
from pathlib import Path
from generate_fixtures import build, serialize
from piano_core.midi.output_events import note_on, note_off
from piano_core.playback.timeline import TimelineEvent

events = []
melody = [60, 64, 67, 72, 71, 67, 65, 62]
for index in range(40):
    midi = melody[index % len(melody)]
    onset = 2 + index * 1.5
    events.extend([TimelineEvent(onset, note_on(1, midi, 84)),
                   TimelineEvent(onset + .9, note_off(1, midi, 0))])
payload = serialize(build(events, 64), 'demo-study', 'Evening Study')
Path(__file__).with_name('immersive_demo.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
