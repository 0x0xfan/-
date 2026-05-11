import json
from pathlib import Path
from types import SimpleNamespace

import scripts.generate_infoflow_video_cached as cached


def test_prepare_render_jobs_sets_chunk_seconds_and_skips_existing(tmp_path: Path, monkeypatch):
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    output = final_dir / "a.mp4"
    output.write_bytes(b"mp4")
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {
                "output_video": "",
                "base_clips": [{"asset": "remote.mp4"}],
                "segments": [
                    {
                        "track2_asset": {
                            "path": "overlay.png",
                            "sequence_assets": [{"path": "nested.png"}],
                            "revenue_paths": ["revenue.png"],
                        }
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cached, "_is_readable_mp4", lambda path: path == output)

    jobs = cached._prepare_render_jobs(
        {"results": [{"timeline": str(timeline_path), "output": str(output)}]},
        {
            "remote.mp4": "cached-remote.mp4",
            "overlay.png": "cached-overlay.png",
            "nested.png": "cached-nested.png",
            "revenue.png": "cached-revenue.png",
        },
        final_dir,
        render_chunk_seconds=12.0,
        skip_existing=True,
    )

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert jobs == [{"timeline_path": str(timeline_path), "output_path": str(output), "skip": True}]
    assert timeline["output_video"] == str(output)
    assert timeline["render_chunk_seconds"] == 12.0
    assert timeline["base_clips"][0]["asset"] == "cached-remote.mp4"
    assert timeline["segments"][0]["track2_asset"]["path"] == "cached-overlay.png"
    assert timeline["segments"][0]["track2_asset"]["sequence_assets"][0]["path"] == "cached-nested.png"
    assert timeline["segments"][0]["track2_asset"]["revenue_paths"] == ["cached-revenue.png"]


def test_render_timelines_skips_existing_and_renders_pending(tmp_path: Path, monkeypatch):
    calls = []
    existing = tmp_path / "existing.mp4"
    pending = tmp_path / "pending.mp4"
    timeline = tmp_path / "timeline.json"
    timeline.write_text("{}", encoding="utf-8")

    def fake_render(timeline_path: Path, output_path: Path):
        calls.append((timeline_path, output_path))
        output_path.write_bytes(b"ok")

    monkeypatch.setattr(cached, "_render_one_timeline_in_subprocess", fake_render)

    rendered, skipped = cached._render_timelines(
        [
            {"timeline_path": str(timeline), "output_path": str(existing), "skip": True},
            {"timeline_path": str(timeline), "output_path": str(pending), "skip": False},
        ],
        max_workers=2,
    )

    assert rendered == [str(pending)]
    assert skipped == [str(existing)]
    assert calls == [(timeline, pending)]


def test_material_root_for_missing_outputs_filters_existing_scripts(tmp_path: Path, monkeypatch):
    material_root = tmp_path / "materials"
    script_dir = material_root / cached.SCRIPT_DIR_NAME
    final_dir = tmp_path / "final"
    script_dir.mkdir(parents=True)
    final_dir.mkdir()
    (script_dir / "a.txt").write_text("a", encoding="utf-8")
    (script_dir / "b.txt").write_text("b", encoding="utf-8")
    monkeypatch.setattr(cached, "_is_readable_mp4", lambda path: path.name == "a.mp4")

    build_root, skipped = cached._material_root_for_missing_outputs(material_root, final_dir, skip_existing=True)

    assert build_root != material_root
    assert skipped == [str(final_dir / "a.mp4")]
    copied = sorted(path.name for path in (build_root / cached.SCRIPT_DIR_NAME).glob("*.txt"))
    assert copied == ["b.txt"]


def test_all_existing_fast_path_does_not_resolve_tts_or_build(tmp_path: Path, monkeypatch):
    material_root = tmp_path / "materials"
    script_dir = material_root / cached.SCRIPT_DIR_NAME
    final_dir = tmp_path / "final"
    work_output = tmp_path / "work"
    script_dir.mkdir(parents=True)
    final_dir.mkdir()
    (script_dir / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(cached, "_is_readable_mp4", lambda path: True)

    def fail(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(cached, "_resolve_tts_config", fail)
    monkeypatch.setattr(cached, "build_from_material_folder", fail)
    monkeypatch.setattr(
        cached,
        "argparse",
        SimpleNamespace(
            ArgumentParser=lambda **kwargs: _Parser(
                material_root=str(material_root),
                asset_root="",
                work_output=str(work_output),
                final_dir=str(final_dir),
                cache_dir="",
                tts_config="tts.config.yaml",
                provider=None,
                appid=None,
                token=None,
                voice=None,
                voice_strategy=None,
                speed_ratio=None,
                endpoint=None,
                orientation="landscape",
                subtitle_offset=0.0,
                seed=42,
                generate_object_images=False,
                render_workers=2,
                render_chunk_seconds=10.0,
                skip_existing=True,
            )
        ),
    )

    cached.main()

    status = json.loads((work_output / "render_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "done"
    assert status["rendered_mp4"] == []
    assert status["skipped_mp4"] == [str(final_dir / "a.mp4")]


class _Parser:
    def __init__(self, **values):
        self.values = values

    def add_argument(self, *args, **kwargs):
        return None

    def parse_args(self):
        return SimpleNamespace(**self.values)
