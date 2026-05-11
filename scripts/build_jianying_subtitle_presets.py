from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("E:/剪映数据/预设/JianyingPro Presets/Text_V2")
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "styles" / "jianying_subtitle_presets.json"
FONT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "fonts" / "subtitles"
PREVIEW_OUTPUT_DIR = PROJECT_ROOT / "assets" / "styles" / "subtitle_previews"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Jianying Text_V2 presets as project subtitle styles.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Jianying Text_V2 preset directory")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Project subtitle style JSON")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise SystemExit(f"Text_V2 preset directory not found: {source}")

    FONT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    presets = []
    for index, preset_path in enumerate(sorted(source.glob("*.textpreset"), key=_natural_key), 1):
        converted = _convert_preset(preset_path, index)
        if converted:
            presets.append(converted)

    output = {
        "version": 1,
        "source": str(source),
        "default_mode": "one_subtitle_style_per_video",
        "count": len(presets),
        "presets": presets,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {output_path}")
    print(f"subtitle_presets={len(presets)}")
    for preset in presets:
        print(f"- {preset['id']} font={preset['font_name']} fill={preset['subtitle_fill']} stroke={preset['subtitle_stroke']} width={preset['subtitle_stroke_width']}")


def _convert_preset(path: Path, index: int) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    font_resource = _resource(data, "fonts")
    flower_resource = _resource(data, "flower")
    source_font = _path(font_resource.get("file_path"))
    imported_font = _copy_font(source_font, style.get("font_name") or path.stem) if source_font and source_font.exists() else ""
    preview = _copy_preview(data.get("cover_image_path"), path.stem)

    fill = _normalize_hex(style.get("color"), "#ffffff")
    stroke, stroke_width = _subtitle_stroke(style, fill)
    shadow = _subtitle_shadow(style)
    preset_id = f"jy_subtitle_{index:02d}_{_short_hash(str(path), str(style.get('font_resource_id')), fill)}"

    return {
        "id": preset_id,
        "source_preset": str(data.get("name") or path.stem),
        "source_text": str(data.get("content") or ""),
        "source_file": str(path),
        "preview": preview,
        "font_name": str(style.get("font_name") or ""),
        "font": imported_font,
        "source_font": str(source_font) if source_font else "",
        "font_resource_id": str(style.get("font_resource_id") or ""),
        "flower_id": str(style.get("flowerResId") or ""),
        "flower_available": bool(_path(flower_resource.get("file_path")) and _path(flower_resource.get("file_path")).exists()),
        "subtitle_font_size": 48,
        "subtitle_fill": fill,
        "subtitle_stroke": stroke,
        "subtitle_stroke_width": stroke_width,
        "subtitle_shadow": shadow["enabled"],
        "subtitle_shadow_color": shadow["color"],
        "subtitle_shadow_alpha": shadow["alpha"],
        "subtitle_shadow_offset": shadow["offset"],
        "subtitle_max_width_ratio": 0.88,
        "subtitle_bottom_margin": 66,
        "subtitle_line_gap": 10,
        "subtitle_max_lines": 2,
        "source": {
            "font_size": _number(style.get("font_size"), None),
            "scale_x": _number(style.get("scale_x"), None),
            "scale_y": _number(style.get("scale_y"), None),
            "transform_x": _number(style.get("transform_x"), None),
            "transform_y": _number(style.get("transform_y"), None),
            "rotation": _number(style.get("rotation"), None),
            "use_effect_default_color": bool(style.get("use_effect_default_color")),
        },
    }


def _resource(data: dict[str, Any], panel: str) -> dict[str, Any]:
    for item in data.get("resources", []) or []:
        if isinstance(item, dict) and item.get("panel") == panel:
            return item
    return {}


def _copy_font(source: Path, font_name: Any) -> str:
    suffix = source.suffix.lower() or ".ttf"
    stem = _safe_stem(str(font_name) or source.stem)
    target = FONT_OUTPUT_DIR / f"{stem}_{_file_hash(source)[:8]}{suffix}"
    if not target.exists():
        shutil.copy2(source, target)
    return _rel(target)


def _copy_preview(raw_path: Any, preset_stem: str) -> str:
    source = _path(raw_path)
    if not source or not source.exists():
        return ""
    target = PREVIEW_OUTPUT_DIR / f"{_safe_stem(preset_stem)}{source.suffix.lower() or '.jpeg'}"
    if not target.exists():
        shutil.copy2(source, target)
    return _rel(target)


def _subtitle_stroke(style: dict[str, Any], fill: str) -> tuple[str, int]:
    border_enabled = bool(style.get("border_item_checked"))
    border_color = _normalize_hex(style.get("border_color"), "")
    border_width = _number(style.get("border_width"), 0.0) or 0.0
    if border_enabled and border_color:
        return border_color, int(round(max(2, min(6, border_width * 55))))
    if _luminance(fill) >= 0.55:
        return "#000000", 3
    return "#ffffff", 3


def _subtitle_shadow(style: dict[str, Any]) -> dict[str, Any]:
    if not style.get("shadow_checked"):
        return {"enabled": False, "color": "#000000", "alpha": 0.0, "offset": [0, 0]}
    color = _normalize_hex(style.get("shadow_color"), "#000000")
    alpha = max(0.0, min(1.0, _number(style.get("shadow_alpha"), 0.6) or 0.6))
    distance = _number(style.get("shadow_distance"), 5.0) or 5.0
    angle = math.radians(_number(style.get("shadow_angle"), -45.0) or -45.0)
    return {
        "enabled": True,
        "color": color,
        "alpha": round(alpha, 3),
        "offset": [round(math.cos(angle) * distance), round(math.sin(angle) * distance)],
    }


def _path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value).replace("/", "\\"))


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _normalize_hex(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not text.startswith("#"):
        return default
    if len(text) == 4:
        return "#" + "".join(ch * 2 for ch in text[1:]).lower()
    if len(text) in {7, 9}:
        return text[:7].lower()
    return default


def _luminance(hex_color: str) -> float:
    value = _normalize_hex(hex_color, "#ffffff")
    r = int(value[1:3], 16) / 255
    g = int(value[3:5], 16) / 255
    b = int(value[5:7], 16) / 255
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _file_hash(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short_hash(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:8]


def _safe_stem(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\s]+', "_", value.strip()).strip("_")
    return text[:42] or "subtitle"


def _natural_key(path: Path) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name)]


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
