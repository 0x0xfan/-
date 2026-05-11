from __future__ import annotations

import subprocess
import wave
from pathlib import Path


def get_media_duration(path: str | Path) -> float:
    media = Path(path)
    if media.suffix.lower() == ".wav":
        with wave.open(str(media), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())
