from __future__ import annotations

import html
import json
import mimetypes
import random
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from svf.batch import build_from_material_folder, inspect_material_folder
from svf.assets import scan_assets
from svf.pipeline import build_project, load_config


DEFAULT_CONFIG = "demo_project/config.yaml"
PICKER_DIR = Path(tempfile.gettempdir()) / "svf_folder_picker"


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start the local visual interface."""
    root = Path.cwd().resolve()
    handler = _make_handler(root)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"可视化界面已启动：{url}")
    print("按 Ctrl+C 关闭。")
    server.serve_forever()


def _make_handler(root: Path):
    class GuiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                query = parse_qs(parsed.query)
                config_path = query.get("config", [DEFAULT_CONFIG])[0]
                self._send_html(_render_home(root, config_path))
                return
            if parsed.path == "/media":
                self._send_media(root, parse_qs(parsed.query).get("path", [""])[0])
                return
            if parsed.path == "/pick-folder":
                current = parse_qs(parsed.query).get("current", [""])[0]
                picker_id = _start_folder_picker(current)
                self._send_json({"picker_id": picker_id})
                return
            if parsed.path == "/pick-folder-result":
                picker_id = parse_qs(parsed.query).get("id", [""])[0]
                self._send_json(_folder_picker_result(picker_id))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            data = self._read_form()
            config_path = data.get("config_path", [DEFAULT_CONFIG])[0].strip() or DEFAULT_CONFIG
            material_root = data.get("material_root", [""])[0].strip()
            output_dir = data.get("output_dir", [""])[0].strip()
            message = ""
            error = ""
            try:
                if parsed.path == "/batch-build":
                    render = data.get("render", ["off"])[0] == "on"
                    seed = random.SystemRandom().randint(1, 2_147_483_647)
                    voices = _parse_lines(data.get("voices", [""])[0])
                    voice_strategy = data.get("voice_strategy", ["round_robin"])[0]
                    tts_provider = data.get("tts_provider", [""])[0]
                    tts_model = data.get("tts_model", [""])[0].strip()
                    tts_endpoint = data.get("tts_endpoint", [""])[0].strip()
                    tts_api_key = data.get("tts_api_key", [""])[0].strip()
                    summary = build_from_material_folder(
                        material_root,
                        output_dir=output_dir,
                        seed=seed,
                        render=render,
                        voices=voices,
                        voice_strategy=voice_strategy,
                        tts_provider=tts_provider,
                        tts_model=tts_model,
                        tts_endpoint=tts_endpoint,
                        tts_api_key=tts_api_key,
                    )
                    message = f"批量生成完成：{summary['count']} 条，输出在 {summary['output_dir']}"
                    self._send_html(_render_home(root, config_path, material_root=material_root, output_dir=output_dir, message=message))
                    return
                if parsed.path == "/save-script":
                    script_text = data.get("script_text", [""])[0]
                    config_file = _resolve_under_root(root, config_path)
                    config = load_config(config_file)
                    script_path = config_file.parent / config["input"]["script"]
                    script_path.write_text(script_text, encoding="utf-8")
                    message = f"文案已保存：{_display_path(script_path, root)}"
                elif parsed.path == "/build":
                    render = data.get("render", ["off"])[0] == "on"
                    seed = int(data.get("seed", ["42"])[0] or 42)
                    timeline = build_project(_resolve_under_root(root, config_path), seed=seed, render=render)
                    if render:
                        message = f"视频已生成：{timeline['output_video']}"
                    else:
                        message = "timeline.json 和 subtitles.srt 已生成。"
                elif parsed.path == "/create-sample":
                    from svf.sample_project import create_sample_project

                    project_dir = data.get("project_dir", ["demo_project"])[0].strip() or "demo_project"
                    sample_path = _resolve_under_root(root, project_dir)
                    create_sample_project(sample_path)
                    config_path = _display_path(sample_path / "config.yaml", root)
                    message = f"示例项目已创建：{_display_path(sample_path, root)}"
                else:
                    self.send_error(404)
                    return
            except Exception as exc:
                error = str(exc)
            self._send_html(_render_home(root, config_path, material_root=material_root, output_dir=output_dir, message=message, error=error))

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            return parse_qs(raw, keep_blank_values=True)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, data: dict[str, Any]) -> None:
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_media(self, root_dir: Path, raw_path: str) -> None:
            try:
                path = _resolve_under_root(root_dir, unquote(raw_path))
            except ValueError:
                self.send_error(403)
                return
            if not path.exists() or not path.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return GuiHandler


def _render_home(root: Path, config_path: str, material_root: str = "", output_dir: str = "", message: str = "", error: str = "") -> str:
    state = _load_state(root, config_path)
    material_root = material_root or "demo_project"
    batch_state = inspect_material_folder(_resolve_loose_path(root, material_root))
    safe_config = _escape(config_path)
    safe_material_root = _escape(material_root)
    safe_output_dir = _escape(output_dir)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Short Video Factory</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #20242a;
      --muted: #657080;
      --line: #dde2e8;
      --accent: #167a72;
      --accent-dark: #0f5f59;
      --danger: #b42318;
      --shadow: 0 10px 24px rgba(18, 25, 38, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    }}
    header {{
      padding: 22px 28px 16px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    main {{
      max-width: 760px;
      padding: 18px 28px 28px;
      margin: 0 auto;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }}
    h2 {{ margin: 0 0 12px; font-size: 17px; letter-spacing: 0; }}
    label {{ display: block; margin: 12px 0 6px; color: var(--muted); font-size: 13px; }}
    input, textarea, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 11px;
      font: inherit;
      background: #fff;
      color: var(--text);
    }}
    textarea {{ min-height: 260px; resize: vertical; line-height: 1.65; }}
    button, .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      padding: 0 14px;
      background: var(--accent);
      color: white;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
    }}
    button:hover, .button:hover {{ background: var(--accent-dark); }}
    .secondary {{ background: #334155; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    .path-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
    }}
    .path-row button {{ min-width: 72px; }}
    .provider-panel {{ display: none; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .notice, .error {{
      margin-bottom: 14px;
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
    }}
    .notice {{ background: #e7f6f2; color: #0d5c53; }}
    .error {{ background: #fee4e2; color: var(--danger); }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }}
    .stat strong {{ display: block; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    video {{
      width: min(360px, 100%);
      max-height: 520px;
      background: #111827;
      border-radius: 8px;
      display: block;
      margin-bottom: 10px;
    }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    @media (max-width: 960px) {{
      main {{ padding: 14px; }}
      header {{ padding: 18px 14px 12px; }}
      .split, .stats {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Short Video Factory</h1>
    <div class="muted">当前工作目录：{_escape(str(root))}</div>
  </header>
  <main>
      <section>
        <h2>批量生成</h2>
        {_message_html(message, error)}
        <form method="post" action="/batch-build">
          <label>素材主文件夹</label>
          <div class="path-row">
            <input id="material_root" name="material_root" value="{safe_material_root}">
            <button type="button" onclick="pickFolder('material_root')">选择</button>
          </div>
          <div class="muted" style="margin-top:8px">需要包含：文案/*.txt 和 轨道1/素材组/视频或图片。</div>
          <label>视频导出位置</label>
          <div class="path-row">
            <input id="output_dir" name="output_dir" value="{safe_output_dir}" placeholder="不填则导出到 素材主文件夹/输出；相对路径基于素材主文件夹">
            <button type="button" onclick="pickFolder('output_dir')">选择</button>
          </div>
          <label>音色列表</label>
          <textarea name="voices" style="min-height:120px" placeholder="每行一个音色，例如：&#10;zh_female_meilinvyou_emo_v2_mars_bigtts&#10;zh_female_tianmei_mars_bigtts"></textarea>
          <label>音色分配</label>
          <select name="voice_strategy">
            <option value="round_robin">按顺序轮换</option>
            <option value="random">随机抽取</option>
          </select>
          <label>配音接口</label>
          <select id="tts_provider" name="tts_provider" onchange="updateTtsFields()">
            <option value="">暂不生成配音</option>
            <option value="volcengine">火山引擎/豆包</option>
            <option value="minimax">MiniMax</option>
            <option value="qwen">阿里云/Qwen TTS</option>
            <option value="openai">OpenAI TTS</option>
            <option value="elevenlabs">ElevenLabs</option>
            <option value="tencent">腾讯云 TTS</option>
            <option value="xunfei">科大讯飞</option>
          </select>
          <div id="tts_none" class="provider-panel">
            <div class="muted" style="margin-top:10px">不生成配音时，会用文案长度估算时间线。</div>
          </div>
          <div id="tts_volcengine" class="provider-panel">
            <label>火山 AppID</label>
            <input data-name="tts_model" placeholder="例如 2418099736">
            <label>火山 Access Token</label>
            <input type="password" data-name="tts_api_key" placeholder="只用于本次生成，不写入输出文件">
            <label>音色 voice_type</label>
            <textarea data-name="voices" style="min-height:96px" placeholder="每行一个音色，例如：&#10;zh_female_meilinvyou_emo_v2_mars_bigtts&#10;zh_female_tianmei_mars_bigtts"></textarea>
            <label>接口地址</label>
            <input data-name="tts_endpoint" placeholder="可选；用默认火山接口时留空">
          </div>
          <div id="tts_minimax" class="provider-panel">
            <label>MiniMax API Key</label>
            <input type="password" data-name="tts_api_key" placeholder="只用于本次生成，不写入输出文件">
            <label>MiniMax 模型</label>
            <input data-name="tts_model" placeholder="例如 speech-2.8-hd">
            <label>音色 voice_id</label>
            <textarea data-name="voices" style="min-height:96px" placeholder="每行一个音色，例如：&#10;female-shaonv&#10;female-tianmei-jingpin"></textarea>
            <label>接口地址</label>
            <input data-name="tts_endpoint" placeholder="可选；用默认 MiniMax 接口时留空">
          </div>
          <div id="tts_coming" class="provider-panel">
            <div class="notice" style="margin-top:12px">这个接口入口先保留，具体参数和调用逻辑还没接入。当前可用的是火山引擎/豆包。</div>
          </div>
          <label class="row" style="color:var(--text)">
            <input type="checkbox" name="render" style="width:auto" checked>
            直接渲染视频
          </label>
          <button type="submit">生成全部 txt</button>
        </form>
      </section>
      <section style="margin-top:18px">
        <h2>素材扫描</h2>
        <div class="stats">
          <div class="stat"><span class="muted">文案 txt</span><strong>{batch_state["script_count"]}</strong></div>
          <div class="stat"><span class="muted">轨道1组</span><strong>{len(batch_state["track1_groups"])}</strong></div>
          <div class="stat"><span class="muted">素材文件</span><strong>{batch_state["asset_count"]}</strong></div>
        </div>
        <table>
          <tr><th>轨道1子文件夹</th><th>素材数</th></tr>
          {_track1_group_rows(batch_state["track1_groups"])}
        </table>
        <div class="muted" style="margin-top:10px">输出目录：{_escape(batch_state["output_dir"])}</div>
      </section>
  </main>
</body>
<script>
async function pickFolder(inputId) {{
  const input = document.getElementById(inputId);
  const button = event && event.currentTarget ? event.currentTarget : null;
  const oldText = button ? button.textContent : '';
  if (button) {{
    button.disabled = true;
    button.textContent = '选择中';
  }}
  try {{
    const response = await fetch('/pick-folder?current=' + encodeURIComponent(input.value || ''));
    const start = await response.json();
    if (!start.picker_id) throw new Error('选择器没有启动');
    for (let i = 0; i < 300; i++) {{
      await new Promise(resolve => setTimeout(resolve, 500));
      const resultResponse = await fetch('/pick-folder-result?id=' + encodeURIComponent(start.picker_id));
      const result = await resultResponse.json();
      if (result.status === 'done') {{
        if (result.path) input.value = result.path;
        return;
      }}
      if (result.status === 'error') throw new Error(result.error || '选择器失败');
    }}
    throw new Error('选择超时');
  }} catch (error) {{
    alert('无法打开文件夹选择窗口：' + error);
  }} finally {{
    if (button) {{
      button.disabled = false;
      button.textContent = oldText;
    }}
  }}
}}
function updateTtsFields() {{
  const provider = document.getElementById('tts_provider').value;
  const panels = document.querySelectorAll('.provider-panel');
  panels.forEach(panel => {{
    panel.style.display = 'none';
    panel.querySelectorAll('input, textarea').forEach(input => {{
      if (input.dataset.name) {{
        input.removeAttribute('name');
      }}
    }});
  }});
  let activeId = 'tts_none';
  if (provider === 'volcengine') activeId = 'tts_volcengine';
  else if (provider === 'minimax') activeId = 'tts_minimax';
  else if (provider) activeId = 'tts_coming';
  const active = document.getElementById(activeId);
  if (!active) return;
  active.style.display = 'block';
  active.querySelectorAll('input, textarea').forEach(input => {{
    if (input.dataset.name) {{
      input.setAttribute('name', input.dataset.name);
    }}
  }});
}}
document.addEventListener('DOMContentLoaded', updateTtsFields);
</script>
</html>"""


