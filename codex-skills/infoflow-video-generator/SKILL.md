---
name: infoflow-video-generator
description: Batch generate Chinese infoflow/ad short videos from a material folder. Use when the user wants to turn folders containing 文案 txt files, 轨道1 background clips, 其他素材 semantic overlay folders, and TTS credentials into exported short videos with generated voiceover, subtitles, timeline, and mp4 output.
---

# 信息流视频生成

Use this skill to batch-generate vertical infoflow videos from a local material folder.

## Core Workflow

1. Inspect the material folder structure.
2. Confirm it contains `文案/`, `轨道1/`, and optionally `其他素材/`.
3. Preflight TTS before any generation: `tts.config.yaml` must have a non-empty `active` provider, the provider must be supported by the script, required credentials/appid/voices must be present, and a very short test synthesis must succeed.
4. If TTS is not connected, incomplete, quota-exceeded, or otherwise failing, stop before generation. Do not run with `--provider=`, empty `active`, estimated timing, no-voice output, or BGM-only preview.
5. Run `scripts/generate_infoflow_video.py`.
6. Check the generated `输出/` directory for:
   - `batch_summary.json`
   - `配音/*.mp3`
   - `配音/*.timestamps.json`
   - `<文案文件名>/timeline.json`
   - `<文案文件名>/subtitles.srt`
   - `<文案文件名>/<文案文件名>.mp4`

Read `references/folder-structure.md` when the user asks how to organize materials or why a clip was/was not inserted.

## Install Dependencies

Before first use on a new machine, install dependencies in a Python 3.11 virtual environment:

```powershell
py -3.11 -m venv .venv-infoflow
.\.venv-infoflow\Scripts\python -m pip install --upgrade pip
.\.venv-infoflow\Scripts\python -m pip install -r <skill_dir>\scripts\requirements.txt
```

Use Python 3.11. Avoid Python 3.14 with the pinned Pillow/MoviePy stack.

## Inspect Materials

`--inspect` may run without TTS because it only checks folder structure and does not generate audio, subtitles, timeline, or mp4.

```powershell
.\.venv-infoflow\Scripts\python <skill_dir>\scripts\generate_infoflow_video.py "Z:\办公\B站方面\信息流模板" --inspect
```

## Generate With 火山引擎 / 豆包 TTS

Preferred workflow: read credentials from `tts.config.yaml` in the project root. The bundled template is `tts.config.example.yaml`.

```powershell
.\.venv-infoflow\Scripts\python <skill_dir>\scripts\generate_infoflow_video.py `
  "Z:\办公\B站方面\信息流模板"
```

`--token` is sensitive. Prefer keeping it in local `tts.config.yaml`; do not write it into generated docs, logs, or skill files.

## Output Directory

Default output is `素材主文件夹/输出`.

Use a custom export directory:

```powershell
.\.venv-infoflow\Scripts\python <skill_dir>\scripts\generate_infoflow_video.py `
  "Z:\办公\B站方面\信息流模板" `
  --output-dir "F:\成品视频\0503"
```

## Matching Rules

- One `.txt` under `文案/` becomes one video.
- One video uses exactly one subfolder under `轨道1/`; it does not mix groups.
- `其他素材/` overlays are selected by folder-name semantic match against the current subtitle text.
- The matching uses folder names, not media filenames.
- If no folder name matches a subtitle segment, do not insert an overlay for that segment.
- Subtitles split on punctuation including `，,。！？；.!?;`.

## Visual QA Rules

- Operation recordings under folders such as `上文案`, `改价格`, `发货方式`, `上传图片` must only appear for explicit operation steps. Do not use them for abstract advice such as `价格战`, `靠复制粘贴`, `自动发货`, or general strategy claims.
- Revenue screenshots must stay readable. When several adjacent lines use the same revenue folder, render the actually matched images as large overlapping/cumulative images; do not shrink them into tiny four-image grids just to fill the screen.
- For 2-3 revenue screenshots, prefer a large stacked/covered layout where the newest or most relevant image is the readable primary image. Older images may be partially covered; readability beats showing every image equally.
- If a continuous gap without track2 material is longer than 3 seconds, enrich it on top of track1. For normal 3-8 second gaps, add one short transparent sticker/big-text overlay near the start.
- For long gaps around 8 seconds or more, use a cumulative sticker layout: semantic labels appear one by one with the corresponding lines and remain visible until the gap ends, similar to benefit chips such as `低成本` / `不用囤货`.
- Auto-generated overlays should be transparent overlays on top of track1. Avoid full-screen white cards or generic placeholder copy such as “没有本地素材时自动补画面”.
- Gap stickers must use short semantic labels extracted from the current line, such as `复制素材` or `成本很低`. Do not invent generic subtitle copy such as `记住这句`.
- Gap sticker labels must be intentional semantic labels, not raw sentence slices. For example, use `一周实测` instead of `像这一周的时`, and use `闲鱼方法` instead of adding explanatory subtitle text.
- Any generated card/sticker text must fit inside its container by shrinking, trimming, or widening within screen bounds; never let text overflow outside the card.
- Keep subtitles single-line when possible; shrink text to fit instead of wrapping by default.

Run `python -m pytest tests -q` after changing matching or rendering behavior when working inside this repo.

## Notes

- Current supported TTS providers in the bundled script: `volcengine`, `minimax`.
- 火山 TTS stores timestamp sidecars and uses them for subtitle timing.
- MiniMax support is present, but account/model permissions must be verified before relying on it.
- The previous web UI is not part of the skill workflow.
