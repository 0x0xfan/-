from __future__ import annotations

import json
import random
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from svf.assets import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, scan_assets
from svf.generated_visuals import generate_object_assets_for_blocks
from svf.media import get_media_duration
from svf.renderer import render_video
from svf.subtitles import distribute_segments, segments_from_text_and_timestamp_words, segments_from_timestamp_words, write_srt
from svf.timeline import generate_base_clips, match_assets_for_segments, parse_script_blocks
from svf.tts import default_voice_for_provider, synthesize_tts, timestamp_sidecar_path

SCRIPT_DIR_NAME = "文案"
TRACK1_DIR_NAME = "轨道1"
OUTPUT_DIR_NAME = "输出"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUBTITLE_STYLE_PATH = PROJECT_ROOT / "assets" / "styles" / "jianying_subtitle_presets.json"

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
    asset_root: str | Path | None = None,
    seed: int = 42,
    render: bool = True,
    voices: list[str] | None = None,
    voice_strategy: str = "round_robin",
    tts_provider: str = "",
    tts_model: str = "",
    tts_endpoint: str = "",
    tts_api_key: str = "",
    tts_speed_ratio: float | None = None,
    tts_api_version: str = "",
    tts_resource_id: str = "",
    tts_sample_rate: int | None = None,
    image_api_key: str = "",
    image_model: str = "gpt-image-2",
    image_endpoint: str = "",
    generate_object_images: bool = False,
    seconds_per_char: float = 0.16,
    min_segment_duration: float = 1.6,
    clip_duration: float = 3.0,
    resolution: tuple[int, int] = (1920, 1080),
    subtitle_offset: float = 0.0,
) -> dict[str, Any]:
    """Build one video for each txt file under 文案 using one fixed 轨道1 subgroup per video."""
    root = Path(material_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"素材主文件夹不存在：{root}")

    media_root = Path(asset_root).resolve() if asset_root else root
    if not media_root.exists():
        raise FileNotFoundError(f"绱犳潗涓绘枃浠跺す涓嶅瓨鍦細{media_root}")

    script_files = _script_files(root)
    if not script_files:
        raise FileNotFoundError(f"没有找到文案 txt：{root / SCRIPT_DIR_NAME}")

    track1_groups = _track1_groups(media_root)
    all_assets = scan_assets(media_root)
    track2_assets = _track2_assets(media_root, all_assets)
    bgm_assets = _assets_in_named_dirs(media_root, all_assets, {"bgm", "背景音乐", "音乐"}, {"audio"})
    sound_effect_assets = _project_sound_effect_assets() + _assets_in_named_dirs(media_root, all_assets, {"音效", "sfx", "sound_effects"}, {"audio"})
    bgm_sequence = list(bgm_assets)
    rng = random.Random(seed)
    rng.shuffle(bgm_sequence)

    output_root = _resolve_output_dir(root, output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    voices = [voice.strip() for voice in voices or [] if voice.strip()]
    if tts_provider and not voices:
        default_voice = default_voice_for_provider(tts_provider)
        voices = [default_voice] if default_voice else ["female-shaonv"]
    results = []

    for index, script_path in enumerate(script_files):
        voice = _choose_voice(voices, index, rng, voice_strategy)
        text = script_path.read_text(encoding="utf-8-sig")
        blocks = parse_script_blocks(text)
        script_track2_assets = list(track2_assets)
        video_dir = output_root / script_path.stem
        video_dir.mkdir(parents=True, exist_ok=True)
        if generate_object_images:
            script_track2_assets.extend(
                generate_object_assets_for_blocks(
                    blocks,
                    output_root,
                    script_path.stem,
                    api_key=image_api_key,
                    model=image_model,
                    endpoint=image_endpoint,
                )
            )
        voice_audio = ""
        if tts_provider:
            if not voice:
                raise ValueError("已选择配音接口，但音色列表为空")
            clean_text = "\n".join(block.get("text", "") for block in blocks if block.get("text", ""))
            voice_audio_path = output_root / "配音" / f"{script_path.stem}.mp3"
            voice_audio_path.parent.mkdir(parents=True, exist_ok=True)
            tts_kwargs = {
                "api_key": tts_api_key,
                "voice": voice,
                "model": tts_model,
                "endpoint": tts_endpoint,
            }
            if tts_speed_ratio is not None:
                tts_kwargs["speed_ratio"] = tts_speed_ratio
            if tts_api_version:
                tts_kwargs["api_version"] = tts_api_version
            if tts_resource_id:
                tts_kwargs["resource_id"] = tts_resource_id
            if tts_sample_rate:
                tts_kwargs["sample_rate"] = tts_sample_rate
            voice = _synthesize_tts_with_voice_fallback(
                tts_provider,
                clean_text,
                voice_audio_path,
                tts_kwargs,
                voices,
                voice,
                blocks,
            )
            voice_audio = str(voice_audio_path)
            duration = get_media_duration(voice_audio_path)
            segments = (
                _segments_from_tts_timestamps(voice_audio_path, [block.get("text", "") for block in blocks])
                or _segments_from_block_audio_timeline(voice_audio_path, blocks)
                or distribute_segments(blocks, duration)
            )
        else:
            duration = _estimate_duration(blocks, seconds_per_char, min_segment_duration)
            segments = distribute_segments(blocks, duration)
        segments = _apply_subtitle_offset(segments, duration, subtitle_offset)
        matched_segments = _limit_generated_object_overlays(
            _fill_fallback_track2_assets(
                match_assets_for_segments(segments, script_track2_assets, rules=[]),
                script_track2_assets,
                output_root,
                script_path.stem,
                duration,
            )
        )

        group = track1_groups[index % len(track1_groups)] if track1_groups else None
        if track1_groups:
            shuffled_assets = list(group["assets"])
            rng.shuffle(shuffled_assets)
            base_clips = generate_base_clips(duration, shuffled_assets, clip_duration)
            track1_group_name = group["name"]
        else:
            base_clips = []
            track1_group_name = ""
        style = _style_for_video(index)

        timeline = {
            "project": {"title": script_path.stem, "resolution": list(resolution), "fps": 24},
            "duration": duration,
            "voice_audio": voice_audio or None,
            "bgm_audio": _choose_bgm_audio(bgm_sequence, index),
            "sound_effects": _sound_effect_events(matched_segments, sound_effect_assets),
            "base_clips": base_clips,
            "segments": matched_segments,
            "style": style,
            "subtitle_style_id": style.get("subtitle_style_id", "default"),
            "subtitle_style_source": style.get("subtitle_style_source", ""),
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
        if tts_speed_ratio is not None:
            timeline["tts"]["speed_ratio"] = tts_speed_ratio
        if tts_api_version:
            timeline["tts"]["api_version"] = tts_api_version
        if tts_resource_id:
            timeline["tts"]["resource_id"] = tts_resource_id
        if tts_sample_rate:
            timeline["tts"]["sample_rate"] = tts_sample_rate
        if generate_object_images:
            timeline["generated_object_images"] = [
                {
                    "path": asset.get("path", ""),
                    "object": asset.get("generated_object", ""),
                    "tags": asset.get("tags", []),
                }
                for asset in script_track2_assets
                if asset.get("generated_object")
            ]
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
                "subtitle_style_id": style.get("subtitle_style_id", "default"),
                "subtitle_style_source": style.get("subtitle_style_source", ""),
            }
        )

    summary = {
        "material_root": str(root),
        "asset_root": str(media_root),
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
    total_duration: float | None = None,
) -> list[dict[str, Any]]:
    grouped = _group_assets_by_semantic_folder(assets)
    folder_offsets: dict[str, int] = {}
    result = []
    previous_revenue_asset: dict[str, Any] | None = None
    giveaway_context_remaining = 0
    giveaway_matched = False
    before_practice = True
    for segment in segments:
        enriched = dict(segment)
        text = str(enriched.get("text", ""))
        segment_for_matching = dict(enriched)
        segment_for_matching["before_practice"] = before_practice
        is_giveaway_context_line = _giveaway_context_for_next(text)
        in_giveaway_hold = is_giveaway_context_line or giveaway_context_remaining > 0
        if not enriched.get("track2_asset"):
            sequence_asset = _maybe_make_priority_text_sequence_asset(text)
            if sequence_asset:
                enriched["track2_asset"] = sequence_asset
                enriched["match_reason"] = "generated:text_sequence"
                if sequence_asset.get("contains_giveaway"):
                    giveaway_matched = True
            if not enriched.get("track2_asset"):
                list_asset = _maybe_make_long_list_text_sequence_asset(
                    text,
                    segment_for_matching,
                    total_duration,
                    grouped,
                    giveaway_context_remaining > 0,
                    not giveaway_matched,
                )
                if list_asset:
                    enriched["track2_asset"] = list_asset
                    enriched["match_reason"] = (
                        "generated:semantic_sequence" if list_asset.get("kind") == "semantic_asset_sequence" else "generated:text_sequence"
                    )
                    if list_asset.get("contains_giveaway"):
                        giveaway_matched = True
            if not enriched.get("track2_asset"):
                sequence_asset = _maybe_make_semantic_sequence_asset(
                    text,
                    grouped,
                    segment_for_matching,
                    total_duration,
                    giveaway_context_remaining > 0,
                    not giveaway_matched,
                )
                if sequence_asset:
                    enriched["track2_asset"] = sequence_asset
                    enriched["match_reason"] = "generated:semantic_sequence"
                    if sequence_asset.get("contains_giveaway"):
                        giveaway_matched = True
            match = _find_semantic_folder_match(
                text,
                grouped,
                segment_for_matching,
                total_duration,
                giveaway_context_remaining > 0,
                not giveaway_matched,
            )
            if not enriched.get("track2_asset") and match:
                folder, folder_assets = match
                offset = folder_offsets.get(folder, 0)
                enriched["track2_asset"] = folder_assets[offset % len(folder_assets)]
                enriched["match_reason"] = f"folder:{folder}"
                folder_offsets[folder] = offset + 1
                if _is_giveaway_folder(_normalize_semantic_text(folder)):
                    giveaway_matched = True
                previous_revenue_asset = (
                    enriched["track2_asset"] if _is_revenue_folder(_normalize_semantic_text(folder)) else previous_revenue_asset
                )
            elif previous_revenue_asset and _should_extend_revenue_asset(text):
                enriched["track2_asset"] = previous_revenue_asset
                enriched["match_reason"] = "extended:revenue_context"
        elif _is_revenue_text(text):
            previous_revenue_asset = enriched.get("track2_asset")
        if not enriched.get("track2_asset") and not in_giveaway_hold:
            sequence = _maybe_make_text_sequence_asset(text) or _maybe_make_keyword_text_sequence_asset(text)
            if sequence:
                enriched["track2_asset"] = sequence
                enriched["match_reason"] = "generated:text_sequence"
        if is_giveaway_context_line:
            giveaway_context_remaining = 4
        elif giveaway_context_remaining > 0:
            giveaway_context_remaining -= 1
        if _is_practical_operation_start(text):
            before_practice = False
        result.append(enriched)
    return result


