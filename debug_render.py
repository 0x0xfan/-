from svf.renderer import render_video
from pathlib import Path
import json
import os

with open('demo_project/output/timeline.json', encoding='utf-8') as f:
    timeline = json.load(f)

print("DEBUG: About to render video")
print("DEBUG: Voice:", timeline.get('voice_audio'))
print("DEBUG: BGM:", timeline.get('bgm_audio'))
print("DEBUG: Base clips:", len(timeline.get('base_clips', [])))
print("DEBUG: Segments:", len(timeline.get('segments', [])))

output_path = 'demo_project/output/debug_render.mp4'
render_video(timeline, output_path)

print("DEBUG: render_video() completed")
print("DEBUG: Output exists:", os.path.exists(output_path))
if os.path.exists(output_path):
    print("DEBUG: Output size:", os.path.getsize(output_path) / 1024, "KB")
