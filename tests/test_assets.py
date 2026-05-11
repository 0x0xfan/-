from pathlib import Path

from svf.assets import scan_assets


def test_scan_assets_reads_sidecar_metadata(tmp_path: Path):
    topic = tmp_path / "assets" / "track2_topic" / "doubao"
    topic.mkdir(parents=True)
    image = topic / "doubao_home.png"
    image.write_bytes(b"fake")
    metadata = topic / "doubao_home.json"
    metadata.write_text('{"tags": ["豆包", "首页"], "kind": "image", "priority": 8}', encoding="utf-8")

    assets = scan_assets(tmp_path / "assets")

    assert assets[0]["path"].endswith("doubao_home.png")
    assert assets[0]["rel_dir"].endswith("track2_topic\\doubao") or assets[0]["rel_dir"].endswith("track2_topic/doubao")
    assert assets[0]["tags"] == ["豆包", "首页"]
    assert assets[0]["kind"] == "image"
    assert assets[0]["priority"] == 8


def test_scan_assets_infers_tags_without_metadata(tmp_path: Path):
    base = tmp_path / "assets" / "track1_base" / "woman_working"
    base.mkdir(parents=True)
    video = base / "typing_01.mp4"
    video.write_bytes(b"fake")

    assets = scan_assets(tmp_path / "assets")

    assert assets[0]["kind"] == "video"
    assert "track1_base" in assets[0]["tags"]
    assert "woman_working" in assets[0]["tags"]
    assert "typing_01" in assets[0]["tags"]
