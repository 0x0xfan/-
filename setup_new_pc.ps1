param(
    [string]$PythonVersion = "3.11",
    [string]$VenvDir = ".venv-win"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "== Infoflow Video Generator: new PC setup =="

function Assert-Command($Name, $Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing $Name. $Hint"
    }
}

Assert-Command "py" "Install Python $PythonVersion first, including the py launcher."

$pythonCheck = & py "-$PythonVersion" -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Python $PythonVersion was not found. Use Python 3.11 for the pinned MoviePy/Pillow stack."
}
Write-Host "Python: $pythonCheck"

$pythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host "Creating virtual environment: $VenvDir"
    & py "-$PythonVersion" -m venv $VenvDir
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

if (-not (Get-Command "ffmpeg" -ErrorAction SilentlyContinue)) {
    Write-Warning "ffmpeg was not found in PATH. Rendering and audio concat need ffmpeg and ffprobe. Install them, then reopen the terminal."
} else {
    $ffmpegVersion = (& ffmpeg -version | Select-Object -First 1)
    Write-Host "ffmpeg: $ffmpegVersion"
}

if (-not (Test-Path -LiteralPath "tts.config.yaml")) {
    Copy-Item -LiteralPath "codex-skills\infoflow-video-generator\tts.config.example.yaml" -Destination "tts.config.yaml"
    Write-Host "Created tts.config.yaml from the template. Fill in your TTS credentials."
} else {
    Write-Host "tts.config.yaml already exists; keeping it."
}

Write-Host ""
Write-Host "Setup complete. Start the GUI with: .\start_gui.bat"
Write-Host "Inspect a material folder with: .\.venv-win\Scripts\python.exe scripts\generate_infoflow_video.py `"YOUR_MATERIAL_FOLDER`" --inspect"
