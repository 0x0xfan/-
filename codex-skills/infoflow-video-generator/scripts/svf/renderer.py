from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from moviepy.editor import AudioFileClip, CompositeAudioClip, ImageClip, VideoClip, VideoFileClip, CompositeVideoClip, vfx
from moviepy.audio.fx.all import audio_loop

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def render_video(timeline: dict[str, Any], output_path: str | Path) -> None:
    """渲染第一版视频：底层画面 + 轨道2素材 + 底部字幕 + 旁白/BGM。"""
    if _should_render_in_chunks(timeline):
        _render_video_chunked(timeline, output_path)
        return

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    project = timeline.get("project", {})
    width, height = project.get("resolution", [720, 1280])
    fps = int(project.get("fps", 24))
    duration = float(timeline["duration"])
    style = timeline.get("style", {})

    clips = []
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
        overlay = _make_group_overlay_clip(group, width, height, group_duration)
        overlay = overlay.set_duration(group_duration)
        transition = _choose_overlay_transition(first, asset)
        overlay = _apply_overlay_transition(overlay, transition, width, height, group_duration).set_start(start)
        clips.append(overlay)
        if len(group) > 1 and not _is_keyword_sequence_asset(asset):
            sticker = _sticker_clip(_sticker_text_for_group(group), width, height).set_start(start + min(0.45, group_duration / 4)).set_duration(max(group_duration - 0.45, 0.4))
            clips.append(sticker)

    for gap in _long_track2_gaps(segments):
        start = float(gap["start"])
        end = float(gap["end"])
        clip = _gap_enrichment_clip(gap, width, height, end - start).set_start(start).set_duration(end - start)
        clips.append(clip)

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
    for event in timeline.get("sound_effects", []) or []:
        path = event.get("path")
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

    video.write_videofile(str(output_path), fps=fps, codec="libx264", audio_codec="aac", verbose=False, logger=None)
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
    duration = float(timeline.get("duration", 0))
    return duration > 30 or base_count + overlay_count > 16


def _render_video_chunked(timeline: dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    project = timeline.get("project", {})
    width, height = project.get("resolution", [720, 1280])
    fps = int(project.get("fps", 24))
    duration = float(timeline["duration"])
    chunk_seconds = max(3.0, float(timeline.get("render_chunk_seconds", 10.0)))
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
            sticker_start = start + min(0.45, group_duration / 4)
            sticker_end = sticker_start + max(group_duration - 0.45, 0.4)
            if _overlaps(sticker_start, sticker_end, window_start, window_end):
                sticker = _sticker_clip(_sticker_text_for_group(group), width, height).set_duration(sticker_end - sticker_start)
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
        video.write_videofile(str(output_path), fps=fps, codec="libx264", audio=False, verbose=False, logger=None)
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
    for event in timeline.get("sound_effects", []) or []:
        path = event.get("path")
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
        audio.write_audiofile(str(output_path), fps=44100, codec="aac", verbose=False, logger=None)
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
            "-shortest",
            str(output_path),
        ]
    )


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
    if _is_keyword_sequence_asset(asset):
        return _keyword_sequence_clip(asset, width, height, duration)
    path = asset["path"]
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTS:
        return _make_overlay_video_clip(path, width, height, duration)
    return _make_overlay_image_clip(path, width, height, asset)


def _make_group_overlay_clip(group: list[dict[str, Any]], width: int, height: int, duration: float):
    asset = group[0].get("track2_asset") or {}
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
    rel_dir = str(asset.get("rel_dir", ""))
    tags = [str(tag) for tag in asset.get("tags", [])]
    if _is_revenue_asset(asset):
        return f"revenue:{rel_dir or Path(str(asset.get('path', ''))).parent}"
    return str(asset.get("path", ""))


def _overlay_merge_gap(asset: dict[str, Any]) -> float:
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
    img = Image.open(path).convert("RGB")
    if img.height > img.width or _is_document_asset(asset):
        scale = min(width / img.width, height / img.height, 1.0)
        if img.height > img.width:
            scale = min(scale, (height - 260) / img.height)
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        if _is_document_asset(asset):
            return ImageClip(_pil_to_array(_image_with_blurred_backdrop(path, img, width, height)))
        return ImageClip(_pil_to_array(img))
    return _make_image_clip(path, width, height)


def _make_video_clip(path: str | Path, width: int, height: int, duration: float, stretch_video: bool = False):
    clip = VideoFileClip(str(path), audio=False)
    clip = _fit_duration(clip, duration, stretch_video=stretch_video)
    clip = clip.resize(height=height)
    if clip.w < width:
        clip = clip.resize(width=width)
    x_center = clip.w / 2
    y_center = clip.h / 2
    return clip.crop(x_center=x_center, y_center=y_center, width=width, height=height)


