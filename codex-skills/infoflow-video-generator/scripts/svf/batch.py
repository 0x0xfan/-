from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from svf.assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, scan_assets
from svf.media import get_media_duration
from svf.renderer import render_video
from svf.subtitles import distribute_segments, segments_from_text_and_timestamp_words, segments_from_timestamp_words, write_srt
from svf.timeline import generate_base_clips, match_assets_for_segments, parse_script_blocks
from svf.tts import synthesize_tts, timestamp_sidecar_path

SCRIPT_DIR_NAME = "文案"
TRACK1_DIR_NAME = "轨道1"
OUTPUT_DIR_NAME = "输出"

DEFAULT_STYLE = {
    "font": "C:/Windows/Fonts/msyh.ttc",
    "subtitle_font_size": 42,
    "subtitle_fill": "white",
    "subtitle_stroke": "black",
    "subtitle_stroke_width": 3,
    "bgm_volume": 0.08,
    "voice_volume": 1.0,
}


def build_from_material_folder(
    material_root: str | Path,
    output_dir: str | Path | None = None,
    seed: int = 42,
    render: bool = True,
    voices: list[str] | None = None,
    voice_strategy: str = "round_robin",
    tts_provider: str = "",
    tts_model: str = "",
    tts_endpoint: str = "",
    tts_api_key: str = "",
    seconds_per_char: float = 0.16,
    min_segment_duration: float = 1.6,
    clip_duration: float = 3.0,
    resolution: tuple[int, int] = (1920, 1080),
    subtitle_offset: float = -0.12,
) -> dict[str, Any]:
    """Build one video for each txt file under 文案 using one fixed 轨道1 subgroup per video."""
    root = Path(material_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"素材主文件夹不存在：{root}")

    script_files = _script_files(root)
    if not script_files:
        raise FileNotFoundError(f"没有找到文案 txt：{root / SCRIPT_DIR_NAME}")

    track1_groups = _track1_groups(root)
    all_assets = scan_assets(root)
    track2_assets = _track2_assets(root, all_assets)
    bgm_assets = _assets_in_named_dirs(root, all_assets, {"bgm", "背景音乐", "音乐"}, {"audio"})
    sound_effect_assets = _assets_in_named_dirs(root, all_assets, {"音效", "sfx", "sound_effects"}, {"audio"})

    output_root = _resolve_output_dir(root, output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    voices = [voice.strip() for voice in voices or [] if voice.strip()]
    if tts_provider and not voices:
        voices = ["female-shaonv"]
    results = []

    for index, script_path in enumerate(script_files):
        voice = _choose_voice(voices, index, rng, voice_strategy)
        text = script_path.read_text(encoding="utf-8-sig")
        blocks = parse_script_blocks(text)
        video_dir = output_root / script_path.stem
        video_dir.mkdir(parents=True, exist_ok=True)
        voice_audio = ""
        if tts_provider:
            if not voice:
                raise ValueError("已选择配音接口，但音色列表为空")
            clean_text = "\n".join(block.get("text", "") for block in blocks if block.get("text", ""))
            voice_audio_path = output_root / "配音" / f"{script_path.stem}.mp3"
            voice_audio_path.parent.mkdir(parents=True, exist_ok=True)
            synthesize_tts(
                tts_provider,
                clean_text,
                voice_audio_path,
                api_key=tts_api_key,
                voice=voice,
                model=tts_model,
                endpoint=tts_endpoint,
            )
            voice_audio = str(voice_audio_path)
            duration = get_media_duration(voice_audio_path)
            segments = _segments_from_tts_timestamps(voice_audio_path, [block.get("text", "") for block in blocks]) or distribute_segments(blocks, duration)
        else:
            duration = _estimate_duration(blocks, seconds_per_char, min_segment_duration)
            segments = distribute_segments(blocks, duration)
        segments = _apply_subtitle_offset(segments, duration, subtitle_offset)
        matched_segments = _fill_fallback_track2_assets(match_assets_for_segments(segments, track2_assets, rules=[]), track2_assets, output_root, script_path.stem)

        group = track1_groups[index % len(track1_groups)] if track1_groups else None
        if track1_groups:
            shuffled_assets = list(group["assets"])
            rng.shuffle(shuffled_assets)
            base_clips = generate_base_clips(duration, shuffled_assets, clip_duration)
            track1_group_name = group["name"]
        else:
            base_clips = []
            track1_group_name = ""

        timeline = {
            "project": {"title": script_path.stem, "resolution": list(resolution), "fps": 24},
            "duration": duration,
            "voice_audio": voice_audio or None,
            "bgm_audio": bgm_assets[0]["path"] if bgm_assets else None,
            "sound_effects": _sound_effect_events(matched_segments, sound_effect_assets),
            "base_clips": base_clips,
            "segments": matched_segments,
            "style": DEFAULT_STYLE,
            "output_video": str(video_dir / f"{script_path.stem}.mp4"),
            "source_script": str(script_path),
            "track1_group": track1_group_name,
            "voice_name": voice,
            "tts": {
                "provider": tts_provider,
                "model": tts_model,
                "endpoint": tts_endpoint,
                "api_key_provided": bool(tts_api_key),
            },
        }
        (video_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        write_srt(matched_segments, video_dir / "subtitles.srt")
        if render:
            render_video(timeline, video_dir / f"{script_path.stem}.mp4")
        results.append(
            {
                "script": str(script_path),
                "output": str(video_dir / f"{script_path.stem}.mp4"),
                "timeline": str(video_dir / "timeline.json"),
                "track1_group": track1_group_name,
                "voice_name": voice,
                "tts_provider": tts_provider,
                "tts_model": tts_model,
                "tts_api_key_provided": bool(tts_api_key),
                "voice_audio": voice_audio,
                "duration": duration,
            }
        )

    summary = {
        "material_root": str(root),
        "output_dir": str(output_root),
        "count": len(results),
        "tts_provider": tts_provider,
        "tts_model": tts_model,
        "tts_api_key_provided": bool(tts_api_key),
        "results": results,
    }
    (output_root / "batch_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def inspect_material_folder(material_root: str | Path) -> dict[str, Any]:
    root = Path(material_root).resolve()
    scripts = _script_files(root) if root.exists() else []
    groups = _track1_groups(root) if root.exists() else []
    assets = scan_assets(root) if root.exists() else []
    return {
        "root": str(root),
        "script_count": len(scripts),
        "scripts": [str(path) for path in scripts],
        "track1_groups": [{"name": group["name"], "count": len(group["assets"])} for group in groups],
        "asset_count": len(assets),
        "output_dir": str(root / OUTPUT_DIR_NAME),
    }


def _resolve_output_dir(root: Path, output_dir: str | Path | None) -> Path:
    if output_dir is None or str(output_dir).strip() == "":
        return root / OUTPUT_DIR_NAME
    raw = Path(output_dir)
    return raw if raw.is_absolute() else root / raw


def _script_files(root: Path) -> list[Path]:
    script_dir = root / SCRIPT_DIR_NAME
    if not script_dir.exists():
        return []
    return sorted(path for path in script_dir.glob("*.txt") if path.is_file())


def _track1_groups(root: Path) -> list[dict[str, Any]]:
    track1_root = root / TRACK1_DIR_NAME
    if not track1_root.exists():
        return []
    groups = []
    child_dirs = sorted(path for path in track1_root.iterdir() if path.is_dir())
    if child_dirs:
        for child in child_dirs:
            assets = _media_files(child, IMAGE_EXTS | VIDEO_EXTS)
            if assets:
                groups.append({"name": child.name, "assets": [str(path) for path in assets]})
    else:
        assets = _media_files(track1_root, IMAGE_EXTS | VIDEO_EXTS)
        if assets:
            groups.append({"name": TRACK1_DIR_NAME, "assets": [str(path) for path in assets]})
    return groups


def _track2_assets(root: Path, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for asset in assets:
        path = Path(asset["path"])
        rel_parts = path.relative_to(root).parts
        if not rel_parts or rel_parts[0] != "其他素材":
            continue
        if path.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
            result.append(asset)
    return result


def _fill_fallback_track2_assets(
    segments: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    output_root: Path | None = None,
    video_stem: str = "",
) -> list[dict[str, Any]]:
    grouped = _group_assets_by_semantic_folder(assets)
    folder_offsets: dict[str, int] = {}
    result = []
    previous_revenue_asset: dict[str, Any] | None = None
    for segment in segments:
        enriched = dict(segment)
        if not enriched.get("track2_asset"):
            text = str(enriched.get("text", ""))
            match = _find_semantic_folder_match(text, grouped)
            if match:
                folder, folder_assets = match
                offset = folder_offsets.get(folder, 0)
                enriched["track2_asset"] = folder_assets[offset % len(folder_assets)]
                enriched["match_reason"] = f"folder:{folder}"
                folder_offsets[folder] = offset + 1
                previous_revenue_asset = enriched["track2_asset"] if _is_revenue_text(text) else previous_revenue_asset
            elif previous_revenue_asset and _should_extend_revenue_asset(text):
                enriched["track2_asset"] = previous_revenue_asset
                enriched["match_reason"] = "extended:revenue_context"
            elif output_root:
                card = _maybe_make_keyword_card(text, output_root, video_stem)
                if card:
                    enriched["track2_asset"] = card
                    enriched["match_reason"] = "generated:keyword_card"
                    previous_revenue_asset = card if "收益数据" in card.get("tags", []) else previous_revenue_asset
        elif _is_revenue_text(str(enriched.get("text", ""))):
            previous_revenue_asset = enriched.get("track2_asset")
        result.append(enriched)
    return result


def _group_assets_by_semantic_folder(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        folder = _semantic_folder_name(asset)
        if folder:
            grouped.setdefault(folder, []).append(asset)
    return grouped


def _semantic_folder_name(asset: dict[str, Any]) -> str:
    rel_dir = str(asset.get("rel_dir", ""))
    parts = [part for part in Path(rel_dir).parts if part and part not in {".", "其他素材"}]
    if not parts:
        return ""
    return parts[-1]


def _find_semantic_folder_match(text: str, grouped: dict[str, list[dict[str, Any]]]) -> tuple[str, list[dict[str, Any]]] | None:
    normalized_text = _normalize_semantic_text(text)
    candidates = []
    for folder, assets in grouped.items():
        normalized_folder = _normalize_semantic_text(folder)
        if not normalized_folder:
            continue
        if not _should_use_semantic_folder_match(normalized_text, normalized_folder):
            continue
        if normalized_folder in normalized_text or normalized_text in normalized_folder:
            candidates.append((len(normalized_folder), folder, assets))
            continue
        token_hits = sum(1 for token in _folder_tokens(normalized_folder) if token and token in normalized_text)
        token_hits += _semantic_alias_hits(normalized_text, normalized_folder)
        if token_hits:
            candidates.append((token_hits, folder, assets))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, folder, assets = candidates[0]
    return folder, assets


def _folder_tokens(text: str) -> list[str]:
    return [token for token in text.replace("_", " ").replace("-", " ").split() if token]


def _normalize_semantic_text(text: str) -> str:
    return "".join(char for char in text.lower() if char not in " ，,。！？；.!?;：:、（）()[]【】 \t\r\n")


def _semantic_alias_hits(text: str, folder: str) -> int:
    alias_groups = {
        "收益": ["收益", "收入", "赚", "几k", "5000", "数据", "情况", "这么多", "单子"],
        "资料": ["教程", "资料", "领取", "免费", "违禁词", "知识点", "三连", "关注"],
        "同行案例": ["同行", "案例", "卖得好", "想要数", "浏览量", "这个品"],
        "上传图片": ["上传图片", "传图片", "添加图片", "选择图片"],
        "上文案": ["文案粘贴", "粘贴文案", "写描述", "商品描述", "详情描述"],
        "ai修改": ["ai修改", "自带的ai", "修改一下"],
        "发布": ["发布", "点击发布"],
        "打开闲鱼": ["打开闲鱼"],
        "打开上架": ["上架"],
        "改价格": ["改价格", "设置价格", "价格好", "价格填", "填写价格", "1.99", "5.99"],
        "设置原价": ["设置原价", "设置价格", "价格好", "价格填", "填写价格", "1.99", "5.99"],
        "发货方式": ["发货方式", "选择发货", "设置发货"],
        "标签": ["标签", "多写一点"],
    }
    score = 0
    for anchor, aliases in alias_groups.items():
        if anchor in folder and any(_normalize_semantic_text(alias) in text for alias in aliases):
            score += 3
    return score


def _should_use_semantic_folder_match(text: str, folder: str) -> bool:
    if "上传图片" in folder:
        return any(marker in text for marker in ["上传图片", "传图片", "添加图片", "选择图片"])
    if "上文案" in folder:
        if "复制粘贴" in text and not any(marker in text for marker in ["文案粘贴", "粘贴文案", "写描述"]):
            return False
        return any(marker in text for marker in ["文案粘贴", "粘贴文案", "写描述", "商品描述", "详情描述"])
    if "改价格" in folder or "设置原价" in folder:
        if "价格战" in text:
            return False
        has_price_marker = any(marker in text for marker in ["价格好", "价格填", "填写价格", "设置价格", "设置成", "改价格", "199", "599"])
        has_action_marker = any(marker in text for marker in ["设置", "填写", "填", "改", "定价", "价格"])
        return has_price_marker and has_action_marker
    if "发货方式" in folder:
        if "自动发货" in text:
            return False
        return any(marker in text for marker in ["发货方式", "选择发货", "设置发货", "改发货", "填写发货"])
    return True


def _maybe_make_keyword_card(text: str, output_root: Path, video_stem: str) -> dict[str, Any] | None:
    keywords = _extract_keyword_cards(text)
    if not keywords:
        return None
    card_dir = output_root / "自动素材" / video_stem
    card_dir.mkdir(parents=True, exist_ok=True)
    keyword = keywords[0]
    if keyword not in {"收益数据", "低成本"}:
        return None
    variant = _keyword_visual_variant(keyword, text)
    filename = keyword if variant == "default" else f"{keyword}_{variant}"
    path = card_dir / f"{_safe_filename(filename)}.png"
    if not path.exists():
        _write_keyword_card(path, keyword, variant)
    return {"path": str(path), "rel_dir": str(path.parent), "tags": [keyword, variant, "自动素材"], "kind": "image", "priority": 5}


def _extract_keyword_cards(text: str) -> list[str]:
    normalized = text.replace(" ", "")
    keywords = []
    revenue_markers = ["收益", "收入", "赚", "几K", "5000", "这么多", "单子收益", "额外收入", "首付"]
    if any(marker.lower() in normalized.lower() for marker in revenue_markers):
        keywords.append("收益数据")
    if any(marker in normalized for marker in ["没有什么成本", "没什么成本", "无成本", "不需要囤货", "不囤货"]):
        keywords.append("低成本")
    if any(marker in normalized for marker in ["几百单", "出几百单", "几百个单"]):
        keywords.append("订单爆发")
    if any(marker in normalized for marker in ["复制粘贴", "复制下来", "粘贴过来"]):
        keywords.append("复制粘贴")
    if any(marker in normalized for marker in ["标签", "多写一点"]):
        keywords.append("标签优化")
    for marker in ["极氪9X", "极氪9x", "Zeekr9X", "ZEEKR9X"]:
        if marker.lower() in normalized.lower():
            keywords.append("极氪9X")
            break
    return keywords


def _keyword_visual_variant(keyword: str, text: str) -> str:
    normalized = _normalize_semantic_text(text)
    if keyword == "收益数据":
        if any(marker in normalized for marker in ["几k", "副业", "额外收入"]):
            return "表情包"
        if any(marker in normalized for marker in ["这么多", "看一下", "情况", "真实"]):
            return "订单弹窗"
        if any(marker in normalized for marker in ["首付", "年底", "车子"]):
            return "目标进度"
        return "数据看板"
    return "default"


def _write_keyword_card(path: Path, keyword: str, variant: str = "default") -> None:
    if keyword == "收益数据":
        if variant == "表情包":
            _write_revenue_meme_card(path)
        elif variant == "订单弹窗":
            _write_revenue_notification_card(path)
        elif variant == "目标进度":
            _write_revenue_goal_card(path)
        else:
            _write_revenue_dashboard_card(path)
        return
    if keyword == "低成本":
        _write_simple_visual_card(path, "不用囤货", "低成本试错", (22, 118, 92), "0 库存 / 轻启动 / 可复制")
        return
    if keyword == "订单爆发":
        _write_simple_visual_card(path, "订单开始增长", "每天多出几单", (214, 94, 42), "成交提醒 / 复购 / 批量化")
        return
    if keyword == "复制粘贴":
        _write_simple_visual_card(path, "复制 - 修改 - 发布", "把流程标准化", (68, 99, 196), "图片 / 文案 / 标签 / 价格")
        return
    if keyword == "标签优化":
        _write_simple_visual_card(path, "标签写得越准", "曝光越容易进来", (138, 82, 190), "关键词 / 场景 / 人群 / 需求")
        return

    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (15, 20, 26))
    draw = ImageDraw.Draw(image)
    font = _load_card_font(128)
    small = _load_card_font(44)
    draw.rectangle((0, 0, width, height), fill=(15, 20, 26))
    for y in range(0, height, 4):
        color = (15 + y // 120, 22 + y // 180, 30 + y // 160)
        draw.line((0, y, width, y), fill=color, width=4)
    title = keyword
    subtitle = "关键词视觉素材"
    bbox = draw.textbbox((0, 0), title, font=font, stroke_width=2)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, 405), title, font=font, fill="white", stroke_width=2, stroke_fill="black")
    sb = draw.textbbox((0, 0), subtitle, font=small)
    draw.text(((width - (sb[2] - sb[0])) // 2, 575), subtitle, font=small, fill=(180, 220, 255))
    image.save(path)


def _write_simple_visual_card(path: Path, title: str, subtitle: str, accent: tuple[int, int, int], footer: str) -> None:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (246, 248, 250))
    draw = ImageDraw.Draw(image)
    title_font = _load_card_font(86)
    sub_font = _load_card_font(54)
    item_font = _load_card_font(38)
    draw.rectangle((0, 0, width, height), fill=(246, 248, 250))
    draw.rounded_rectangle((180, 150, 1740, 910), radius=52, fill=(255, 255, 255), outline=(222, 228, 236), width=3)
    draw.rounded_rectangle((240, 230, 520, 510), radius=60, fill=accent)
    for i, mark in enumerate(["✓", "✓", "✓"]):
        draw.text((315, 245 + i * 82), mark, font=sub_font, fill=(255, 255, 255))
    draw.text((610, 250), title, font=title_font, fill=(38, 48, 62))
    draw.text((615, 375), subtitle, font=sub_font, fill=accent)
    draw.rounded_rectangle((610, 560, 1580, 660), radius=32, fill=(240, 244, 248))
    draw.text((660, 583), footer, font=item_font, fill=(74, 88, 104))
    draw.rounded_rectangle((610, 720, 1580, 795), radius=28, fill=accent)
    draw.text((690, 735), "这一句没有本地素材时自动补画面", font=item_font, fill=(255, 255, 255))
    image.save(path)


def _is_revenue_text(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    return any(marker in normalized for marker in ["收益", "收入", "赚", "几k", "这么多", "单子", "首付"])


def _should_extend_revenue_asset(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    return any(marker in normalized for marker in ["看一下", "情况", "真实", "有效", "这段时间"])


def _is_abstract_card(asset: dict[str, Any]) -> bool:
    tags = [str(tag) for tag in asset.get("tags", [])]
    return bool(tags and tags[0] in {"低成本", "订单爆发", "复制粘贴", "标签优化"})


def _should_extend_abstract_asset(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    return any(marker in normalized for marker in ["方法", "分享", "操作"])


def _write_revenue_dashboard_card(path: Path) -> None:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (246, 248, 250))
    draw = ImageDraw.Draw(image)
    title_font = _load_card_font(58)
    amount_font = _load_card_font(78)
    label_font = _load_card_font(34)
    small_font = _load_card_font(28)
    tiny_font = _load_card_font(24)

    draw.rectangle((0, 0, width, 118), fill=(20, 24, 31))
    draw.text((98, 34), "近7天接单收益", font=title_font, fill=(255, 255, 255))
    draw.text((1450, 44), "数据看板", font=label_font, fill=(160, 178, 196))

    _rounded_box(draw, (100, 170, 620, 410), (255, 255, 255), outline=(220, 226, 234))
    draw.text((140, 205), "本周收入", font=label_font, fill=(86, 96, 110))
    draw.text((140, 272), "¥5,286", font=amount_font, fill=(20, 130, 86))
    draw.text((145, 365), "较上周 +37.8%", font=small_font, fill=(236, 113, 34))

    _rounded_box(draw, (680, 170, 1180, 410), (255, 255, 255), outline=(220, 226, 234))
    draw.text((720, 205), "完成订单", font=label_font, fill=(86, 96, 110))
    draw.text((720, 272), "24 单", font=amount_font, fill=(38, 87, 175))
    draw.text((725, 365), "平均客单 ¥220", font=small_font, fill=(86, 96, 110))

    _rounded_box(draw, (1240, 170, 1820, 410), (255, 255, 255), outline=(220, 226, 234))
    draw.text((1280, 205), "待结算", font=label_font, fill=(86, 96, 110))
    draw.text((1280, 272), "¥1,480", font=amount_font, fill=(188, 83, 36))
    draw.text((1285, 365), "预计48小时到账", font=small_font, fill=(86, 96, 110))

    _rounded_box(draw, (100, 465, 1160, 940), (255, 255, 255), outline=(220, 226, 234))
    draw.text((140, 505), "每日收益趋势", font=label_font, fill=(44, 52, 64))
    bars = [360, 540, 420, 690, 810, 620, 910]
    labels = ["一", "二", "三", "四", "五", "六", "日"]
    max_bar = max(bars)
    chart_left, chart_bottom = 170, 850
    bar_w, gap = 84, 48
    for i, value in enumerate(bars):
        x0 = chart_left + i * (bar_w + gap)
        bar_h = int(value / max_bar * 270)
        color = (28, 151, 112) if i < 5 else (38, 112, 205)
        draw.rounded_rectangle((x0, chart_bottom - bar_h, x0 + bar_w, chart_bottom), radius=18, fill=color)
        draw.text((x0 + 8, chart_bottom + 22), labels[i], font=small_font, fill=(96, 108, 122))
        draw.text((x0 - 4, chart_bottom - bar_h - 42), f"{value}", font=tiny_font, fill=(96, 108, 122))

    _rounded_box(draw, (1225, 465, 1820, 940), (255, 255, 255), outline=(220, 226, 234))
    draw.text((1265, 505), "最近订单", font=label_font, fill=(44, 52, 64))
    rows = [
        ("视频剪辑", "已完成", "+¥680"),
        ("商品图优化", "已交付", "+¥260"),
        ("详情页文案", "进行中", "+¥380"),
        ("账号搭建", "已完成", "+¥920"),
    ]
    y = 585
    for name, status, amount in rows:
        draw.rounded_rectangle((1265, y, 1780, y + 72), radius=18, fill=(247, 249, 252), outline=(230, 235, 241))
        draw.text((1292, y + 18), name, font=small_font, fill=(38, 47, 61))
        draw.text((1465, y + 18), status, font=small_font, fill=(92, 110, 126))
        draw.text((1640, y + 18), amount, font=small_font, fill=(20, 130, 86))
        y += 88

    image.save(path)


def _write_revenue_meme_card(path: Path) -> None:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (255, 245, 214))
    draw = ImageDraw.Draw(image)
    title_font = _load_card_font(76)
    big_font = _load_card_font(150)
    label_font = _load_card_font(42)
    small_font = _load_card_font(32)

    draw.rectangle((0, 0, width, height), fill=(255, 245, 214))
    for x in range(-height, width, 120):
        draw.line((x, height, x + height, 0), fill=(255, 230, 160), width=22)
    draw.rounded_rectangle((145, 125, 1775, 925), radius=52, fill=(255, 255, 255), outline=(42, 42, 42), width=8)
    draw.text((250, 210), "副业收入突然到账", font=title_font, fill=(30, 30, 30))
    draw.text((250, 340), "+ ¥ 5,286", font=big_font, fill=(18, 148, 92))
    draw.rounded_rectangle((250, 560, 910, 700), radius=36, fill=(255, 92, 92))
    draw.text((305, 590), "今天又赚到啦", font=label_font, fill=(255, 255, 255))
    draw.rounded_rectangle((1010, 560, 1655, 700), radius=36, fill=(255, 190, 52))
    draw.text((1065, 590), "离买车更近一步", font=label_font, fill=(38, 38, 38))

    face_x, face_y = 1410, 335
    draw.ellipse((face_x - 120, face_y - 120, face_x + 120, face_y + 120), fill=(255, 218, 79), outline=(30, 30, 30), width=6)
    draw.ellipse((face_x - 68, face_y - 38, face_x - 28, face_y + 5), fill=(30, 30, 30))
    draw.ellipse((face_x + 28, face_y - 38, face_x + 68, face_y + 5), fill=(30, 30, 30))
    draw.arc((face_x - 72, face_y - 8, face_x + 72, face_y + 88), 0, 180, fill=(30, 30, 30), width=8)
    draw.text((255, 770), "别问，问就是今天也在努力搞钱。", font=small_font, fill=(92, 84, 70))
    image.save(path)


def _write_revenue_notification_card(path: Path) -> None:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (235, 241, 247))
    draw = ImageDraw.Draw(image)
    title_font = _load_card_font(62)
    money_font = _load_card_font(96)
    label_font = _load_card_font(38)
    small_font = _load_card_font(30)

    draw.rectangle((0, 0, width, height), fill=(235, 241, 247))
    for i, color in enumerate([(255, 255, 255), (247, 252, 255), (241, 248, 255)]):
        y = 180 + i * 210
        draw.rounded_rectangle((360 + i * 35, y, 1560 - i * 35, y + 150), radius=34, fill=color, outline=(216, 226, 235), width=2)
    draw.text((420, 145), "订单收益提醒", font=title_font, fill=(35, 45, 58))
    rows = [
        ("视频剪辑接单", "+¥680", "刚刚到账"),
        ("商品图优化", "+¥260", "3分钟前"),
        ("详情页文案", "+¥380", "8分钟前"),
    ]
    for index, (name, amount, time_text) in enumerate(rows):
        y = 205 + index * 210
        draw.ellipse((430, y + 36, 510, y + 116), fill=(18, 148, 92))
        draw.text((453, y + 55), "¥", font=small_font, fill=(255, 255, 255))
        draw.text((545, y + 35), name, font=label_font, fill=(34, 45, 58))
        draw.text((545, y + 88), time_text, font=small_font, fill=(118, 132, 148))
        draw.text((1210, y + 34), amount, font=money_font, fill=(18, 148, 92))
    draw.rounded_rectangle((560, 850, 1360, 930), radius=40, fill=(20, 130, 86))
    draw.text((705, 868), "这段时间做的情况", font=label_font, fill=(255, 255, 255))
    image.save(path)


def _write_revenue_goal_card(path: Path) -> None:
    width, height = 1920, 1080
    image = Image.new("RGB", (width, height), (18, 24, 32))
    draw = ImageDraw.Draw(image)
    title_font = _load_card_font(70)
    big_font = _load_card_font(106)
    label_font = _load_card_font(40)
    small_font = _load_card_font(30)

    draw.rectangle((0, 0, width, height), fill=(18, 24, 32))
    draw.rounded_rectangle((160, 145, 1760, 900), radius=48, fill=(28, 37, 50), outline=(72, 92, 115), width=3)
    draw.text((240, 230), "年底首付目标", font=title_font, fill=(255, 255, 255))
    draw.text((240, 350), "已攒 42%", font=big_font, fill=(255, 205, 74))
    draw.rounded_rectangle((245, 535, 1675, 610), radius=38, fill=(55, 66, 80))
    draw.rounded_rectangle((245, 535, 846, 610), radius=38, fill=(255, 205, 74))
    draw.text((245, 675), "剪辑接单 + 闲鱼副业 + 每日复盘", font=label_font, fill=(215, 225, 235))
    draw.text((245, 750), "目标不是空想，是一笔一笔赚出来的。", font=small_font, fill=(150, 166, 184))

    car_y = 760
    draw.rounded_rectangle((1260, car_y, 1595, car_y + 82), radius=36, fill=(235, 241, 248))
    draw.polygon([(1325, car_y), (1390, car_y - 72), (1510, car_y - 72), (1570, car_y)], fill=(235, 241, 248))
    draw.ellipse((1302, car_y + 52, 1378, car_y + 128), fill=(16, 20, 26))
    draw.ellipse((1484, car_y + 52, 1560, car_y + 128), fill=(16, 20, 26))
    image.save(path)


def _rounded_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: tuple[int, int, int], outline: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=26, fill=fill, outline=outline, width=2)


def _load_card_font(size: int):
    candidates = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _safe_filename(value: str) -> str:
    return "".join(char for char in value if char not in '\\/:*?"<>|').strip() or "keyword"


def _apply_subtitle_offset(segments: list[dict[str, Any]], duration: float, offset: float) -> list[dict[str, Any]]:
    if not offset:
        return segments
    shifted = []
    for segment in segments:
        item = dict(segment)
        item["start"] = round(max(0.0, float(item["start"]) + offset), 3)
        item["end"] = round(min(duration, max(item["start"] + 0.1, float(item["end"]) + offset)), 3)
        shifted.append(item)
    return shifted


def _assets_in_named_dirs(root: Path, assets: list[dict[str, Any]], names: set[str], kinds: set[str]) -> list[dict[str, Any]]:
    result = []
    for asset in assets:
        rel_parts = set(Path(asset["path"]).relative_to(root).parts)
        if rel_parts & names and asset.get("kind") in kinds:
            result.append(asset)
    return result


def _sound_effect_events(segments: list[dict[str, Any]], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not assets:
        return []
    events: list[dict[str, Any]] = []
    last_start_by_path: dict[str, float] = {}
    low_cost_emitted = 0
    for segment in segments:
        text = str(segment.get("text", ""))
        track2_asset = segment.get("track2_asset") or {}
        asset = _choose_sound_effect(text, track2_asset, assets)
        if not asset:
            continue
        start = max(float(segment.get("start", 0)) + 0.06, 0.0)
        path = str(asset["path"])
        if start - last_start_by_path.get(path, -99.0) < 1.2:
            continue
        last_start_by_path[path] = start
        events.append(
            {
                "path": path,
                "start": start,
                "volume": 0.42,
                "max_duration": 0.75,
                "reason": _sound_effect_reason(text, track2_asset),
            }
        )
        if _is_low_cost_segment(text, track2_asset):
            low_cost_emitted += 1
            if low_cost_emitted >= 3:
                last_start_by_path[path] = 10**9
    return events


def _choose_sound_effect(text: str, track2_asset: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if _is_revenue_text(text):
        return _find_sound_asset(assets, ["钱响", "金币", "收银", "提示"])
    if _is_low_cost_segment(text, track2_asset):
        return _find_sound_asset(assets, ["唰", "打响指", "提示", "魔法"])
    return None


def _find_sound_asset(assets: list[dict[str, Any]], preferred_names: list[str]) -> dict[str, Any] | None:
    for name in preferred_names:
        for asset in assets:
            path = str(asset.get("path", ""))
            if name.lower() in Path(path).stem.lower():
                return asset
    return assets[0] if assets else None


def _sound_effect_reason(text: str, track2_asset: dict[str, Any]) -> str:
    if _is_revenue_text(text):
        return "revenue"
    if _is_low_cost_segment(text, track2_asset):
        return "keyword_sequence"
    return "overlay"


def _is_low_cost_segment(text: str, track2_asset: dict[str, Any]) -> bool:
    tags = [str(tag) for tag in track2_asset.get("tags", [])]
    normalized = _normalize_semantic_text(text)
    return bool(tags and tags[0] == "低成本") or any(marker in normalized for marker in ["成本", "囤货", "轻启动"])


def _media_files(root: Path, exts: set[str]) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in exts)


def _estimate_duration(blocks: list[dict[str, Any]], seconds_per_char: float, min_segment_duration: float) -> float:
    if not blocks:
        return 3.0
    total = 0.0
    for block in blocks:
        total += max(len(block.get("text", "")) * seconds_per_char, min_segment_duration)
    return round(max(total, 3.0), 3)


def _segments_from_tts_timestamps(audio_path: str | Path, texts: list[str] | None = None) -> list[dict[str, Any]]:
    sidecar = timestamp_sidecar_path(audio_path)
    if not sidecar.exists():
        return []
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    words = data.get("frontend", {}).get("words", [])
    if not isinstance(words, list):
        return []
    if texts:
        aligned = segments_from_text_and_timestamp_words(texts, words)
        if aligned:
            return aligned
    return segments_from_timestamp_words(words)


def _choose_voice(voices: list[str], index: int, rng: random.Random, strategy: str) -> str:
    if not voices:
        return ""
    if strategy == "random":
        return rng.choice(voices)
    return voices[index % len(voices)]
