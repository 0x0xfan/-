# Short Video Factory 第一版开发文档

> 项目目标：把“文案 + 旁白 + 素材库”自动合成为一个最基础的短视频。第一版先跑通主流程，不追求复杂花字、美化动效和精确口型/逐字字幕。

## 1. 当前版本能力

当前第一版已经实现：

1. 读取 `config.yaml` 项目配置。
2. 读取 `input/script.md` 文案。
3. 支持在文案中用 `[[标签]]` 手动指定素材。
4. 读取旁白音频时长。
5. 在没有 Whisper 的情况下，按文字长度把文案粗略分配到音频时间轴。
6. 扫描素材目录：
   - `assets/track1_base/`：底层背景素材。
   - `assets/track2_topic/`：根据字幕/关键词出现的主题素材。
   - `assets/bgm/`：背景音乐。
7. 根据手动标签和关键词规则匹配轨道 2 素材。
8. 生成：
   - `output/timeline.json`
   - `output/subtitles.srt`
   - `output/final_video.mp4`
9. 使用 MoviePy/Pillow 渲染：
   - 竖屏视频。
   - 底层图片素材。
   - 主题图片覆盖。
   - 底部白字黑边字幕。
   - 旁白 + BGM 混音。
10. 提供 `create-sample` 命令生成可跑通的示例项目。
11. 有 pytest 测试覆盖核心逻辑。

## 2. 当前项目路径

当前代码在：

```text
/home/xugouwl/.openclaw/workspace-dev/short_video_factory
```

如果复制到 Windows，整个 `short_video_factory` 文件夹一起复制即可。

## 3. 目录结构

```text
short_video_factory/
  README.md
  DEV_DOC.md
  requirements.txt
  pytest.ini
  config.example.yaml

  svf/
    __init__.py
    cli.py              # 命令行入口
    pipeline.py         # 总流程：读取配置、生成时间线、调用渲染
    assets.py           # 扫描素材库和读取素材 metadata
    timeline.py         # 文案标签解析、素材匹配、轨道1时间线生成
    subtitles.py        # 字幕时间分配、SRT 写入
    media.py            # 音视频时长读取
    renderer.py         # MoviePy/Pillow 视频渲染
    sample_project.py   # 创建示例项目

  tests/
    test_assets.py
    test_pipeline.py
    test_subtitles.py
    test_timeline.py
```

## 4. Windows 环境安装

### 4.1 安装 Python

建议安装 Python 3.11。

下载地址：

```text
https://www.python.org/downloads/windows/
```

安装时勾选：

```text
Add Python to PATH
```

验证：

```powershell
python --version
```

期望类似：

```text
Python 3.11.x
```

### 4.2 安装 FFmpeg

虽然 MoviePy 自带 imageio-ffmpeg，但建议 Windows 仍安装 FFmpeg，后面扩展真实视频素材会更稳。

一种简单方式：

```powershell
winget install Gyan.FFmpeg
```

验证：

```powershell
ffmpeg -version
ffprobe -version
```

如果提示找不到命令，需要把 FFmpeg 的 `bin` 目录加入系统 PATH。

### 4.3 创建虚拟环境

在项目根目录打开 PowerShell：

```powershell
cd short_video_factory
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4.4 运行测试

```powershell
pytest -q
```

期望：

```text
8 passed
```

## 5. 快速跑通示例

### 5.1 创建示例项目

```powershell
python -m svf.cli create-sample demo_project
```

会生成：

```text
demo_project/
  config.yaml
  input/script.md
  input/voice.wav
  assets/track1_base/computer/base_01.png
  assets/track2_topic/doubao/doubao_home.png
  assets/track2_topic/income/income_proof.png
  assets/bgm/bgm.wav
```

### 5.2 生成视频

```powershell
python -m svf.cli build demo_project/config.yaml
```

输出：

```text
demo_project/output/final_video.mp4
demo_project/output/timeline.json
demo_project/output/subtitles.srt
```

## 6. 如何创建自己的视频项目

建议复制 `demo_project`，改成自己的项目名。

```powershell
Copy-Item -Recurse demo_project my_video_001
```

然后替换：

```text
my_video_001/input/script.md       # 文案
my_video_001/input/voice.wav       # 旁白，支持 wav；mp3 后续也可用 ffprobe 读取
my_video_001/assets/track1_base/   # 底层背景素材
my_video_001/assets/track2_topic/  # 根据字幕触发的主题素材
my_video_001/assets/bgm/           # 背景音乐
```

执行：

```powershell
python -m svf.cli build my_video_001/config.yaml
```

## 7. 文案写法

普通文案：

```markdown
我最近发现一个适合普通人的副业。
就是用豆包写小说。
我自己试了三个月，收益已经跑出来了。
```

手动指定素材：

```markdown
就是用豆包写小说。[[doubao]]
我自己试了三个月，收益已经跑出来了。[[income]]
```

规则：

1. 一行就是一个字幕段落。
2. `#` 开头的行会被忽略。
3. `[[doubao]]` 这类标签不会显示在字幕里，只用于匹配素材。
4. 手动标签优先级高于关键词自动匹配。

## 8. 素材目录规范

推荐结构：

