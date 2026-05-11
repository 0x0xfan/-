from svf.timeline import parse_script_blocks, match_assets_for_segments, generate_base_clips


def test_parse_script_blocks_supports_manual_asset_tags():
    script = """我最近发现一个副业。\n用豆包写小说。[[doubao]]\n3个月收益出来了。[[income]]"""

    blocks = parse_script_blocks(script)

    assert blocks == [
        {"text": "我最近发现一个副业。", "manual_tags": []},
        {"text": "用豆包写小说。", "manual_tags": ["doubao"]},
        {"text": "3个月收益出来了。", "manual_tags": ["income"]},
    ]


def test_parse_script_blocks_splits_sentences_by_punctuation():
    blocks = parse_script_blocks("第一句，第二句。第三句！第四句")

    assert blocks == [
        {"text": "第一句，", "manual_tags": []},
        {"text": "第二句。", "manual_tags": []},
        {"text": "第三句！", "manual_tags": []},
        {"text": "第四句", "manual_tags": []},
    ]


def test_parse_script_blocks_keeps_decimal_prices_together():
    blocks = parse_script_blocks("一般我会设置成1.99到5.99，分类再设置一下。")

    assert blocks == [
        {"text": "一般我会设置成1.99到5.99，", "manual_tags": []},
        {"text": "分类再设置一下。", "manual_tags": []},
    ]


def test_match_assets_prefers_manual_tags_then_keywords():
    segments = [
        {"start": 0.0, "end": 2.0, "text": "用豆包写小说。", "manual_tags": ["doubao"]},
        {"start": 2.0, "end": 4.0, "text": "收益已经出来了。", "manual_tags": []},
    ]
    assets = [
        {"path": "assets/topic/doubao/a.png", "tags": ["doubao", "AI工具"], "kind": "image"},
        {"path": "assets/topic/income/b.png", "tags": ["收益", "收入"], "kind": "image"},
    ]
    rules = [
        {"event": "income_proof", "keywords": ["收益", "收入"], "asset_tags": ["收益"]},
    ]

    matched = match_assets_for_segments(segments, assets, rules)

    assert matched[0]["track2_asset"]["path"] == "assets/topic/doubao/a.png"
    assert matched[0]["match_reason"] == "manual_tag:doubao"
    assert matched[1]["track2_asset"]["path"] == "assets/topic/income/b.png"
    assert matched[1]["match_reason"] == "rule:income_proof"


def test_generate_base_clips_covers_duration_without_gap():
    clips = generate_base_clips(
        total_duration=10.0,
        base_assets=["a.mp4", "b.mp4"],
        clip_duration=3.0,
        speed=1.0,
    )

    assert clips[0]["start"] == 0.0
    assert clips[-1]["end"] == 10.0
    assert all(clips[i]["end"] == clips[i + 1]["start"] for i in range(len(clips) - 1))
    assert [clip["asset"] for clip in clips] == ["a.mp4", "b.mp4", "a.mp4", "b.mp4"]
