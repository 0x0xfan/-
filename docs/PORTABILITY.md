# 换电脑使用说明

这份项目可以直接整个文件夹复制到新电脑。最稳的方式是：源码、`assets/`、`codex-skills/`、`docs/`、`scripts/`、`svf/`、`tests/` 都带走；虚拟环境和运行产物不要带。

## 新电脑第一次运行

1. 安装 Python 3.11。
2. 安装 ffmpeg，并确认 `ffmpeg`、`ffprobe` 能在 PowerShell 里直接运行。
3. 在项目根目录运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_new_pc.ps1
```

4. 检查 `tts.config.yaml`。如果是自己电脑迁移，可以把旧电脑的真实配置复制过来；如果是给别人用，只给模板，不要带真实 token。
5. 启动界面：

```powershell
.\start_gui.bat
```

## 打便携包

默认打包不会包含真实 `tts.config.yaml`，避免把火山/MiniMax 密钥一起发出去：

```powershell
.\pack_portable.ps1
```

如果只是给自己的新电脑用，并且确认压缩包不会外发，可以带上真实配置：

```powershell
.\pack_portable.ps1 -IncludeSecrets
```

## 推荐保留

- `assets/`：字体、音效、样式预设，换电脑稳定性的核心。
- `codex-skills/`：当前生成脚本和模板的技能副本。
- `docs/`：生成规则和维护说明。
- `scripts/`、`svf/`：实际程序代码。
- `requirements.txt`、`setup_new_pc.ps1`、`start_gui.bat`：初始化和启动入口。
- `tts.config.yaml`：只在自己的机器之间迁移，里面有真实密钥。

## 可以不带

- `.venv/`、`.venv-win/`、`.venv-infoflow/`：虚拟环境，新电脑重新安装更稳。
- `runs/`、`debug_frames/`、`tmp_gap_card_checks/`、`tts_test/`、`volc_batch_test/`：运行产物和调试缓存。
- `__pycache__/`、`.pytest_cache/`：Python 缓存。
- 根目录旧 zip 和临时 mp4：历史包/临时文件，不影响运行。

## 素材文件夹要求

素材主文件夹至少包含：

- `文案/`：一个 `.txt` 生成一个视频。
- `轨道1/`：背景视频或图片素材，按子文件夹分组。
- `其他素材/`：可选，按语义文件夹名匹配字幕内容。

先用检查命令确认结构：

```powershell
.\.venv-win\Scripts\python.exe scripts\generate_infoflow_video.py "你的素材文件夹" --inspect
```

正式生成：

```powershell
.\.venv-win\Scripts\python.exe scripts\generate_infoflow_video.py "你的素材文件夹" --output-dir "输出目录"
```
