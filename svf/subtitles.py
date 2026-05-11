from __future__ import annotations

from pathlib import Path
from typing import Any


def distribute_segments(blocks: list[dict[str, Any]], total_duration: float) -> list[dict[str, Any]]:
    """没有 Whisper 时的保底方案：按文字长度把文案分配到音频总时长。"""
    if not blocks:
        return []
    weights = [max(len(block.get("text", "")), 1) for block in blocks]
    total_weight = sum(weights)
    cursor = 0.0
    segments: list[dict[str, Any]] = []
    for index, (block, weight) in enumerate(zip(blocks, weights)):
        if index == len(blocks) - 1:
            end = float(total_duration)
        else:
            duration = total_duration * weight / total_weight
            end = round(cursor + duration, 3)
        segment = dict(block)
        segment["start"] = round(cursor, 3)
        segment["end"] = round(end, 3)
        segments.append(segment)
        cursor = end
    return segments


def write_srt(segments: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for index, segment in enumerate(segments, start=1):
        parts.append(str(index))
        parts.append(f"{_format_srt_time(segment['start'])} --> {_format_srt_time(segment['end'])}")
        parts.append(str(segment.get("text", "")))
        parts.append("")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def segments_from_timestamp_words(words: list[dict[str, Any]], max_chars: int = 24) -> list[dict[str, Any]]:
    """Build subtitle segments from TTS word timestamps."""
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        text = str(word.get("word", ""))
        if not text:
            continue
        current.append(word)
        joined = "".join(str(item.get("word", "")) for item in current)
        if _ends_sentence(text) or len(joined) >= max_chars:
            segments.append(_timestamp_chunk_to_segment(current))
            current = []
    if current:
        segments.append(_timestamp_chunk_to_segment(current))
    return segments


def segments_from_text_and_timestamp_words(
    texts: list[str],
    words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Align user-authored sentence blocks to TTS timestamps without breaking their text."""
    segments: list[dict[str, Any]] = []
    cursor = 0
    normalized_words = [_normalize_tts_text(str(word.get("word", ""))) for word in words]
    for text in texts:
        clean_text = text.strip()
        if not clean_text:
            continue
        target = _normalize_tts_text(clean_text)
        if not target:
            continue
        start_index = cursor
        consumed = ""
        end_index = cursor - 1
        while end_index + 1 < len(words) and len(consumed) < len(target):
            end_index += 1
            consumed += normalized_words[end_index]
        if end_index < start_index:
            continue
        segments.append(
            {
                "text": clean_text,
                "manual_tags": [],
                "start": round(float(words[start_index].get("start_time", 0)) / 1000.0, 3),
                "end": round(float(words[end_index].get("end_time", 0)) / 1000.0, 3),
            }
        )
        cursor = end_index + 1
    return segments


def _format_srt_time(seconds: float) -> str:
    milliseconds_total = int(round(seconds * 1000))
    milliseconds = milliseconds_total % 1000
    total_seconds = milliseconds_total // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _timestamp_chunk_to_segment(words: list[dict[str, Any]]) -> dict[str, Any]:
    text = "".join(str(item.get("word", "")) for item in words).strip()
    start = float(words[0].get("start_time", 0)) / 1000.0
    end = float(words[-1].get("end_time", 0)) / 1000.0
    return {
        "text": text,
        "manual_tags": [],
        "start": round(start, 3),
        "end": round(max(end, start + 0.1), 3),
    }


def _ends_sentence(text: str) -> bool:
    return text.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))


def _normalize_tts_text(text: str) -> str:
    table = str.maketrans(
        {
            "，": "",
            "。": "",
            "！": "",
            "？": "",
            "；": "",
            "、": "",
            ",": "",
            ".": "",
            "!": "",
            "?": "",
            ";": "",
            " ": "",
            "\n": "",
            "\r": "",
            "\t": "",
        }
    )
    return text.translate(table)
