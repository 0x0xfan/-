from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont


OBJECT_SPECS: list[dict[str, Any]] = [
    {
        "keyword": "手机",
        "aliases": ["手机"],
        "prompt": "a clean modern smartphone showing a simple ecommerce chat and order notification interface, no readable text, bright product photo style, transparent background",
    },
    {
        "keyword": "快递盒",
        "aliases": ["快递盒", "纸箱", "包裹"],
        "prompt": "a neat cardboard delivery box with shipping tape, product photo style, isolated object, transparent background",
    },
    {
        "keyword": "电脑",
        "aliases": ["电脑"],
        "prompt": "a laptop computer with a clean ecommerce dashboard on screen, no readable text, modern product photo style, transparent background",
    },
    {
        "keyword": "矿泉水",
        "aliases": ["矿泉水"],
        "prompt": "a clear plastic bottled mineral water with blue cap, realistic product photo, isolated object, transparent background, no brand, no text",
    },
    {
        "keyword": "汽车",
        "aliases": ["车子", "汽车", "极氪9X", "极氪9x"],
        "prompt": "a premium modern electric SUV silhouette, glossy studio product render, no logo, transparent background",
    },
]


def generate_object_assets_for_blocks(
    blocks: list[dict[str, Any]],
    output_root: str | Path,
    video_stem: str,
    api_key: str = "",
    model: str = "gpt-image-2",
    endpoint: str = "",
    max_assets: int = 2,
) -> list[dict[str, Any]]:
    """Generate reusable visual object assets for concrete nouns found in the script."""
    output_dir = Path(output_root) / "自动素材" / video_stem / "物体名词"
    selected = _select_object_specs(blocks, max_assets=max_assets)
    assets: list[dict[str, Any]] = []
    for spec in selected:
        path = output_dir / f"{_safe_filename(str(spec['keyword']))}.png"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if api_key:
                    _generate_openai_image(path, str(spec["prompt"]), api_key, model=model, endpoint=endpoint)
                else:
                    _write_fallback_object_image(path, str(spec["keyword"]))
            except Exception:
                _write_fallback_object_image(path, str(spec["keyword"]))
        assets.append(
            {
                "path": str(path),
                "rel_dir": str(path.parent),
                "kind": "image",
                "tags": [str(spec["keyword"]), "物体名词", "AI生成素材", *spec.get("aliases", [])],
                "priority": 7,
                "generated_object": str(spec["keyword"]),
                "match_terms": spec.get("aliases", []),
            }
        )
    return assets


def _select_object_specs(blocks: list[dict[str, Any]], max_assets: int) -> list[dict[str, Any]]:
    text = "\n".join(str(block.get("text", "")) for block in blocks)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, spec in enumerate(OBJECT_SPECS):
        aliases = [str(alias) for alias in spec.get("aliases", [])]
        score = sum(_object_alias_score(text, alias) for alias in aliases if alias)
        if score:
            scored.append((score, -index, spec))
    scored.sort(reverse=True)
    return [spec for _, _, spec in scored[:max_assets]]


def _object_alias_score(text: str, alias: str) -> int:
    normalized = str(text or "")
    if not alias or alias not in normalized:
        return 0

    score = normalized.count(alias)
    visual_contexts = [
        "比如",
        "例如",
        "就像",
        "一瓶",
        "一台",
        "一辆",
        "一个",
        "桌上",
        "旁边",
        "放着",
        "放了",
        "出现",
        "看到",
    ]
    incidental_contexts = [
        "盯着手机",
        "守着手机",
        "看手机",
        "拿着手机",
        "刷手机",
        "玩手机",
        "一直盯着",
    ]
    if any(context in normalized for context in incidental_contexts):
        score -= 2
    if any(context in normalized for context in visual_contexts):
        score += 2
    if alias in {"矿泉水", "极氪9X", "极氪9x"} and "就像" in normalized:
        score += 2
    return max(score, 0)


def _generate_openai_image(path: Path, prompt: str, api_key: str, model: str, endpoint: str) -> None:
    url = endpoint.strip() or "https://api.openai.com/v1/images/generations"
    payload = {
        "model": model or "gpt-image-2",
        "prompt": prompt,
        "size": "1024x1024",
        "background": "transparent",
    }
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=180,
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else response.text[:500]
        raise RuntimeError(f"OpenAI image generation failed: {response.status_code} {message}")
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI image generation returned no b64_json")
    path.write_bytes(base64.b64decode(b64))


