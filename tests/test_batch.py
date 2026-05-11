from pathlib import Path
import json

from PIL import Image

import svf.batch
from svf.batch import _choose_bgm_audio, _extract_sequence_terms, _fill_fallback_track2_assets, _sound_effect_events
from svf.batch import build_from_material_folder, inspect_material_folder
from svf.generated_visuals import _select_object_specs


def test_batch_uses_one_track1_subfolder_per_script(tmp_path: Path):
    root = tmp_path / "materials"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "轨道1" / "女2").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("第一条文案\n第二句", encoding="utf-8")
    (root / "文案" / "b.txt").write_text("另一条文案", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")
    Image.new("RGB", (720, 1280), "blue").save(root / "轨道1" / "女2" / "b.png")

    summary = build_from_material_folder(root, render=False)

    assert summary["count"] == 2
    assert summary["results"][0]["track1_group"] == "女1"
    assert summary["results"][1]["track1_group"] == "女2"
    assert (root / "输出" / "a" / "timeline.json").exists()
    assert (root / "输出" / "b" / "subtitles.srt").exists()


def test_inspect_material_folder_counts_scripts_and_track1_groups(tmp_path: Path):
    root = tmp_path / "materials"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("文案", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")

    info = inspect_material_folder(root)

    assert info["script_count"] == 1
    assert info["track1_groups"] == [{"name": "女1", "count": 1}]


def test_batch_assigns_multiple_voices_round_robin(tmp_path: Path):
    root = tmp_path / "materials"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("第一条文案", encoding="utf-8")
    (root / "文案" / "b.txt").write_text("第二条文案", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")

    summary = build_from_material_folder(root, render=False, voices=["voice_a", "voice_b"])

    assert summary["results"][0]["voice_name"] == "voice_a"
    assert summary["results"][1]["voice_name"] == "voice_b"
    timeline = json.loads((root / "输出" / "a" / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["voice_name"] == "voice_a"


def test_batch_assigns_one_subtitle_style_per_video(tmp_path: Path, monkeypatch):
    root = tmp_path / "materials"
    script_dir = root / svf.batch.SCRIPT_DIR_NAME
    track1_dir = root / svf.batch.TRACK1_DIR_NAME / "group"
    script_dir.mkdir(parents=True)
    track1_dir.mkdir(parents=True)
    for name in ["a", "b", "c"]:
        (script_dir / f"{name}.txt").write_text("line one\nline two", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(track1_dir / "a.png")
    monkeypatch.setattr(
        svf.batch,
        "_load_project_subtitle_presets",
        lambda: (
            {"id": "style_a", "source_preset": "preset_a", "font": "font_a.ttf", "subtitle_fill": "#ffffff"},
            {"id": "style_b", "source_preset": "preset_b", "font": "font_b.ttf", "subtitle_fill": "#ffde00"},
        ),
    )

    summary = build_from_material_folder(root, render=False)

    assert [item["subtitle_style_id"] for item in summary["results"]] == ["style_a", "style_b", "style_a"]
    for item in summary["results"]:
        timeline = json.loads(Path(item["timeline"]).read_text(encoding="utf-8"))
        assert timeline["subtitle_style_id"] == timeline["style"]["subtitle_style_id"]
        assert timeline["style"]["font"] in {"font_a.ttf", "font_b.ttf"}
        assert all("subtitle_style_id" not in segment for segment in timeline["segments"])


def test_batch_writes_to_custom_output_dir(tmp_path: Path):
    root = tmp_path / "materials"
    out = tmp_path / "exports"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("第一条文案", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")

    summary = build_from_material_folder(root, output_dir=out, render=False)

    assert summary["output_dir"] == str(out)
    assert (out / "a" / "timeline.json").exists()
    assert not (root / "输出" / "a" / "timeline.json").exists()


def test_fallback_track2_assets_match_semantic_folder_name():
    segments = [
        {"text": "然后上传图片，", "track2_asset": None, "match_reason": "none"},
        {"text": "没有对应素材的句子", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼操作/上传图片/a.mp4", "rel_dir": "其他素材/闲鱼操作/上传图片", "kind": "video"},
        {"path": "其他素材/闲鱼操作/发布/b.mp4", "rel_dir": "其他素材/闲鱼操作/发布", "kind": "video"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["track2_asset"]["path"] == "其他素材/闲鱼操作/上传图片/a.mp4"
    assert filled[0]["match_reason"] == "folder:上传图片"
    assert filled[1]["track2_asset"] is None


def test_negative_parallel_selling_points_become_text_sequence():
    terms = _extract_sequence_terms("给大家揭秘一个不用囤货、不用发快递，也不用露脸拍视频的低成本副业。")

    assert terms == ["不用囤货", "不用发快递", "不用露脸拍视频"]


def test_opening_negative_parallel_selling_points_render_before_local_assets():
    segments = [
        {"text": "给大家揭秘一个不用囤货、不用发快递，也不用露脸拍视频的低成本副业。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼收益图/a.png", "rel_dir": "其他素材/闲鱼收益图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["match_reason"] == "generated:text_sequence"
    assert filled[0]["track2_asset"]["sequence_terms"] == ["不用囤货", "不用发快递", "不用露脸拍视频", "低成本副业"]


def test_generic_dunhao_list_becomes_word_by_word_sequence():
    segments = [
        {"text": "这个项目卖的是资料、模板、教程、素材这类产品。", "track2_asset": None, "match_reason": "none"},
    ]

    filled = _fill_fallback_track2_assets(segments, [])

    assert filled[0]["match_reason"] == "generated:text_sequence"
    assert filled[0]["track2_asset"]["sequence_terms"] == ["资料", "模板", "教程", "素材"]


def test_search_enumeration_prefers_word_by_word_big_text_before_peer_case_asset():
    segments = [
        {"text": "打开闲鱼，搜索资料、模板、教程、表格、AI 工具这些词。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼同行案例/a.png", "rel_dir": "其他素材/闲鱼同行案例", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["match_reason"] == "generated:text_sequence"
    assert filled[0]["track2_asset"]["sequence_terms"] == ["资料", "模板", "教程", "表格", "AI 工具"]


def test_peer_case_asset_still_matches_after_search_list_when_talking_about同行():
    segments = [
        {"text": "打开闲鱼，搜索资料、模板、教程、表格、AI 工具这些词。", "track2_asset": None, "match_reason": "none"},
        {"text": "然后找同行卖得好的品，看想要数和浏览量。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼同行案例/a.png", "rel_dir": "其他素材/闲鱼同行案例", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[1]["match_reason"] == "folder:闲鱼同行案例"


def test_outcome_promise_prefers_revenue_asset():
    segments = [
        {"text": "我一个完全不懂电商的人，也能把闲鱼虚拟资料这个流程跑通。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼收益图/a.png", "rel_dir": "其他素材/闲鱼收益图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["match_reason"] == "folder:闲鱼收益图"


def test_opening_result_proof_before_practice_prefers_revenue_asset():
    segments = [
        {"text": "说出来你可能不信，我这个号刚开始就拿到了好的结果。", "track2_asset": None, "match_reason": "none"},
        {"text": "打开闲鱼，搜索虚拟资料相关的词。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼收益图/a.png", "rel_dir": "其他素材/闲鱼收益图", "kind": "image"},
        {"path": "其他素材/闲鱼同行案例/b.png", "rel_dir": "其他素材/闲鱼同行案例", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["match_reason"] == "folder:闲鱼收益图"
    assert filled[1]["match_reason"] == "folder:闲鱼同行案例"


def test_result_word_after_practice_does_not_force_revenue_asset():
    segments = [
        {"text": "打开闲鱼，搜索虚拟资料相关的词。", "track2_asset": None, "match_reason": "none"},
        {"text": "然后看搜索结果，选择卖得好的同行案例。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/闲鱼收益图/a.png", "rel_dir": "其他素材/闲鱼收益图", "kind": "image"},
        {"path": "其他素材/闲鱼同行案例/b.png", "rel_dir": "其他素材/闲鱼同行案例", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[1]["match_reason"] == "folder:闲鱼同行案例"


def test_giveaway_material_only_matches_closing_cta():
    segments = [
        {"text": "这个项目做的是闲鱼虚拟资料，", "start": 1.0, "end": 2.0, "track2_asset": None, "match_reason": "none"},
        {"text": "我整理好了选品库和运营教程，想要的评论区打闲鱼，我发你。", "start": 44.0, "end": 48.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=50.0)

    assert filled[0]["track2_asset"] is None
    assert filled[1]["track2_asset"]["path"] == "其他素材/赠送资料图/a.png"


def test_giveaway_material_does_not_match_early_cta():
    segments = [
        {"text": "我整理好了选品库和运营教程，想要的评论区打闲鱼，我发你。", "start": 6.0, "end": 9.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=50.0)

    assert filled[0]["track2_asset"] is None


def test_giveaway_material_does_not_match_middle_segment_that_only_ends_near_closing():
    segments = [
        {"text": "我整理好了选品库和运营教程，想要的评论区打闲鱼，我发你。", "start": 30.0, "end": 43.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=50.0)

    assert filled[0]["track2_asset"] is None


def test_giveaway_material_requires_known_timing():
    segments = [
        {"text": "我整理好了选品库和运营教程，想要的评论区打闲鱼，我发你。", "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets)

    assert filled[0]["track2_asset"] is None


def test_giveaway_material_matches_closing_cta_after_material_context():
    segments = [
        {"text": "我整理好了选品库、爆品库和运营教程。", "start": 42.0, "end": 44.0, "track2_asset": None, "match_reason": "none"},
        {"text": "想要的先一键三连，然后评论区打“闲鱼”，我发你。", "start": 44.0, "end": 48.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=50.0)

    assert filled[0]["track2_asset"]["path"] == "其他素材/赠送资料图/a.png"
    assert filled[1]["track2_asset"] is None


def test_material_list_segment_prefers_multi_asset_sequence_when_multiple_specific_assets_exist():
    segments = [
        {
            "text": "我整理好了选品库、爆品库、知识库和违规词库。",
            "start": 42.0,
            "end": 47.6,
            "track2_asset": None,
            "match_reason": "none",
        },
    ]
    assets = [
        {"path": "其他素材/赠送资料图/爆品表.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "爆品表"]},
        {"path": "其他素材/赠送资料图/违规词.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "违规词"]},
        {"path": "其他素材/赠送资料图/闲鱼卖家知识库.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "闲鱼卖家知识库"]},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=50.0)

    assert filled[0]["match_reason"] == "generated:semantic_sequence"
    assert filled[0]["track2_asset"]["kind"] == "semantic_asset_sequence"
    assert [Path(item["path"]).stem for item in filled[0]["track2_asset"]["sequence_assets"]] == ["爆品表", "闲鱼卖家知识库", "违规词"]


def test_closing_giveaway_long_list_uses_two_or_three_material_images_before_cta():
    segments = [
        {
            "text": "我整理好了完整的选品库、爆品库、自动发货软件、运营教程、知识库、违规词库和 7 天出单教程。",
            "start": 95.0,
            "end": 105.0,
            "track2_asset": None,
            "match_reason": "none",
        },
        {"text": "想要的先一键三连，", "start": 105.0, "end": 107.0, "track2_asset": None, "match_reason": "none"},
        {"text": "然后在评论区打“教程”。", "start": 107.0, "end": 110.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/爆品表.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "爆品表"]},
        {"path": "其他素材/赠送资料图/违规词.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "违规词"]},
        {"path": "其他素材/赠送资料图/闲鱼卖家知识库.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "闲鱼卖家知识库"]},
        {"path": "其他素材/赠送资料图/闲鱼虚拟操作教程.png", "rel_dir": "其他素材/赠送资料图", "kind": "image", "tags": ["其他素材", "赠送资料图", "操作教程"]},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=110.0)

    assert filled[0]["match_reason"] == "generated:semantic_sequence"
    assert filled[0]["track2_asset"]["kind"] == "semantic_asset_sequence"
    assert 2 <= len(filled[0]["track2_asset"]["sequence_assets"]) <= 3


def test_giveaway_context_survives_short_cta_split():
    segments = [
        {"text": "我已经把 7 天出单教程、选品库和违规词库整理好了。", "start": 48.0, "end": 53.0, "track2_asset": None, "match_reason": "none"},
        {"text": "想跟着流程做的，", "start": 53.0, "end": 55.0, "track2_asset": None, "match_reason": "none"},
        {"text": "一键三连，", "start": 55.0, "end": 56.0, "track2_asset": None, "match_reason": "none"},
        {"text": "评论区打“7天”。", "start": 56.0, "end": 58.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {"path": "其他素材/赠送资料图/a.png", "rel_dir": "其他素材/赠送资料图", "kind": "image"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=58.0)

    assert [bool(item["track2_asset"]) for item in filled] == [True, False, False, False]


def test_fallback_uses_embedded_big_text_sequences_not_generated_cards(tmp_path: Path):
    segments = [
        {"text": "就有这么多，", "track2_asset": None, "match_reason": "none"},
        {"text": "给大家看一下这段时间做的情况，", "track2_asset": None, "match_reason": "none"},
        {"text": "最重要的是没有什么成本，", "track2_asset": None, "match_reason": "none"},
    ]
    assets: list[dict] = []

    filled = _fill_fallback_track2_assets(segments, assets, tmp_path, "demo")

    assert filled[0]["match_reason"] == "generated:text_sequence"
    assert filled[0]["track2_asset"]["kind"] == "generated_text_sequence"
    assert filled[0]["track2_asset"]["sequence_terms"] == ["收益增长"]
    assert filled[1]["track2_asset"] is None
    assert filled[2]["match_reason"] == "generated:text_sequence"
    assert filled[2]["track2_asset"]["sequence_terms"] == ["低成本"]
    assert not list((tmp_path / "自动素材").glob("**/*.png"))


def test_operation_assets_only_match_explicit_operation_steps(tmp_path: Path):
    assets = [
        {"path": "其他素材/闲鱼操作/改价格/a.mp4", "rel_dir": "其他素材/闲鱼操作/改价格", "kind": "video"},
        {"path": "其他素材/闲鱼操作/上文案/b.mp4", "rel_dir": "其他素材/闲鱼操作/上文案", "kind": "video"},
        {"path": "其他素材/闲鱼操作/发货方式/c.mp4", "rel_dir": "其他素材/闲鱼操作/发货方式", "kind": "video"},
    ]
    segments = [
        {"text": "所以不要去打价格战，", "track2_asset": None, "match_reason": "none"},
        {"text": "我就是靠这个复制粘贴，", "track2_asset": None, "match_reason": "none"},
        {"text": "还有自动发货，", "track2_asset": None, "match_reason": "none"},
        {"text": "如果你还不清楚怎么操作，", "track2_asset": None, "match_reason": "none"},
        {"text": "然后把文案粘贴过来，", "track2_asset": None, "match_reason": "none"},
        {"text": "一般我会设置成1.99到5.99，", "track2_asset": None, "match_reason": "none"},
    ]

    filled = _fill_fallback_track2_assets(segments, assets, tmp_path, "demo")

    assert filled[0]["track2_asset"] is None
    assert filled[1]["track2_asset"] is None
    assert filled[2]["track2_asset"] is None
    assert filled[3]["track2_asset"] is None
    assert filled[4]["match_reason"] == "folder:上文案"
    assert filled[5]["match_reason"] == "folder:改价格"
    assert not (tmp_path / "自动素材" / "demo" / "订单爆发.png").exists()
    assert not (tmp_path / "自动素材" / "demo" / "复制粘贴.png").exists()


def test_sound_effect_events_focus_on_revenue_and_keyword_sequence():
    assets = [
        {"path": "音效/钱响.mp3", "rel_dir": "音效", "kind": "audio"},
        {"path": "音效/唰.mp3", "rel_dir": "音效", "kind": "audio"},
    ]
    segments = [
        {"text": "就有这么多，", "start": 1.0, "end": 2.0, "track2_asset": {}},
        {"text": "打开闲鱼，", "start": 3.0, "end": 4.0, "track2_asset": {"path": "其他素材/打开闲鱼/a.mp4"}},
        {"text": "最重要的是没有什么成本，", "start": 5.0, "end": 6.8, "track2_asset": {"tags": ["低成本"]}},
    ]

    events = _sound_effect_events(segments, assets)

    assert events == []


def test_bgm_audio_rotates_per_video():
    assets = [{"path": "bgm/a.mp3"}, {"path": "bgm/b.mp3"}, {"path": "bgm/c.mp3"}]

    assert [_choose_bgm_audio(assets, index) for index in range(4)] == [
        "bgm/a.mp3",
        "bgm/b.mp3",
        "bgm/c.mp3",
        "bgm/a.mp3",
    ]


def test_generated_object_asset_matches_alias():
    segments = [
        {"text": "桌上放了一瓶矿泉水，", "start": 1.0, "end": 2.0, "track2_asset": None, "match_reason": "none"},
    ]
    assets = [
        {
            "path": "自动素材/demo/物体名词/矿泉水.png",
            "rel_dir": "自动素材/demo/物体名词/矿泉水",
            "kind": "image",
            "generated_object": "矿泉水",
            "match_terms": ["矿泉水"],
            "tags": ["矿泉水", "物体名词"],
        },
    ]

    filled = _fill_fallback_track2_assets(segments, assets, total_duration=10.0)

    assert filled[0]["track2_asset"]["generated_object"] == "矿泉水"


def test_object_image_selection_ignores_abstract_nouns():
    blocks = [
        {"text": "把虚拟资料和网盘链接发给客户，不要用图片表现。"},
        {"text": "PPT模板这种抽象交付也应该用大字。"},
        {"text": "桌上出现一瓶矿泉水，旁边停着极氪9X。"},
    ]

    selected = _select_object_specs(blocks, max_assets=5)
    keywords = [item["keyword"] for item in selected]

    assert "矿泉水" in keywords
    assert "汽车" in keywords
    assert "资料包" not in keywords
    assert "网盘链接" not in keywords
    assert "PPT模板" not in keywords


def test_object_image_selection_ignores_incidental_phone_mentions():
    blocks = [
        {"text": "如果你白天要上班，没有时间一直盯着手机，也可以看看闲鱼虚拟资料。"},
        {"text": "它最大的好处之一，就是可以配自动发货。"},
    ]

    selected = _select_object_specs(blocks, max_assets=5)
    keywords = [item["keyword"] for item in selected]

    assert "手机" not in keywords


def test_generated_object_overlays_are_limited():
    segments = [
        {"text": "a", "track2_asset": {"path": "a.png", "generated_object": "资料包"}, "match_reason": "folder:资料包"},
        {"text": "b", "track2_asset": {"path": "b.png", "generated_object": "订单"}, "match_reason": "folder:订单"},
        {"text": "c", "track2_asset": {"path": "c.png", "generated_object": "手机"}, "match_reason": "folder:手机"},
    ]

    limited = svf.batch._limit_generated_object_overlays(segments, max_count=2)

    assert [bool(item["track2_asset"]) for item in limited] == [True, True, False]


def test_tts_voice_fallback_retries_next_voice(monkeypatch, tmp_path: Path):
    calls = []

    def fake_synthesize(provider, text, output_path, **kwargs):
        calls.append(kwargs["voice"])
        if kwargs["voice"] == "bad":
            raise RuntimeError("3031 Init Engine Instance failed")
        Path(output_path).write_bytes(b"ok")

    monkeypatch.setattr(svf.batch, "synthesize_tts", fake_synthesize)

    voice = svf.batch._synthesize_tts_with_voice_fallback(
        "volcengine",
        "text",
        tmp_path / "a.mp3",
        {"api_key": "x", "voice": "bad", "model": "appid", "endpoint": ""},
        ["bad", "good"],
        "bad",
    )

    assert voice == "good"
    assert calls == ["bad", "good"]


def test_batch_records_tts_config_without_storing_api_key(tmp_path: Path, monkeypatch):
    root = tmp_path / "materials"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("第一条文案", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")

    monkeypatch.setattr(svf.batch, "synthesize_tts", lambda *args, **kwargs: Path(args[2]).write_bytes(b"fake"))
    monkeypatch.setattr(svf.batch, "get_media_duration", lambda path: 5.0)

    summary = build_from_material_folder(
        root,
        render=False,
        tts_provider="minimax",
        tts_model="speech-02-hd",
        tts_api_key="secret-key",
    )

    timeline = json.loads((root / "输出" / "a" / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["tts"] == {
        "provider": "minimax",
        "model": "speech-02-hd",
        "endpoint": "",
        "api_key_provided": True,
    }
    assert "secret-key" not in (root / "输出" / "a" / "timeline.json").read_text(encoding="utf-8")
    assert summary["tts_api_key_provided"] is True


def test_batch_generates_tts_before_timeline_when_provider_is_set(tmp_path: Path, monkeypatch):
    root = tmp_path / "materials"
    (root / "文案").mkdir(parents=True)
    (root / "轨道1" / "女1").mkdir(parents=True)
    (root / "文案" / "a.txt").write_text("第一条文案。[[tag]]", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(root / "轨道1" / "女1" / "a.png")

    def fake_synthesize(provider, text, output_path, api_key, voice, model="", endpoint="", speed_ratio=None):
        assert provider == "minimax"
        assert text == "第一条文案。"
        audio_path = Path(output_path)
        audio_path.write_bytes(b"fake")
        audio_path.with_suffix(".timestamps.json").write_text(
            json.dumps(
                {
                    "frontend": {
                        "words": [
                            {"word": "第", "start_time": 0, "end_time": 200},
                            {"word": "一", "start_time": 200, "end_time": 400},
                            {"word": "条。", "start_time": 400, "end_time": 800},
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return audio_path

    monkeypatch.setattr(svf.batch, "synthesize_tts", fake_synthesize)
    monkeypatch.setattr(svf.batch, "get_media_duration", lambda path: 9.5)

    summary = build_from_material_folder(
        root,
        render=False,
        voices=["female-shaonv"],
        tts_provider="minimax",
        tts_model="speech-2.8-hd",
        tts_api_key="secret-key",
    )

    timeline = json.loads((root / "输出" / "a" / "timeline.json").read_text(encoding="utf-8"))
    assert timeline["duration"] == 9.5
    assert timeline["voice_audio"].endswith("配音\\a.mp3") or timeline["voice_audio"].endswith("配音/a.mp3")
    assert timeline["segments"][0]["text"] == "第一条文案。"
    assert timeline["segments"][0]["start"] == 0.0
    assert timeline["segments"][0]["end"] == 0.8
    assert timeline["project"]["resolution"] == [1920, 1080]
    assert summary["results"][0]["voice_audio"]


def test_batch_uses_per_block_audio_timeline_when_tts_has_no_word_timestamps(tmp_path: Path, monkeypatch):
    root = tmp_path / "materials"
    script_dir = root / svf.batch.SCRIPT_DIR_NAME
    track1_dir = root / svf.batch.TRACK1_DIR_NAME / "group"
    script_dir.mkdir(parents=True)
    track1_dir.mkdir(parents=True)
    (script_dir / "a.txt").write_text("第一句。\n第二句。", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(track1_dir / "a.png")
    durations = {"第一句。": 1.25, "第二句。": 2.5}

    def fake_synthesize(provider, text, output_path, **kwargs):
        Path(output_path).write_bytes(text.encode("utf-8"))

    monkeypatch.setattr(svf.batch, "synthesize_tts", fake_synthesize)
    monkeypatch.setattr(svf.batch, "get_media_duration", lambda path: durations.get(Path(path).read_text(encoding="utf-8"), 3.75))
    monkeypatch.setattr(svf.batch, "_concat_tts_block_audio", lambda paths, output_path, temp_dir: Path(output_path).write_bytes(b"joined"))

    build_from_material_folder(
        root,
        render=False,
        tts_provider="volcengine",
        tts_model="appid",
        tts_api_key="secret-key",
        voices=["voice_a"],
    )

    timeline = json.loads((root / svf.batch.OUTPUT_DIR_NAME / "a" / "timeline.json").read_text(encoding="utf-8"))
    assert [(s["text"], s["start"], s["end"]) for s in timeline["segments"]] == [
        ("第一句。", 0.0, 1.25),
        ("第二句。", 1.25, 3.75),
    ]


def test_batch_passes_tts_speed_ratio_and_records_it(tmp_path: Path, monkeypatch):
    root = tmp_path / "materials"
    script_dir = root / svf.batch.SCRIPT_DIR_NAME
    track1_dir = root / svf.batch.TRACK1_DIR_NAME / "group"
    script_dir.mkdir(parents=True)
    track1_dir.mkdir(parents=True)
    (script_dir / "a.txt").write_text("line one", encoding="utf-8")
    Image.new("RGB", (720, 1280), "red").save(track1_dir / "a.png")
    captured = {}

    def fake_synthesize(*args, **kwargs):
        captured["speed_ratio"] = kwargs.get("speed_ratio")
        Path(args[2]).write_bytes(b"fake")

    monkeypatch.setattr(svf.batch, "synthesize_tts", fake_synthesize)
    monkeypatch.setattr(svf.batch, "get_media_duration", lambda path: 5.0)

    build_from_material_folder(
        root,
        render=False,
        tts_provider="volcengine",
        tts_model="appid",
        tts_api_key="secret-key",
        voices=["voice_a"],
        tts_speed_ratio=1.05,
    )

    timeline = json.loads((root / svf.batch.OUTPUT_DIR_NAME / "a" / "timeline.json").read_text(encoding="utf-8"))
    assert captured["speed_ratio"] == 1.05
    assert timeline["tts"]["speed_ratio"] == 1.05