def _maybe_make_priority_text_sequence_asset(text: str) -> dict[str, Any] | None:
    words = _extract_priority_sequence_terms(text)
    if len(words) < 2:
        return None
    return _text_sequence_asset(words, "自动素材/文本序列", ["优先大字序列"])


def _maybe_make_text_sequence_asset(text: str) -> dict[str, Any] | None:
    words = _extract_sequence_terms(text)
    if len(words) < 2:
        return None
    return _text_sequence_asset(words, "自动素材/文本序列", [])


def _text_sequence_asset(words: list[str], rel_dir: str, extra_tags: list[str]) -> dict[str, Any]:
    return {
        "path": f"generated://text_sequence/{_safe_filename('-'.join(words[:4]))}",
        "rel_dir": rel_dir,
        "kind": "generated_text_sequence",
        "tags": ["文本序列", "大字序列", *extra_tags, *words],
        "sequence_terms": words,
        "priority": 6,
    }


def _maybe_make_keyword_text_sequence_asset(text: str) -> dict[str, Any] | None:
    words = _extract_keyword_sequence_terms(text)
    if len(words) < 1:
        return None
    return {
        "path": f"generated://text_sequence/{_safe_filename('-'.join(words[:4]))}",
        "rel_dir": "自动素材/内置大字",
        "kind": "generated_text_sequence",
        "tags": ["文本序列", "大字序列", "内置剪映大字", *words],
        "sequence_terms": words,
        "priority": 6,
    }