def _load_state(root: Path, config_path: str) -> dict[str, Any]:
    state: dict[str, Any] = {"script_text": "", "script_path": "", "assets": [], "outputs": {}}
    try:
        config_file = _resolve_under_root(root, config_path)
        config = load_config(config_file)
        script_path = config_file.parent / config["input"]["script"]
        asset_root = config_file.parent / config["assets"]["root"]
        output_dir = config_file.parent / config.get("output", {}).get("dir", "output")
        output_video = output_dir / config.get("output", {}).get("filename", "final_video.mp4")
        timeline_path = output_dir / "timeline.json"
        srt_path = output_dir / "subtitles.srt"
        state["script_path"] = _display_path(script_path, root)
        state["script_text"] = script_path.read_text(encoding="utf-8") if script_path.exists() else ""
        state["assets"] = scan_assets(asset_root)
        state["outputs"] = {
            "video": _display_path(output_video, root) if output_video.exists() else "",
            "timeline": _display_path(timeline_path, root) if timeline_path.exists() else "",
            "srt": _display_path(srt_path, root) if srt_path.exists() else "",
        }
        if timeline_path.exists():
            state["timeline"] = json.loads(timeline_path.read_text(encoding="utf-8"))
    except Exception as exc:
        state["load_error"] = str(exc)
    return state


