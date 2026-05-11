from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from moviepy.editor import AudioFileClip, CompositeAudioClip, ImageClip, VideoClip, VideoFileClip, CompositeVideoClip, vfx
from moviepy.audio.fx.all import audio_loop
import numpy as np
from moviepy.video import VideoClip as moviepy_video_clip_module

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIG_TEXT_STYLE_PATH = PROJECT_ROOT / "assets" / "styles" / "jianying_big_text_presets.json"


def _memory_friendly_blit(im1, im2, pos=None, mask=None, ismask=False):
    if pos is None:
        pos = [0, 0]

    xp, yp = pos
    x1 = max(0, -xp)
    y1 = max(0, -yp)
    h1, w1 = im1.shape[:2]
    h2, w2 = im2.shape[:2]
    xp2 = min(w2, xp + w1)
    yp2 = min(h2, yp + h1)
    x2 = min(w1, w2 - xp)
    y2 = min(h1, h2 - yp)
    xp1 = max(0, xp)
    yp1 = max(0, yp)

    if (xp1 >= xp2) or (yp1 >= yp2):
        return im2

    blitted = im1[y1:y2, x1:x2]
    new_im2 = np.array(im2, copy=True)

    if mask is None:
        new_im2[yp1:yp2, xp1:xp2] = blitted
    else:
        mask_region = mask[y1:y2, x1:x2]
        if ismask:
            mask_float = mask_region.astype(np.float32, copy=False)
            blit_region = new_im2[yp1:yp2, xp1:xp2].astype(np.float32, copy=False)
            blitted_region = blitted.astype(np.float32, copy=False)
            new_im2[yp1:yp2, xp1:xp2] = mask_float * blitted_region + (1.0 - mask_float) * blit_region
            return new_im2

        alpha = np.clip(mask_region * 255.0, 0, 255).astype(np.uint16, copy=False)
        target = new_im2[yp1:yp2, xp1:xp2]
        if blitted.ndim == 2:
            src = blitted.astype(np.uint16, copy=False)
            dst = target.astype(np.uint16, copy=False)
            target[:] = ((alpha * src + (255 - alpha) * dst + 127) // 255).astype(np.uint8, copy=False)
        else:
            for channel in range(blitted.shape[2]):
                src = blitted[:, :, channel].astype(np.uint16, copy=False)
                dst = target[:, :, channel].astype(np.uint16, copy=False)
                target[:, :, channel] = ((alpha * src + (255 - alpha) * dst + 127) // 255).astype(np.uint8, copy=False)

    return new_im2.astype("uint8", copy=False) if not ismask else new_im2


moviepy_video_clip_module.blit = _memory_friendly_blit


def render_video(timeline: dict[str, Any], output_path: str | Path) -> None:
    if _should_render_in_chunks(timeline):
        _render_video_chunked(timeline, output_path)
        return

    """渲染第一版视频：底层画面 + 轨道2素材 + 底部字幕 + 旁白/BGM。"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    project = timeline.get("project", {})
    width, height = project.get("resolution", [720, 1280])
    fps = int(project.get("fps", 24))
    duration = float(timeline["duration"])
    style = timeline.get("style", {})

    clips = []
    big_text_sfx_events: list[dict[str, Any]] = []
    base_clips = timeline.get("base_clips", [])
    if base_clips:
        for item in base_clips:
            clip_duration = item["end"] - item["start"]
            clip = _make_visual_clip(item["asset"], width, height, clip_duration)
            clip = clip.set_start(item["start"]).set_duration(clip_duration)
            clips.append(clip)
    else:
        clips.append(ImageClip(_solid_frame(width, height, (30, 30, 30))).set_duration(duration))

    segments = timeline.get("segments", [])
    for group in _overlay_groups(segments):
        first = group[0]
        asset = first.get("track2_asset")
        if not asset:
            continue
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        group_duration = max(end - start, 0.1)
        try:
            overlay = _make_group_overlay_clip(group, width, height, group_duration)
        except Exception:
            continue
        overlay = overlay.set_duration(group_duration)
        transition = _choose_overlay_transition(first, asset)
        overlay = _apply_overlay_transition(overlay, transition, width, height, group_duration).set_start(start)
        clips.append(overlay)
        if len(group) > 1 and not _is_keyword_sequence_asset(asset):
            sticker_text = _sticker_text_for_group(group)
            sticker_start = start + min(0.45, group_duration / 4)
            sticker_duration = max(group_duration - 0.45, 0.4)
            sticker_variant = _big_text_variant(sticker_text, 31)
            sticker = _sticker_clip(sticker_text, width, height, variant=sticker_variant).set_start(sticker_start).set_duration(sticker_duration)
            clips.append(sticker)
            big_text_sfx_events.extend(_big_text_sfx_events(sticker_variant, sticker_start, sticker_duration))

    for gap in _long_track2_gaps(segments):
        start = float(gap["start"])
        end = float(gap["end"])
        gap_duration = end - start
        clip = _gap_enrichment_clip(gap, width, height, gap_duration).set_start(start).set_duration(gap_duration)
        clips.append(clip)
        big_text_sfx_events.extend(_gap_big_text_sfx_events(gap, start, gap_duration))

    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        seg_duration = max(end - start, 0.1)
        subtitle = _subtitle_clip(segment.get("text", ""), width, height, style).set_start(start).set_duration(seg_duration)
        clips.append(subtitle)

    video = CompositeVideoClip(clips, size=(width, height)).set_duration(duration)

    audio_clips = []
    voice = timeline.get("voice_audio")
    if voice and Path(voice).exists():
        audio_clips.append(AudioFileClip(voice).volumex(float(style.get("voice_volume", 1.0))))
    bgm = timeline.get("bgm_audio")
    if bgm and Path(bgm).exists():
        bgm_clip = AudioFileClip(bgm).volumex(float(style.get("bgm_volume", 0.1)))
        if bgm_clip.duration < duration:
            bgm_clip = audio_loop(bgm_clip, duration=duration)
        else:
            bgm_clip = bgm_clip.subclip(0, duration)
        audio_clips.append(bgm_clip)
    for event in _dedupe_sfx_events(list(timeline.get("sound_effects", []) or []) + big_text_sfx_events):
        path = _resolve_project_path(str(event.get("path", "")))
        start = max(float(event.get("start", 0)), 0.0)
        if not path or start >= duration or not Path(path).exists():
            continue
        sfx_clip = AudioFileClip(path)
        max_duration = min(float(event.get("max_duration", 0.75)), max(duration - start, 0.05), sfx_clip.duration)
        if max_duration <= 0:
            sfx_clip.close()
            continue
        audio_clips.append(
            sfx_clip.subclip(0, max_duration).volumex(float(event.get("volume", 0.35))).set_start(start)
        )
    if audio_clips:
        video = video.set_audio(CompositeAudioClip(audio_clips))

    _write_video_file(
        video,
        output_path,
        fps=fps,
        audio=True,
        ffmpeg_params=["-movflags", "+faststart", "-avoid_negative_ts", "make_zero"],
    )
    video.close()
    for clip in clips:
        try:
            clip.close()
        except Exception:
            pass


def _should_render_in_chunks(timeline: dict[str, Any]) -> bool:
    if timeline.get("render_chunked") is False:
        return False
    base_count = len(timeline.get("base_clips", []) or [])
    overlay_count = sum(1 for segment in timeline.get("segments", []) or [] if segment.get("track2_asset"))
    duration = float(timeline.get("duration", 0) or 0)
    return duration > 30 or base_count + overlay_count > 16


def _render_video_chunked(timeline: dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    project = timeline.get("project", {})
    width, height = project.get("resolution", [720, 1280])
    fps = int(project.get("fps", 24))
    duration = float(timeline["duration"])
    chunk_seconds = max(3.0, float(timeline.get("render_chunk_seconds", 8.0) or 8.0))
    skipped_assets: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="svf_render_") as temp_root:
        temp_dir = Path(temp_root)
        chunk_paths: list[Path] = []
        start = 0.0
        index = 0
        while start < duration - 0.001:
            end = min(start + chunk_seconds, duration)
            chunk_path = temp_dir / f"chunk_{index:04d}.mp4"
            _render_video_window(timeline, chunk_path, start, end, width, height, fps, skipped_assets)
            chunk_paths.append(chunk_path)
            start = end
            index += 1

        video_only_path = temp_dir / "video_only.mp4"
        _concat_video_chunks(chunk_paths, video_only_path, temp_dir)

        audio_path = temp_dir / "audio.m4a"
        if _write_audio_track(timeline, audio_path, duration):
            _mux_audio(video_only_path, audio_path, output_path)
        else:
            if output_path.exists():
                output_path.unlink()
            shutil.copy2(video_only_path, output_path)

    if skipped_assets:
        log_path = output_path.with_suffix(".skipped_assets.json")
        log_path.write_text(json.dumps(skipped_assets, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_video_window(
    timeline: dict[str, Any],
    output_path: Path,
    window_start: float,
    window_end: float,
    width: int,
    height: int,
    fps: int,
    skipped_assets: list[dict[str, str]],
) -> None:
    window_duration = max(window_end - window_start, 0.1)
    style = timeline.get("style", {})
    clips = [ImageClip(_solid_frame(width, height, (30, 30, 30))).set_duration(window_duration)]

    for item in timeline.get("base_clips", []) or []:
        start = float(item["start"])
        end = float(item["end"])
        if not _overlaps(start, end, window_start, window_end):
            continue
        clip = _make_visual_clip(item["asset"], width, height, end - start)
        clip = _slice_clip_to_window(clip, start, end, window_start, window_end)
        if clip:
            clips.append(clip)

    segments = timeline.get("segments", []) or []
    for group in _overlay_groups(segments):
        first = group[0]
        asset = first.get("track2_asset")
        if not asset:
            continue
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        if not _overlaps(start, end, window_start, window_end):
            continue
        group_duration = max(end - start, 0.1)
        try:
            overlay = _make_group_overlay_clip(group, width, height, group_duration).set_duration(group_duration)
            transition = _choose_overlay_transition(first, asset)
            overlay = _apply_overlay_transition(overlay, transition, width, height, group_duration)
            overlay = _slice_clip_to_window(overlay, start, end, window_start, window_end)
        except Exception as exc:
            skipped_assets.append(
                {
                    "path": str(asset.get("path", "")),
                    "window": f"{window_start:.3f}-{window_end:.3f}",
                    "reason": str(exc),
                }
            )
            continue
        if overlay:
            clips.append(overlay)
        if len(group) > 1 and not _is_keyword_sequence_asset(asset):
            sticker_text = _sticker_text_for_group(group)
            sticker_start = start + min(0.45, group_duration / 4)
            sticker_duration = max(group_duration - 0.45, 0.4)
            sticker_end = sticker_start + sticker_duration
            if _overlaps(sticker_start, sticker_end, window_start, window_end):
                sticker_variant = _big_text_variant(sticker_text, 31)
                sticker = _sticker_clip(sticker_text, width, height, variant=sticker_variant).set_duration(sticker_duration)
                sticker = _slice_clip_to_window(sticker, sticker_start, sticker_end, window_start, window_end)
                if sticker:
                    clips.append(sticker)

    for gap in _long_track2_gaps(segments):
        start = float(gap["start"])
        end = float(gap["end"])
        if not _overlaps(start, end, window_start, window_end):
            continue
        clip = _gap_enrichment_clip(gap, width, height, end - start).set_duration(end - start)
        clip = _slice_clip_to_window(clip, start, end, window_start, window_end)
        if clip:
            clips.append(clip)

    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if not _overlaps(start, end, window_start, window_end):
            continue
        subtitle = _subtitle_clip(segment.get("text", ""), width, height, style).set_duration(max(end - start, 0.1))
        subtitle = _slice_clip_to_window(subtitle, start, end, window_start, window_end)
        if subtitle:
            clips.append(subtitle)

    video = CompositeVideoClip(clips, size=(width, height)).set_duration(window_duration)
    try:
        _write_video_file(video, output_path, fps=fps, audio=False)
    finally:
        video.close()
        _close_clips(clips)


def _overlaps(start: float, end: float, window_start: float, window_end: float) -> bool:
    return start < window_end and end > window_start


def _slice_clip_to_window(clip, start: float, end: float, window_start: float, window_end: float):
    clip_start = max(start, window_start)
    clip_end = min(end, window_end)
    if clip_end <= clip_start:
        clip.close()
        return None
    local_start = max(clip_start - start, 0.0)
    local_end = min(local_start + (clip_end - clip_start), end - start)
    if local_start > 0 or local_end < end - start:
        clip = clip.subclip(local_start, local_end)
    return clip.set_start(clip_start - window_start).set_duration(clip_end - clip_start)


def _concat_video_chunks(chunk_paths: list[Path], output_path: Path, temp_dir: Path) -> None:
    concat_file = temp_dir / "chunks.txt"
    concat_file.write_text("".join(f"file '{path.name}'\n" for path in chunk_paths), encoding="utf-8")
    _run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)])


def _write_audio_track(timeline: dict[str, Any], output_path: Path, duration: float) -> bool:
    style = timeline.get("style", {})
    audio_clips = []
    source_clips = []
    voice = timeline.get("voice_audio")
    if voice and Path(voice).exists():
        voice_clip = AudioFileClip(voice)
        source_clips.append(voice_clip)
        voice_duration = min(duration, voice_clip.duration)
        if voice_duration > 0:
            audio_clips.append(voice_clip.subclip(0, voice_duration).volumex(float(style.get("voice_volume", 1.0))))
    bgm = timeline.get("bgm_audio")
    if bgm and Path(bgm).exists():
        bgm_source = AudioFileClip(bgm)
        source_clips.append(bgm_source)
        if bgm_source.duration < duration:
            bgm_clip = audio_loop(bgm_source, duration=duration)
        else:
            bgm_clip = bgm_source.subclip(0, duration)
        audio_clips.append(bgm_clip.volumex(float(style.get("bgm_volume", 0.1))))
    events = _dedupe_sfx_events(list(timeline.get("sound_effects", []) or []) + _timeline_big_text_sfx_events(timeline))
    for event in events:
        path = _resolve_project_path(str(event.get("path", "")))
        start = max(float(event.get("start", 0)), 0.0)
        if not path or start >= duration or not Path(path).exists():
            continue
        sfx_clip = AudioFileClip(path)
        source_clips.append(sfx_clip)
        max_duration = min(float(event.get("max_duration", 0.75)), max(duration - start, 0.05), sfx_clip.duration)
        if max_duration > 0:
            audio_clips.append(
                sfx_clip.subclip(0, max_duration).volumex(float(event.get("volume", 0.35))).set_start(start)
            )
    if not audio_clips:
        _close_clips(source_clips)
        return False
    audio = CompositeAudioClip(audio_clips).set_duration(duration)
    try:
        audio.write_audiofile(str(output_path), fps=48000, codec="aac", verbose=False, logger=None)
    finally:
        audio.close()
        _close_clips(audio_clips)
        _close_clips(source_clips)
    return True


def _mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    _run_ffmpeg(
        [
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            str(output_path),
        ]
    )


def _write_video_file(
    video,
    output_path: str | Path,
    fps: int,
    audio: bool = True,
    ffmpeg_params: list[str] | None = None,
) -> None:
    codec, preset, codec_params = _video_encoder_options()
    params = [*codec_params, *(ffmpeg_params or [])]
    kwargs = {
        "fps": fps,
        "codec": codec,
        "audio": audio,
        "ffmpeg_params": params,
        "verbose": False,
        "logger": None,
    }
    if audio:
        kwargs["audio_codec"] = "aac"
        kwargs["audio_fps"] = 48000
    if preset:
        kwargs["preset"] = preset
    try:
        video.write_videofile(str(output_path), **kwargs)
    except Exception:
        if codec == "libx264":
            raise
        fallback_params = ["-pix_fmt", "yuv420p", *(ffmpeg_params or [])]
        fallback_kwargs = {
            "fps": fps,
            "codec": "libx264",
            "audio": audio,
            "preset": "veryfast",
            "ffmpeg_params": fallback_params,
            "verbose": False,
            "logger": None,
        }
        if audio:
            fallback_kwargs["audio_codec"] = "aac"
            fallback_kwargs["audio_fps"] = 48000
        video.write_videofile(str(output_path), **fallback_kwargs)


def _video_encoder_options() -> tuple[str, str | None, list[str]]:
    configured = os.getenv("SVF_VIDEO_ENCODER", "auto").strip().lower()
    if configured in {"cpu", "libx264", "x264"}:
        return "libx264", "veryfast", ["-pix_fmt", "yuv420p"]
    if configured in {"nvenc", "h264_nvenc"} or (configured == "auto" and _ffmpeg_has_encoder("h264_nvenc")):
        return "h264_nvenc", "p4", ["-pix_fmt", "yuv420p", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    return "libx264", "veryfast", ["-pix_fmt", "yuv420p"]


@lru_cache(maxsize=8)
def _ffmpeg_has_encoder(name: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0 and name in result.stdout


def _timeline_big_text_sfx_events(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    segments = timeline.get("segments", []) or []
    for group in _overlay_groups(segments):
        first = group[0]
        asset = first.get("track2_asset")
        if not asset:
            continue
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        group_duration = max(end - start, 0.1)
        if len(group) > 1 and not _is_keyword_sequence_asset(asset):
            sticker_text = _sticker_text_for_group(group)
            sticker_start = start + min(0.45, group_duration / 4)
            sticker_duration = max(group_duration - 0.45, 0.4)
            sticker_variant = _big_text_variant(sticker_text, 31)
            events.extend(_big_text_sfx_events(sticker_variant, sticker_start, sticker_duration))
    for gap in _long_track2_gaps(segments):
        start = float(gap["start"])
        end = float(gap["end"])
        gap_duration = end - start
        events.extend(_gap_big_text_sfx_events(gap, start, gap_duration))
    return _dedupe_sfx_events(events)


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}: {detail[-2000:]}")


def _close_clips(clips: list[Any]) -> None:
    seen = set()
    for clip in clips:
        if not clip:
            continue
        marker = id(clip)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            clip.close()
        except Exception:
            pass


def _make_visual_clip(path: str | Path, width: int, height: int, duration: float, stretch_video: bool = False):
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTS:
        return _make_video_clip(path, width, height, duration, stretch_video=stretch_video)
    return _make_image_clip(path, width, height)


def _make_overlay_clip(asset: dict[str, Any], width: int, height: int, duration: float):
    if _is_revenue_asset(asset):
        return _revenue_slideshow_clip(asset, width, height, duration)
    if _is_semantic_sequence_asset(asset):
        return _semantic_sequence_clip(asset, width, height, duration)
    if _is_text_sequence_asset(asset):
        return _text_sequence_clip(asset, width, height, duration)
    if _is_keyword_sequence_asset(asset):
        return _keyword_sequence_clip(asset, width, height, duration)
    path = asset["path"]
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTS:
        return _make_overlay_video_clip(path, width, height, duration)
    return _make_overlay_image_clip(path, width, height, asset)


def _make_group_overlay_clip(group: list[dict[str, Any]], width: int, height: int, duration: float):
    asset = group[0].get("track2_asset") or {}
    if _is_semantic_sequence_asset(asset):
        return _semantic_sequence_group_clip(group, width, height, duration)
    if _is_revenue_asset(asset):
        return _revenue_accumulating_clip(group, width, height, duration)
    return _make_overlay_clip(asset, width, height, duration)


def _overlay_groups(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_path = ""
    for segment in segments:
        asset = segment.get("track2_asset")
        path = str(asset.get("path", "")) if asset else ""
        group_key = _overlay_group_key(asset) if asset else ""
        if not path:
            if current:
                groups.append(current)
                current = []
                current_path = ""
            continue
        can_merge = (
            current
            and group_key == current_path
            and float(segment.get("start", 0)) - float(current[-1].get("end", 0)) <= _overlay_merge_gap(asset)
        )
        if can_merge:
            current.append(segment)
        else:
            if current:
                groups.append(current)
            current = [segment]
            current_path = group_key
    if current:
        groups.append(current)
    return groups


def _overlay_group_key(asset: dict[str, Any]) -> str:
    if _is_semantic_sequence_asset(asset):
        return str(asset.get("path", "semantic_sequence"))
    if _is_text_sequence_asset(asset):
        return str(asset.get("path", "text_sequence"))
    rel_dir = str(asset.get("rel_dir", ""))
    if _is_revenue_asset(asset):
        return f"revenue:{rel_dir or Path(str(asset.get('path', ''))).parent}"
    return str(asset.get("path", ""))


def _overlay_merge_gap(asset: dict[str, Any]) -> float:
    if _is_semantic_sequence_asset(asset):
        return 0.15
    if _is_text_sequence_asset(asset):
        return 0.15
    if _is_revenue_asset(asset):
        return 2.0
    return 0.7


def _long_track2_gaps(segments: list[dict[str, Any]], min_duration: float = 3.0) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for segment in segments:
        if segment.get("track2_asset"):
            if current:
                gaps.extend(_gap_from_segments(current, min_duration))
                current = []
            continue
        current.append(segment)
    if current:
        gaps.extend(_gap_from_segments(current, min_duration))
    return gaps


def _gap_from_segments(segments: list[dict[str, Any]], min_duration: float) -> list[dict[str, Any]]:
    if not segments:
        return []
    start = float(segments[0].get("start", 0))
    end = float(segments[-1].get("end", start))
    gap_duration = end - start
    if gap_duration < min_duration:
        return []
    max_items = _gap_sticker_count(gap_duration)
    selected: list[dict[str, Any]] = []
    used_titles: set[str] = set()
    min_spacing = 1.8
    for segment, theme in _gap_sticker_candidates(segments):
        title = str(theme.get("title", "")).strip()
        if not title or title in used_titles:
            continue
        item_start = max(float(segment.get("start", start)), start)
        if selected and item_start - float(selected[-1]["start"]) < min_spacing:
            continue
        item_end = min(end, item_start + 2.2)
        if item_end - item_start < 0.6:
            continue
        selected.append(
            {
                "start": item_start,
                "end": item_end,
                "text": str(segment.get("text", "")),
                "title": title,
                "icon": str(theme.get("icon", "spark")),
                "accent": tuple(theme.get("accent", (42, 99, 235))),
            }
        )
        used_titles.add(title)
        if len(selected) >= max_items:
            break
    if len(selected) <= 1:
        return selected
    return [{"start": start, "end": end, "text": selected[0]["text"], "items": selected}]


def _gap_sticker_count(duration: float) -> int:
    if duration >= 14.0:
        return 4
    if duration >= 8.0:
        return 3
    return 1


def _gap_sticker_candidates(segments: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for segment in segments:
        theme = _gap_theme(str(segment.get("text", "")))
        if theme.get("skip") or not str(theme.get("title", "")).strip():
            continue
        candidates.append((segment, theme))
    return candidates


def _make_image_clip(path: str | Path, width: int, height: int) -> ImageClip:
    img = Image.open(path).convert("RGB")
    scale = max(width / img.width, height / img.height)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left = max((resized.width - width) // 2, 0)
    top = max((resized.height - height) // 2, 0)
    frame = resized.crop((left, top, left + width, top + height))
    return ImageClip(_pil_to_array(frame))


def _make_overlay_image_clip(path: str | Path, width: int, height: int, asset: dict[str, Any]) -> ImageClip:
    img = Image.open(path).convert("RGBA")
    if img.height > img.width or _is_document_asset(asset) or img.getchannel("A").getextrema()[0] < 255:
        if _is_document_asset(asset):
            max_width = width * 0.66
            max_height = height - 300
        elif img.height > img.width:
            max_width = width * 0.62
            max_height = height - 320
        else:
            max_width = width * 0.72
            max_height = height * 0.72
        scale = min(max_width / img.width, max_height / img.height, 1.0)
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        return _imageclip_from_rgba(img)
    return _make_image_clip(path, width, height)


def _imageclip_from_rgba(image: Image.Image) -> ImageClip:
    rgba = _pil_to_array(image.convert("RGBA"))
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3] / 255.0
    return ImageClip(rgb).set_mask(ImageClip(alpha, ismask=True))


def _resize_clip_if_needed(clip, *, width: int | None = None, height: int | None = None):
    target_width = int(width) if width else None
    target_height = int(height) if height else None
    current_width = int(round(getattr(clip, "w", 0) or 0))
    current_height = int(round(getattr(clip, "h", 0) or 0))

    if target_height and current_height and current_height != target_height:
        clip = clip.resize(height=target_height)
        current_width = int(round(getattr(clip, "w", 0) or 0))
    if target_width and current_width and current_width != target_width:
        clip = clip.resize(width=target_width)
    return clip


def _make_video_clip(path: str | Path, width: int, height: int, duration: float, stretch_video: bool = False):
    clip = VideoFileClip(str(path), audio=False, target_resolution=(height, None), resize_algorithm="fast_bilinear")
    clip = _fit_duration(clip, duration, stretch_video=stretch_video)
    clip = _resize_clip_if_needed(clip, height=height)
    if int(round(getattr(clip, "w", 0) or 0)) < width:
        clip = _resize_clip_if_needed(clip, width=width)
    x_center = clip.w / 2
    y_center = clip.h / 2
    return clip.crop(x_center=x_center, y_center=y_center, width=width, height=height)


def _make_overlay_video_clip(path: str | Path, width: int, height: int, duration: float):
    clip = VideoFileClip(str(path), audio=False, target_resolution=(min(height, 1080), None), resize_algorithm="fast_bilinear")
    clip = _fit_duration(clip, duration, stretch_video=True)
    if clip.h > clip.w:
        scale = min(width / clip.w, (height - 260) / clip.h, 1.0)
        return clip.resize(scale) if scale < 1.0 else clip
    clip = _resize_clip_if_needed(clip, height=height)
    if int(round(getattr(clip, "w", 0) or 0)) < width:
        clip = _resize_clip_if_needed(clip, width=width)
    x_center = clip.w / 2
    y_center = clip.h / 2
    return clip.crop(x_center=x_center, y_center=y_center, width=width, height=height)


def _fit_duration(clip, duration: float, stretch_video: bool = False):
    if stretch_video and clip.duration > 0:
        return clip.fx(vfx.speedx, factor=clip.duration / duration).set_duration(duration)
    if clip.duration < duration:
        return clip.loop(duration=duration)
    return clip.subclip(0, duration)


def _overlay_position(clip, width: int, height: int) -> tuple[float, float]:
    clip_w = getattr(clip, "w", width)
    clip_h = getattr(clip, "h", height)
    if clip_h < height and clip_h > clip_w * 1.2:
        return ((width - clip_w) / 2, 36)
    return ((width - clip_w) / 2, (height - clip_h) / 2)


def _choose_overlay_transition(segment: dict[str, Any], asset: dict[str, Any]) -> str:
    if _is_video_overlay_asset(asset):
        return "cut"
    key = f"{segment.get('start')}|{segment.get('text')}|{asset.get('path')}"
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
    transitions = ["fade", "slide_left", "slide_right", "slide_up", "slide_down", "pop"]
    return transitions[int(digest[:8], 16) % len(transitions)]


def _apply_overlay_transition(clip, transition: str, width: int, height: int, duration: float):
    base_x, base_y = _overlay_position(clip, width, height)
    if transition == "cut":
        return clip.set_position((base_x, base_y))
    transition_duration = min(0.22, max(duration / 5, 0.08))
    clip_w = getattr(clip, "w", width)
    clip_h = getattr(clip, "h", height)

    if transition == "fade":
        return clip.set_position((base_x, base_y))
    if transition == "pop":
        scaled = clip.resize(lambda t: 0.96 + 0.04 * min(t / transition_duration, 1.0))
        return scaled.set_position((base_x, base_y))

    offsets = {
        "slide_left": (-min(width * 0.18, clip_w * 0.35), 0),
        "slide_right": (min(width * 0.18, clip_w * 0.35), 0),
        "slide_up": (0, -min(height * 0.14, clip_h * 0.25)),
        "slide_down": (0, min(height * 0.14, clip_h * 0.25)),
    }
    dx, dy = offsets.get(transition, (0, 0))

    def position(t: float):
        if t < transition_duration:
            progress = _ease_out(t / transition_duration)
            return (base_x + dx * (1 - progress), base_y + dy * (1 - progress))
        if duration - t < transition_duration:
            progress = _ease_out(max((duration - t) / transition_duration, 0.0))
            return (base_x + dx * (1 - progress), base_y + dy * (1 - progress))
        return (base_x, base_y)

    return clip.set_position(position)


def _ease_out(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1 - (1 - value) * (1 - value)


def _is_document_asset(asset: dict[str, Any]) -> bool:
    text = " ".join(str(item) for item in asset.get("tags", [])) + " " + str(asset.get("rel_dir", "")) + " " + str(asset.get("path", ""))
    return any(keyword in text for keyword in ["教程", "资料", "表格", "知识库", "流程", "违禁词", "爆品表", "赠送资料图"])


def _image_with_blurred_backdrop(source_path: str | Path, foreground: Image.Image, width: int, height: int) -> Image.Image:
    background = Image.open(source_path).convert("RGB")
    scale = max(width / background.width, height / background.height)
    background = background.resize((round(background.width * scale), round(background.height * scale)), Image.LANCZOS)
    left = max((background.width - width) // 2, 0)
    top = max((background.height - height) // 2, 0)
    background = background.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(18))
    shade = Image.new("RGB", (width, height), (18, 20, 24))
    background = Image.blend(background, shade, 0.38)
    x = (width - foreground.width) // 2
    y = max(24, (height - foreground.height) // 2 - 40)
    background.paste(foreground, (x, y))
    return background


def _is_revenue_asset(asset: dict[str, Any]) -> bool:
    text = _asset_text(asset)
    return "收益" in text or "收入" in text


def _is_video_overlay_asset(asset: dict[str, Any]) -> bool:
    return Path(str(asset.get("path", ""))).suffix.lower() in VIDEO_EXTS


def _is_operation_asset(asset: dict[str, Any]) -> bool:
    text = _asset_text(asset)
    return "闲鱼操作" in text or "上传图片" in text or "打开闲鱼" in text or "打开上架" in text or "上文案" in text or "改价格" in text or "发布" in text or "发货方式" in text or "图片写标签" in text


def _asset_text(asset: dict[str, Any]) -> str:
    return " ".join(str(item) for item in asset.get("tags", [])) + " " + str(asset.get("rel_dir", "")) + " " + str(asset.get("path", ""))


def _revenue_slideshow_clip(asset: dict[str, Any], width: int, height: int, duration: float) -> VideoClip:
    source_paths = _sibling_image_paths(asset)
    if not source_paths:
        return _make_overlay_image_clip(asset["path"], width, height, asset).set_duration(duration)
    paths = source_paths[: min(3, len(source_paths))]
    reveal_times = _distributed_reveal_times(len(paths), duration)
    layers = _revenue_collage_layers(paths, width, height)

    def make_frame(t: float):
        return _pil_to_array(_composite_revenue_layers(layers, reveal_times, t))

    return _rgba_video_clip(make_frame, duration)


def _revenue_accumulating_clip(group: list[dict[str, Any]], width: int, height: int, duration: float) -> VideoClip:
    paths, reveal_times = _revenue_group_paths_and_times(group, duration)
    if not paths:
        asset = group[0].get("track2_asset") or {}
        return _make_overlay_clip(asset, width, height, duration)
    layers = _revenue_collage_layers(paths, width, height)

    def make_frame(t: float):
        return _pil_to_array(_composite_revenue_layers(layers, reveal_times, t))

    return _rgba_video_clip(make_frame, duration)


def _revenue_group_paths_and_times(group: list[dict[str, Any]], duration: float, max_items: int = 3) -> tuple[list[Path], list[float]]:
    if not group:
        return [], []
    group_start = float(group[0].get("start", 0))
    paths: list[Path] = []
    reveal_times: list[float] = []
    seen: set[str] = set()

    for segment in group:
        asset = segment.get("track2_asset") or {}
        raw_path = str(asset.get("path", ""))
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        relative_start = max(float(segment.get("start", group_start)) - group_start, 0.0)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
        reveal_times.append(relative_start)
        if len(paths) >= max_items:
            return paths, reveal_times

    return paths, reveal_times


def _distributed_reveal_times(count: int, duration: float) -> list[float]:
    return [_distributed_reveal_time(index, count, duration) for index in range(count)]


def _distributed_reveal_time(index: int, count: int, duration: float) -> float:
    if index <= 0 or count <= 1:
        return 0.0
    step = max(duration / count, 0.9)
    return min(index * step, max(duration - 0.35, 0.0))


def _sibling_image_paths(asset: dict[str, Any]) -> list[Path]:
    current = Path(str(asset.get("path", "")))
    folder = current.parent
    if not folder.exists():
        return [current] if current.exists() else []
    images = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTS)
    if current in images:
        images.remove(current)
        images.insert(0, current)
    return images


def _revenue_collage_layers(paths: list[Path], width: int, height: int) -> list[Image.Image]:
    count = max(len(paths), 1)
    slots = _revenue_collage_slots(count, width, height)
    return [_revenue_collage_layer(path, slot, width, height) for path, slot in zip(paths, slots)]


def _revenue_collage_slots(count: int, width: int, height: int) -> list[tuple[int, int, int, int]]:
    safe_top = 42
    safe_bottom = max(safe_top + 520, height - 190)
    safe_h = safe_bottom - safe_top
    if count == 1:
        slot_w = round(width * 0.84)
        return [((width - slot_w) // 2, safe_top, slot_w, safe_h)]
    if count == 2:
        slot_w = min(round(width * 0.62), 1080)
        x0 = round((width - slot_w) / 2 - width * 0.08)
        return [
            (x0, safe_top + 34, slot_w, safe_h - 54),
            (x0 + round(width * 0.16), safe_top, slot_w, safe_h),
        ]

    slot_w = min(round(width * 0.58), 1050)
    x0 = round((width - slot_w) / 2)
    slots = [
        (x0 - round(width * 0.10), safe_top + 58, slot_w, safe_h - 120),
        (x0 + round(width * 0.08), safe_top + 20, slot_w, safe_h - 86),
        (x0 - round(width * 0.02), safe_top + 74, slot_w, safe_h - 18),
    ]
    return slots[:count]


def _revenue_collage_layer(path: Path, slot: tuple[int, int, int, int], width: int, height: int) -> Image.Image:
    x, y, slot_w, slot_h = slot
    image = Image.open(path).convert("RGBA")
    max_w = max(1, slot_w - 26)
    max_h = max(1, slot_h - 26)
    scale = min(max_w / image.width, max_h / image.height)
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = x + (slot_w - image.width) // 2
    py = y + (slot_h - image.height) // 2

    shadow = Image.new("RGBA", (image.width + 36, image.height + 36), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((18, 18, image.width + 18, image.height + 18), radius=28, fill=(0, 0, 0, 92))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    layer.alpha_composite(shadow, (px - 18, py - 18))

    border = Image.new("RGBA", (image.width + 12, image.height + 12), (255, 255, 255, 238))
    layer.alpha_composite(border, (px - 6, py - 6))
    layer.alpha_composite(image, (px, py))
    return layer


def _composite_revenue_layers(layers: list[Image.Image], reveal_times: list[float], t: float) -> Image.Image:
    if not layers:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    canvas = Image.new("RGBA", layers[0].size, (0, 0, 0, 0))
    for layer, reveal_time in zip(layers, reveal_times):
        progress = min(max((t - reveal_time) / 0.22, 0.0), 1.0)
        if progress <= 0:
            continue
        if progress >= 1:
            canvas.alpha_composite(layer)
            continue
        faded = layer.copy()
        alpha = faded.getchannel("A").point(lambda value: round(value * progress))
        faded.putalpha(alpha)
        canvas.alpha_composite(faded)
    return canvas


def _is_keyword_sequence_asset(asset: dict[str, Any]) -> bool:
    tags = [str(tag) for tag in asset.get("tags", [])]
    return bool(tags and tags[0] in {"低成本"})


def _is_semantic_sequence_asset(asset: dict[str, Any]) -> bool:
    return str(asset.get("kind", "")) == "semantic_asset_sequence" or bool(asset.get("sequence_assets"))


def _is_text_sequence_asset(asset: dict[str, Any]) -> bool:
    return str(asset.get("kind", "")) == "generated_text_sequence" or bool(asset.get("sequence_terms"))


def _semantic_sequence_clip(asset: dict[str, Any], width: int, height: int, duration: float) -> VideoClip:
    sequence_assets = [item for item in asset.get("sequence_assets", []) if isinstance(item, dict)]
    bounded = _semantic_sequence_bounded_clips(sequence_assets, width, height)
    if not bounded:
        fallback_asset = sequence_assets[0] if sequence_assets else asset
        return _make_overlay_image_clip(fallback_asset["path"], width, height, fallback_asset).set_duration(duration)
    clips, min_left, min_top, box_w, box_h = bounded
    reveal_times = _distributed_reveal_times(len(clips), duration)
    composite_clips = [
        clip.set_start(reveal).set_duration(max(duration - reveal, 0.1))
        for clip, reveal in zip(clips, reveal_times)
    ]
    return CompositeVideoClip(composite_clips, size=(box_w, box_h)).set_position((min_left, min_top)).set_duration(duration)


def _semantic_sequence_group_clip(group: list[dict[str, Any]], width: int, height: int, duration: float) -> VideoClip:
    asset = group[0].get("track2_asset") or {}
    sequence_assets = [item for item in asset.get("sequence_assets", []) if isinstance(item, dict)]
    bounded = _semantic_sequence_bounded_clips(sequence_assets, width, height)
    if not bounded:
        return _semantic_sequence_clip(asset, width, height, duration)
    clips, min_left, min_top, box_w, box_h = bounded
    reveal_times = _distributed_reveal_times(len(clips), duration)
    composite_clips = [
        clip.set_start(reveal).set_duration(max(duration - reveal, 0.1))
        for clip, reveal in zip(clips, reveal_times)
    ]
    return CompositeVideoClip(composite_clips, size=(box_w, box_h)).set_position((min_left, min_top)).set_duration(duration)


def _semantic_sequence_bounded_clips(
    sequence_assets: list[dict[str, Any]],
    width: int,
    height: int,
) -> tuple[list[ImageClip], int, int, int, int] | None:
    paths = [
        Path(str(item.get("path", "")))
        for item in sequence_assets
        if Path(str(item.get("path", ""))).suffix.lower() in IMAGE_EXTS and Path(str(item.get("path", ""))).exists()
    ][:3]
    if not paths:
        return None
    slots = _revenue_collage_slots(len(paths), width, height)
    bounded_items: list[tuple[ImageClip, int, int, int, int]] = []
    for path, slot in zip(paths, slots):
        layer = _revenue_collage_layer(path, slot, width, height)
        bounded = _bounded_rgba_layer_clip(layer)
        if bounded:
            bounded_items.append(bounded)
    if not bounded_items:
        return None
    min_left = min(item[1] for item in bounded_items)
    min_top = min(item[2] for item in bounded_items)
    max_right = max(item[3] for item in bounded_items)
    max_bottom = max(item[4] for item in bounded_items)
    clips = [
        clip.set_position((left - min_left, top - min_top))
        for clip, left, top, _, _ in bounded_items
    ]
    return clips, min_left, min_top, max_right - min_left, max_bottom - min_top


def _bounded_rgba_layer_clip(layer: Image.Image) -> tuple[ImageClip, int, int, int, int] | None:
    alpha = layer.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    cropped = layer.crop((left, top, right, bottom))
    return _imageclip_from_rgba(cropped), left, top, right, bottom


def _text_sequence_clip(asset: dict[str, Any], width: int, height: int, duration: float) -> VideoClip:
    words = [str(item).strip() for item in asset.get("sequence_terms", []) if str(item).strip()]
    if not words:
        tags = [str(tag).strip() for tag in asset.get("tags", []) if str(tag).strip()]
        words = [tag for tag in tags if tag not in {"文本序列", "大字序列"}]
    words = words[:6] or ["重点"]
    accent = _sequence_accent(words)

    def make_frame(t: float):
        return _pil_to_array(_text_sequence_frame(words, accent, width, height, t, duration))

    return _rgba_video_clip(make_frame, duration)


def _sequence_accent(words: list[str]) -> tuple[int, int, int]:
    digest = hashlib.sha1("|".join(words).encode("utf-8", errors="ignore")).hexdigest()
    palette = [(37, 99, 235), (234, 88, 12), (22, 118, 92), (190, 24, 93), (126, 87, 194), (217, 119, 6)]
    return palette[int(digest[:4], 16) % len(palette)]


def _keyword_sequence_clip(asset: dict[str, Any], width: int, height: int, duration: float) -> VideoClip:
    tags = [str(tag) for tag in asset.get("tags", [])]
    keyword = tags[0] if tags else ""
    if keyword == "低成本":
        words = ["不用囤货", "低成本", "轻启动"]
        accent = (22, 118, 92)
    else:
        words = [keyword]
        accent = (68, 99, 196)

    def make_frame(t: float):
        return _pil_to_array(_keyword_sequence_frame(words, accent, width, height, t, duration))

    return _rgba_video_clip(make_frame, duration)


def _rgba_video_clip(make_rgba_frame, duration: float) -> VideoClip:
    cache: dict[str, Any] = {"t": None, "frame": None}

    def rgba_frame(t: float):
        if cache["t"] != t:
            cache["t"] = t
            cache["frame"] = make_rgba_frame(t)
        return cache["frame"]

    def rgb_frame(t: float):
        frame = rgba_frame(t)
        return frame[:, :, :3]

    def mask_frame(t: float):
        frame = rgba_frame(t)
        if frame.shape[2] < 4:
            import numpy as np

            return np.ones(frame.shape[:2])
        return frame[:, :, 3] / 255.0

    clip = VideoClip(make_frame=rgb_frame, duration=duration)
    return clip.set_mask(VideoClip(make_frame=mask_frame, ismask=True, duration=duration))


def _keyword_sequence_frame(words: list[str], accent: tuple[int, int, int], width: int, height: int, t: float, duration: float) -> Image.Image:
    return _text_sequence_frame(words, accent, width, height, t, duration)


def _text_sequence_frame(words: list[str], accent: tuple[int, int, int], width: int, height: int, t: float, duration: float) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    step = max(duration / max(len(words), 1), 0.5)
    visible = min(len(words), max(1, int(t / step) + 1))
    for index, word in enumerate(words[:visible]):
        reveal = index * step
        layer = _big_text_frame(
            word,
            width,
            height,
            max(t - reveal, 0.0),
            max(duration - reveal, 0.45),
            variant=_big_text_variant(word, index + 17),
            position_override=(0.5, 0.42),
            max_width_ratio=0.7,
        )
        if len(words) > 1:
            layer = _shift_layer_for_sequence(layer, width, height, index, len(words), visible)
        image.alpha_composite(layer)
    return image


def _shift_layer_for_sequence(layer: Image.Image, width: int, height: int, index: int, count: int, visible: int) -> Image.Image:
    bbox = layer.getchannel("A").getbbox()
    if not bbox:
        return layer
    left, top, right, bottom = bbox
    item_w = right - left
    item_h = bottom - top
    columns = min(max(count, 1), 3)
    rows = math.ceil(count / columns)
    slot_w = width * 0.78 / columns
    slot_h = height * 0.42 / max(rows, 1)
    row = index // columns
    col = index % columns
    start_x = width * 0.11
    start_y = height * 0.22
    target_cx = start_x + slot_w * (col + 0.5)
    target_cy = start_y + slot_h * (row + 0.5)
    dx = round(target_cx - (left + item_w / 2))
    dy = round(target_cy - (top + item_h / 2))
    shifted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    _alpha_composite_clipped(shifted, layer, dx, dy)
    return shifted


def _alpha_composite_clipped(base: Image.Image, overlay: Image.Image, x: int, y: int) -> None:
    src_x = max(-x, 0)
    src_y = max(-y, 0)
    dst_x = max(x, 0)
    dst_y = max(y, 0)
    paste_w = min(overlay.width - src_x, base.width - dst_x)
    paste_h = min(overlay.height - src_y, base.height - dst_y)
    if paste_w <= 0 or paste_h <= 0:
        return
    crop = overlay.crop((src_x, src_y, src_x + paste_w, src_y + paste_h))
    base.alpha_composite(crop, (dst_x, dst_y))


def _sticker_clip(text: str, width: int, height: int, variant: int | None = None) -> ImageClip:
    selected = _big_text_variant(text, 31) if variant is None else variant
    return ImageClip(
        _pil_to_array(
            _big_text_frame(
                text,
                width,
                height,
                0.32,
                1.2,
                variant=selected,
                position_override=(0.5, 0.42),
                max_width_ratio=0.78,
            )
        )
    ).set_position((0, 0))


def _legacy_sticker_clip(text: str, width: int, height: int) -> ImageClip:
    canvas = Image.new("RGBA", (360, 150), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _load_font("C:/Windows/Fonts/msyh.ttc", 46)
    draw.rounded_rectangle((18, 38, 330, 122), radius=34, fill=(255, 235, 28, 245), outline=(41, 87, 255, 255), width=5)
    draw.text((58, 54), text, font=font, fill=(31, 77, 230), stroke_width=2, stroke_fill=(255, 255, 255))
    draw.rectangle((312, 12, 330, 48), fill=(255, 47, 91, 255))
    draw.rectangle((30, 12, 48, 48), fill=(255, 47, 91, 255))
    x = width - 410
    y = 128
    return ImageClip(_pil_to_array(canvas)).set_position((x, y))


def _sticker_text_for_group(group: list[dict[str, Any]]) -> str:
    joined = "".join(str(segment.get("text", "")) for segment in group)
    if any(marker in joined for marker in ["这么多", "情况", "真实"]):
        return "真实到账"
    if any(marker in joined for marker in ["囤货", "成本"]):
        return "门槛低"
    return "太香了"


def _gap_enrichment_clip(gap: str | dict[str, Any], width: int, height: int, duration: float) -> VideoClip:
    if isinstance(gap, dict) and len(gap.get("items", []) or []) > 1:
        gap_start = float(gap.get("start", 0))
        items = _gap_sequence_items(gap.get("items", []) or [], gap_start)

        def make_sequence_frame(t: float):
            return _pil_to_array(_gap_sequence_frame(items, width, height, t))

        return _rgba_video_clip(make_sequence_frame, duration)

    text = str(gap.get("text", "")) if isinstance(gap, dict) else str(gap)
    theme = _gap_theme(text)

    def make_frame(t: float):
        return _pil_to_array(_gap_enrichment_frame(theme, width, height, t, duration))

    return _rgba_video_clip(make_frame, duration)


def _gap_sequence_items(raw_items: list[dict[str, Any]], gap_start: float) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in raw_items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        items.append(
            {
                "title": title,
                "icon": str(item.get("icon", "spark")),
                "accent": tuple(item.get("accent", (42, 99, 235))),
                "reveal": max(float(item.get("start", gap_start)) - gap_start, 0.0),
            }
        )
    return items


def _gap_sequence_frame(items: list[dict[str, Any]], width: int, height: int, t: float) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not items:
        return canvas
    visible = [item for item in items if t >= float(item.get("reveal", 0.0))]
    if not visible:
        return canvas

    for index, item in enumerate(items):
        if item not in visible:
            continue
        reveal = float(item.get("reveal", 0.0))
        title = str(item.get("title", "")).strip()
        layer = _big_text_frame(
            title,
            width,
            height,
            max(t - reveal, 0.0),
            1.8,
            variant=_big_text_variant(title, index),
            position_override=(0.5, 0.42),
            max_width_ratio=0.78,
        )
        canvas.alpha_composite(layer)
    return canvas


def _gap_theme(text: str) -> dict[str, Any]:
    normalized = text.replace(" ", "")
    if "几百单" in normalized or "出单" in normalized:
        return {"title": "稳定出单", "subtitle": "", "icon": "money", "accent": (22, 118, 92)}
    if "空余时间" in normalized or ("每天" in normalized and ("小时" in normalized or "空" in normalized)):
        return {"title": "碎片时间", "subtitle": "", "icon": "clock", "accent": (42, 99, 235)}
    if "闲鱼" in normalized and "方法" in normalized:
        return {"title": "闲鱼方法", "subtitle": "", "icon": "spark", "accent": (234, 88, 12)}
    themes = [
        (["额外收入", "副业", "赚"], "副业收入", "money", (22, 118, 92)),
        (["方法", "分享"], "实操方法", "spark", (234, 88, 12)),
        (["一本万利", "无数次"], "一次复用", "loop", (126, 87, 194)),
        (["千人千面"], "千人千面", "target", (126, 87, 194)),
        (["价格战"], "避开价格战", "target", (220, 38, 38)),
        (["操作", "教程"], "操作教程", "gift", (217, 119, 6)),
        (["领取"], "资料领取", "gift", (217, 119, 6)),
    ]
    for markers, title, icon, accent in themes:
        if any(marker in normalized for marker in markers):
            return {"title": title, "subtitle": "", "icon": icon, "accent": accent}
    cleaned = normalized.strip("，。！？；,.!?;")
    title = _gap_fallback_title(cleaned)
    return {"title": title, "subtitle": "", "icon": "spark", "accent": (42, 99, 235), "skip": not bool(title)}


def _gap_fallback_title(text: str) -> str:
    cleaned = text.strip("，。！？；,.!?;")
    if not cleaned:
        return ""
    if "一周" in cleaned or "这段时间" in cleaned or "最近" in cleaned:
        return "一周实测"
    if "图片" in cleaned and "文案" in cleaned and any(marker in cleaned for marker in ["复制", "找到", "搬"]):
        return "复制素材"
    if "复制" in cleaned and "粘贴" in cleaned:
        return "复制粘贴"
    if "买一次" in cleaned and "卖" in cleaned:
        return "一次复用"
    if "千人千面" in cleaned:
        return "千人千面"
    if "价格战" in cleaned:
        return "避开价格战"
    if "素材" in cleaned and any(marker in cleaned for marker in ["找", "搬", "复用"]):
        return "素材复用"
    if "方法" in cleaned and "闲鱼" in cleaned:
        return "闲鱼方法"
    if "方法" in cleaned:
        return "实操方法"
    if "发货" in cleaned:
        return "自动发货"
    if "成本" in cleaned:
        return "成本很低"
    if "几百单" in cleaned or "出单" in cleaned:
        return "稳定出单"
    if "怎么操作" in cleaned or "不清楚操作" in cleaned:
        return "操作教程"
    if "闲鱼" in cleaned:
        return "闲鱼实操"
    generic = _generic_gap_label(cleaned)
    if generic:
        return generic
    for prefix in ("然后", "就是", "所以", "如果", "那么", "这个", "这些", "大家"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 2:
            cleaned = cleaned[len(prefix) :]
            break
    return ""


def _generic_gap_label(text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(text or ""))
    cleaned = cleaned.strip("，。！？；,.!?;：:、")
    if not cleaned:
        return ""
    keyword = _extract_gap_keyword(cleaned)
    if keyword:
        return keyword
    for pattern in [
        r"(不用[^，。！？；,.!?;]{1,6})",
        r"(不需要[^，。！？；,.!?;]{1,6})",
        r"(直接[^，。！？；,.!?;]{1,6})",
        r"(先[^，。！？；,.!?;]{1,6})",
        r"(重点看[^，。！？；,.!?;]{1,6})",
        r"(核心[^，。！？；,.!?;]{1,6})",
    ]:
        match = re.search(pattern, cleaned)
        if match:
            return _fit_gap_label(match.group(1))
    stripped = re.sub(r"^(然后|就是|所以|如果|那么|这个|这些|大家|你要做的|第一步|第二步|第三步|第四步|第五步|第六步)", "", cleaned)
    stripped = re.sub(r"(可以|就是|真的|其实|因为|以后|以后就).*", "", stripped)
    return _fit_gap_label(stripped) if _looks_like_short_keyword(stripped) else ""


def _extract_gap_keyword(text: str) -> str:
    exact_markers = [
        "没利润",
        "有利润",
        "利润高",
        "利润低",
        "没收益",
        "有收益",
        "低成本",
        "低门槛",
        "不用物流",
        "不用囤货",
        "不用发货",
        "不用压库存",
        "自动发货",
        "运营教程",
        "知识库",
        "违规词库",
        "选品库",
        "爆品库",
        "实操流程",
        "出单教程",
        "网盘拉新",
        "会员佣金",
    ]
    for marker in exact_markers:
        if marker in text:
            return marker
    if "利润" in text:
        if any(marker in text for marker in ["肯定没", "没有", "没"]):
            return "没利润"
        return "利润"
    return ""


def _looks_like_short_keyword(text: str) -> bool:
    value = str(text or "").strip("，。！？；,.!?;：:、 ")
    if not (2 <= len(value) <= 6):
        return False
    if any(marker in value for marker in ["这个", "那个", "大家", "觉得", "肯定", "其实", "因为", "所以"]):
        return False
    return True


def _fit_gap_label(text: str) -> str:
    value = str(text or "").strip("，。！？；,.!?;：:、 ")
    if len(value) < 2:
        return ""
    if len(value) <= 10:
        return value
    for marker in ["，", "。", "！", "？", "；", ",", ".", "!", "?", ";"]:
        if marker in value:
            value = value.split(marker, 1)[0]
            break
    if len(value) <= 10:
        return value
    return value[:10]


def _gap_enrichment_frame(theme: dict[str, Any], width: int, height: int, t: float, duration: float) -> Image.Image:
    if theme.get("skip"):
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))
    title = str(theme.get("title", "")).strip()
    if not title:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))
    return _big_text_frame(
        title,
        width,
        height,
        t,
        duration,
        variant=_big_text_variant(title, 0),
        position_override=(0.5, 0.42),
        max_width_ratio=0.82,
    )


def _fit_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_size: int,
    min_size: int,
    max_width: int,
    stroke_width: int = 0,
) -> tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont, tuple[int, int, int, int]]:
    value = text.strip() or "重点来了"
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_path, size, sample_text=value)
        bbox = draw.textbbox((0, 0), value, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return value, font, bbox
    font = _load_font(font_path, min_size, sample_text=value)
    suffix = "..."
    while len(value) > 1:
        candidate = value[:-1].rstrip() + suffix
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return candidate, font, bbox
        value = value[:-1].rstrip()
    bbox = draw.textbbox((0, 0), value, font=font, stroke_width=stroke_width)
    return value, font, bbox


def _fit_big_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_size: int,
    min_size: int,
    max_width: int,
    stroke_width: int = 0,
    max_lines: int = 2,
) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont, list[tuple[int, int, int, int]]]:
    value = str(text or "").strip() or "重点来了"
    line_options = _big_text_line_options(value, max_lines=max_lines)
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_path, size, sample_text=value)
        for lines in line_options:
            bboxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in lines]
            if all((bbox[2] - bbox[0]) <= max_width for bbox in bboxes):
                return lines, font, bboxes
    font = _load_font(font_path, min_size, sample_text=value)
    best_lines = line_options[-1]
    bboxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in best_lines]
    if all((bbox[2] - bbox[0]) <= max_width for bbox in bboxes):
        return best_lines, font, bboxes
    wrapped = _wrap_big_text_to_width(draw, value, font, max_width, stroke_width, max_lines)
    bboxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in wrapped]
    return wrapped, font, bboxes


def _big_text_line_options(text: str, max_lines: int = 2) -> list[list[str]]:
    value = str(text or "").strip()
    if not value:
        return [["重点来了"]]
    options: list[list[str]] = [[value]]
    if max_lines >= 2 and len(value) >= 5:
        split = _balanced_text_split(value)
        if split and split not in options:
            options.append(split)
    return options


def _balanced_text_split(text: str) -> list[str] | None:
    value = str(text or "").strip()
    if len(value) < 5:
        return None
    preferred = [
        "以后",
        "之后",
        "然后",
        "但是",
        "因为",
        "所以",
        "不用",
        "不要",
        "直接",
        "每天",
        "晚上",
        "小时",
        "资料",
        "交付",
    ]
    candidates = set(range(max(2, len(value) // 2 - 2), min(len(value) - 1, len(value) // 2 + 3) + 1))
    for marker in preferred:
        index = value.find(marker)
        if 1 < index < len(value) - 1:
            candidates.add(index)
        end = index + len(marker)
        if index >= 0 and 1 < end < len(value) - 1:
            candidates.add(end)
    best: tuple[int, int] | None = None
    for split in sorted(candidates):
        left = value[:split].strip()
        right = value[split:].strip()
        if len(left) < 2 or len(right) < 2:
            continue
        score = abs(len(left) - len(right))
        if best is None or score < best[0]:
            best = (score, split)
    if best is None:
        return None
    split = best[1]
    return [value[:split].strip(), value[split:].strip()]


def _wrap_big_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    stroke_width: int,
    max_lines: int,
) -> list[str]:
    value = str(text or "").strip()
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if current and bbox[2] - bbox[0] > max_width and len(lines) < max_lines - 1:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return lines
    return [*lines[: max_lines - 1], "".join(lines[max_lines - 1 :])]


def _big_text_frame(
    text: str,
    width: int,
    height: int,
    t: float,
    duration: float,
    variant: int = 0,
    position_override: tuple[float, float] | None = None,
    max_width_ratio: float | None = None,
) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    value = str(text or "").strip()
    if not value:
        return canvas
    style = _big_text_style(variant)
    font_path = _resolve_project_path(style.get("font", ""))
    fill = str(style.get("fill", "#ffffff"))
    stroke = str(style.get("stroke", "#000000"))
    stroke_width = int(style.get("stroke_width", 0) or 0)
    shadow = bool(style.get("shadow", True))
    entry_duration = float(style.get("entry_duration", 0.28) or 0.28)
    progress = min(max(t / max(entry_duration, 0.08), 0.0), 1.0)
    eased = _big_text_ease(style.get("animation", "pop"), progress)
    fade_out = min(max((duration - t) / 0.22, 0.0), 1.0)
    alpha = round(max(0, min(255, 255 * eased * fade_out)))
    size_multiplier = float(style.get("size_multiplier", 1.0) or 1.0)
    scale = (0.78 + 0.22 * eased) * size_multiplier
    draw = ImageDraw.Draw(canvas)
    max_size = 112 if width >= height else 92
    min_size = 46 if width >= height else 38
    safe_x, safe_y = _big_text_safe_margins(width, height)
    configured_ratio = float(style.get("max_width_ratio", 0.72) or 0.72)
    active_ratio = max_width_ratio if max_width_ratio is not None else configured_ratio
    max_width = min(int(width * active_ratio), max(1, width - safe_x * 2))
    lines, font, bboxes = _fit_big_text_lines(
        draw,
        value,
        font_path,
        max_size,
        min_size,
        max_width,
        stroke_width=stroke_width,
        max_lines=2,
    )
    scaled_size = max(min_size, round(getattr(font, "size", max_size) * scale))
    font = _load_font(font_path, scaled_size, sample_text=value)
    lines, font, bboxes = _fit_big_text_lines(
        draw,
        value,
        font_path,
        scaled_size,
        min_size,
        max_width,
        stroke_width=stroke_width,
        max_lines=2,
    )
    line_gap = max(6, round(getattr(font, "size", max_size) * float(style.get("line_spacing", 0.12) or 0.12)))
    text_w = max((bbox[2] - bbox[0] for bbox in bboxes), default=0)
    text_h = sum((bbox[3] - bbox[1] for bbox in bboxes), 0) + line_gap * max(len(lines) - 1, 0)

    position = style.get("position") if isinstance(style.get("position"), dict) else {}
    if position_override is None:
        px = float(position.get("x", 0.5) or 0.5)
        py = float(position.get("y", 0.24) or 0.24)
    else:
        px, py = position_override
    dx, dy = _big_text_motion(style.get("animation", "pop"), progress, variant)
    group_x = round(width * px - text_w / 2 + dx)
    group_y = round(height * py - text_h / 2 + dy)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)

    if shadow or not stroke_width:
        shadow_color = _hex_to_rgba(str(style.get("shadow_color", "#000000")), round(255 * float(style.get("shadow_alpha", 0.58) or 0.58) * alpha / 255))
        shadow_distance = float(style.get("shadow_distance", 5.0) or 5.0)
        shadow_angle = math.radians(float(style.get("shadow_angle", -45.0) or -45.0))
        shadow_dx = round(math.cos(shadow_angle) * shadow_distance + 6)
        shadow_dy = round(math.sin(shadow_angle) * shadow_distance + 10)
        shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        _draw_big_text_lines(sdraw, lines, bboxes, font, group_x + shadow_dx, group_y + shadow_dy, text_w, line_gap, shadow_color, stroke_width, shadow_color)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(float(style.get("shadow_blur", 1.0) or 1.0)))
        layer.alpha_composite(shadow_layer)
    rotation = float(style.get("rotation", 0.0) or 0.0) * eased
    _draw_big_text_lines(
        layer_draw,
        lines,
        bboxes,
        font,
        group_x,
        group_y,
        text_w,
        line_gap,
        _hex_to_rgba(fill, alpha),
        stroke_width,
        _hex_to_rgba(stroke, alpha),
    )
    if abs(rotation) >= 0.5:
        layer = layer.rotate(rotation, resample=Image.BICUBIC, center=(round(width * px + dx), round(height * py + dy)))
    layer = _keep_layer_in_safe_area(layer, width, height, safe_x, safe_y)
    canvas.alpha_composite(layer)
    return canvas


def _draw_big_text_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    bboxes: list[tuple[int, int, int, int]],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    group_x: int,
    group_y: int,
    text_w: int,
    line_gap: int,
    fill: tuple[int, int, int, int],
    stroke_width: int,
    stroke_fill: tuple[int, int, int, int],
) -> None:
    cursor_y = group_y
    for line, bbox in zip(lines, bboxes):
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        x = round(group_x + (text_w - line_w) / 2 - bbox[0])
        y = round(cursor_y - bbox[1])
        draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        cursor_y += line_h + line_gap


def _big_text_style(variant: int) -> dict[str, Any]:
    presets = _load_big_text_presets()
    if not presets:
        return {"font": "C:/Windows/Fonts/msyh.ttc", "fill": "#ffffff", "stroke": "#000000", "stroke_width": 4, "shadow": True}
    return presets[variant % len(presets)]


def _big_text_variant(text: str, salt: int = 0) -> int:
    presets = _load_big_text_presets()
    if not presets:
        return salt
    digest = hashlib.sha1(f"{salt}:{text}".encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % len(presets)


def _big_text_sfx_events(variant: int, start: float, duration: float) -> list[dict[str, Any]]:
    style = _big_text_style(variant)
    effects = []
    for effect in style.get("default_sfx", []) or []:
        path = str(effect.get("path", ""))
        if not path:
            continue
        offset = max(float(effect.get("offset", 0.0) or 0.0), 0.0)
        if offset >= duration:
            continue
        effects.append((offset, effect))
    if not effects:
        return []
    offset, effect = min(effects, key=lambda item: (item[0] > 0.25, item[0]))
    return [
        {
            "path": str(effect.get("path", "")),
            "start": start + offset,
            "volume": min(max(float(effect.get("volume", 1.0) or 1.0) * 0.22, 0.08), 0.28),
            "max_duration": min(float(effect.get("max_duration", 0.45) or 0.45), 0.45, max(duration - offset, 0.05)),
            "reason": "big_text_style",
        }
    ]


def _gap_big_text_sfx_events(gap: str | dict[str, Any], start: float, duration: float) -> list[dict[str, Any]]:
    if isinstance(gap, dict) and len(gap.get("items", []) or []) > 1:
        gap_start = float(gap.get("start", start))
        events = []
        for index, item in enumerate(_gap_sequence_items(gap.get("items", []) or [], gap_start)):
            title = str(item.get("title", "")).strip()
            reveal = float(item.get("reveal", 0.0) or 0.0)
            variant = _big_text_variant(title, index)
            events.extend(_big_text_sfx_events(variant, start + reveal, max(duration - reveal, 0.1)))
        return _dedupe_sfx_events(events)

    text = str(gap.get("text", "")) if isinstance(gap, dict) else str(gap)
    theme = _gap_theme(text)
    title = str(theme.get("title", "")).strip()
    if theme.get("skip") or not title:
        return []
    return _big_text_sfx_events(_big_text_variant(title, 0), start, duration)


def _dedupe_sfx_events(events: list[dict[str, Any]], min_gap: float = 0.8) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: float(item.get("start", 0) or 0)):
        path = str(event.get("path", ""))
        if not path:
            continue
        start = max(float(event.get("start", 0) or 0), 0.0)
        if deduped and start - float(deduped[-1].get("start", 0) or 0) < min_gap:
            continue
        normalized = dict(event)
        normalized["start"] = start
        normalized["volume"] = min(float(normalized.get("volume", 0.22) or 0.22), 0.3)
        normalized["max_duration"] = min(float(normalized.get("max_duration", 0.45) or 0.45), 0.5)
        deduped.append(normalized)
    return deduped


def _big_text_ease(animation: Any, progress: float) -> float:
    x = min(max(progress, 0.0), 1.0)
    name = str(animation or "pop")
    if name in {"bounce", "tilt_pop"}:
        c1 = 1.70158
        c3 = c1 + 1
        return min(1.15, 1 + c3 * pow(x - 1, 3) + c1 * pow(x - 1, 2))
    if name == "flip":
        return math.sin(x * math.pi / 2)
    if name == "split":
        return _ease_out(x)
    return _ease_out(x)


def _big_text_motion(animation: Any, progress: float, variant: int) -> tuple[float, float]:
    x = min(max(progress, 0.0), 1.0)
    eased = _ease_out(x)
    name = str(animation or "pop")
    if name == "slide_left":
        return (-44 * (1 - eased), 18 * (1 - eased))
    if name == "slide_right":
        return (44 * (1 - eased), 18 * (1 - eased))
    if name == "slide_mix":
        direction = -1 if variant % 2 else 1
        return (direction * 52 * (1 - eased), 18 * (1 - eased))
    if name == "rise":
        return (0, 34 * (1 - eased))
    if name == "flip":
        return (0, 10 * math.sin((1 - x) * math.pi))
    if name == "split":
        direction = -1 if variant % 2 else 1
        return (direction * 20 * (1 - eased), 16 * (1 - eased))
    if name == "pulse":
        return (0, 10 * math.sin(x * math.pi) * (1 - x))
    return (0, 20 * (1 - eased))


def _big_text_safe_margins(width: int, height: int) -> tuple[int, int]:
    return max(28, round(width * 0.035)), max(24, round(height * 0.045))


def _keep_layer_in_safe_area(layer: Image.Image, width: int, height: int, safe_x: int, safe_y: int) -> Image.Image:
    bbox = layer.getchannel("A").getbbox()
    if not bbox:
        return layer
    left, top, right, bottom = bbox
    dx = 0
    dy = 0
    if left < safe_x:
        dx = safe_x - left
    elif right > width - safe_x:
        dx = width - safe_x - right
    if top < safe_y:
        dy = safe_y - top
    elif bottom > height - safe_y:
        dy = height - safe_y - bottom
    if not dx and not dy:
        return layer
    shifted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shifted.alpha_composite(layer, (round(dx), round(dy)))
    return shifted


@lru_cache(maxsize=1)
def _load_big_text_presets() -> list[dict[str, Any]]:
    if not BIG_TEXT_STYLE_PATH.exists():
        return []
    try:
        data = json.loads(BIG_TEXT_STYLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    presets = data.get("presets", [])
    return presets if isinstance(presets, list) else []


def _resolve_project_path(path: str) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    return str(PROJECT_ROOT / candidate)


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = (value or "#ffffff").strip()
    if not value.startswith("#") or len(value) not in {4, 7}:
        return (255, 255, 255, alpha)
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), alpha)


def _gap_icon_symbol(icon: str) -> str:
    symbols = {
        "clock": "1h",
        "money": "+",
        "spark": "!",
        "loop": "∞",
        "target": "准",
        "gift": "领",
    }
    return symbols.get(icon, "!")


def _subtitle_clip(text: str, width: int, height: int, style: dict[str, Any]) -> ImageClip:
    max_width = int(width * float(style.get("subtitle_max_width_ratio", 0.88) or 0.88))
    max_lines = max(1, int(style.get("subtitle_max_lines", 2) or 2))
    stroke_width = max(0, int(style.get("subtitle_stroke_width", 3) or 0))
    line_gap = max(0, int(style.get("subtitle_line_gap", 10) or 0))
    font_size = max(18, int(style.get("subtitle_font_size", 44) or 44))
    min_font_size = max(18, int(style.get("subtitle_min_font_size", min(30, font_size)) or min(30, font_size)))
    font_path = _resolve_project_path(str(style.get("font", "")))

    scratch = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    lines, font = _fit_subtitle_lines(
        draw,
        str(text or ""),
        font_path,
        font_size,
        min_font_size,
        max_width,
        max_lines,
        stroke_width,
    )
    metrics = [_text_bbox(draw, line, font, stroke_width) for line in lines]
    text_height = sum(bbox[3] - bbox[1] for bbox in metrics) + line_gap * max(len(lines) - 1, 0)
    pad_x = max(18, stroke_width * 4 + 10)
    pad_y = max(14, stroke_width * 3 + 8)
    shadow_enabled = bool(style.get("subtitle_shadow", False))
    shadow_offset = style.get("subtitle_shadow_offset", [0, 0])
    try:
        shadow_dx = int(shadow_offset[0])
        shadow_dy = int(shadow_offset[1])
    except (TypeError, ValueError, IndexError):
        shadow_dx = shadow_dy = 0
    shadow_extra_x = abs(shadow_dx) + (8 if shadow_enabled else 0)
    shadow_extra_y = abs(shadow_dy) + (8 if shadow_enabled else 0)
    canvas_height = min(height, max(1, text_height + pad_y * 2 + shadow_extra_y))
    canvas = Image.new("RGBA", (width, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    y = pad_y
    fill = _color_to_rgba(style.get("subtitle_fill", "white"), 255)
    stroke_fill = _color_to_rgba(style.get("subtitle_stroke", "black"), 255)
    for line, bbox in zip(lines, metrics):
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = max(pad_x, min(width - text_w - pad_x, (width - text_w) // 2)) - bbox[0]
        text_y = y - bbox[1]
        if shadow_enabled:
            alpha = int(255 * max(0.0, min(1.0, float(style.get("subtitle_shadow_alpha", 0.45) or 0.45))))
            shadow_color = _color_to_rgba(style.get("subtitle_shadow_color", "#000000"), alpha)
            shadow_layer = Image.new("RGBA", (width, canvas_height), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_layer)
            shadow_draw.text(
                (x + shadow_dx, text_y + shadow_dy),
                line,
                font=font,
                fill=shadow_color,
                stroke_width=stroke_width,
                stroke_fill=shadow_color,
            )
            canvas.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(3)))
        draw.text(
            (x, text_y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        y += text_h + line_gap
    bottom_margin = max(0, int(style.get("subtitle_bottom_margin", 66) or 66))
    y_pos = max(0, height - canvas_height - bottom_margin)
    return ImageClip(_pil_to_array(canvas)).set_position((0, y_pos))


def _fit_subtitle_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_size: int,
    min_size: int,
    max_width: int,
    max_lines: int,
    stroke_width: int,
) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    value = _normalize_subtitle_text(text)
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_path, size, sample_text=value)
        lines = _wrap_text_to_width(draw, value, font, max_width, max_lines, stroke_width)
        if lines and "".join(lines) == value and all(_text_width(draw, line, font, stroke_width) <= max_width for line in lines):
            return lines, font
    font = _load_font(font_path, min_size, sample_text=value)
    return _wrap_text_to_width(draw, value, font, max_width, max_lines, stroke_width, ellipsize=True), font


def _normalize_subtitle_text(text: str) -> str:
    value = "".join(str(text or "").split())
    return value or " "


def _wrap_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
    stroke_width: int,
    ellipsize: bool = False,
) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font, stroke_width) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    lines = [_pull_leading_punctuation(lines, index) for index in range(len(lines))]
    lines = [line for line in lines if line] or [""]
    if ellipsize:
        if len(lines) >= max_lines and "".join(lines) != text:
            lines[-1] = _ellipsize_to_width(draw, lines[-1], font, max_width, stroke_width)
        lines = [_ellipsize_to_width(draw, line, font, max_width, stroke_width) if _text_width(draw, line, font, stroke_width) > max_width else line for line in lines]
    return lines or [""]


def _pull_leading_punctuation(lines: list[str], index: int) -> str:
    if index <= 0 or not lines[index]:
        return lines[index]
    punctuation = "，。！？；,.!?;、：:"
    line = lines[index]
    while line and line[0] in punctuation:
        lines[index - 1] += line[0]
        line = line[1:]
    return line


def _ellipsize_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    stroke_width: int,
) -> str:
    suffix = "..."
    value = text.rstrip()
    while value and _text_width(draw, value + suffix, font, stroke_width) > max_width:
        value = value[:-1].rstrip()
    return (value + suffix) if value else suffix


def _text_bbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    stroke_width: int,
) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text or " ", font=font, stroke_width=stroke_width)


def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    stroke_width: int,
) -> int:
    bbox = _text_bbox(draw, text, font, stroke_width)
    return bbox[2] - bbox[0]


def _color_to_rgba(value: Any, alpha: int = 255) -> tuple[int, int, int, int] | str:
    if isinstance(value, str) and value.strip().startswith("#"):
        return _hex_to_rgba(value, alpha)
    return value if isinstance(value, str) else (255, 255, 255, alpha)


def _wrap_text(text: str, max_chars: int) -> list[str]:
    raw_lines = [text[i : i + max_chars] for i in range(0, len(text), max_chars)] or [""]
    lines: list[str] = []
    for line in raw_lines:
        if lines and line in {"，", ",", "。", "！", "？", "；", ".", "!", "?", ";"}:
            lines[-1] += line
        else:
            lines.append(line)
    return lines


def _load_font(font_path: str, size: int, sample_text: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path] if font_path else []
    candidates += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            font = ImageFont.truetype(candidate, size=size)
            if _font_can_render(font, sample_text):
                return font
    return ImageFont.load_default()


def _font_can_render(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, sample_text: str) -> bool:
    text = "".join(ch for ch in str(sample_text or "") if "\u4e00" <= ch <= "\u9fff")
    if not text:
        return True
    try:
        replacement_text = "\ufffd" * len(text)
        if bytes(font.getmask(text, mode="L")) == bytes(font.getmask(replacement_text, mode="L")):
            return False
        replacement = font.getlength("?" * len(text))
        measured = font.getlength(text)
    except Exception:
        return True
    return abs(measured - replacement) > max(1.0, len(text) * 0.2)


def _solid_frame(width: int, height: int, color: tuple[int, int, int]):
    return _pil_to_array(Image.new("RGB", (width, height), color))


def _pil_to_array(image: Image.Image):
    import numpy as np

    return np.array(image)