def _maybe_make_long_list_text_sequence_asset(
    text: str,
    segment: dict[str, Any] | None,
    total_duration: float | None,
    grouped: dict[str, list[dict[str, Any]]],
    giveaway_context: bool,
    allow_giveaway: bool,
) -> dict[str, Any] | None:
    words = _extract_sequence_terms(text)
    if len(words) < 3:
        return None
    normalized_text = _normalize_semantic_text(text)
    if _is_giveaway_material_list_text(normalized_text):
        semantic = _maybe_make_semantic_sequence_asset(
            text,
            grouped,
            segment,
            total_duration,
            giveaway_context,
            allow_giveaway,
            folder_filter=_is_giveaway_folder,
        )
        if semantic and len(semantic.get("sequence_assets", []) or []) >= 2:
            return semantic
        if _is_closing_lead_segment(segment, total_duration):
            return None
    return _text_sequence_asset(words, "自动素材/文本序列", ["长列表大字"])


def _extract_sequence_terms(text: str, max_terms: int = 6) -> list[str]:
    cleaned = _strip_sentence_punctuation(text)
    if not cleaned:
        return []
    negative_terms = _extract_negative_parallel_terms(cleaned, max_terms=max_terms)
    if len(negative_terms) >= 2:
        return negative_terms
    if not _looks_like_enumeration(cleaned):
        return []
    candidates = re.split(r"[、/／|]+|(?:以及|还有|和|与|及)", cleaned)
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        term = _clean_sequence_term(candidate)
        if not term or term in seen:
            continue
        terms.append(term)
        seen.add(term)
        if len(terms) >= max_terms:
            break
    return terms if len(terms) >= 2 else []


def _extract_priority_sequence_terms(text: str, max_terms: int = 6) -> list[str]:
    cleaned = _strip_sentence_punctuation(text)
    if not cleaned:
        return []
    terms = _extract_negative_parallel_terms(cleaned, max_terms=max_terms)
    if len(terms) < 2:
        return []
    benefit = _extract_benefit_close_term(cleaned)
    if benefit and benefit not in terms and len(terms) < max_terms:
        terms.append(benefit)
    return terms


