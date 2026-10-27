"""Additional synthetic catalog entries, using the canonical pipeline unchanged."""
import json
from pathlib import Path
from generate_fixtures import build, serialize
from piano_core.midi.output_events import note_on, note_off
from piano_core.playback.timeline import TimelineEvent

fixtures = []
for song_id, title, melody, spacing in [
    ('demo-prelude', 'Quiet Prelude', [60, 67, 64, 72, 67, 64, 62, 65], 1.25),
    ('demo-waltz', 'Window Waltz', [60, 64, 67, 62, 65, 69], 1.5),
]:
    events = []
    for index in range(32):
        midi = melody[index % len(melody)]
        onset = 2 + index * spacing
        events.extend([TimelineEvent(onset, note_on(1, midi, 78)),
                       TimelineEvent(onset + .8, note_off(1, midi, 0))])
    fixtures.append(serialize(build(events, 4 + 32 * spacing), song_id, title))
Path(__file__).with_name('player_library_demo.json').write_text(json.dumps(fixtures, indent=2, sort_keys=True) + '\n')
