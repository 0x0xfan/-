from __future__ import annotations

import math
import shutil
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CONFIG = """project:
  title: "示例短视频"
  resolution: [720, 1280]
  fps: 24

input:
  script: "input/script.md"
  voice_audio: "input/voice.wav"

assets:
  root: "assets"

output:
  dir: "output"
  filename: "final_video.mp4"

style:
  font: ""
  subtitle_font_size: 42
  subtitle_fill: "white"
  subtitle_stroke: "black"
  subtitle_stroke_width: 3
  bgm_volume: 0.08
  voice_volume: 1.0

rules:
  base_clip_duration: 3.0
  base_speed: 1.0
  events:
    - event: "doubao_tool"
      keywords: ["豆包", "AI", "提示词"]
      asset_tags: ["doubao", "豆包"]
    - event: "income_proof"
      keywords: ["收益", "收入", "赚钱", "收获"]
      asset_tags: ["income", "收益"]
"""

SCRIPT = """# 示例短视频
我最近发现一个适合普通人的副业。
就是用豆包写小说。[[doubao]]
我自己试了三个月，收益已经跑出来了。[[income]]
第一版工具先把基础画面、字幕和素材匹配跑通。
"""


def create_sample_project(project_dir: Path) -> None:
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    (project_dir / "input").mkdir()
    (project_dir / "assets" / "track1_base" / "computer").mkdir(parents=True)
    (project_dir / "assets" / "track2_topic" / "doubao").mkdir(parents=True)
    (project_dir / "assets" / "track2_topic" / "income").mkdir(parents=True)
    (project_dir / "assets" / "bgm").mkdir(parents=True)

    (project_dir / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (project_dir / "input" / "script.md").write_text(SCRIPT, encoding="utf-8")
    _write_silent_wav(project_dir / "input" / "voice.wav", duration=8.0, volume=0.18, frequency=440)
    _write_silent_wav(project_dir / "assets" / "bgm" / "bgm.wav", duration=10.0, volume=0.08, frequency=220)

    _write_image(project_dir / "assets" / "track1_base" / "computer" / "base_01.png", "电脑操作底层素材", (52, 73, 94))
    _write_image(project_dir / "assets" / "track2_topic" / "doubao" / "doubao_home.png", "豆包 / AI 写作", (36, 123, 160))
    _write_image(project_dir / "assets" / "track2_topic" / "income" / "income_proof.png", "收益证明截图", (46, 125, 50))
    (project_dir / "assets" / "track2_topic" / "doubao" / "doubao_home.json").write_text(
        '{"tags": ["doubao", "豆包", "AI"], "kind": "image", "priority": 8}', encoding="utf-8"
    )
    (project_dir / "assets" / "track2_topic" / "income" / "income_proof.json").write_text(
        '{"tags": ["income", "收益", "收入"], "kind": "image", "priority": 8}', encoding="utf-8"
    )


def _write_image(path: Path, text: str, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (720, 1280), color)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((80, 560), text, fill="white", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _write_silent_wav(path: Path, duration: float, volume: float, frequency: float) -> None:
    sample_rate = 44100
    frames = int(sample_rate * duration)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for i in range(frames):
            value = int(32767 * volume * math.sin(2 * math.pi * frequency * i / sample_rate))
            wav.writeframesraw(value.to_bytes(2, byteorder="little", signed=True))