def _extract_benefit_close_term(text: str) -> str:
    cleaned = _strip_sentence_punctuation(text)
    for marker in ["低成本副业", "低成本项目", "低成本玩法", "低成本"]:
        if marker in cleaned:
            return marker
    return ""


def _looks_like_enumeration(text: str) -> bool:
    if any(mark in text for mark in ["、", "/", "／", "|"]):
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]{2,}(?:以及|还有|和|与|及)[\u4e00-\u9fffA-Za-z0-9]{2,}", text))


def _extract_negative_parallel_terms(text: str, max_terms: int = 6) -> list[str]:
    cleaned_terms: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"(?:不用|不需要|无需)[^，,。！？；.!?;、]{1,16}")
    for match in pattern.finditer(text):
        raw = re.sub(r"的(?:低成本副业|副业|项目|流程|方法|模式|产品|内容|玩法|路子).*$", "", match.group(0)).strip()
        cleaned = _clean_sequence_term(raw)
        if not cleaned:
            continue
        cleaned = re.sub(r"(也|还|都)$", "", cleaned).strip()
        if cleaned and cleaned not in seen:
            cleaned_terms.append(cleaned)
            seen.add(cleaned)
        if len(cleaned_terms) >= max_terms:
            break
    return cleaned_terms


def _strip_sentence_punctuation(text: str) -> str:
    return str(text).strip().strip("，,。！？；.!?;：:、 ")


def _clean_sequence_term(text: str) -> str:
    value = _strip_sentence_punctuation(text)
    value = re.sub(r"^(也|还|都|并且|而且|同时)", "", value).strip()
    value = _trim_sequence_intro(value)
    value = _trim_sequence_subject_prefix(value)
    value = re.sub(r"^(我整理好了|整理好了|我已经把|已经把|我把|都整理好了)(?:完整的|完整|全部的|全部)?", "", value).strip()
    value = re.sub(r"^(比如|例如|包括|包含|搜索|直接搜索|重点看|主要看|核心动作|就是|以及|还有|和|与|及)", "", value).strip()
    value = re.sub(r"(这些词|这类产品|这类|相关的词|这些|等等|等)$", "", value).strip()
    value = value.strip("“”\"'「」『』《》<> ")
    if not value:
        return ""
    if len(value) > 8:
        return ""
    if value in {"这些", "这类", "相关", "直接", "首先", "然后", "再"}:
        return ""
    return value


def _trim_sequence_subject_prefix(value: str) -> str:
    for marker in ["卖的是", "做的是", "卖的就是", "做的就是", "就是", "是"]:
        idx = value.rfind(marker)
        if idx < 0:
            continue
        tail = value[idx + len(marker) :].strip(" ：:，,")
        if 1 <= len(tail) <= 8:
            return tail
    return value


def _trim_sequence_intro(value: str) -> str:
    best_idx = -1
    best_marker = ""
    for marker in ["比如", "例如", "包括", "包含", "直接搜索", "搜索", "搜一下", "搜", "关键词"]:
        idx = value.rfind(marker)
        if idx > best_idx:
            best_idx = idx
            best_marker = marker
    if best_idx >= 0:
        tail = value[best_idx + len(best_marker) :].strip(" ：:，,")
        if tail:
            return tail
    return value


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


def _find_semantic_folder_match(
    text: str,
    grouped: dict[str, list[dict[str, Any]]],
    segment: dict[str, Any] | None = None,
    total_duration: float | None = None,
    giveaway_context: bool = False,
    allow_giveaway: bool = True,
) -> tuple[str, list[dict[str, Any]]] | None:
    normalized_text = _normalize_semantic_text(text)
    candidates = []
    for folder, assets in grouped.items():
        normalized_folder = _normalize_semantic_text(folder)
        if not normalized_folder:
            continue
        if not _should_use_semantic_folder_match(
            normalized_text,
            normalized_folder,
            segment,
            total_duration,
            giveaway_context,
            allow_giveaway,
        ):
            continue
        if _is_giveaway_folder(normalized_folder):
            candidates.append((999, folder, assets))
            continue
        if _is_revenue_folder(normalized_folder):
            candidates.append((900, folder, assets))
            continue
        if normalized_folder in normalized_text or normalized_text in normalized_folder:
            candidates.append((len(normalized_folder), folder, assets))
            continue
        object_hits = _generated_object_hits(normalized_text, assets)
        if object_hits:
            candidates.append((object_hits, folder, assets))
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


