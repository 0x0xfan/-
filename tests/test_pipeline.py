from pathlib import Path

from svf.sample_project import create_sample_project
from svf.pipeline import build_project


def test_build_project_generates_timeline_and_srt_without_render(tmp_path: Path):
    project = tmp_path / "demo"
    create_sample_project(project)

    timeline = build_project(project / "config.yaml", render=False)

    assert (project / "output" / "timeline.json").exists()
    assert (project / "output" / "subtitles.srt").exists()
    assert timeline["duration"] == 8.0
    assert len(timeline["segments"]) == 5
    assert any(segment["track2_asset"] for segment in timeline["segments"])
