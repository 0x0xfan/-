from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import yaml

from svf.assets import scan_assets
from svf.media import get_media_duration
from svf.subtitles import distribute_segments, write_srt
from svf.timeline import generate_base_clips, match_assets_for_segments, parse_script_blocks
from svf.renderer import render_video


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_project(config_path: str | Path, seed: int = 42, render: bool = True) -> dict[str, Any]:
    random.seed(seed)
    config_path = Path(config_path)
    project_dir = config_path.parent
    config = load_config(config_path)

    script_path = project_dir / config["input"]["script"]
    voice_path = project_dir / config["input"]["voice_audio"]
    asset_root = project_dir / config["assets"]["root"]
    output_dir = project_dir / config.get("output", {}).get("dir", "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    script_text = script_path.read_text(encoding="utf-8")
    blocks = parse_script_blocks(script_text)
    duration = get_media_duration(voice_path)
    segments = distribute_segments(blocks, duration)

    all_assets = scan_assets(asset_root)
    track2_assets = [a for a in all_assets if "track2_topic" in a["path"]]
    base_assets = [a["path"] for a in all_assets if "track1_base" in a["path"] and a["kind"] in {"video", "image"}]
    bgm_assets = [a["path"] for a in all_assets if "bgm" in a["path"] and a["kind"] == "audio"]

    matched_segments = match_assets_for_segments(
        segments,
        track2_assets,
        config.get("rules", {}).get("events", []),
    )
    base_clips = generate_base_clips(
        total_duration=duration,
        base_assets=base_assets,
        clip_duration=float(config.get("rules", {}).get("base_clip_duration", 3.0)),
        speed=float(config.get("rules", {}).get("base_speed", 1.0)),
    ) if base_assets else []

    timeline = {
        "project": config.get("project", {}),
        "duration": duration,
        "voice_audio": str(voice_path),
        "bgm_audio": bgm_assets[0] if bgm_assets else None,
        "base_clips": base_clips,
        "segments": matched_segments,
        "style": config.get("style", {}),
        "output_video": str(output_dir / config.get("output", {}).get("filename", "final_video.mp4")),
    }

    timeline_path = output_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(matched_segments, output_dir / "subtitles.srt")

    if render:
        render_video(timeline, output_dir / config.get("output", {}).get("filename", "final_video.mp4"))

    return timeline