def _resolve_under_root(root: Path, value: str) -> Path:
    raw = Path(value)
    path = raw if raw.is_absolute() else root / raw
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("路径必须位于当前项目目录内")
    return resolved


def _resolve_loose_path(root: Path, value: str) -> Path:
    raw = Path(value)
    return (raw if raw.is_absolute() else root / raw).resolve()


def _parse_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _start_folder_picker(current: str = "") -> str:
    PICKER_DIR.mkdir(parents=True, exist_ok=True)
    picker_id = uuid4().hex
    result_path = PICKER_DIR / f"{picker_id}.json"
    initial = current.strip()
    env = None
    if initial:
        import os

        env = os.environ.copy()
        env["SVF_INITIAL_DIR"] = initial
    script = r"""
import os
import json
import sys
import tkinter as tk
from tkinter import filedialog

result_file = sys.argv[1]
try:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    initial = os.environ.get("SVF_INITIAL_DIR", "")
    kwargs = {"title": "选择文件夹", "mustexist": False}
    if initial and os.path.isdir(initial):
        kwargs["initialdir"] = initial
    path = filedialog.askdirectory(**kwargs)
    root.destroy()
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({"status": "done", "path": path or ""}, f, ensure_ascii=False)
except Exception as exc:
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({"status": "error", "error": str(exc)}, f, ensure_ascii=False)
"""
    subprocess.Popen(
        [sys.executable, "-c", script, str(result_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    return picker_id


def _folder_picker_result(picker_id: str) -> dict[str, str]:
    if not picker_id or not all(char.isalnum() for char in picker_id):
        return {"status": "error", "error": "invalid picker id"}
    result_path = PICKER_DIR / f"{picker_id}.json"
    if not result_path.exists():
        return {"status": "pending"}
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "error", "error": "invalid picker result"}


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _message_html(message: str, error: str) -> str:
    if error:
        return f'<div class="error">{_escape(error)}</div>'
    if message:
        return f'<div class="notice">{_escape(message)}</div>'
    return ""


def _duration_text(state: dict[str, Any]) -> str:
    timeline = state.get("timeline")
    if not timeline:
        return "-"
    return f"{float(timeline.get('duration', 0)):.1f}s"


def _video_html(path: str | None) -> str:
    if not path:
        return '<div class="muted">还没有可预览的视频。</div>'
    return f'<video controls src="/media?path={quote(path)}"></video>'


def _output_link(label: str, path: str | None) -> str:
    if not path:
        return f'<div class="muted">{_escape(label)}：未生成</div>'
    return f'<div><a class="button" href="/media?path={quote(path)}" target="_blank">{_escape(label)}</a> <span class="muted">{_escape(path)}</span></div>'


def _asset_rows(assets: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for asset in assets:
        kind = str(asset.get("kind", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return '<tr><td colspan="2" class="muted">暂无素材</td></tr>'
    return "\n".join(f"<tr><td>{_escape(kind)}</td><td>{count}</td></tr>" for kind, count in sorted(counts.items()))


def _track1_group_rows(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return '<tr><td colspan="2" class="muted">没有找到 轨道1 子文件夹</td></tr>'
    return "\n".join(f"<tr><td>{_escape(group['name'])}</td><td>{group['count']}</td></tr>" for group in groups)


def _segment_rows(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return '<tr><td colspan="4" class="muted">还没有生成时间线</td></tr>'
    rows = []
    for segment in segments:
        asset = segment.get("track2_asset") or {}
        asset_name = Path(str(asset.get("path", ""))).name if asset else "-"
        time_text = f"{float(segment.get('start', 0)):.2f}-{float(segment.get('end', 0)):.2f}"
        rows.append(
            "<tr>"
            f"<td>{_escape(time_text)}</td>"
            f"<td>{_escape(str(segment.get('text', '')))}</td>"
            f"<td>{_escape(asset_name)}</td>"
            f"<td>{_escape(str(segment.get('match_reason', '-')))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
