@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
  echo 未找到 .venv-win 环境。
  echo 正在尝试运行新电脑初始化脚本...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_new_pc.ps1"
  if errorlevel 1 (
    echo 初始化失败，请按提示安装 Python 3.11 / ffmpeg 后重试。
    pause
    exit /b 1
  )
)

if not exist "tts.config.yaml" (
  copy "codex-skills\infoflow-video-generator\tts.config.example.yaml" "tts.config.yaml" >nul
  echo 已创建 tts.config.yaml 模板，请先填入配音配置。
  pause
  exit /b 1
)

".venv-win\Scripts\python.exe" -m svf.cli gui