def _write_fallback_object_image(path: Path, keyword: str) -> None:
    width = height = 1024
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = _accent_for_keyword(keyword)
    _draw_object_shadow(image, keyword)
    _draw_object_symbol(draw, keyword, color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _draw_object_shadow(image: Image.Image, keyword: str) -> None:
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    if keyword == "手机":
        draw.rounded_rectangle((340, 210, 690, 820), radius=62, fill=(0, 0, 0, 72))
    elif keyword == "矿泉水":
        draw.rounded_rectangle((405, 210, 620, 835), radius=70, fill=(0, 0, 0, 70))
    elif keyword == "电脑":
        draw.rounded_rectangle((210, 260, 815, 715), radius=42, fill=(0, 0, 0, 70))
    elif keyword == "快递盒":
        draw.polygon([(260, 400), (520, 250), (780, 400), (780, 660), (520, 810), (260, 660)], fill=(0, 0, 0, 64))
    else:
        draw.rounded_rectangle((195, 390, 830, 690), radius=105, fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    image.alpha_composite(shadow)


def _draw_object_symbol(draw: ImageDraw.ImageDraw, keyword: str, color: tuple[int, int, int]) -> None:
    fill = color + (255,)
    soft = color + (42,)
    if keyword == "手机":
        draw.rounded_rectangle((360, 190, 665, 840), radius=58, fill=(22, 28, 37, 255), outline=fill, width=16)
        draw.rounded_rectangle((392, 255, 633, 740), radius=28, fill=(242, 247, 252, 255))
        draw.ellipse((488, 765, 538, 815), fill=fill)
        draw.rectangle((462, 222, 563, 238), fill=(72, 86, 106, 255))
        return
    if keyword == "快递盒":
        cardboard = (184, 126, 69, 255)
        light = (230, 178, 112, 255)
        draw.polygon([(260, 400), (520, 260), (780, 400), (520, 540)], fill=light, outline=fill)
        draw.polygon([(260, 400), (520, 540), (520, 800), (260, 650)], fill=cardboard, outline=fill)
        draw.polygon([(780, 400), (520, 540), (520, 800), (780, 650)], fill=(202, 145, 82, 255), outline=fill)
        draw.line((520, 260, 520, 800), fill=(255, 230, 160, 255), width=18)
        return
    if keyword == "电脑":
        draw.rounded_rectangle((230, 260, 795, 620), radius=30, fill=(28, 36, 48, 255), outline=fill, width=14)
        draw.rounded_rectangle((270, 305, 755, 575), radius=16, fill=(235, 241, 248, 255))
        draw.polygon([(180, 650), (850, 650), (760, 740), (270, 740)], fill=(71, 85, 105, 255), outline=fill)
        return
    if keyword == "矿泉水":
        draw.rectangle((455, 190, 570, 275), fill=fill)
        draw.rounded_rectangle((405, 245, 620, 845), radius=78, fill=(232, 248, 255, 210), outline=fill, width=14)
        draw.rounded_rectangle((380, 455, 645, 575), radius=28, fill=(220, 244, 255, 230), outline=fill, width=8)
        draw.arc((420, 300, 605, 760), start=90, end=270, fill=(255, 255, 255, 180), width=10)
        return
    draw.rounded_rectangle((200, 380, 825, 630), radius=110, fill=(42, 53, 70, 255), outline=fill, width=16)
    draw.polygon([(310, 380), (470, 260), (650, 380)], fill=(70, 86, 110, 255), outline=fill)
    draw.ellipse((300, 580, 430, 710), fill=(245, 248, 252, 255), outline=fill, width=14)
    draw.ellipse((595, 580, 725, 710), fill=(245, 248, 252, 255), outline=fill, width=14)


def _accent_for_keyword(keyword: str) -> tuple[int, int, int]:
    palette = {
        "手机": (37, 99, 235),
        "快递盒": (217, 119, 6),
        "电脑": (71, 85, 105),
        "矿泉水": (2, 132, 199),
        "汽车": (30, 64, 175),
    }
    return palette.get(keyword, (42, 99, 235))


def _load_font(size: int):
    for candidate in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _safe_filename(value: str) -> str:
    return "".join(char for char in value if char not in '\\/:*?"<>|').strip() or "object"


def openai_image_api_key(config: dict[str, Any] | None = None) -> str:
    if config:
        providers = config.get("providers", {}) if isinstance(config.get("providers", {}), dict) else {}
        openai = providers.get("openai", {}) if isinstance(providers.get("openai", {}), dict) else {}
        value = openai.get("image_api_key") or openai.get("api_key") or openai.get("token")
        if value:
            return str(value).strip()
    return os.getenv("OPENAI_API_KEY", "").strip()


def openai_image_model(config: dict[str, Any] | None = None) -> str:
    if config:
        providers = config.get("providers", {}) if isinstance(config.get("providers", {}), dict) else {}
        openai = providers.get("openai", {}) if isinstance(providers.get("openai", {}), dict) else {}
        value = openai.get("image_model")
        if value:
            return str(value).strip()
    return "gpt-image-2"
