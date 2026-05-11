from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from moviepy.editor import AudioFileClip, CompositeAudioClip, CompositeVideoClip, VideoClip, VideoFileClip, vfx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from svf.renderer import _big_text_frame, _load_big_text_presets, _resolve_project_path


DEFAULT_OUTPUT = Path(r"Z:\办公\B站方面\信息流导出\style_preview_jianying.mp4")
DEFAULT_BACKGROUND_ROOT = Path(r"Z:\办公\B站方面\信息流模板\轨道1")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
PREVIEW_TEXTS = [
    "低成本副业",
    "复制素材",
    "不用囤货",
    "稳定出单",
    "先看这一步",
    "避开价格战",
    "一周实测",
    "闲鱼方法",
    "资料领取",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a preview video for project-local Jianying big-text presets.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Preview mp4 path")
    parser.add_argument("--background", default="", help="Background video path")
    parser.add_argument("--start-index", type=int, default=13, help="First preset index to preview")
    parser.add_argument("--limit", type=int, default=18, help="Number of presets to preview")
    parser.add_argument("--seconds", type=float, default=1.45, help="Seconds per preset")
    args = parser.parse_args()

    presets = _load_big_text_presets()
    if not presets:
        raise SystemExit("No big-text presets found")

    output = Path(args.output)
    background = Path(args.background) if args.background else _first_background_video(DEFAULT_BACKGROUND_ROOT)
    if not background:
        raise SystemExit("No background video found")

    start_index = max(0, min(args.start_index, len(presets) - 1))
    selected = list(range(start_index, min(len(presets), start_index + max(1, args.limit))))
    seconds = max(args.seconds, 0.5)
    total_duration = seconds * len(selected)

    bg_source = VideoFileClip(str(background)).without_audio()
    if bg_source.duration < total_duration:
        bg = bg_source.fx(vfx.loop, duration=total_duration)
    else:
        bg = bg_source.subclip(0, total_duration)
    clips = [bg]
    audio_clips = []

    for slot, preset_index in enumerate(selected):
        start = slot * seconds
        duration = min(seconds, max(total_duration - start, 0.1))
        text = PREVIEW_TEXTS[slot % len(PREVIEW_TEXTS)]
        clips.append(_preset_clip(text, bg.w, bg.h, preset_index, duration).set_start(start).set_duration(duration))
        for effect in presets[preset_index].get("default_sfx", []) or []:
            sfx = _sfx_clip(effect, start, duration)
            if sfx is not None:
                audio_clips.append(sfx)

    final = CompositeVideoClip(clips, size=bg.size).set_duration(total_duration)
    if audio_clips:
        final = final.set_audio(CompositeAudioClip(audio_clips).set_duration(total_duration))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    final.write_videofile(str(output), fps=24, codec="libx264", audio_codec="aac", preset="medium", threads=4, verbose=False, logger=None)
    final.close()
    bg.close()
    bg_source.close()
    print(output)
    print(f"presets={len(presets)} previewed={len(selected)} start_index={start_index}")


def _preset_clip(text: str, width: int, height: int, preset_index: int, duration: float) -> VideoClip:
    cache = {"t": None, "frame": None}

    def rgba_frame(t: float):
        if cache["t"] != t:
            cache["t"] = t
            cache["frame"] = np.array(_big_text_frame(text, width, height, t, duration, preset_index))
        return cache["frame"]

    def rgb_frame(t: float):
        frame = rgba_frame(t)
        return frame[:, :, :3]

    def mask_frame(t: float):
        frame = rgba_frame(t)
        return frame[:, :, 3] / 255.0

    return VideoClip(make_frame=rgb_frame, duration=duration).set_mask(VideoClip(make_frame=mask_frame, ismask=True, duration=duration))


def _sfx_clip(effect: dict, start: float, duration: float):
    path = Path(_resolve_project_path(str(effect.get("path", ""))))
    if not path.exists():
        return None
    try:
        clip = AudioFileClip(str(path))
    except OSError:
        return None
    offset = max(float(effect.get("offset", 0.0) or 0.0), 0.0)
    if offset >= duration:
        clip.close()
        return None
    max_duration = min(float(effect.get("max_duration", 0.65) or 0.65), clip.duration, max(duration - offset, 0.05))
    return clip.subclip(0, max_duration).volumex(min(max(float(effect.get("volume", 1.0) or 1.0) * 0.34, 0.12), 0.48)).set_start(start + offset)


def _first_background_video(root: Path) -> Path | None:
    if not root.exists():
        return None
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            return path
    return None


if __name__ == "__main__":
    main()