def _maybe_make_semantic_sequence_asset(
    text: str,
    grouped: dict[str, list[dict[str, Any]]],
    segment: dict[str, Any] | None = None,
    total_duration: float | None = None,
    giveaway_context: bool = False,
    allow_giveaway: bool = True,
    folder_filter: Any | None = None,
) -> dict[str, Any] | None:
    duration = _segment_duration(segment)
    if duration < 3.0:
        return None
    hint_terms = _extract_asset_hint_terms(text)
    if len(hint_terms) < 2:
        return None

    ranked_sequences: list[tuple[int, str, list[dict[str, Any]]]] = []
    normalized_text = _normalize_semantic_text(text)
    for folder, assets in grouped.items():
        normalized_folder = _normalize_semantic_text(folder)
        if not normalized_folder:
            continue
        if folder_filter and not folder_filter(normalized_folder):
            continue
        if not _should_use_semantic_folder_match(
            normalized_text,
            normalized_folder,
            segment,
            total_duration,
            giveaway_context,
            allow_giveaway,
        ):
            continue
        sequence_assets = _match_assets_to_hint_terms(folder, assets, hint_terms, duration)
        if len(sequence_assets) < 2:
            continue
        score = sum(item["score"] for item in sequence_assets)
        ranked_sequences.append((score, folder, sequence_assets))

    if not ranked_sequences:
        return None

    ranked_sequences.sort(key=lambda item: item[0], reverse=True)
    _, folder, sequence_assets = ranked_sequences[0]
    labels = [item["label"] for item in sequence_assets]
    return {
        "path": f"generated://semantic_sequence/{_safe_filename(folder)}-{_safe_filename('-'.join(labels[:3]))}",
        "rel_dir": f"自动素材/语义素材序列/{folder}",
        "kind": "semantic_asset_sequence",
        "tags": [folder, "素材序列", *labels],
        "sequence_assets": [item["asset"] for item in sequence_assets],
        "priority": 6,
        "contains_giveaway": _is_giveaway_folder(_normalize_semantic_text(folder)),
    }


def _match_assets_to_hint_terms(folder: str, assets: list[dict[str, Any]], hint_terms: list[str], duration: float) -> list[dict[str, Any]]:
    max_items = 3 if duration >= 4.8 else 2
    used_assets: set[str] = set()
    matched: list[dict[str, Any]] = []
    for hint in hint_terms:
        best_asset: dict[str, Any] | None = None
        best_label = ""
        best_score = 0
        for asset in assets:
            asset_key = str(asset.get("path", "")).lower()
            if asset_key in used_assets or str(asset.get("kind", "")) != "image":
                continue
            label, score = _best_asset_label_for_hint(asset, hint, folder)
            if score > best_score:
                best_asset = asset
                best_label = label
                best_score = score
        if best_asset and best_score >= 2:
            used_assets.add(str(best_asset.get("path", "")).lower())
            matched.append({"asset": best_asset, "label": best_label, "score": best_score})
        if len(matched) >= max_items:
            break
    return matched


def _best_asset_label_for_hint(asset: dict[str, Any], hint: str, folder: str) -> tuple[str, int]:
    hint_normalized = _normalize_semantic_text(hint)
    best_label = ""
    best_score = 0
    generic_terms = {
        _normalize_semantic_text(folder),
        _normalize_semantic_text("其他素材"),
        _normalize_semantic_text("赠送资料图"),
        _normalize_semantic_text("闲鱼同行案例"),
        _normalize_semantic_text("闲鱼收益图"),
    }
    raw_terms = [*asset.get("tags", []), Path(str(asset.get("path", ""))).stem]
    for raw in raw_terms:
        label = str(raw).strip()
        normalized = _normalize_semantic_text(label)
        if not normalized or normalized in generic_terms:
            continue
        score = _semantic_overlap_score(hint_normalized, normalized)
        if score > best_score:
            best_label = label
            best_score = score
    return best_label, best_score


def _extract_asset_hint_terms(text: str, max_terms: int = 6) -> list[str]:
    cleaned = _strip_sentence_punctuation(text)
    if not cleaned:
        return []
    candidates = re.split(r"[、/／|]+|(?:以及|还有|和|与|及)", cleaned)
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        term = _clean_asset_hint_term(candidate)
        normalized = _normalize_semantic_text(term)
        if not normalized or normalized in seen:
            continue
        terms.append(term)
        seen.add(normalized)
        if len(terms) >= max_terms:
            break
    return terms


def _clean_asset_hint_term(text: str) -> str:
    value = _strip_sentence_punctuation(text)
    value = re.sub(r"^(我整理好了|整理好了|我已经把|已经把|我把|都整理好了)(?:完整的|完整|全部的|全部)?", "", value).strip()
    value = re.sub(r"^(打开闲鱼|打开|直接搜索|搜索|搜一下|搜|比如|例如|包括|像|还有)", "", value).strip()
    value = re.sub(r"(这些词|相关的词|这类|这一类|这些|等等|等)$", "", value).strip()
    value = re.sub(r"(想要.*|评论区.*|我发你.*|发你.*|领取.*|一键三连.*)$", "", value).strip("：:，, ")
    value = value.strip("“”\"'《》[]（）() ")
    normalized = _normalize_semantic_text(value)
    if len(normalized) < 2 or len(normalized) > 12:
        return ""
    return value


