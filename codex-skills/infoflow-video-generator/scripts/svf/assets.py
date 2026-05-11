from __future__ import annotations

import json
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS


def scan_assets(asset_root: str | Path) -> list[dict[str, Any]]:
    """扫描素材目录，读取同名 json 元数据；没有元数据则从路径推断标签。"""
    root = Path(asset_root)
    if not root.exists():
        return []

    assets: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        sidecar = path.with_suffix(".json")
        metadata: dict[str, Any] = {}
        if sidecar.exists():
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))

        rel_parts = path.relative_to(root).with_suffix("").parts
        inferred_tags = [part for part in rel_parts if part]
        tags = metadata.get("tags") or inferred_tags
        kind = metadata.get("kind") or _infer_kind(path)
        assets.append(
            {
                "path": str(path),
                "rel_dir": str(path.relative_to(root).parent),
                "tags": tags,
                "kind": kind,
                "priority": int(metadata.get("priority", 5)),
            }
        )
    return assets


def _infer_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "unknown"
