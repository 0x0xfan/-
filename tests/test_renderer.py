import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from svf.renderer import _big_text_frame, _big_text_safe_margins, _big_text_sfx_events, _choose_overlay_transition, _dedupe_sfx_events, _fit_big_text_lines, _fit_subtitle_lines, _fit_text_to_width, _font_can_render, _gap_enrichment_frame, _gap_from_segments, _gap_theme, _keyword_sequence_frame, _load_big_text_presets, _load_font, _make_overlay_clip, _make_overlay_image_clip, _revenue_collage_slots, _subtitle_clip, _text_width, _video_encoder_options


def test_gap_fallback_title_has_no_generic_subtitle():
    theme = _gap_theme("\u627e\u5230\u540e\u628a\u4ed6\u7684\u56fe\u7247\u548c\u6587\u6848\u590d\u5236\u4e0b\u6765\u3002")

    assert theme["title"] == "\u590d\u5236\u7d20\u6750"
    assert theme["subtitle"] == ""


def test_gap_title_uses_keyword_not_incomplete_sentence_slice():
    theme = _gap_theme("就觉得这个项目肯定没利润，")

    assert theme["title"] == "没利润"


def test_gap_title_skips_when_no_keyword_can_be_extracted():
    theme = _gap_theme("其实不是这样。")

    assert theme.get("skip") is True


def test_video_encoder_can_be_forced_to_cpu(monkeypatch):
    monkeypatch.setenv("SVF_VIDEO_ENCODER", "cpu")

    assert _video_encoder_options()[0] == "libx264"


def test_video_encoder_can_be_forced_to_nvenc(monkeypatch):
    monkeypatch.setenv("SVF_VIDEO_ENCODER", "nvenc")

    codec, preset, params = _video_encoder_options()

    assert codec == "h264_nvenc"
    assert preset == "p4"
    assert "-cq" in params


def test_gap_title_is_fit_to_card_text_area():
    image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = "\u8fd9\u662f\u4e00\u53e5\u7279\u522b\u7279\u522b\u957f\u7684\u515c\u5e95\u6d4b\u8bd5\u6587\u6848\u770b\u770b\u4f1a\u4e0d\u4f1a\u8d85\u51fa\u5361\u7247"

    fitted, font, bbox = _fit_text_to_width(
        draw,
        text,
        "C:/Windows/Fonts/msyh.ttc",
        max_size=74,
        min_size=38,
        max_width=420,
        stroke_width=3,
    )

    assert fitted != text
    assert bbox[2] - bbox[0] <= 420
    assert draw.textbbox((0, 0), fitted, font=font, stroke_width=3)[2] <= 420


def test_gap_enrichment_frame_renders_without_generic_subtitle():
    theme = _gap_theme("\u627e\u5230\u540e\u628a\u4ed6\u7684\u56fe\u7247\u548c\u6587\u6848\u590d\u5236\u4e0b\u6765\u3002")

    frame = _gap_enrichment_frame(theme, 1920, 1080, 0.35, 2.2)

    assert frame.size == (1920, 1080)


def test_keyword_sequence_frame_uses_transparent_big_text_not_cards():
    frame = _keyword_sequence_frame(["不用囤货", "低成本", "轻启动"], (22, 118, 92), 1920, 1080, 0.7, 2.4)
    alpha_bbox = frame.getchannel("A").getbbox()

    assert alpha_bbox is not None
    assert frame.getpixel((260, 345))[3] == 0


def test_portrait_overlay_image_keeps_background_transparent(tmp_path: Path):
    path = tmp_path / "portrait.png"
    Image.new("RGBA", (300, 900), (240, 248, 255, 255)).save(path)

    clip = _make_overlay_image_clip(path, 1920, 1080, {"tags": ["物体名词"], "rel_dir": ""})

    assert clip.w < 1920
    assert clip.h < 1080
    assert clip.mask is not None
    assert float(clip.mask.get_frame(0)[0, 0]) > 0.99


def test_transparent_overlay_image_preserves_alpha(tmp_path: Path):
    path = tmp_path / "object.png"
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((260, 260, 764, 764), fill=(255, 255, 255, 255))
    image.save(path)

    clip = _make_overlay_image_clip(path, 1920, 1080, {"tags": ["AI生成素材"], "rel_dir": ""})

    assert clip.mask is not None
    assert float(clip.mask.get_frame(0)[0, 0]) == 0.0


def test_semantic_sequence_overlay_renders_multiple_images(tmp_path: Path):
    paths = []
    for index, color in enumerate(["red", "green", "blue"], start=1):
        path = tmp_path / f"item_{index}.png"
        Image.new("RGBA", (900, 620), color).save(path)
        paths.append(path)

    clip = _make_overlay_clip(
        {
            "path": "generated://semantic_sequence/demo",
            "kind": "semantic_asset_sequence",
            "sequence_assets": [{"path": str(path), "rel_dir": "其他素材/赠送资料图", "kind": "image"} for path in paths],
        },
        1920,
        1080,
        4.8,
    )

    assert clip.mask is not None
    frame = clip.get_frame(0.1)
    assert frame.shape[0] < 1080
    assert frame.shape[1] < 1920