def _semantic_overlap_score(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if left in right or right in left:
        return min(len(left), len(right)) + 3
    longest = 0
    for i in range(len(left)):
        for j in range(len(right)):
            k = 0
            while i + k < len(left) and j + k < len(right) and left[i + k] == right[j + k]:
                k += 1
            if k > longest:
                longest = k
    return longest


def _segment_duration(segment: dict[str, Any] | None) -> float:
    if not segment:
        return 0.0
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", start) or start)
    return max(end - start, 0.0)


def _generated_object_hits(text: str, assets: list[dict[str, Any]]) -> int:
    score = 0
    for asset in assets:
        terms = [str(term) for term in asset.get("match_terms", []) if str(term).strip()]
        if not terms and asset.get("generated_object"):
            terms = [str(asset.get("generated_object"))]
        for term in terms:
            normalized = _normalize_semantic_text(term)
            if normalized and normalized in text:
                score += max(4, len(normalized))
    return score


def _folder_tokens(text: str) -> list[str]:
    return [token for token in text.replace("_", " ").replace("-", " ").split() if token]


def _normalize_semantic_text(text: str) -> str:
    return "".join(char for char in text.lower() if char not in " ，,。！？；.!?;：:、（）()[]【】 \t\r\n")


def _semantic_alias_hits(text: str, folder: str) -> int:
    alias_groups = {
        "收益": ["收益", "收入", "赚", "几k", "5000", "数据", "情况", "这么多", "单子", "跑通", "流程跑通", "低成本副业"],
        "资料": ["领取", "免费", "违禁词", "知识点", "三连", "关注", "评论区", "我发你"],
        "同行案例": ["同行", "案例", "卖得好", "想要数", "浏览量", "这个品", "搜索", "搜出来", "相关的词", "这些词"],
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


def _should_use_semantic_folder_match(
    text: str,
    folder: str,
    segment: dict[str, Any] | None = None,
    total_duration: float | None = None,
    giveaway_context: bool = False,
    allow_giveaway: bool = True,
) -> bool:
    if _is_giveaway_folder(folder):
        if not allow_giveaway:
            return False
        if _is_giveaway_material_list_text(text):
            if _has_giveaway_cta_marker(text):
                return _is_closing_segment(segment, total_duration)
            return _is_closing_lead_segment(segment, total_duration)
        return _is_closing_segment(segment, total_duration) and _is_giveaway_asset_text(text, giveaway_context)
    if _is_revenue_folder(folder):
        return _is_revenue_text(text, segment)
    if "同行案例" in folder:
        return _is_peer_case_text(text)
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


def _is_revenue_folder(folder: str) -> bool:
    return "收益" in folder or "收入" in folder


def _is_peer_case_text(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    if _looks_like_enumeration(str(text)) and not _has_peer_case_specific_marker(normalized):
        return False
    has_search_intent = any(marker in normalized for marker in ["搜索", "搜出来", "相关的词", "这些词", "关键词"])
    has_market_signal = any(marker in normalized for marker in ["同行", "案例", "想要数", "浏览量", "卖得好", "这个品"])
    has_enumeration = _looks_like_enumeration(str(text)) or any(
        marker in normalized for marker in ["资料", "模板", "教程", "表格", "工具", "ai工具"]
    )
    return has_market_signal or (has_search_intent and has_enumeration)


def _has_peer_case_specific_marker(text: str) -> bool:
    return any(marker in text for marker in ["同行", "案例", "想要数", "浏览量", "卖得好", "这个品", "搜出来"])


def _is_giveaway_folder(folder: str) -> bool:
    return "赠送资料图" in folder


def _is_giveaway_asset_text(text: str, giveaway_context: bool = False) -> bool:
    has_material_marker = any(marker in text for marker in ["整理好了", "资料", "教程", "选品库", "知识库", "违禁词"])
    has_cta_marker = _has_giveaway_cta_marker(text)
    has_intent_marker = any(marker in text for marker in ["想要", "想学", "领取"])
    return has_cta_marker and (has_material_marker or has_intent_marker or giveaway_context)


def _is_giveaway_material_list_text(text: str) -> bool:
    has_collection_marker = any(marker in text for marker in ["整理好了", "我已经把", "已经把", "我把", "都整理好了"])
    has_material_marker = any(
        marker in text
        for marker in ["选品库", "爆品库", "自动发货软件", "自动发货工具", "运营教程", "知识库", "违规词库", "违禁词库", "出单教程"]
    )
    has_list_shape = "、" in text or ("和" in text and has_material_marker)
    return has_collection_marker and has_material_marker and has_list_shape


def _has_giveaway_cta_marker(text: str) -> bool:
    return any(marker in text for marker in ["评论区", "我发你", "发你", "领取", "一键三连", "打“", "打\""])


def _giveaway_context_for_next(text: str) -> bool:
    return any(marker in text for marker in ["整理好了", "选品库", "爆品库", "自动发货软件", "运营教程", "知识库", "违规词库", "违禁词库", "出单教程"])


def _is_closing_segment(segment: dict[str, Any] | None, total_duration: float | None) -> bool:
    if not segment or not total_duration:
        return False
    start = float(segment.get("start", 0.0) or 0.0)
    closing_start = max(float(total_duration) * 0.72, float(total_duration) - 12.0)
    return start >= closing_start


def _is_closing_lead_segment(segment: dict[str, Any] | None, total_duration: float | None) -> bool:
    if not segment or not total_duration:
        return False
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", start) or start)
    closing_start = max(float(total_duration) * 0.72, float(total_duration) - 12.0)
    lead_start = max(0.0, closing_start - 8.0)
    return start >= lead_start or end >= closing_start


def _extract_keyword_sequence_terms(text: str) -> list[str]:
    normalized = text.replace(" ", "")
    keywords: list[str] = []
    revenue_markers = ["收益", "收入", "赚", "几K", "5000", "这么多", "单子收益", "额外收入", "首付"]
    if any(marker.lower() in normalized.lower() for marker in revenue_markers):
        keywords.append("收益增长")
    if any(marker in normalized for marker in ["没有什么成本", "没什么成本", "无成本", "不需要囤货", "不囤货"]):
        keywords.append("低成本")
    return _dedupe_texts(keywords)[:4]


def _dedupe_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _is_revenue_text(text: str, segment: dict[str, Any] | None = None) -> bool:
    normalized = _normalize_semantic_text(text)
    if any(
        marker in normalized
        for marker in ["收益", "收入", "赚", "几k", "这么多", "单子", "首付", "跑通", "流程跑通", "低成本副业"]
    ):
        return True
    return bool(segment and segment.get("before_practice") and _is_opening_result_proof_text(normalized))


def _is_opening_result_proof_text(text: str) -> bool:
    has_result_marker = any(
        marker in text
        for marker in [
            "拿到结果",
            "拿到了结果",
            "拿到好的结果",
            "拿到了好的结果",
            "好的结果",
            "效果不错",
            "效果很好",
            "结果不错",
            "结果很好",
            "结果还可以",
            "做出结果",
            "做出了结果",
            "跑出结果",
            "跑出来结果",
            "出结果",
            "出单",
            "爆单",
            "卖出去",
            "卖出去了",
            "成交",
            "到账",
            "回本",
            "首单",
        ]
    )
    has_proof_marker = any(marker in text for marker in ["看一下", "给大家看", "给你看", "展示", "证明", "真实"])
    return has_result_marker or has_proof_marker


def _is_practical_operation_start(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    if any(marker in normalized for marker in ["实操", "教程开始", "开始做", "开始上架", "开始操作", "接下来操作"]):
        return True
    operation_markers = [
        "打开闲鱼",
        "打开上架",
        "搜索",
        "搜",
        "上传图片",
        "传图片",
        "添加图片",
        "选择图片",
        "文案粘贴",
        "粘贴文案",
        "写描述",
        "商品描述",
        "详情描述",
        "设置价格",
        "填写价格",
        "改价格",
        "发货方式",
        "选择发货",
        "设置发货",
        "点击发布",
        "发布",
        "上架",
    ]
    return any(marker in normalized for marker in operation_markers)


def _should_extend_revenue_asset(text: str) -> bool:
    normalized = _normalize_semantic_text(text)
    return any(marker in normalized for marker in ["看一下", "情况", "真实", "有效", "这段时间"])


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
    normalized_names = {str(name).lower() for name in names}
    for asset in assets:
        rel_dir = str(asset.get("rel_dir", ""))
        rel_parts = {part.lower() for part in Path(rel_dir).parts if part}
        if not rel_parts:
            try:
                rel_parts = {part.lower() for part in Path(asset["path"]).relative_to(root).parts}
            except ValueError:
                rel_parts = {part.lower() for part in Path(asset["path"]).parts}
        if rel_parts & normalized_names and asset.get("kind") in kinds:
            result.append(asset)
    return result


def _choose_bgm_audio(assets: list[dict[str, Any]], index: int) -> str | None:
    if not assets:
        return None
    return assets[index % len(assets)]["path"]


def _project_sound_effect_assets() -> list[dict[str, Any]]:
    sfx_root = PROJECT_ROOT / "assets" / "sfx"
    if not sfx_root.exists():
        return []
    return [
        {"path": str(path), "kind": "audio", "tags": [path.stem, path.parent.name]}
        for path in sorted(sfx_root.rglob("*"))
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    ]


def _style_for_video(index: int) -> dict[str, Any]:
    style = dict(DEFAULT_STYLE)
    preset = _subtitle_style_for_video(index)
    if not preset:
        style["subtitle_style_id"] = "default"
        return style
    style.update(preset)
    style["subtitle_style_id"] = str(preset.get("id") or f"subtitle_{index + 1}")
    style["subtitle_style_source"] = str(preset.get("source_preset") or preset.get("source_file") or "")
    return style


def _subtitle_style_for_video(index: int) -> dict[str, Any] | None:
    presets = _load_project_subtitle_presets()
    if not presets:
        return None
    return dict(presets[index % len(presets)])


@lru_cache(maxsize=1)
def _load_project_subtitle_presets() -> tuple[dict[str, Any], ...]:
    if not SUBTITLE_STYLE_PATH.exists():
        return ()
    try:
        data = json.loads(SUBTITLE_STYLE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    presets = data.get("presets", [])
    if not isinstance(presets, list):
        return ()
    return tuple(preset for preset in presets if isinstance(preset, dict))


def _sound_effect_events(segments: list[dict[str, Any]], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return []


def _choose_sound_effect(text: str, track2_asset: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if _is_revenue_text(text):
        return _find_sound_asset(assets, ["钱响", "金币", "收银", "提示"])
    if _is_low_cost_segment(text, track2_asset):
        return _find_sound_asset(assets, ["唰", "打响指", "提示", "魔法"])
    return None


def _choose_sound_effect(text: str, track2_asset: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if _is_revenue_text(text):
        return _find_sound_asset(assets, ["cash", "money", "coin", "钱响", "金币", "收银", "提示"])
    if _is_low_cost_segment(text, track2_asset):
        return _find_sound_asset(assets, ["whoosh", "snap", "notification", "唰", "打响指", "提示", "魔法"])
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


def _synthesize_tts_with_voice_fallback(
    provider: str,
    text: str,
    output_path: Path,
    tts_kwargs: dict[str, Any],
    voices: list[str],
    selected_voice: str,
    blocks: list[dict[str, Any]] | None = None,
) -> str:
    attempted: set[str] = set()
    candidates = [selected_voice] + [voice for voice in voices if voice != selected_voice]
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate or candidate in attempted:
            continue
        attempted.add(candidate)
        kwargs = dict(tts_kwargs)
        kwargs["voice"] = candidate
        try:
            _synthesize_tts_text_or_blocks(provider, text, output_path, kwargs, blocks)
            return candidate
        except RuntimeError as exc:
            last_error = exc
            if not _is_retryable_tts_error(exc):
                raise
    if last_error:
        raise last_error
    raise ValueError("没有可用的 TTS 音色")


def _synthesize_tts_text_or_blocks(
    provider: str,
    text: str,
    output_path: Path,
    tts_kwargs: dict[str, Any],
    blocks: list[dict[str, Any]] | None = None,
) -> None:
    if blocks and len(blocks) > 1:
        _synthesize_tts_by_blocks(provider, blocks, output_path, tts_kwargs)
        return
    synthesize_tts(provider, text, output_path, **tts_kwargs)


def _synthesize_tts_by_blocks(
    provider: str,
    blocks: list[dict[str, Any]],
    output_path: Path,
    tts_kwargs: dict[str, Any],
) -> None:
    import subprocess
    import tempfile

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="svf_block_tts_") as temp_root:
        temp_dir = Path(temp_root)
        chunk_paths: list[Path] = []
        timeline: list[dict[str, Any]] = []
        cursor = 0.0
        for index, block in enumerate(blocks):
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            chunk_path = temp_dir / f"block_{index:03d}.mp3"
            synthesize_tts(provider, text, chunk_path, **tts_kwargs)
            duration = get_media_duration(chunk_path)
            chunk_paths.append(chunk_path)
            timeline.append(
                {
                    "text": text,
                    "manual_tags": block.get("manual_tags", []),
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "duration": round(duration, 3),
                }
            )
            cursor += duration
        _concat_tts_block_audio(chunk_paths, output_path, temp_dir)
    output_path.with_suffix(".block_timeline.json").write_text(
        json.dumps({"segments": timeline, "duration": round(cursor, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _concat_tts_block_audio(audio_paths: list[Path], output_path: Path, temp_dir: Path) -> None:
    concat_file = temp_dir / "concat.txt"
    concat_file.write_text("".join(f"file '{path.name}'\n" for path in audio_paths), encoding="utf-8")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"ffmpeg 拼接分句配音失败：{detail[-1200:]}")


def _is_retryable_tts_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in [
            "Init Engine Instance failed",
            "Fail to feed text",
            "voice",
            "音色",
            "3031",
        ]
    )


def _segments_from_block_audio_timeline(audio_path: str | Path, blocks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    sidecar = Path(audio_path).with_suffix(".block_timeline.json")
    if not sidecar.exists():
        return []
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    segments = data.get("segments", [])
    if not isinstance(segments, list):
        return []
    return [dict(segment) for segment in segments if isinstance(segment, dict)]


def _limit_generated_object_overlays(segments: list[dict[str, Any]], max_count: int = 2) -> list[dict[str, Any]]:
    used = 0
    result = []
    for segment in segments:
        item = dict(segment)
        asset = item.get("track2_asset") or {}
        if asset.get("generated_object"):
            used += 1
            if used > max_count:
                item["track2_asset"] = None
                item["match_reason"] = "none"
        result.append(item)
    return result
