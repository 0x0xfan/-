from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from moviepy.editor import AudioFileClip, CompositeAudioClip, CompositeVideoClip, VideoClip, VideoFileClip
from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANDIDATES = Path("jianying_style_candidates.json")
BACKGROUND = Path(r"Z:\办公\B站方面\信息流导出\1\1.mp4")
OUTPUT = Path(r"Z:\办公\B站方面\信息流导出\style_preview_jianying.mp4")
SFX_DIR = Path(r"Z:\办公\B站方面\信息流模板\音效")

PREVIEW_TEXTS = [
    "低成本副业",
    "复制素材",
    "不用囤货",
    "稳定出单",
    "先看这一步",
]


def main() -> None:
    styles = _pick_styles()
    if not styles:
        raise SystemExit("No portable styles with existing fonts found")

    bg = VideoFileClip(str(BACKGROUND)).subclip(0, 14)
    clips = [bg]
    audio_clips = []
    if bg.audio is not None:
        audio_clips.append(bg.audio.volumex(0.9))

    starts = [0.7, 3.2, 5.7, 8.2, 10.7]
    for index, style in enumerate(styles):
        text = PREVIEW_TEXTS[index % len(PREVIEW_TEXTS)]
        overlay = _make_overlay(style, text, bg.w, bg.h, 2.1).set_start(starts[index]).set_duration(2.1)
        clips.append(overlay)
        sfx = _make_sfx(style, starts[index], index)
        if sfx is not None:
            audio_clips.append(sfx)

    final = CompositeVideoClip(clips, size=bg.size).set_duration(13.5)
    if audio_clips:
        final = final.set_audio(CompositeAudioClip(audio_clips))
    if OUTPUT.exists():
        OUTPUT.unlink()
    final.write_videofile(str(OUTPUT), fps=24, codec="libx264", audio_codec="aac", preset="medium", threads=4)
    final.close()
    bg.close()

    print(OUTPUT)
    for style in styles:
        print(style["preset"], style["font"]["file"], style["style"].get("fill"), style["style"].get("stroke"))


def _pick_styles() -> list[dict]:
    data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    picked = []
    seen = set()
    for item in data["candidates"]:
        if item.get("recommended_use") != "portable_style":
            continue
        font_path = item.get("font", {}).get("path", "")
        if not font_path or not Path(font_path).exists():
            continue
        key = (Path(font_path).name, item.get("style", {}).get("fill"), (item.get("style", {}).get("stroke") or {}).get("color"))
        if key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) >= 5:
            break
    return picked


def _make_overlay(style: dict, text: str, width: int, height: int, duration: float) -> VideoClip:
    font_path = style["font"]["path"]
    fill = style["style"].get("fill") or "#ffffff"
    stroke = style["style"].get("stroke") or {}
    stroke_color = stroke.get("color") or "#000000"
    stroke_width = 7 if stroke.get("color") else 0
    shadow = style["style"].get("shadow") or {}
    has_shadow = bool(shadow.get("enabled"))

    def render_rgba(t: float):
        progress = min(max(t / 0.32, 0), 1)
        scale = 0.72 + 0.28 * _ease_out_back(progress)
        alpha = round(255 * min(max(t / 0.18, 0), 1) * min(max((duration - t) / 0.28, 0), 1))
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        max_size = 118 if width >= height else 92
        font, bbox = _fit_font(draw, text, font_path, max_size, 54, int(width * 0.76), stroke_width)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (width - text_w) // 2 - bbox[0]
        y = int(height * 0.22)
        preset_name = str(style.get("preset", ""))
        if "闲鱼引导" in preset_name:
            y = int(height * 0.18)
        elif "右侧" in preset_name:
            x = int(width * 0.58)
            y = int(height * 0.26)
        elif "左侧" in preset_name:
            x = int(width * 0.10)
            y = int(height * 0.28)

        cx = x + text_w / 2
        cy = y + text_h / 2

        scaled_font_size = max(36, round(font.size * scale))
        font2 = ImageFont.truetype(font_path, size=scaled_font_size)
        draw = ImageDraw.Draw(canvas)
        bbox2 = draw.textbbox((0, 0), text, font=font2, stroke_width=stroke_width)
        tx = round(cx - (bbox2[2] - bbox2[0]) / 2 - bbox2[0])
        ty = round(cy - (bbox2[3] - bbox2[1]) / 2 - bbox2[1])
        if has_shadow or not stroke_width:
            shadow_alpha = round(150 * alpha / 255)
            shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            sdraw = ImageDraw.Draw(shadow_layer)
            sdraw.text((tx + 9, ty + 10), text, font=font2, fill=(0, 0, 0, shadow_alpha), stroke_width=stroke_width, stroke_fill=(0, 0, 0, shadow_alpha))
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(1.1))
            canvas.alpha_composite(shadow_layer)
            draw = ImageDraw.Draw(canvas)
        draw.text(
            (tx, ty),
            text,
            font=font2,
            fill=_hex_to_rgba(fill, alpha),
            stroke_width=stroke_width,
            stroke_fill=_hex_to_rgba(stroke_color, alpha),
        )
        return np.array(canvas)

    def frame(t: float):
        return render_rgba(t)[:, :, :3]

    def mask_frame(t: float):
        return render_rgba(t)[:, :, 3] / 255.0

    mask = VideoClip(mask_frame, ismask=True, duration=duration)
    return VideoClip(frame, duration=duration).set_mask(mask).set_position((0, 0))


