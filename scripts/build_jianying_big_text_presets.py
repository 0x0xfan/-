from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT_ROOT / "jianying_style_candidates.json"
DEFAULT_ASSET_MANIFEST = PROJECT_ROOT / "assets" / "styles" / "jianying_imported_assets.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "styles" / "jianying_big_text_presets.json"
FONT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "fonts" / "jianying"
MAX_CURATED_PRESETS = 56
MAX_PRESETS_PER_SOURCE = 4

# These are intentionally narrow: Combination presets also contain subtitle
# bars, person-layout templates, guides and watermarks that should not be used
# as generated infoflow big-word stickers.
CURATED_BIG_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "impact",
        (
            "\u91cd\u70b9",  # 重点
            "\u5212\u91cd\u70b9",  # 划重点
            "\u8b66\u793a\u6587\u5b57",  # 警示文字
            "\u5f69\u8272\u56db\u5b57",  # 彩色四字
            "\u56db\u5b57\u51fa\u73b0",  # 四字出现
            "\u56db\u5b57\u5b57\u5e55",  # 四字字幕
            "\u4e94\u5b57\u5b57\u5e55",  # 五字字幕
            "\u516d\u5b57\u4e2d\u95f4",  # 六字中间
        ),
    ),
    (
        "split_glitch",
        (
            "\u6bdb\u523a",  # 毛刺
            "\u5206\u88c2",  # 分裂
            "\u5272\u88c2",  # 割裂
            "\u5207\u5272",  # 切割
            "\u659c\u5207",  # 斜切
            "\u6495\u88c2",  # 撕裂
        ),
    ),
    (
        "number",
        (
            "\u6570\u5b57",  # 数字
            "\u5e8f\u53f7",  # 序号
            "no.",
        ),
    ),
    (
        "quote",
        (
            "\u91d1\u53e5",  # 金句
        ),
    ),
    (
        "opening",
        (
            "\u5f00\u5934",  # 开头
            "\u5c01\u9762\u6807\u9898",  # 封面标题
            "\u8282\u70b9\u6807\u9898",  # 节点标题
        ),
    ),
    (
        "multi_word",
        (
            "\u591a\u5b57\u51fa\u73b0",  # 多字出现
            "\u591a\u5b57\u5168\u5c4f",  # 多字全屏
            "\u9010\u5b57",  # 逐字
            "\u4e09\u5b57\u53e0\u52a0",  # 三字叠加
            "\u4e24\u5b57\u4e09\u7ec4",  # 两字三组
            "\u4fa7\u9762\u56db\u5b57",  # 侧面四字
            "\u9876\u90e8\u56db\u5b57",  # 顶部四字
            "\u9876\u90e8\u6587\u5b57",  # 顶部文字
            "\u9876\u90e8\u5b57\u4f53",  # 顶部字体
            "\u5de6\u53f3\u8fdb\u5b57",  # 左右进字
            "\u4e0a\u4e0b\u6ed1\u52a8",  # 上下滑动
            "\u89c2\u70b9\u8f93\u51fa",  # 观点输出
            "\u5e76\u5217",  # 并列
        ),
    ),
    (
        "explain",
        (
            "\u8be6\u7ec6\u8bb2\u89e3",  # 详细讲解
        ),
    ),
    (
        "motion",
        (
            "\u7ffb\u8f6c\u5b57\u5e55",  # 翻转字幕
            "\u5f39\u51fa\u5b57\u4f53",  # 弹出字体
        ),
    ),
)
CURATED_EXCLUDE_MARKERS = (
    "\u95f2\u9c7c\u5f15\u5bfc",  # 闲鱼引导
    "\u77e5\u8bc6\u5e93",  # 知识库
    "\u8d44\u6599",  # 资料
    "\u5de6\u4fa7\u5b57\u5e55",  # 左侧字幕
    "\u53f3\u4fa7\u5b57\u5e55",  # 右侧字幕
    "\u5e95\u5c42\u5b57\u5e55",  # 底层字幕
    "\u5c0f\u6807\u9898\u5b57\u5e55",  # 小标题字幕
    "\u4e3e\u4f8b\u8bf4\u660e\u5b57\u5e55",  # 举例说明字幕
    "\u8d39\u7528\u5b57\u5e55",  # 费用字幕
    "\u591a\u5b57\u5e55",  # 多字幕
    "\u4e24\u6392\u5b57\u5e55",  # 两排字幕
    "\u4e09\u6392\u5b57\u5e55",  # 三排字幕
    "\u4e24\u53e5\u8bdd\u5b57\u5e55",  # 两句话字幕
    "\u4eba\u7269",  # 人物
    "\u5934\u50cf",  # 头像
    "\u6c34\u5370",  # 水印
    "\u80cc\u666f",  # 背景
    "\u4e3b\u8f68\u9053",  # 主轨道
    "\u7d20\u6750\u5c55\u793a",  # 素材展示
    "\u73af\u7ed5\u5b57\u5e55",  # 环绕字幕
    "\u63d0\u95ee\u5b57\u5e55",  # 提问字幕
    "\u6253\u5f00\u5b57\u5e55",  # 打开字幕
    "\u5b57\u5de6\u4eba\u53f3",  # 字左人右
    "\u5b57\u5728\u4eba\u540e",  # 字在人后
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build project-local Jianying big-text presets.")
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES), help="Scanner output JSON")
    parser.add_argument("--assets", default=str(DEFAULT_ASSET_MANIFEST), help="Imported font/SFX manifest")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Preset output JSON")
    args = parser.parse_args()

    candidates_path = Path(args.candidates)
    manifest_path = Path(args.assets)
    output_path = Path(args.output)

    candidates_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    font_map = _source_map(manifest.get("fonts", []))
    sfx_map = _source_map(manifest.get("sfx", []))
    project_sfx = _project_sfx_paths()

    candidates = candidates_data.get("candidates", [])
    selected = _select_curated_candidates(candidates)

    presets = []
    for item in selected:
        preset = _convert_candidate(item, len(presets) + 1, font_map, sfx_map, project_sfx)
        if preset:
            presets.append(preset)

    output = {
        "version": 3,
        "default_mode": "pure_text_no_cards",
        "selection_mode": "curated_infoflow_big_text",
        "source": "project_embedded_jianying_big_text_presets",
        "import_note": "Runtime uses only this project-local JSON and copied assets; Jianying directories are build-time inputs only.",
        "counts": {
            "portable_style": sum(1 for item in presets if item.get("source_recommended_use") == "portable_style"),
            "approximate_in_renderer": sum(1 for item in presets if item.get("source_recommended_use") == "approximate_in_renderer"),
            "style_reference": sum(1 for item in presets if item.get("source_recommended_use") == "use_as_visual_reference_or_export_overlay"),
            "total": len(presets),
        },
        "selection": {
            "max_total": MAX_CURATED_PRESETS,
            "max_per_source": MAX_PRESETS_PER_SOURCE,
            "categories": dict(Counter(item.get("category", "uncategorized") for item in presets)),
            "excluded_markers": list(CURATED_EXCLUDE_MARKERS),
        },
        "fallback_sfx": [
            "assets/sfx/infoflow/whoosh.mp3",
            "assets/sfx/infoflow/notification.mp3",
            "assets/sfx/infoflow/snap.mp3",
            "assets/sfx/infoflow/cash.mp3",
        ],
        "presets": presets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {output_path}")
    print(f"portable_style={output['counts']['portable_style']}")
    print(f"approximate_in_renderer={output['counts']['approximate_in_renderer']}")
    print(f"style_reference={output['counts']['style_reference']}")
    print(f"total={output['counts']['total']}")


def _select_curated_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for order, item in enumerate(candidates):
        category = _curated_category(item)
        if not category:
            continue
        if not _has_existing_font(item):
            continue
        if _too_complex_for_renderer(item):
            continue
        ranked.append((_curated_score(item, category), -order, category, item))

    selected: list[dict[str, Any]] = []
    per_source: Counter[str] = Counter()
    seen_style_keys: set[tuple[Any, ...]] = set()
    for _, _, category, item in sorted(ranked, reverse=True):
        source_name = str(item.get("preset") or "")
        if per_source[source_name] >= MAX_PRESETS_PER_SOURCE:
            continue
        key = _style_key(item)
        if key in seen_style_keys:
            continue
        converted = dict(item)
        converted["curated_category"] = category
        converted["source_recommended_use"] = item.get("recommended_use")
        converted["recommended_use"] = _portable_recommendation(item)
        selected.append(converted)
        per_source[source_name] += 1
        seen_style_keys.add(key)
        if len(selected) >= MAX_CURATED_PRESETS:
            break
    return selected


def _curated_category(item: dict[str, Any]) -> str:
    name = str(item.get("preset") or "")
    sample_text = str(item.get("sample_text") or "")
    text = f"{name} {sample_text}".lower()
    if any(marker.lower() in text for marker in CURATED_EXCLUDE_MARKERS):
        return ""
    for category, markers in CURATED_BIG_TEXT_RULES:
        if any(marker.lower() in text for marker in markers):
            return category
    return ""


def _has_existing_font(item: dict[str, Any]) -> bool:
    font = item.get("font") if isinstance(item.get("font"), dict) else {}
    source_font = str(font.get("path") or "")
    return bool(source_font and Path(source_font).exists())


def _too_complex_for_renderer(item: dict[str, Any]) -> bool:
    complexity = item.get("complexity") if isinstance(item.get("complexity"), dict) else {}
    effect_count = int(_number(complexity.get("text_effect_count"), 0) or 0)
    sticker_count = int(_number(complexity.get("sticker_count"), 0) or 0)
    if effect_count > 6:
        return True
    if sticker_count > 10:
        return True
    return False


def _curated_score(item: dict[str, Any], category: str) -> int:
    source_score = int(_number(item.get("score"), 0) or 0)
    text = str(item.get("sample_text") or "").strip()
    complexity = item.get("complexity") if isinstance(item.get("complexity"), dict) else {}
    category_boost = {
        "split_glitch": 18,
        "impact": 16,
        "number": 15,
        "opening": 14,
        "multi_word": 13,
        "motion": 12,
        "quote": 10,
        "explain": 8,
    }.get(category, 0)
    score = source_score + category_boost
    if 1 <= len(text.replace("\n", "")) <= 10:
        score += 8
    if len(text.replace("\n", "")) > 18:
        score -= 10
    if item.get("recommended_use") == "portable_style":
        score += 8
    elif item.get("recommended_use") == "approximate_in_renderer":
        score += 5
    else:
        score += 2
    score -= min(12, int(_number(complexity.get("text_effect_count"), 0) or 0) * 2)
    score -= min(8, int(_number(complexity.get("sticker_count"), 0) or 0))
    return score


def _portable_recommendation(item: dict[str, Any]) -> str:
    source_use = str(item.get("recommended_use") or "")
    if source_use in {"portable_style", "approximate_in_renderer"}:
        return source_use
    return "style_reference"


def _style_key(item: dict[str, Any]) -> tuple[Any, ...]:
    style = item.get("style") if isinstance(item.get("style"), dict) else {}
    stroke = style.get("stroke") if isinstance(style.get("stroke"), dict) else {}
    placement = item.get("placement") if isinstance(item.get("placement"), dict) else {}
    font = item.get("font") if isinstance(item.get("font"), dict) else {}
    return (
        item.get("preset"),
        Path(str(font.get("path") or "")).name.lower(),
        style.get("fill"),
        stroke.get("color"),
        round(_number(stroke.get("width"), 0.0) or 0.0, 4),
        _animation_name(str(item.get("preset") or ""), str(item.get("sample_text") or ""), _number(placement.get("rotation"), 0.0) or 0.0),
    )


def _convert_candidate(
    item: dict[str, Any],
    index: int,
    font_map: dict[str, str],
    sfx_map: dict[str, str],
    project_sfx: dict[str, str],
) -> dict[str, Any] | None:
    source_font = str((item.get("font") or {}).get("path") or "")
    font_path = font_map.get(_norm_path(source_font))
    if not font_path:
        font_path = _copy_font_to_project(source_font)
    if not font_path:
        return None

    style = item.get("style") or {}
    placement = item.get("placement") or {}
    stroke = style.get("stroke") if isinstance(style.get("stroke"), dict) else {}
    shadow = style.get("shadow") if isinstance(style.get("shadow"), dict) else {}
    transform = placement.get("transform") if isinstance(placement.get("transform"), dict) else {}
    scale = placement.get("scale") if isinstance(placement.get("scale"), dict) else {}

    stroke_color = _normalize_hex(stroke.get("color"), "#000000")
    stroke_width = _stroke_width(stroke)
    font_size_hint = _number(style.get("font_size"), 15.0) or 15.0
    source_scale = _number(scale.get("x"), 1.0) or 1.0
    source_rotation = _number(placement.get("rotation"), 0.0) or 0.0
    source_name = str(item.get("preset") or "jianying")
    source_text = str(item.get("sample_text") or "")

    preset_id = f"jy_{index:03d}_{item.get('recommended_use')}_{_short_hash(source_name, source_text, source_font)}"
    position = _position_from_transform(transform, index)
    animation = _animation_name(source_name, source_text, source_rotation)
    default_sfx = _convert_sfx(item.get("sound_effects") or [], sfx_map, project_sfx)

    return {
        "id": preset_id,
        "source_preset": source_name,
        "source_text": source_text,
        "font": font_path,
        "fill": _normalize_hex(style.get("fill"), "#ffffff"),
        "stroke": stroke_color,
        "stroke_width": stroke_width,
        "shadow": bool(shadow.get("enabled")) or stroke_width == 0,
        "shadow_color": _normalize_hex(shadow.get("color"), "#000000"),
        "shadow_alpha": _clamp(_number(shadow.get("alpha"), 0.58) or 0.58, 0.25, 0.9),
        "shadow_distance": _clamp(_number(shadow.get("distance"), 5.0) or 5.0, 3.0, 12.0),
        "shadow_angle": _clamp(_number(shadow.get("angle"), -45.0) or -45.0, -90.0, 90.0),
        "shadow_blur": _clamp((_number(shadow.get("smoothing"), 0.014) or 0.014) * 80.0, 0.6, 2.6),
        "position": position,
        "rotation": _clamp(source_rotation, -24.0, 24.0),
        "source_scale": source_scale,
        "font_size_hint": font_size_hint,
        "size_multiplier": _size_multiplier(font_size_hint, source_scale),
        "max_width_ratio": _max_width_ratio(position, source_text),
        "tracking": _clamp((_number(style.get("letter_spacing"), 0.0) or 0.0) * 18.0, 0.0, 7.0),
        "line_spacing": _clamp(_number(style.get("line_spacing"), 0.02) or 0.02, -0.2, 0.3),
        "animation": animation,
        "entry_duration": 0.34 if animation in {"bounce", "tilt_pop", "split"} else 0.28,
        "default_sfx": default_sfx,
        "score": item.get("score"),
        "category": item.get("curated_category", ""),
        "source_recommended_use": item.get("source_recommended_use") or item.get("recommended_use"),
        "recommended_use": _portable_recommendation(item),
    }


def _source_map(items: list[dict[str, Any]]) -> dict[str, str]:
    result = {}
    for item in items:
        source = item.get("source")
        path = item.get("path")
        if source and path:
            result[_norm_path(str(source))] = str(path)
    return result


def _copy_font_to_project(source_font: str) -> str:
    if not source_font:
        return ""
    source = Path(source_font)
    if not source.exists() or source.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
        return ""
    FONT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    target = FONT_OUTPUT_DIR / f"{_safe_stem(source.stem)}_{_file_hash(source)[:8]}{suffix}"
    if not target.exists():
        shutil.copy2(source, target)
    return _rel(target)


def _project_sfx_paths() -> dict[str, str]:
    paths = {
        "whoosh": "assets/sfx/infoflow/唰.mp3",
        "snap": "assets/sfx/infoflow/打响指声音.mp3",
        "notification": "assets/sfx/infoflow/提示音.mp3",
        "wow": "assets/sfx/infoflow/哇呜.mp3",
        "magic": "assets/sfx/infoflow/魔法音效.mp3",
        "coin": "assets/sfx/infoflow/金币散落.mp3",
        "cash": "assets/sfx/infoflow/钱响.mp3",
        "typing": "assets/sfx/infoflow/打字声.mp3",
        "dagger": "assets/sfx/jianying/匕首_6152ae5c.mp3",
        "ding": "assets/sfx/jianying/ding_可爱提示音_c4e2aa56.mp3",
        "success": "assets/sfx/jianying/任务完成_a29026ea.mp3",
        "water": "assets/sfx/jianying/水滴落下音效_370a9b61.mp3",
        "space": "assets/sfx/jianying/按空格键_f04d85b0.mp3",
    }
    return {key: value for key, value in paths.items() if (PROJECT_ROOT / value).exists()}


def _convert_sfx(sounds: list[dict[str, Any]], sfx_map: dict[str, str], project_sfx: dict[str, str]) -> list[dict[str, Any]]:
    converted = []
    seen = set()
    for sound in sounds:
        path = str(sound.get("path") or "")
        name = str(sound.get("name") or Path(path).stem)
        mapped = sfx_map.get(_norm_path(path))
        approximate = False
        if not mapped:
            mapped = _fallback_sfx(name, project_sfx)
            approximate = True
        if not mapped or mapped in seen:
            continue
        seen.add(mapped)
        offset = _clamp(_number(sound.get("target_start_seconds"), 0.0) or 0.0, 0.0, 1.15)
        duration = _clamp(_number(sound.get("source_duration_seconds"), None) or _number(sound.get("duration_seconds"), 0.65) or 0.65, 0.08, 0.85)
        volume = _clamp(_number(sound.get("volume"), 1.0) or 1.0, 0.25, 1.35)
        converted.append(
            {
                "path": mapped,
                "name": name,
                "volume": round(volume, 3),
                "offset": round(offset, 3),
                "max_duration": round(duration, 3),
                "approximate": approximate,
            }
        )
        if len(converted) >= 3:
            break
    return converted


def _fallback_sfx(name: str, project_sfx: dict[str, str]) -> str:
    lowered = name.lower()
    rules = [
        (("唰", "嗖", "咻", "woosh", "页面切换", "滑动", "拉扯"), "whoosh"),
        (("打字", "键盘", "空格"), "typing"),
        (("任务完成", "正确", "成功", "提示", "ding", "叮"), "success"),
        (("刀", "匕首", "撕纸"), "dagger"),
        (("弹跳", "弹簧", "卡通", "可爱", "气泡", "啵", "waoo"), "wow"),
        (("水滴", "一滴水"), "water"),
        (("仙尘", "魔法"), "magic"),
        (("拳击", "嘭", "砰", "盖章", "开瓶盖", "弹出"), "snap"),
        (("钱", "金币", "收银"), "cash"),
    ]
    for markers, key in rules:
        if any(marker.lower() in lowered for marker in markers):
            return project_sfx.get(key, "")
    return project_sfx.get("notification", "")


def _position_from_transform(transform: dict[str, Any], index: int) -> dict[str, float]:
    fallback_positions = [(0.50, 0.24), (0.28, 0.30), (0.68, 0.30), (0.50, 0.40)]
    fallback_x, fallback_y = fallback_positions[(index - 1) % len(fallback_positions)]
    raw_x = _number(transform.get("x"), None)
    raw_y = _number(transform.get("y"), None)
    x = fallback_x if raw_x is None else (raw_x + 1.0) / 2.0
    y = fallback_y if raw_y is None else (raw_y + 1.0) / 2.0
    return {"x": round(_clamp(x, 0.10, 0.90), 4), "y": round(_clamp(y, 0.14, 0.70), 4)}


def _animation_name(source_name: str, source_text: str, rotation: float) -> str:
    text = f"{source_name} {source_text}".lower()
    if "翻转" in text:
        return "flip"
    if any(marker in text for marker in ("分裂", "割裂", "切割", "斜切", "撕裂", "裂开")):
        return "split"
    if "左" in text and "右" in text:
        return "slide_mix"
    if "左侧" in text or "字左" in text:
        return "slide_left"
    if "右侧" in text:
        return "slide_right"
    if any(marker in text for marker in ("多字", "逐字", "弹出")):
        return "bounce"
    if any(marker in text for marker in ("详细讲解", "重点", "金句", "观点")):
        return "pulse"
    if "开头" in text or "标题" in text or "顶部" in text:
        return "rise"
    if abs(rotation) >= 8:
        return "tilt_pop"
    return "pop"


def _size_multiplier(font_size_hint: float, source_scale: float) -> float:
    font_factor = max(0.75, min(1.25, (max(font_size_hint, 4.0) / 15.0) ** 0.18))
    scale_factor = max(0.82, min(1.22, max(source_scale, 0.2) ** 0.14))
    return round(_clamp(font_factor * scale_factor, 0.78, 1.28), 3)


def _max_width_ratio(position: dict[str, float], sample_text: str) -> float:
    x = position.get("x", 0.5)
    if len(sample_text) >= 10:
        return 0.78
    if x < 0.25 or x > 0.75:
        return 0.56
    return 0.72


def _stroke_width(stroke: dict[str, Any]) -> int:
    color = _normalize_hex(stroke.get("color"), "")
    alpha = _number(stroke.get("alpha"), 1.0) or 1.0
    if not color or alpha <= 0.05:
        return 0
    width = _number(stroke.get("width"), 0.0) or 0.0
    return int(round(_clamp(width * 90.0, 4.0, 8.0)))


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


def _norm_path(path: str) -> str:
    return str(Path(path)).replace("/", "\\").lower()


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short_hash(*parts: str) -> str:
    raw = "\n".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:8]


def _safe_stem(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\s]+', "_", value.strip()).strip("_")
    return text[:42] or "font"


def _number(value: Any, default: float | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


if __name__ == "__main__":
    main()