def _make_overlay_video_clip(path: str | Path, width: int, height: int, duration: float):
    clip = VideoFileClip(str(path), audio=False)
    clip = _fit_duration(clip, duration, stretch_video=True)
    if clip.h > clip.w:
        scale = min(width / clip.w, (height - 260) / clip.h, 1.0)
        return clip.resize(scale) if scale < 1.0 else clip
    clip = clip.resize(height=height)
    if clip.w < width:
        clip = clip.resize(width=width)
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
    if _is_operation_asset(asset) or _is_revenue_asset(asset) or _is_keyword_sequence_asset(asset):
        return "none"
    key = f"{segment.get('start')}|{segment.get('text')}|{asset.get('path')}"
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
    transitions = ["fade", "slide_left", "slide_right", "slide_up", "slide_down", "pop"]
    return transitions[int(digest[:8], 16) % len(transitions)]


def _apply_overlay_transition(clip, transition: str, width: int, height: int, duration: float):
    base_x, base_y = _overlay_position(clip, width, height)
    if transition == "none":
        return clip.set_position((base_x, base_y))
    transition_duration = min(0.22, max(duration / 5, 0.08))
    clip_w = getattr(clip, "w", width)
    clip_h = getattr(clip, "h", height)

    if transition == "fade":
        return clip.fx(vfx.fadein, transition_duration).fx(vfx.fadeout, transition_duration).set_position((base_x, base_y))
    if transition == "pop":
        scaled = clip.resize(lambda t: 0.96 + 0.04 * min(t / transition_duration, 1.0))
        return scaled.fx(vfx.fadein, transition_duration).set_position((base_x, base_y))

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

    return clip.fx(vfx.fadein, transition_duration * 0.6).set_position(position)


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


