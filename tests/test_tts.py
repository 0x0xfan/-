from pathlib import Path

import pytest

from svf import tts as tts_module
from svf.tts import synthesize_tts, synthesize_volcengine, timestamp_sidecar_path


def test_synthesize_tts_rejects_unknown_provider(tmp_path: Path):
    with pytest.raises(ValueError, match="暂不支持"):
        synthesize_tts("unknown", "hello", tmp_path / "a.mp3", "key", "voice")


def test_volcengine_requires_appid(tmp_path: Path):
    with pytest.raises(ValueError, match="AppID"):
        synthesize_volcengine("hello", tmp_path / "a.mp3", "token", "voice", "")


def test_timestamp_sidecar_path_uses_audio_stem(tmp_path: Path):
    assert timestamp_sidecar_path(tmp_path / "a.mp3") == tmp_path / "a.timestamps.json"


def test_synthesize_tts_supports_windows_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    expected = tmp_path / "a.mp3"

    def fake_synthesize_windows(text: str, output_path: Path, voice: str = "", speed_ratio: float | None = None):
        assert text == "hello"
        assert output_path == expected
        assert voice == "Microsoft Huihui Desktop"
        assert speed_ratio == pytest.approx(1.05)
        return output_path

    monkeypatch.setattr(tts_module, "synthesize_windows", fake_synthesize_windows)
    result = synthesize_tts("windows", "hello", expected, "", "Microsoft Huihui Desktop", speed_ratio=1.05)
    assert result == expected


def test_synthesize_tts_supports_edge_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    expected = tmp_path / "edge.mp3"

    def fake_synthesize_edge(text: str, output_path: Path, voice: str = "", speed_ratio: float | None = None):
        assert text == "hello"
        assert output_path == expected
        assert voice == "zh-CN-XiaoxiaoNeural"
        assert speed_ratio == pytest.approx(1.05)
        return output_path

    monkeypatch.setattr(tts_module, "synthesize_edge", fake_synthesize_edge)
    result = synthesize_tts("edge", "hello", expected, "", "zh-CN-XiaoxiaoNeural", speed_ratio=1.05)
    assert result == expected


def test_volcengine_v3_uses_official_headers_and_speaker_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def iter_lines(self, decode_unicode=True):
            yield '{"code":0,"data":"aGk="}'

        def json(self):
            return {"code": 0, "data": "aGk="}

    def fake_post(url, headers, data, stream=False, timeout=0):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr(tts_module.requests, "post", fake_post)

    out = tmp_path / "v3.mp3"
    synthesize_volcengine(
        "hello",
        out,
        "access-key",
        "saturn_zh_female_qingyingduoduo_cs_tob",
        "appid",
        api_version="v3",
        resource_id="volc.service_type.10029",
        speed_ratio=1.05,
    )

    assert captured["url"].endswith("/api/v3/tts/unidirectional")
    assert captured["stream"] is True
    assert captured["headers"]["X-Api-App-Id"] == "appid"
    assert captured["headers"]["X-Api-Access-Key"] == "access-key"
    assert captured["headers"]["X-Api-Resource-Id"] == "volc.service_type.10029"
    assert b'"speaker": "saturn_zh_female_qingyingduoduo_cs_tob"' in captured["data"]
    assert out.read_bytes() == b"hi"
