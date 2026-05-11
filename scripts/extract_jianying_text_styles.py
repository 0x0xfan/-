from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


TEXT_TYPES = {"text", "subtitle"}
EFFECT_TYPES = {"text_effect", "video_effect", "face_effect"}
FONT_EXTS = {".ttf", ".otf", ".ttc"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract reusable text style candidates from Jianying combination presets.")
    parser.add_argument("preset_root", help="Jianying Combination/Presets directory")
    parser.add_argument("--output", default="jianying_style_candidates.json", help="JSON output path")
    parser.add_argument("--limit", type=int, default=200, help="Maximum number of candidates to write")
    args = parser.parse_args()

    preset_root = Path(args.preset_root)
    if not preset_root.exists():
        raise SystemExit(f"Preset directory not found: {preset_root}")

    candidates = []
    preset_count = 0
    for preset_dir in sorted((p for p in preset_root.iterdir() if p.is_dir()), key=lambda p: p.name):
        draft_path = preset_dir / "preset_draft" / "draft_content.json"
        if not draft_path.exists():
            continue
        preset_count += 1
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            candidates.append(
                {
                    "preset": preset_dir.name,
                    "draft": str(draft_path),
                    "error": f"invalid json: {exc}",
                    "score": 0,
                }
            )
            continue

        nodes = list(_walk_json(draft))
        text_layers = [node for node in nodes if node.get("type") in TEXT_TYPES and ("content" in node or "font_path" in node)]
        effect_layers = [node for node in nodes if node.get("type") in EFFECT_TYPES]
        sticker_layers = [node for node in nodes if node.get("type") == "sticker"]
        animations = [node for node in nodes if node.get("type") in {"sticker_animation", "material_animation"} or "animations" in node]
        sound_effects = _sound_effects_for_nodes(nodes)

        for index, layer in enumerate(text_layers):
            content = _parse_content(layer.get("content"))
            content_styles = content.get("styles") if isinstance(content, dict) else None
            primary_style = content_styles[0] if isinstance(content_styles, list) and content_styles else {}
            font_path = _first_text(
                _deep_get(primary_style, ["font", "path"]),
                layer.get("font_path"),
                _first_font_path(layer.get("fonts")),
            )
            text = _first_text(content.get("text") if isinstance(content, dict) else "", layer.get("base_content"), layer.get("translate_original_text"))
            fill = _first_text(_fill_to_hex(primary_style.get("fill")), layer.get("text_color"))
            stroke = _first_stroke(primary_style.get("strokes"), layer)
            shadow = _first_shadow(primary_style.get("shadows"), layer)
            segment = _segment_for_material(draft, str(layer.get("id", "")))
            score, reasons = _score_candidate(layer, font_path, effect_layers, sticker_layers, animations, segment, text, stroke, shadow)

            candidates.append(
                {
                    "preset": preset_dir.name,
                    "draft": str(draft_path),
                    "preview": _preview_path(preset_dir),
                    "layer_index": index,
                    "layer_id": layer.get("id", ""),
                    "type": layer.get("type", ""),
                    "sample_text": text,
                    "font": {
                        "path": font_path,
                        "file": Path(font_path).name if font_path else "",
                        "exists": bool(font_path and Path(font_path).exists()),
                    },
                    "style": {
                        "font_size": _number(layer.get("font_size"), _number(_deep_get(primary_style, ["size"]), None)),
                        "fill": fill,
                        "stroke": stroke,
                        "shadow": shadow,
                        "letter_spacing": _number(layer.get("letter_spacing"), None),
                        "line_spacing": _number(layer.get("line_spacing"), None),
                        "alignment": layer.get("alignment", None),
                    },
                    "placement": {
                        "scale": _deep_get(segment, ["clip", "scale"]) if segment else None,
                        "transform": _deep_get(segment, ["clip", "transform"]) if segment else None,
                        "rotation": _deep_get(segment, ["clip", "rotation"]) if segment else None,
                        "duration_seconds": _duration_seconds(_deep_get(segment, ["target_timerange", "duration"])) if segment else None,
                    },
                    "complexity": {
                        "text_effect_count": len(effect_layers),
                        "sticker_count": len(sticker_layers),
                        "animation_count": len(animations),
                        "sound_effect_count": len(sound_effects),
                        "has_keyframes": bool(segment and segment.get("common_keyframes")),
                        "effect_names": [_safe_text(effect.get("name")) for effect in effect_layers[:6]],
                    },
                    "sound_effects": sound_effects,
                    "score": score,
                    "reasons": reasons,
                    "recommended_use": _recommended_use(score, effect_layers, sticker_layers, animations),
                }
            )

    ranked = sorted(candidates, key=lambda item: (item.get("score", 0), item["font"]["exists"] if "font" in item else False), reverse=True)
    output = {
        "source": str(preset_root),
        "preset_count": preset_count,
        "candidate_count": len(candidates),
        "written_count": min(args.limit, len(ranked)),
        "font_summary": _font_summary(ranked),
        "candidates": ranked[: args.limit],
    }
    output_path = Path(args.output)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Scanned presets: {preset_count}")
    print(f"Text style candidates: {len(candidates)}")
    print(f"Wrote: {output_path.resolve()}")
    for item in ranked[:10]:
        font = item.get("font", {})
        style = item.get("style", {})
        print(
            f"- score={item.get('score', 0):>2} preset={item.get('preset')} "
            f"text={item.get('sample_text')!r} font={font.get('file')} exists={font.get('exists')} "
            f"fill={style.get('fill')} stroke={style.get('stroke', {}).get('color')}"
        )


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _parse_content(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}
    return data if isinstance(data, dict) else {}


def _segment_for_material(draft: dict[str, Any], material_id: str) -> dict[str, Any] | None:
    if not material_id:
        return None
    for node in _walk_json(draft):
        if node.get("material_id") == material_id and "target_timerange" in node:
            return node
    return None


def _sound_effects_for_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sounds = {
        str(node.get("id", "")): node
        for node in nodes
        if node.get("type") == "sound" and _safe_text(node.get("id")) and _safe_text(node.get("path"))
    }
    effects: list[dict[str, Any]] = []
    for sound_id, sound in sounds.items():
        path = _safe_text(sound.get("path"))
        for segment in nodes:
            if segment.get("material_id") != sound_id or "target_timerange" not in segment:
                continue
            source_range = segment.get("source_timerange") if isinstance(segment.get("source_timerange"), dict) else {}
            target_range = segment.get("target_timerange") if isinstance(segment.get("target_timerange"), dict) else {}
            effects.append(
                {
                    "name": _safe_text(sound.get("name")),
                    "path": path,
                    "exists": Path(path).exists(),
                    "target_start_seconds": _duration_seconds(target_range.get("start")),
                    "duration_seconds": _duration_seconds(target_range.get("duration")),
                    "source_start_seconds": _duration_seconds(source_range.get("start")),
                    "source_duration_seconds": _duration_seconds(source_range.get("duration")),
                    "volume": _number(segment.get("volume"), 1.0),
                }
            )
    return sorted(effects, key=lambda item: (not item["exists"], item.get("target_start_seconds") or 0))


def _preview_path(preset_dir: Path) -> str:
    previews = sorted(preset_dir.glob("*.jpeg")) + sorted(preset_dir.glob("*.jpg")) + sorted(preset_dir.glob("*.png"))
    return str(previews[0]) if previews else ""


def _first_font_path(fonts: Any) -> str:
    if not isinstance(fonts, list):
        return ""
    for font in fonts:
        if not isinstance(font, dict):
            continue
        path = _safe_text(font.get("path"))
        if path:
            return path
    return ""


def _fill_to_hex(fill: Any) -> str:
    if not isinstance(fill, dict):
        return ""
    color = _deep_get(fill, ["content", "solid", "color"])
    if color is None:
        color = _deep_get(fill, ["solid", "color"])
    return _rgb_to_hex(color)


def _first_stroke(strokes: Any, layer: dict[str, Any]) -> dict[str, Any]:
    stroke: dict[str, Any] = {
        "color": _safe_text(layer.get("border_color")),
        "width": _number(layer.get("border_width"), 0),
        "alpha": _number(layer.get("border_alpha"), 1),
    }
    if isinstance(strokes, list) and strokes:
        first = strokes[0]
        if isinstance(first, dict):
            stroke["color"] = _first_text(_rgb_to_hex(_deep_get(first, ["content", "solid", "color"])), stroke["color"])
            stroke["width"] = _number(first.get("width"), stroke["width"])
            stroke["alpha"] = _number(first.get("alpha"), stroke["alpha"])
    return stroke


def _first_shadow(shadows: Any, layer: dict[str, Any]) -> dict[str, Any]:
    shadow: dict[str, Any] = {
        "enabled": bool(layer.get("has_shadow")),
        "color": _safe_text(layer.get("shadow_color")),
        "alpha": _number(layer.get("shadow_alpha"), None),
        "distance": _number(layer.get("shadow_distance"), None),
        "angle": _number(layer.get("shadow_angle"), None),
        "smoothing": _number(layer.get("shadow_smoothing"), None),
    }
    if isinstance(shadows, list) and shadows:
        first = shadows[0]
        if isinstance(first, dict):
            shadow["enabled"] = True
            shadow["color"] = _first_text(_rgb_to_hex(_deep_get(first, ["content", "solid", "color"])), shadow["color"])
            shadow["alpha"] = _number(first.get("alpha"), shadow["alpha"])
            shadow["distance"] = _number(first.get("distance"), shadow["distance"])
            shadow["angle"] = _number(first.get("angle"), shadow["angle"])
            shadow["smoothing"] = _number(first.get("diffuse"), shadow["smoothing"])
    return shadow


def _score_candidate(
    layer: dict[str, Any],
    font_path: str,
    effect_layers: list[dict[str, Any]],
    sticker_layers: list[dict[str, Any]],
    animations: list[dict[str, Any]],
    segment: dict[str, Any] | None,
    text: str,
    stroke: dict[str, Any],
    shadow: dict[str, Any],
) -> tuple[int, list[str]]:
    score = 30
    reasons: list[str] = []
    if font_path and Path(font_path).exists():
        score += 25
        reasons.append("font file exists locally")
    elif font_path:
        score -= 20
        reasons.append("font path is missing on this machine")
    else:
        score -= 10
        reasons.append("font path not found")

    suffix = Path(font_path).suffix.lower()
    if suffix in FONT_EXTS:
        score += 5

    if _safe_text(layer.get("text_color")) or text:
        score += 5
    if stroke.get("color"):
        score += 8
        reasons.append("has explicit stroke")
    if shadow.get("enabled"):
        score += 5
        reasons.append("has shadow")
    if effect_layers:
        score -= min(25, len(effect_layers) * 8)
        reasons.append("depends on Jianying effects")
    if sticker_layers:
        score -= min(18, len(sticker_layers) * 2)
        reasons.append("uses sticker layers")
    if animations:
        score -= min(12, len(animations) * 2)
        reasons.append("uses animation resources")
    if segment and segment.get("common_keyframes"):
        score -= 8
        reasons.append("has keyframes; only approximate animation is portable")
    if len(_safe_text(text)) <= 8:
        score += 4
        reasons.append("short text is suitable for big-word overlay")

    return max(0, min(100, score)), reasons


def _recommended_use(score: int, effect_layers: list[dict[str, Any]], sticker_layers: list[dict[str, Any]], animations: list[dict[str, Any]]) -> str:
    if score >= 65 and not effect_layers:
        return "portable_style"
    if score >= 45:
        return "approximate_in_renderer"
    if effect_layers or sticker_layers or animations:
        return "use_as_visual_reference_or_export_overlay"
    return "low_priority"


def _font_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    fonts = []
    existing = 0
    for item in candidates:
        font = item.get("font", {})
        file = font.get("file")
        if file:
            fonts.append(file)
        if font.get("exists"):
            existing += 1
    return {
        "unique_font_files": len(set(fonts)),
        "existing_font_candidates": existing,
        "top_font_files": Counter(fonts).most_common(20),
    }


def _duration_seconds(value: Any) -> float | None:
    number = _number(value, None)
    if number is None:
        return None
    return round(number / 1_000_000, 3)


def _deep_get(value: Any, keys: list[str]) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _rgb_to_hex(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or len(value) < 3:
        return ""
    channels = []
    for raw in value[:3]:
        number = _number(raw, 0) or 0
        if number <= 1:
            number *= 255
        channels.append(max(0, min(255, round(number))))
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: Any, default: float | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


if __name__ == "__main__":
    main()