def _make_sfx(style: dict, start: float, index: int):
    selected = _select_sfx(style, index)
    if selected is None:
        return None
    path, metadata = selected
    try:
        clip = AudioFileClip(str(path))
    except OSError:
        return None

    source_start = float(metadata.get("source_start_seconds") or 0)
    source_duration = metadata.get("source_duration_seconds") or metadata.get("duration_seconds") or clip.duration
    source_duration = min(float(source_duration or clip.duration), 0.85, max(0.05, clip.duration - source_start))
    if source_start > 0 or source_duration < clip.duration:
        clip = clip.subclip(source_start, min(clip.duration, source_start + source_duration))

    offset = float(metadata.get("target_start_seconds") or 0)
    if offset < 0 or offset > 1.2:
        offset = 0.0
    volume = min(max(float(metadata.get("volume") or 1.0), 0.25), 1.4)
    return clip.volumex(0.42 * volume).set_start(start + offset)


def _select_sfx(style: dict, index: int):
    for effect in style.get("sound_effects", []) or []:
        path = Path(effect.get("path") or "")
        if effect.get("exists") and path.exists():
            return path, effect

    preset = str(style.get("preset", ""))
    fill = str(style.get("style", {}).get("fill") or "").lower()
    if "闲鱼引导" in preset:
        preferred = ["提示音.mp3", "打响指声音.mp3"]
    elif "左侧" in preset or "右侧" in preset or "左右" in preset:
        preferred = ["唰.mp3", "打响指声音.mp3"]
    elif fill in {"#b71c1c", "#ff7100"}:
        preferred = ["打响指声音.mp3", "唰.mp3"]
    else:
        preferred = ["提示音.mp3", "唰.mp3", "打响指声音.mp3"]
    preferred.append(["唰.mp3", "提示音.mp3", "打响指声音.mp3"][index % 3])

    for name in preferred:
        path = SFX_DIR / name
        if path.exists():
            return path, {"volume": 1.0, "source_start_seconds": 0, "source_duration_seconds": 0.7, "target_start_seconds": 0}
    return None


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: str, max_size: int, min_size: int, max_width: int, stroke_width: int):
    for size in range(max_size, min_size - 1, -2):
        font = ImageFont.truetype(font_path, size=size)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return font, bbox
    font = ImageFont.truetype(font_path, size=min_size)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return font, bbox


def _hex_to_rgba(value: str, alpha: int = 255):
    value = (value or "#ffffff").strip()
    if not value.startswith("#") or len(value) not in {4, 7}:
        return (255, 255, 255, alpha)
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), alpha)


def _ease_out_back(x: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(x - 1, 3) + c1 * pow(x - 1, 2)


if __name__ == "__main__":
    main()
