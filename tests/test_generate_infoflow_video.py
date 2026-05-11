import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.generate_infoflow_video as generate
import scripts.generate_infoflow_video_cached as cached


def test_generate_entrypoint_requires_tts_provider():
    with pytest.raises(SystemExit, match="必须先对接好 TTS"):
        generate._validate_tts_config(
            {"provider": "", "api_key": "", "model": "", "voices": []},
            Path("tts.config.yaml"),
        )


def test_cached_entrypoint_requires_tts_provider():
    with pytest.raises(SystemExit, match="必须先对接好 TTS"):
        cached._validate_tts_config(
            {"provider": "", "api_key": "", "model": "", "voices": []},
            Path("tts.config.yaml"),
        )


def test_preflight_tts_stops_when_synthesis_fails(monkeypatch: pytest.MonkeyPatch):
    def fail(*args, **kwargs):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(generate, "synthesize_tts", fail)

    with pytest.raises(SystemExit, match="TTS 预检失败"):
        generate._preflight_tts(
            {
                "provider": "volcengine",
                "api_key": "token",
                "model": "appid",
                "voices": ["voice"],
                "endpoint": "",
                "speed_ratio": None,
            }
        )


def test_preflight_tts_requires_output_audio(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(generate, "synthesize_tts", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit, match="没有生成有效音频文件"):
        generate._preflight_tts(
            {
                "provider": "minimax",
                "api_key": "key",
                "model": "speech",
                "voices": ["voice"],
                "endpoint": "",
                "speed_ratio": None,
            }
        )


def test_provider_override_uses_matching_provider_config(tmp_path: Path):
    config_path = tmp_path / "tts.config.yaml"
    config_path.write_text(
        """
active: volcengine
voice_strategy: round_robin
providers:
  volcengine:
    appid: volc-app
    token: volc-token
    voices: [volc-voice]
  minimax:
    api_key: minimax-key
    model: speech-2.8-hd
    voices: [female-shaonv]
""",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        tts_config=str(config_path),
        provider="minimax",
        appid=None,
        token=None,
        voice=None,
        voice_strategy=None,
        speed_ratio=None,
        endpoint=None,
    )

    tts = generate._resolve_tts_config(args)

    assert tts["provider"] == "minimax"
    assert tts["api_key"] == "minimax-key"
    assert tts["model"] == "speech-2.8-hd"
    assert tts["voices"] == ["female-shaonv"]


def test_resolve_volcengine_v3_config_fields(tmp_path: Path):
    config_path = tmp_path / "tts.config.yaml"
    config_path.write_text(
        """
active: volcengine
voice_strategy: random
providers:
  volcengine:
    appid: volc-app
    token: volc-token
    api_version: v3
    endpoint: https://openspeech.bytedance.com/api/v3/tts/unidirectional
    resource_id: volc.service_type.10029
    sample_rate: 24000
    speed_ratio: 1.05
    voices: [saturn_zh_female_qingyingduoduo_cs_tob]
""",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        tts_config=str(config_path),
        provider=None,
        appid=None,
        token=None,
        voice=None,
        voice_strategy=None,
        speed_ratio=None,
        endpoint=None,
    )

    tts = generate._resolve_tts_config(args)

    assert tts["provider"] == "volcengine"
    assert tts["api_version"] == "v3"
    assert tts["resource_id"] == "volc.service_type.10029"
    assert tts["sample_rate"] == 24000
    assert tts["speed_ratio"] == pytest.approx(1.05)


def test_build_top_five_videos_only_exports_flat_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    material_root = tmp_path / "materials"
    script_dir = material_root / "文案"
    script_dir.mkdir(parents=True)
    for i in range(1, 8):
        (script_dir / f"{i:02d}.txt").write_text(f"文案{i}", encoding="utf-8")
    output_dir = tmp_path / "exports"

    copied_scripts: list[str] = []

    def fake_build_from_material_folder(build_root, **kwargs):
        copied_scripts.extend(sorted(path.name for path in (Path(build_root) / "文案").glob("*.txt")))
        work_output = Path(kwargs["output_dir"])
        assert kwargs["asset_root"] == material_root
        results = []
        for name in ["01", "02", "03", "04", "05"]:
            video_dir = work_output / name
            video_dir.mkdir(parents=True, exist_ok=True)
            (video_dir / "timeline.json").write_text("{}", encoding="utf-8")
            (video_dir / "subtitles.srt").write_text("1", encoding="utf-8")
            mp4 = video_dir / f"{name}.mp4"
            mp4.write_bytes(b"mp4")
            results.append(
                {
                    "script": str(Path(build_root) / "文案" / f"{name}.txt"),
                    "output": str(mp4),
                    "track1_group": "女1",
                }
            )
        return {"results": results}

    monkeypatch.setattr(generate, "build_from_material_folder", fake_build_from_material_folder)

    args = SimpleNamespace(
        material_root=str(material_root),
        output_dir=str(output_dir),
        orientation="landscape",
        subtitle_offset=0.0,
        no_render=False,
    )
    tts = {
        "voices": ["voice"],
        "voice_strategy": "random",
        "provider": "volcengine",
        "model": "appid",
        "endpoint": "",
        "api_key": "token",
        "speed_ratio": 1.05,
        "api_version": "",
        "resource_id": "",
        "sample_rate": 24000,
    }

    summary = generate._build_top_five_videos_only(args, tts)

    assert copied_scripts == ["01.txt", "02.txt", "03.txt", "04.txt", "05.txt"]
    assert summary["count"] == 5
    assert summary["output_dir"] == str(output_dir)
    assert sorted(path.name for path in output_dir.glob("*.mp4")) == ["01.mp4", "02.mp4", "03.mp4", "04.mp4", "05.mp4"]
    assert not list(output_dir.glob("*.json"))
    assert not list(output_dir.glob("*.srt"))
    assert not any(path.is_dir() for path in output_dir.iterdir())