def _revenue_frame(path: Path, width: int, height: int):
    img = Image.open(path).convert("RGB")
    scale = min(width * 0.78 / img.width, (height - 220) / img.height, 1.15)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (img.width + 36, img.height + 36), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((18, 18, img.width + 18, img.height + 18), radius=30, fill=(0, 0, 0, 86))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    x = (width - img.width) // 2
    y = max(42, (height - img.height) // 2 - 42)
    canvas.alpha_composite(shadow, (x - 18, y - 18))
    canvas.paste(img, (x, y))
    return _pil_to_array(canvas)


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
    def rgb_frame(t: float):
        frame = make_rgba_frame(t)
        return frame[:, :, :3]

    def mask_frame(t: float):
        frame = make_rgba_frame(t)
        if frame.shape[2] < 4:
            import numpy as np

            return np.ones(frame.shape[:2])
        return frame[:, :, 3] / 255.0

    clip = VideoClip(make_frame=rgb_frame, duration=duration)
    return clip.set_mask(VideoClip(make_frame=mask_frame, ismask=True, duration=duration))


def _keyword_sequence_frame(words: list[str], accent: tuple[int, int, int], width: int, height: int, t: float, duration: float) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    title_font = _load_font("C:/Windows/Fonts/msyh.ttc", 58)
    word_font = _load_font("C:/Windows/Fonts/msyh.ttc", 76)

    step = max(duration / max(len(words), 1), 0.7)
    visible = min(len(words), max(1, int(t / step) + 1))
    card_w = 360
    card_h = 126
    gap = 42
    total_w = card_w * len(words) + gap * (len(words) - 1)
    x0 = (width - total_w) // 2
    y = max(120, height // 2 - 115)
    title = "三个条件"
    title_box = draw.textbbox((0, 0), title, font=title_font, stroke_width=3)
    draw.text(((width - (title_box[2] - title_box[0])) // 2, y - 105), title, font=title_font, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 210))
    for index, word in enumerate(words[:visible]):
        progress = 1.0
        if index == visible - 1:
            progress = min(max((t - index * step) / 0.18, 0.0), 1.0)
        scale = 0.88 + 0.12 * _ease_out(progress)
        font = _load_font("C:/Windows/Fonts/msyh.ttc", max(38, round(70 * scale)))
        x = x0 + index * (card_w + gap)
        y_shift = int((1 - _ease_out(progress)) * 26)
        draw.rounded_rectangle((x + 10, y + y_shift + 12, x + card_w + 10, y + y_shift + card_h + 12), radius=32, fill=(0, 0, 0, 120))
        draw.rounded_rectangle((x, y + y_shift, x + card_w, y + y_shift + card_h), radius=32, fill=accent + (235,), outline=(255, 255, 255, 238), width=4)
        bbox = draw.textbbox((0, 0), word, font=font, stroke_width=2)
        tx = x + (card_w - (bbox[2] - bbox[0])) // 2
        draw.text((tx, y + y_shift + 24), word, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 150))
    return image


def _sticker_clip(text: str, width: int, height: int) -> ImageClip:
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
    draw = ImageDraw.Draw(canvas)
    font_path = "C:/Windows/Fonts/msyh.ttc"
    visible = [item for item in items if t >= float(item.get("reveal", 0.0))]
    if not visible:
        return canvas

    count = len(items)
    cols = 2 if count >= 4 else min(count, 3)
    rows = (count + cols - 1) // cols
    card_w = 420 if cols >= 3 else 460
    card_h = 116
    gap_x = 52
    gap_y = 34
    total_w = cols * card_w + (cols - 1) * gap_x
    total_h = rows * card_h + (rows - 1) * gap_y
    x0 = (width - total_w) // 2
    y0 = round(height * 0.24)
    max_y = height - 250 - total_h
    if y0 > max_y:
        y0 = max(72, max_y)

    for index, item in enumerate(items):
        if item not in visible:
            continue
        reveal = float(item.get("reveal", 0.0))
        progress = min(max((t - reveal) / 0.28, 0.0), 1.0)
        eased = _ease_out(progress)
        alpha = round(255 * eased)
        row = index // cols
        col = index % cols
        if row == rows - 1 and count % cols and cols > 1:
            row_count = count % cols
            row_offset = ((cols - row_count) * (card_w + gap_x)) // 2
        else:
            row_offset = 0
        x = x0 + row_offset + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y) + round((1 - eased) * 20)
        accent = tuple(item.get("accent", (42, 99, 235)))

        draw.rounded_rectangle((x + 10, y + 12, x + card_w + 10, y + card_h + 12), radius=30, fill=(0, 0, 0, round(90 * eased)))
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=30, fill=accent + (round(232 * eased),), outline=(255, 255, 255, round(240 * eased)), width=4)
        draw.rounded_rectangle((x + 22, y + 27, x + 84, y + 89), radius=19, fill=(255, 255, 255, round(235 * eased)))
        icon_font = _load_font(font_path, 40)
        symbol = _gap_icon_symbol(str(item.get("icon", "spark")))
        icon_box = draw.textbbox((0, 0), symbol, font=icon_font, stroke_width=1)
        draw.text(
            (x + 53 - (icon_box[2] - icon_box[0]) / 2, y + 58 - (icon_box[3] - icon_box[1]) / 2 - icon_box[1]),
            symbol,
            font=icon_font,
            fill=accent + (alpha,),
            stroke_width=1,
            stroke_fill=(255, 255, 255, round(100 * eased)),
        )
        title = str(item.get("title", "")).strip()
        title, title_font, title_box = _fit_text_to_width(draw, title, font_path, 56, 34, card_w - 126, stroke_width=3)
        text_y = y + (card_h - (title_box[3] - title_box[1])) // 2 - title_box[1]
        draw.text((x + 106, text_y), title, font=title_font, fill=(255, 255, 255, alpha), stroke_width=3, stroke_fill=(0, 0, 0, round(115 * eased)))
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
    for prefix in ("然后", "就是", "所以", "如果", "那么", "这个", "这些", "大家"):
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 2:
            cleaned = cleaned[len(prefix) :]
            break
    return ""


def _gap_enrichment_frame(theme: dict[str, Any], width: int, height: int, t: float, duration: float) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if theme.get("skip"):
        return canvas
    draw = ImageDraw.Draw(canvas)
    accent = tuple(theme.get("accent", (42, 99, 235)))
    title = str(theme.get("title", "")).strip()
    if not title:
        return canvas
    subtitle = str(theme.get("subtitle", "") or "").strip()
    icon = str(theme.get("icon", "spark"))

    progress = min(max(t / 0.28, 0.0), 1.0)
    pulse = 1 + 0.025 * min(max((duration - t) / max(duration, 0.1), 0.0), 1.0)
    eased = _ease_out(progress)
    alpha = round(255 * eased)
    font_path = "C:/Windows/Fonts/msyh.ttc"
    icon_font = _load_font(font_path, 50)
    icon_w = 76
    left_pad = 28
    text_x_offset = left_pad + icon_w + 24
    right_pad = 34
    max_card_w = min(round(width * 0.74), 860) if width >= height else min(round(width * 0.86), 640)
    min_card_w = min(max_card_w, 430)
    max_text_w = max(max_card_w - text_x_offset - right_pad, 180)
    max_title_size = round((74 if width >= height else 64) * pulse)
    min_title_size = 38 if width >= height else 34
    title, title_font, title_box = _fit_text_to_width(draw, title, font_path, max_title_size, min_title_size, max_text_w, stroke_width=3)
    subtitle_font = _load_font(font_path, 32)
    subtitle_box = (0, 0, 0, 0)
    if subtitle:
        subtitle, subtitle_font, subtitle_box = _fit_text_to_width(draw, subtitle, font_path, 32, 24, max_text_w, stroke_width=2)

    title_w = title_box[2] - title_box[0]
    subtitle_w = subtitle_box[2] - subtitle_box[0] if subtitle else 0
    card_w = min(max_card_w, max(min_card_w, text_x_offset + max(title_w, subtitle_w) + right_pad))
    card_h = 154 if subtitle else 130
    x = round(width * 0.08)
    x = min(x, max(24, width - card_w - 28))
    y = round(height * 0.15 + (1 - eased) * 26)
    radius = 28
    draw.rounded_rectangle((x + 10, y + 12, x + card_w + 10, y + card_h + 12), radius=radius, fill=(0, 0, 0, round(95 * eased)))
    draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=radius, fill=(16, 24, 39, round(222 * eased)), outline=accent + (round(235 * eased),), width=4)
    draw.rounded_rectangle((x + 4, y + 12, x + 14, y + card_h - 12), radius=5, fill=accent + (round(245 * eased),))
    icon_x = x + left_pad
    icon_y = y + (card_h - icon_w) // 2
    draw.rounded_rectangle((icon_x, icon_y, icon_x + icon_w, icon_y + icon_w), radius=22, fill=accent + (round(245 * eased),))
    symbol = _gap_icon_symbol(icon)
    icon_box = draw.textbbox((0, 0), symbol, font=icon_font, stroke_width=1)
    draw.text(
        (
            icon_x + icon_w / 2 - (icon_box[2] - icon_box[0]) / 2,
            icon_y + icon_w / 2 - (icon_box[3] - icon_box[1]) / 2 - icon_box[1],
        ),
        symbol,
        font=icon_font,
        fill=(255, 255, 255, alpha),
        stroke_width=1,
        stroke_fill=(0, 0, 0, round(70 * eased)),
    )
    text_x = x + text_x_offset
    title_h = title_box[3] - title_box[1]
    if subtitle:
        title_y = y + 26
        subtitle_y = y + 98
    else:
        title_y = y + (card_h - title_h) // 2 - title_box[1]
        subtitle_y = 0
    draw.text((text_x, title_y), title, font=title_font, fill=(255, 255, 255, alpha), stroke_width=3, stroke_fill=(0, 0, 0, round(115 * eased)))
    if subtitle:
        draw.text((text_x + 2, subtitle_y), subtitle, font=subtitle_font, fill=(222, 236, 255, alpha), stroke_width=2, stroke_fill=(0, 0, 0, round(90 * eased)))
    return canvas


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
        font = _load_font(font_path, size)
        bbox = draw.textbbox((0, 0), value, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return value, font, bbox
    font = _load_font(font_path, min_size)
    suffix = "..."
    while len(value) > 1:
        candidate = value[:-1].rstrip() + suffix
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return candidate, font, bbox
        value = value[:-1].rstrip()
    bbox = draw.textbbox((0, 0), value, font=font, stroke_width=stroke_width)
    return value, font, bbox


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
    canvas = Image.new("RGBA", (width, 170), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_size = int(style.get("subtitle_font_size", 44))
    font = _load_font(style.get("font", ""), font_size)
    line = str(text or "")
    stroke_width = int(style.get("subtitle_stroke_width", 3))
    max_width = width - 120
    while font_size > 28:
        font = _load_font(style.get("font", ""), font_size)
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            break
        font_size -= 2
    bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
    x = (width - (bbox[2] - bbox[0])) // 2
    y = 44
    draw.text(
        (x, y),
        line,
        font=font,
        fill=style.get("subtitle_fill", "white"),
        stroke_width=stroke_width,
        stroke_fill=style.get("subtitle_stroke", "black"),
    )
    return ImageClip(_pil_to_array(canvas)).set_position((0, height - 190))


def _wrap_text(text: str, max_chars: int) -> list[str]:
    return [text]


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path] if font_path else []
    candidates += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _solid_frame(width: int, height: int, color: tuple[int, int, int]):
    return _pil_to_array(Image.new("RGB", (width, height), color))


def _pil_to_array(image: Image.Image):
    import numpy as np

    return np.array(image)
