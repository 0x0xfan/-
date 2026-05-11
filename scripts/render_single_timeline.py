from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from svf.renderer import render_video


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: render_single_timeline.py <timeline.json> <output.mp4>")
    timeline_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    render_video(timeline, output_path)


if __name__ == "__main__":
    main()