def test_overlay_video_assets_use_hard_cut_transition():
    transition = _choose_overlay_transition(
        {"start": 1.2, "text": "打开闲鱼"},
        {"path": "其他素材/闲鱼操作/打开闲鱼/a.mp4", "kind": "video"},
    )

    assert transition == "cut"


def test_jianying_big_text_presets_are_curated_for_infoflow_big_words():
    data = json.loads(Path("assets/styles/jianying_big_text_presets.json").read_text(encoding="utf-8"))
    presets = data.get("presets", [])
    categories = Counter(item.get("category") for item in presets)
    excluded_source_markers = ["闲鱼引导", "知识库", "资料", "左侧字幕", "右侧字幕", "底层字幕", "人物", "水印"]

    assert data.get("selection_mode") == "curated_infoflow_big_text"
    assert data.get("default_mode") == "pure_text_no_cards"
    assert data.get("source") == "project_embedded_jianying_big_text_presets"
    assert data.get("counts", {}).get("total") == len(presets)
    assert len(presets) >= 20
    assert {"impact", "split_glitch", "number", "multi_word", "motion"}.issubset(categories)
    assert all(Path(preset["font"]).exists() for preset in presets if preset.get("font"))
    assert not [
        preset.get("source_preset")
        for preset in presets
        if any(marker in str(preset.get("source_preset") or "") for marker in excluded_source_markers)
    ]


def test_jianying_subtitle_presets_are_project_assets():
    data = json.loads(Path("assets/styles/jianying_subtitle_presets.json").read_text(encoding="utf-8"))
    presets = data.get("presets", [])

    assert data.get("default_mode") == "one_subtitle_style_per_video"
    assert len(presets) == 10
    assert all(Path(preset["font"]).exists() for preset in presets if preset.get("font"))


def test_subtitle_lines_fit_width_before_rendering():
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    max_width = 720
    lines, font = _fit_subtitle_lines(
        draw,
        "这是一句特别特别长的字幕测试看看会不会超出画幅右边",
        "C:/Windows/Fonts/msyh.ttc",
        56,
        28,
        max_width,
        2,
        4,
    )

    assert 1 <= len(lines) <= 2
    assert all(_text_width(draw, line, font, 4) <= max_width for line in lines)


def test_subtitle_clip_uses_bottom_safe_position():
    clip = _subtitle_clip(
        "这是一句特别特别长的字幕测试看看会不会超出画幅右边",
        1080,
        1920,
        {
            "font": "C:/Windows/Fonts/msyh.ttc",
            "subtitle_font_size": 56,
            "subtitle_fill": "#ffde00",
            "subtitle_stroke": "#000000",
            "subtitle_stroke_width": 4,
            "subtitle_max_width_ratio": 0.82,
            "subtitle_bottom_margin": 90,
            "subtitle_max_lines": 2,
        },
    )

    assert clip.w == 1080
    assert 0 < clip.h < 220
    assert clip.pos(0)[1] == 1920 - clip.h - 90


def test_all_big_text_presets_stay_inside_safe_area():
    width, height = 1600, 900
    safe_x, safe_y = _big_text_safe_margins(width, height)

    for index in range(len(_load_big_text_presets())):
        frame = _big_text_frame("避开价格战", width, height, 0.35, 1.6, index)
        bbox = frame.getchannel("A").getbbox()
        assert bbox is not None
        left, top, right, bottom = bbox
        assert left >= safe_x - 1
        assert top >= safe_y - 1
        assert right <= width - safe_x + 1
        assert bottom <= height - safe_y + 1


def test_big_text_font_falls_back_for_missing_chinese_glyphs():
    font = _load_font("C:/Windows/Fonts/marlett.ttf", 72, sample_text="避开价格战")

    assert _font_can_render(font, "避开价格战")


def test_gap_title_uses_semantic_label_instead_of_sentence_slice():
    theme = _gap_theme("\u50cf\u8fd9\u4e00\u5468\u7684\u65f6\u95f4\uff0c")

    assert theme["title"] == "\u4e00\u5468\u5b9e\u6d4b"
    assert "\u50cf\u8fd9" not in theme["title"]
    assert theme["subtitle"] == ""


def test_big_text_lines_keep_complete_semantic_phrase():
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    lines, font, bboxes = _fit_big_text_lines(
        draw,
        "\u665a\u4e0a\u53ea\u6709\u4e00\u4e24\u4e2a\u5c0f\u65f6",
        "C:/Windows/Fonts/msyh.ttc",
        92,
        38,
        760,
        stroke_width=5,
    )

    assert "".join(lines) == "\u665a\u4e0a\u53ea\u6709\u4e00\u4e24\u4e2a\u5c0f\u65f6"
    assert 1 <= len(lines) <= 2
    assert all((bbox[2] - bbox[0]) <= 760 for bbox in bboxes)
    assert getattr(font, "size", 38) >= 38


