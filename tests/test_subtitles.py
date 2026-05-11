from pathlib import Path

from svf.subtitles import distribute_segments, segments_from_text_and_timestamp_words, segments_from_timestamp_words, write_srt


def test_distribute_segments_maps_script_blocks_to_audio_duration():
    blocks = [
        {"text": "第一句", "manual_tags": []},
        {"text": "第二句话更长", "manual_tags": ["asset"]},
    ]

    segments = distribute_segments(blocks, total_duration=6.0)

    assert segments[0]["start"] == 0.0
    assert segments[-1]["end"] == 6.0
    assert segments[1]["manual_tags"] == ["asset"]
    assert segments[0]["end"] < segments[1]["end"]


def test_write_srt_formats_segments(tmp_path: Path):
    segments = [
        {"start": 0.0, "end": 1.5, "text": "第一句"},
        {"start": 1.5, "end": 3.0, "text": "第二句"},
    ]
    out = tmp_path / "subtitles.srt"

    write_srt(segments, out)

    assert out.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,500\n第一句\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\n第二句\n\n"
    )


def test_segments_from_timestamp_words_splits_on_punctuation():
    words = [
        {"word": "你", "start_time": 0, "end_time": 100},
        {"word": "好。", "start_time": 100, "end_time": 300},
        {"word": "再", "start_time": 500, "end_time": 700},
        {"word": "见", "start_time": 700, "end_time": 900},
    ]

    segments = segments_from_timestamp_words(words)

    assert segments == [
        {"text": "你好。", "manual_tags": [], "start": 0.0, "end": 0.3},
        {"text": "再见", "manual_tags": [], "start": 0.5, "end": 0.9},
    ]


def test_segments_from_text_and_timestamp_words_keeps_original_sentences():
    texts = ["像这一周的时间，我通过剪辑接单赚了有 5000 多，给大家看一下这段时间做的单子收益情况，都是真实有效的。"]
    words = [
        {"word": "像", "start_time": 0, "end_time": 100},
        {"word": "这", "start_time": 100, "end_time": 200},
        {"word": "一", "start_time": 200, "end_time": 300},
        {"word": "周", "start_time": 300, "end_time": 400},
        {"word": "的", "start_time": 400, "end_time": 500},
        {"word": "时", "start_time": 500, "end_time": 600},
        {"word": "间，", "start_time": 600, "end_time": 700},
        {"word": "我", "start_time": 700, "end_time": 800},
        {"word": "通", "start_time": 800, "end_time": 900},
        {"word": "过", "start_time": 900, "end_time": 1000},
        {"word": "剪", "start_time": 1000, "end_time": 1100},
        {"word": "辑", "start_time": 1100, "end_time": 1200},
        {"word": "接", "start_time": 1200, "end_time": 1300},
        {"word": "单", "start_time": 1300, "end_time": 1400},
        {"word": "赚", "start_time": 1400, "end_time": 1500},
        {"word": "了", "start_time": 1500, "end_time": 1600},
        {"word": "有", "start_time": 1600, "end_time": 1700},
        {"word": "五", "start_time": 1700, "end_time": 1800},
        {"word": "千", "start_time": 1800, "end_time": 1900},
        {"word": "多，", "start_time": 1900, "end_time": 2000},
        {"word": "给", "start_time": 2000, "end_time": 2100},
        {"word": "大", "start_time": 2100, "end_time": 2200},
        {"word": "家", "start_time": 2200, "end_time": 2300},
        {"word": "看", "start_time": 2300, "end_time": 2400},
        {"word": "一", "start_time": 2400, "end_time": 2500},
        {"word": "下", "start_time": 2500, "end_time": 2600},
        {"word": "这", "start_time": 2600, "end_time": 2700},
        {"word": "段", "start_time": 2700, "end_time": 2800},
        {"word": "时", "start_time": 2800, "end_time": 2900},
        {"word": "间", "start_time": 2900, "end_time": 3000},
        {"word": "做", "start_time": 3000, "end_time": 3100},
        {"word": "的", "start_time": 3100, "end_time": 3200},
        {"word": "单", "start_time": 3200, "end_time": 3300},
        {"word": "子", "start_time": 3300, "end_time": 3400},
        {"word": "收", "start_time": 3400, "end_time": 3500},
        {"word": "益", "start_time": 3500, "end_time": 3600},
        {"word": "情", "start_time": 3600, "end_time": 3700},
        {"word": "况，", "start_time": 3700, "end_time": 3800},
        {"word": "都", "start_time": 3800, "end_time": 3900},
        {"word": "是", "start_time": 3900, "end_time": 4000},
        {"word": "真", "start_time": 4000, "end_time": 4100},
        {"word": "实", "start_time": 4100, "end_time": 4200},
        {"word": "有", "start_time": 4200, "end_time": 4300},
        {"word": "效", "start_time": 4300, "end_time": 4400},
        {"word": "的。", "start_time": 4400, "end_time": 4500},
    ]

    segments = segments_from_text_and_timestamp_words(texts, words)

    assert segments == [{"text": texts[0], "manual_tags": [], "start": 0.0, "end": 4.5}]
