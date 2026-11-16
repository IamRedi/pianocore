"""Refresh or verify the presentation's display-only assets from the app.

The website reuses the renderer and a bounded set of pure display mappings;
it never imports app.js or its device/API clients into a browser page.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/piano_core/web/static"


def display_assets():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    model = "/* Pure display geometry copied from the app; see tools/sync_presentation_assets.py. */\n"
    model += app[app.index("const NOTE_NAMES ="):app.index("function boundedNoteSet")]
    model += app[app.index("export const VISUAL_MODES ="):app.index("/**\n * Strict validation of the canonical score payload")]
    for name in ("isOnsetInHalfOpenSection", "fallingPixelsPerSecond", "fallingTimeWindow",
                 "fallingNoteY", "notePlayState", "visibleNoteSlice", "visualCanvasSize"):
        start = app.index("export function " + name + "(")
        body = app.index("{", app.index(") {", start)) + 1
        depth, index, quote, escaped = 1, body, None, False
        while depth:
            char = app[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        model += app[start:index] + "\n\n"
    result = {"stage-model.js": model.encode(), "visualizer.js": (STATIC / "visualizer.js").read_bytes()}
    for sprite in (STATIC / "crystal").glob("*.png"):
        result["crystal/" + sprite.name] = sprite.read_bytes()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = STATIC / "about/assets/native"
    for name, content in display_assets().items():
        path = destination / name
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                raise SystemExit("Presentation display asset drift: " + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print("Presentation renderer, pure geometry and five sprites match the app.")


if __name__ == "__main__":
    main()