def test_gap_big_text_is_centered_when_no_material():
    frame = _gap_enrichment_frame(
        {"title": "\u665a\u4e0a\u53ea\u6709\u4e00\u4e24\u4e2a\u5c0f\u65f6", "subtitle": "", "icon": "clock"},
        1080,
        1920,
        0.35,
        2.2,
    )
    bbox = frame.getchannel("A").getbbox()

    assert bbox is not None
    left, top, right, bottom = bbox
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    assert 1080 * 0.35 <= center_x <= 1080 * 0.65
    assert 1920 * 0.25 <= center_y <= 1920 * 0.55


def test_big_text_frame_keeps_long_phrase_inside_safe_area():
    width, height = 1080, 1920
    safe_x, safe_y = _big_text_safe_margins(width, height)

    frame = _big_text_frame(
        "\u62ff\u5230\u8d44\u6599\u540e\u4ea4\u4ed8",
        width,
        height,
        0.35,
        1.6,
        0,
        position_override=(0.5, 0.42),
        max_width_ratio=0.82,
    )
    bbox = frame.getchannel("A").getbbox()

    assert bbox is not None
    left, top, right, bottom = bbox
    assert left >= safe_x - 1
    assert top >= safe_y - 1
    assert right <= width - safe_x + 1
    assert bottom <= height - safe_y + 1


def test_big_text_sfx_uses_single_quiet_hit_per_visual():
    events = []
    for index in range(len(_load_big_text_presets())):
        generated = _big_text_sfx_events(index, 3.0, 2.0)
        assert len(generated) <= 1
        events.extend(generated)

    assert all(float(event["volume"]) <= 0.3 for event in events)
    assert all(float(event["max_duration"]) <= 0.5 for event in events)


def test_sfx_events_are_deduped_by_timing():
    events = _dedupe_sfx_events(
        [
            {"path": "a.mp3", "start": 1.0, "volume": 0.8, "max_duration": 1.2},
            {"path": "b.mp3", "start": 1.3, "volume": 0.8, "max_duration": 1.2},
            {"path": "c.mp3", "start": 2.0, "volume": 0.8, "max_duration": 1.2},
        ]
    )

    assert [event["path"] for event in events] == ["a.mp3", "c.mp3"]
    assert all(event["volume"] <= 0.3 for event in events)
    assert all(event["max_duration"] <= 0.5 for event in events)


def test_revenue_three_image_layout_keeps_large_primary_slot():
    slots = _revenue_collage_slots(3, 1920, 1080)

    assert len(slots) == 3
    assert max(slot[2] for slot in slots) >= 1050
    assert max(slot[3] for slot in slots) >= 830


def test_long_track2_gap_gets_multiple_semantic_stickers():
    segments = [
        {"start": 79.39, "end": 80.85, "text": "\u4e70\u4e00\u6b21\u80fd\u5356\u65e0\u6570\u6b21\uff0c"},
        {"start": 81.34, "end": 82.597, "text": "\u4e00\u672c\u4e07\u5229\u3002"},
        {"start": 82.597, "end": 85.372, "text": "\u56e0\u4e3a\u95f2\u9c7c\u662f\u5343\u4eba\u5343\u9762\u7684\u7535\u5546\u5e73\u53f0\uff0c"},
        {"start": 85.802, "end": 87.222, "text": "\u6240\u4ee5\u4e0d\u8981\u53bb\u6253\u4ef7\u683c\u6218\uff0c"},
        {"start": 87.782, "end": 89.542, "text": "\u6211\u5c31\u662f\u9760\u8fd9\u4e2a\u590d\u5236\u7c98\u8d34\uff0c"},
        {"start": 90.052, "end": 91.352, "text": "\u6bcf\u5929\u80fd\u51fa\u51e0\u767e\u5355\u3002"},
        {"start": 91.892, "end": 93.822, "text": "\u5982\u679c\u4f60\u8fd8\u4e0d\u6e05\u695a\u600e\u4e48\u64cd\u4f5c\uff0c"},
    ]

    gaps = _gap_from_segments(segments, 3.0)

    assert len(gaps) == 1
    assert gaps[0]["start"] == 79.39
    assert gaps[0]["end"] == 93.822
    assert [item["title"] for item in gaps[0]["items"]] == ["\u4e00\u6b21\u590d\u7528", "\u5343\u4eba\u5343\u9762", "\u907f\u5f00\u4ef7\u683c\u6218", "\u590d\u5236\u7c98\u8d34"]
    assert [round(item["start"], 3) for item in gaps[0]["items"]] == [79.39, 82.597, 85.802, 87.782]


def test_short_track2_gap_gets_one_sticker():
    segments = [
        {"start": 10.0, "end": 11.5, "text": "\u8fd9\u662f\u6700\u8fd1\u7684\u60c5\u51b5\uff0c"},
        {"start": 11.9, "end": 13.8, "text": "\u6bcf\u5929\u90fd\u80fd\u51fa\u5355\u3002"},
    ]

    gaps = _gap_from_segments(segments, 3.0)

    assert len(gaps) == 1
    assert gaps[0]["start"] == 10.0