```text
assets/
  track1_base/
    computer/
      base_01.png
      base_02.mp4   # 后续会增强视频素材支持

  track2_topic/
    doubao/
      doubao_home.png
      doubao_home.json
    income/
      income_proof.png
      income_proof.json

  bgm/
    bgm.wav
```

### 8.1 素材 metadata

每个素材可以有一个同名 `.json` 文件。

例如：

```text
doubao_home.png
doubao_home.json
```

内容：

```json
{
  "tags": ["doubao", "豆包", "AI"],
  "kind": "image",
  "priority": 8
}
```

如果没有 `.json`，系统会从路径自动推断标签，例如：

```text
assets/track2_topic/income/income_proof.png
```

会推断出：

```text
track2_topic
income
income_proof
```

## 9. config.yaml 说明

示例：

```yaml
project:
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
```

重点字段：

- `project.resolution`：视频尺寸。当前默认竖屏 `[720, 1280]`，后面可改 `[1080, 1920]`。
- `input.script`：文案路径。
- `input.voice_audio`：旁白音频路径。
- `assets.root`：素材根目录。
- `style.font`：字体路径。Windows 可填：`C:/Windows/Fonts/msyh.ttc` 或 `C:/Windows/Fonts/simhei.ttf`。
- `rules.events`：关键词和素材标签匹配规则。

## 10. 当前实现逻辑

### 10.1 文案解析

文件：`svf/timeline.py`

函数：`parse_script_blocks`

作用：

```text
输入 script.md
输出 [{text, manual_tags}]
```

### 10.2 字幕时间分配

文件：`svf/subtitles.py`

函数：`distribute_segments`

当前第一版没有接 Whisper，所以用保底逻辑：

```text
按每行文字长度占比，把旁白总时长分配给每一句。
```

后续优化方向：

```text
接入 faster-whisper，得到真实字幕时间戳。
```

### 10.3 素材扫描

文件：`svf/assets.py`

函数：`scan_assets`

支持：

```text
图片：png/jpg/jpeg/webp
视频：mp4/mov/mkv/avi/webm
音频：mp3/wav/m4a/aac
```

注意：当前渲染器主要稳定支持图片素材，视频素材已经能被扫描，但渲染视频片段还需要下一版增强。

### 10.4 素材匹配

文件：`svf/timeline.py`

函数：`match_assets_for_segments`

优先级：

```text
手动标签 [[doubao]] > 关键词规则 > 无素材
```

### 10.5 视频渲染

文件：`svf/renderer.py`

当前渲染：

```text
底层背景图
轨道2图片覆盖
底部字幕
旁白 + BGM
```

## 11. 测试说明

测试文件：

```text
tests/test_timeline.py
测试文案标签解析、素材匹配、轨道1时间线生成。

tests/test_assets.py
测试素材扫描和 metadata 读取。

tests/test_subtitles.py
测试字幕时间分配和 SRT 输出。

tests/test_pipeline.py
测试示例项目能生成 timeline 和 srt。
```

运行：

```powershell
pytest -q
```

## 12. 已验证结果

在当前 Linux 环境已验证：

```text
Python 3.11.15
ffmpeg 8.0.1
pytest：8 passed
示例视频：demo_project/output/final_video.mp4
视频时长：8.0 秒
文件大小：184K
```

## 13. 下一版建议

按优先级：

1. 接入 faster-whisper，替代当前粗略时间分配。
2. 增强轨道1真实视频素材支持：随机裁切、变速、拼接。
3. 增强轨道2视频素材支持：短录屏按字幕时长自动变速/裁切/冻结。
4. 加入轨道3：随机大字、表情包、箭头、重点词强调。
5. 增加多套字幕样式 presets。
6. 增加 1080x1920 高清输出配置。
7. 增加批量模式：一个目录下多条文案/旁白自动出多条视频。
8. 增加日志和错误提示，方便非开发环境排查。

## 14. 给下一个 Agent 的交接说明

如果换 Agent 接手，请先做这几件事：

1. 阅读 `DEV_DOC.md`。
2. 运行测试：`pytest -q`。
3. 创建并构建示例项目：

```powershell
python -m svf.cli create-sample demo_project
python -m svf.cli build demo_project/config.yaml
```

4. 查看：

```text
demo_project/output/timeline.json
```

5. 不要一上来重构。先保留当前最小闭环，再逐项增强。
6. 当前最关键的短板是“字幕真实时间戳”和“真实视频素材轨道渲染”。
7. 如果新增功能，先补测试，再改实现。

## 15. 常见问题

### Q1：为什么字幕和旁白不是精准对齐？

因为第一版还没有接 Whisper。当前只是按文字长度粗略分配时间。下一版接入 faster-whisper 后会精准很多。

### Q2：为什么我放 mp4 轨道1视频没有按视频动起来？

当前渲染器第一版主要稳定支持图片素材。视频素材扫描已预留，但渲染还需下一版增强。

### Q3：Windows 中文字体不显示怎么办？

在 `config.yaml` 里设置：

```yaml
style:
  font: "C:/Windows/Fonts/msyh.ttc"
```

或者：

```yaml
style:
  font: "C:/Windows/Fonts/simhei.ttf"
```

### Q4：要生成 1080x1920 怎么办？

修改：

```yaml
project:
  resolution: [1080, 1920]
```

注意渲染会更慢。
