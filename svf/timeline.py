from __future__ import annotations

import re
from typing import Any

TAG_RE = re.compile(r"\[\[([^\]]+)\]\]")


def parse_script_blocks(script_text: str) -> list[dict[str, Any]]:
    """把文案拆成句子块，并提取 [[素材标签]]。"""
    blocks: list[dict[str, Any]] = []
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tags = [tag.strip() for tag in TAG_RE.findall(line) if tag.strip()]
        text = TAG_RE.sub("", line).strip()
        for sentence in _split_by_punctuation(text):
            blocks.append({"text": sentence, "manual_tags": tags})
    return blocks


def match_assets_for_segments(
    segments: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """给每个字幕片段匹配轨道2素材：手动标签优先，其次关键词规则。"""
    result: list[dict[str, Any]] = []
    for segment in segments:
        matched_asset = None
        reason = "none"

        manual_tags = segment.get("manual_tags", [])
        for tag in manual_tags:
            matched_asset = _find_asset_by_tag(assets, tag)
            if matched_asset:
                reason = f"manual_tag:{tag}"
                break

        if matched_asset is None:
            text = segment.get("text", "")
            for rule in rules:
                keywords = rule.get("keywords", [])
                if any(keyword in text for keyword in keywords):
                    for asset_tag in rule.get("asset_tags", []):
                        matched_asset = _find_asset_by_tag(assets, asset_tag)
                        if matched_asset:
                            reason = f"rule:{rule.get('event', 'unknown')}"
                            break
                if matched_asset:
                    break

        enriched = dict(segment)
        enriched["track2_asset"] = matched_asset
        enriched["match_reason"] = reason
        result.append(enriched)
    return result


def generate_base_clips(
    total_duration: float,
    base_assets: list[str],
    clip_duration: float,
    speed: float = 1.0,
) -> list[dict[str, Any]]:
    """生成无缝覆盖总时长的轨道1底层视频片段时间轴。"""
    if total_duration <= 0:
        return []
    if not base_assets:
        raise ValueError("base_assets 不能为空")
    if clip_duration <= 0:
        raise ValueError("clip_duration 必须大于 0")
    if speed <= 0:
        raise ValueError("speed 必须大于 0")

    clips: list[dict[str, Any]] = []
    cursor = 0.0
    index = 0
    while cursor < total_duration:
        end = min(total_duration, round(cursor + clip_duration, 6))
        clips.append(
            {
                "start": cursor,
                "end": end,
                "asset": base_assets[index % len(base_assets)],
                "speed": speed,
            }
        )
        cursor = end
        index += 1
    return clips


def _find_asset_by_tag(assets: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    tag_lower = tag.lower()
    for asset in assets:
        tags = [str(item).lower() for item in asset.get("tags", [])]
        path = str(asset.get("path", "")).lower()
        if tag_lower in tags or tag_lower in path:
            return asset
    return None


def _split_by_punctuation(text: str) -> list[str]:
    sentences: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text):
        current.append(char)
        if char in "，,。！？；.!?;" and not _is_decimal_point(text, index):
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
    tail = "".join(current).strip()
    if tail:
        sentences.append(tail)
    return sentences


def _is_decimal_point(text: str, index: int) -> bool:
    if text[index] not in ".。":
        return False
    return index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit()
