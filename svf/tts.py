from __future__ import annotations

import base64
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


MINIMAX_DEFAULT_ENDPOINT = "https://api.minimax.chat/v1/t2a_v2"
MINIMAX_DEFAULT_MODEL = "speech-2.8-hd"
VOLCENGINE_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
VOLCENGINE_V3_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
VOLCENGINE_DEFAULT_CLUSTER = "volcano_tts"
VOLCENGINE_DEFAULT_MODEL = "seed-tts-1.1"
VOLCENGINE_V3_DEFAULT_RESOURCE_ID = "volc.service_type.10029"
VOLCENGINE_MAX_TEXT_CHARS = 360
WINDOWS_DEFAULT_VOICE = "Microsoft Huihui Desktop"
WINDOWS_PROVIDER_ALIASES = {"windows", "system", "sapi", "local"}
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
EDGE_PROVIDER_ALIASES = {"edge", "edge-tts", "edgetts"}


def synthesize_tts(
    provider: str,
    text: str,
    output_path: str | Path,
    api_key: str,
    voice: str,
    model: str = "",
    endpoint: str = "",
    speed_ratio: float | None = None,
    api_version: str = "",
    resource_id: str = "",
    sample_rate: int | None = None,
) -> Path:
    provider = provider.strip().lower()
    if provider == "minimax":
        return synthesize_minimax(text, output_path, api_key, voice, model=model, endpoint=endpoint)
    if provider in {"volcengine", "volc", "doubao"}:
        return synthesize_volcengine(
            text,
            output_path,
            api_key,
            voice,
            appid=model,
            endpoint=endpoint,
            speed_ratio=speed_ratio or 0.85,
            api_version=api_version,
            resource_id=resource_id,
            sample_rate=sample_rate,
        )
    if provider in EDGE_PROVIDER_ALIASES:
        return synthesize_edge(text, output_path, voice=voice, speed_ratio=speed_ratio)
    if provider in WINDOWS_PROVIDER_ALIASES:
        return synthesize_windows(text, output_path, voice=voice, speed_ratio=speed_ratio)
    raise ValueError(f"暂不支持的配音供应商：{provider}")


def default_voice_for_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in EDGE_PROVIDER_ALIASES:
        return EDGE_DEFAULT_VOICE
    if normalized in WINDOWS_PROVIDER_ALIASES:
        return WINDOWS_DEFAULT_VOICE
    return ""


def synthesize_minimax(
    text: str,
    output_path: str | Path,
    api_key: str,
    voice: str,
    model: str = "",
    endpoint: str = "",
) -> Path:
    if not api_key:
        raise ValueError("MiniMax API Key 不能为空")
    if not voice:
        raise ValueError("MiniMax voice_id 不能为空")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "model": model or MINIMAX_DEFAULT_MODEL,
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "output_format": "hex",
        "voice_setting": {
            "voice_id": voice,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
        "subtitle_enable": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(endpoint or MINIMAX_DEFAULT_ENDPOINT, headers=headers, json=payload, timeout=120)
    data = _json_response_or_error(response, "MiniMax TTS")
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code") not in (None, 0):
        raise RuntimeError(f"MiniMax TTS 失败：{base_resp.get('status_code')} {base_resp.get('status_msg')}")
    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        raise RuntimeError("MiniMax TTS 没有返回音频")
    output_path.write_bytes(bytes.fromhex(audio_hex))
    return output_path


def synthesize_volcengine(
    text: str,
    output_path: str | Path,
    token: str,
    voice_type: str,
    appid: str,
    endpoint: str = "",
    cluster: str = VOLCENGINE_DEFAULT_CLUSTER,
    model: str = VOLCENGINE_DEFAULT_MODEL,
    speed_ratio: float = 0.85,
    api_version: str = "",
    resource_id: str = "",
    sample_rate: int | None = None,
) -> Path:
    if not appid:
        raise ValueError("火山引擎 AppID 不能为空")
    if not token:
        raise ValueError("火山引擎 Access Token 不能为空")
    if not voice_type:
        raise ValueError("火山引擎 voice_type 不能为空")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_volcengine_v3(api_version, resource_id, endpoint):
        return _synthesize_volcengine_v3(
            text,
            output_path,
            token=token,
            voice_type=voice_type,
            appid=appid,
            endpoint=endpoint,
            resource_id=resource_id or VOLCENGINE_V3_DEFAULT_RESOURCE_ID,
            speed_ratio=speed_ratio,
            sample_rate=sample_rate or 24000,
        )
    chunks = _split_tts_text(text, VOLCENGINE_MAX_TEXT_CHARS)
    if len(chunks) > 1:
        return _synthesize_volcengine_chunks(
            chunks,
            output_path,
            token=token,
            voice_type=voice_type,
            appid=appid,
            endpoint=endpoint,
            cluster=cluster,
            model=model,
            speed_ratio=speed_ratio,
        )
    return _synthesize_volcengine_single(
        text,
        output_path,
        token=token,
        voice_type=voice_type,
        appid=appid,
        endpoint=endpoint,
        cluster=cluster,
        model=model,
        speed_ratio=speed_ratio,
    )


def _is_volcengine_v3(api_version: str = "", resource_id: str = "", endpoint: str = "") -> bool:
    version = str(api_version or "").strip().lower()
    return version in {"3", "v3", "http-v3"} or bool(str(resource_id or "").strip()) or "/api/v3/" in str(endpoint or "")


def _synthesize_volcengine_single(
    text: str,
    output_path: Path,
    token: str,
    voice_type: str,
    appid: str,
    endpoint: str,
    cluster: str,
    model: str,
    speed_ratio: float,
) -> Path:
    payload: dict[str, Any] = {
        "app": {
            "appid": appid,
            "token": token,
            "cluster": cluster,
        },
        "user": {"uid": "short_video_factory"},
        "audio": {
            "voice_type": voice_type,
            "encoding": "mp3",
            "speed_ratio": speed_ratio,
            "volume_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query",
            "model": model,
            "with_timestamp": 1,
        },
    }
    headers = {
        "Authorization": f"Bearer; {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    response = requests.post(
        endpoint or VOLCENGINE_DEFAULT_ENDPOINT,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=120,
    )
    data = _json_response_or_error(response, "火山引擎 TTS")
    if data.get("code") != 3000:
        raise RuntimeError(f"火山引擎 TTS 失败：{data.get('code')} {data.get('message')}")
    audio_base64 = data.get("data")
    if not audio_base64:
        raise RuntimeError("火山引擎 TTS 没有返回音频")
    output_path.write_bytes(base64.b64decode(audio_base64))
    _write_volcengine_timestamps(output_path, data)
    return output_path


def _synthesize_volcengine_v3(
    text: str,
    output_path: Path,
    token: str,
    voice_type: str,
    appid: str,
    endpoint: str,
    resource_id: str,
    speed_ratio: float,
    sample_rate: int,
) -> Path:
    payload = {
        "user": {"uid": "short_video_factory"},
        "req_params": {
            "text": text,
            "speaker": voice_type,
            "audio_params": {
                "format": "mp3",
                "sample_rate": sample_rate,
                "speech_rate": _volcengine_v3_rate(speed_ratio),
                "loudness_rate": 0,
            },
        },
    }
    additions = {
        "disable_markdown_filter": True,
        "enable_language_detector": True,
    }
    payload["req_params"]["additions"] = json.dumps(additions, ensure_ascii=False)
    request_id = str(uuid4())
    headers = {
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "X-Api-App-Id": appid,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
    }
    response = requests.post(
        endpoint or VOLCENGINE_V3_DEFAULT_ENDPOINT,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        stream=True,
        timeout=120,
    )
    if response.status_code >= 400:
        detail = _volcengine_v3_error_detail(response)
        raise RuntimeError(f"火山引擎 TTS V3 HTTP {response.status_code}：{detail}")
    audio = bytearray()
    response_items: list[dict[str, Any]] = []
    for item in _iter_volcengine_v3_response_items(response):
        response_items.append(item)
        header = item.get("header") if isinstance(item.get("header"), dict) else {}
        code = item.get("code", header.get("code", 0))
        if code not in (0, "0", 20000000, "20000000", None):
            message = item.get("message") or item.get("msg") or header.get("message") or item
            raise RuntimeError(f"火山引擎 TTS V3 失败：{code} {message}")
        data = item.get("data")
        if data:
            audio.extend(base64.b64decode(data))
    if not audio:
        raise RuntimeError("火山引擎 TTS V3 没有返回音频")
    output_path.write_bytes(bytes(audio))
    _write_volcengine_v3_timestamps(output_path, response_items)
    return output_path


def _volcengine_v3_rate(speed_ratio: float) -> int:
    try:
        value = float(speed_ratio)
    except (TypeError, ValueError):
        value = 1.0
    return max(-50, min(100, round((value - 1.0) * 100)))


def _volcengine_v3_error_detail(response: requests.Response) -> str:
    try:
        data = response.json()
        return str(data.get("message") or data.get("msg") or data.get("error") or data)[:500]
    except ValueError:
        return response.text[:500]


def _iter_volcengine_v3_response_items(response: requests.Response) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    if items:
        return items
    try:
        data = response.json()
    except ValueError:
        data = {}
    return [data] if isinstance(data, dict) and data else []


def synthesize_windows(
    text: str,
    output_path: str | Path,
    voice: str = "",
    speed_ratio: float | None = None,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice_name = (voice or "").strip() or WINDOWS_DEFAULT_VOICE
    rate = _windows_rate_from_speed_ratio(speed_ratio)
    with tempfile.TemporaryDirectory(prefix="svf_windows_tts_") as temp_root:
        temp_dir = Path(temp_root)
        text_path = temp_dir / "input.txt"
        wave_path = temp_dir / "speech.wav"
        script_path = temp_dir / "synthesize.ps1"
        text_path.write_text(text, encoding="utf-8")
        script_path.write_text(_windows_tts_script(), encoding="utf-8")
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TextPath",
                str(text_path),
                "-WavePath",
                str(wave_path),
                "-VoiceName",
                voice_name,
                "-Rate",
                str(rate),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"Windows TTS failed: {detail[-1200:]}")
        if not wave_path.exists():
            raise RuntimeError("Windows TTS failed: no wave file produced")
        _transcode_audio_file(wave_path, output_path)
    return output_path


def synthesize_edge(
    text: str,
    output_path: str | Path,
    voice: str = "",
    speed_ratio: float | None = None,
) -> Path:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts 未安装，无法使用 Edge TTS") from exc

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice_name = (voice or "").strip() or EDGE_DEFAULT_VOICE
    rate = _edge_rate_string(speed_ratio)

    async def _run() -> None:
        communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
        await communicate.save(str(output_path))

    asyncio.run(_run())
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Edge TTS 没有生成音频")
    return output_path


def _synthesize_volcengine_chunks(
    chunks: list[str],
    output_path: Path,
    token: str,
    voice_type: str,
    appid: str,
    endpoint: str,
    cluster: str,
    model: str,
    speed_ratio: float,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="svf_tts_") as temp_root:
        temp_dir = Path(temp_root)
        audio_paths: list[Path] = []
        for index, chunk in enumerate(chunks):
            chunk_path = temp_dir / f"chunk_{index:03d}.mp3"
            _synthesize_volcengine_single(
                chunk,
                chunk_path,
                token=token,
                voice_type=voice_type,
                appid=appid,
                endpoint=endpoint,
                cluster=cluster,
                model=model,
                speed_ratio=speed_ratio,
            )
            audio_paths.append(chunk_path)
        _concat_audio_files(audio_paths, output_path, temp_dir)
        _merge_volcengine_timestamp_sidecars(audio_paths, output_path)
    return output_path


def _json_response_or_error(response: requests.Response, label: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = data.get("message") or data.get("status_msg") or response.text[:500]
        code = data.get("code") or data.get("status_code")
        detail = f"{code} {message}".strip() if code else str(message)
        raise RuntimeError(f"{label} HTTP {response.status_code}：{detail}")
    return data


def _split_tts_text(text: str, max_chars: int) -> list[str]:
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return [clean] if clean else []
    units: list[str] = []
    current: list[str] = []
    for char in clean:
        current.append(char)
        if char in "\n。！？；.!?;":
            unit = "".join(current).strip()
            if unit:
                units.append(unit)
            current = []
    tail = "".join(current).strip()
    if tail:
        units.append(tail)

    chunks: list[str] = []
    bucket = ""
    for unit in units:
        if not bucket:
            bucket = unit
            continue
        if len(bucket) + 1 + len(unit) <= max_chars:
            bucket = f"{bucket}\n{unit}"
        else:
            chunks.extend(_split_long_tts_unit(bucket, max_chars))
            bucket = unit
    if bucket:
        chunks.extend(_split_long_tts_unit(bucket, max_chars))
    return [chunk for chunk in chunks if chunk.strip()]


def _split_long_tts_unit(text: str, max_chars: int) -> list[str]:
    value = text.strip()
    if len(value) <= max_chars:
        return [value]
    chunks = []
    start = 0
    while start < len(value):
        chunks.append(value[start : start + max_chars].strip())
        start += max_chars
    return [chunk for chunk in chunks if chunk]


def _windows_tts_script() -> str:
    return r"""
param(
    [Parameter(Mandatory = $true)][string]$TextPath,
    [Parameter(Mandatory = $true)][string]$WavePath,
    [string]$VoiceName = "",
    [int]$Rate = 0
)

Add-Type -AssemblyName System.Speech

$text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $available = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    if ($VoiceName -and $available -contains $VoiceName) {
        $synth.SelectVoice($VoiceName)
    } elseif ($available -contains 'Microsoft Huihui Desktop') {
        $synth.SelectVoice('Microsoft Huihui Desktop')
    }
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($WavePath)
    $synth.Speak($text)
}
finally {
    $synth.Dispose()
}
"""


def _windows_rate_from_speed_ratio(speed_ratio: float | None) -> int:
    if speed_ratio in (None, ""):
        return 0
    try:
        value = float(speed_ratio)
    except (TypeError, ValueError):
        return 0
    mapped = round((value - 1.0) * 20.0)
    return max(-10, min(10, int(mapped)))


def _edge_rate_string(speed_ratio: float | None) -> str:
    if speed_ratio in (None, ""):
        return "+0%"
    try:
        value = float(speed_ratio)
    except (TypeError, ValueError):
        return "+0%"
    percent = round((value - 1.0) * 100.0)
    if percent >= 0:
        return f"+{percent}%"
    return f"{percent}%"


def _concat_audio_files(audio_paths: list[Path], output_path: Path, temp_dir: Path) -> None:
    concat_file = temp_dir / "audio_concat.txt"
    lines = []
    for path in audio_paths:
        safe = str(path).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"ffmpeg 拼接 TTS 音频失败：{detail[-1200:]}")


def _transcode_audio_file(input_path: Path, output_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"ffmpeg audio transcode failed: {detail[-1200:]}")


def _merge_volcengine_timestamp_sidecars(audio_paths: list[Path], output_path: Path) -> None:
    merged_words: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    offset_ms = 0.0
    template_frontend: dict[str, Any] = {}
    for index, audio_path in enumerate(audio_paths):
        duration_ms = _timestamp_duration_ms(audio_path)
        sidecar = timestamp_sidecar_path(audio_path)
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            frontend = data.get("frontend", {})
            if isinstance(frontend, dict) and not template_frontend:
                template_frontend = dict(frontend)
            words = frontend.get("words", []) if isinstance(frontend, dict) else []
            if isinstance(words, list):
                for word in words:
                    if not isinstance(word, dict):
                        continue
                    shifted = dict(word)
                    shifted["start_time"] = round(float(shifted.get("start_time", 0) or 0) + offset_ms)
                    shifted["end_time"] = round(float(shifted.get("end_time", 0) or 0) + offset_ms)
                    merged_words.append(shifted)
        chunks.append({"index": index, "duration_ms": round(duration_ms)})
        offset_ms += duration_ms
    if not merged_words:
        return
    frontend = template_frontend or {}
    frontend["words"] = merged_words
    timestamp_sidecar_path(output_path).write_text(
        json.dumps(
            {
                "provider": "volcengine",
                "duration_ms": round(_audio_duration_ms(output_path) or offset_ms),
                "frontend": frontend,
                "chunks": chunks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _timestamp_duration_ms(audio_path: Path) -> float:
    sidecar = timestamp_sidecar_path(audio_path)
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            value = data.get("duration_ms")
            if value not in (None, ""):
                return float(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _audio_duration_ms(audio_path)


def _audio_duration_ms(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        return 0.0
    try:
        return float(result.stdout.strip()) * 1000.0
    except ValueError:
        return 0.0


def timestamp_sidecar_path(audio_path: str | Path) -> Path:
    return Path(audio_path).with_suffix(".timestamps.json")


def _write_volcengine_timestamps(output_path: Path, data: dict[str, Any]) -> None:
    addition = data.get("addition", {})
    frontend_raw = addition.get("frontend") if isinstance(addition, dict) else None
    if not frontend_raw:
        return
    try:
        frontend = json.loads(frontend_raw)
    except json.JSONDecodeError:
        return
    timestamp_sidecar_path(output_path).write_text(
        json.dumps(
            {
                "provider": "volcengine",
                "duration_ms": addition.get("duration"),
                "frontend": frontend,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_volcengine_v3_timestamps(output_path: Path, items: list[dict[str, Any]]) -> None:
    words: list[dict[str, Any]] = []
    duration_ms: Any = None
    for item in items:
        if duration_ms is None:
            duration_ms = item.get("duration") or item.get("duration_ms")
        addition = item.get("addition") or item.get("additions") or {}
        if isinstance(addition, str):
            try:
                addition = json.loads(addition)
            except json.JSONDecodeError:
                addition = {}
        frontend_raw = addition.get("frontend") if isinstance(addition, dict) else None
        if isinstance(frontend_raw, str):
            try:
                frontend = json.loads(frontend_raw)
            except json.JSONDecodeError:
                frontend = {}
        elif isinstance(frontend_raw, dict):
            frontend = frontend_raw
        else:
            frontend = {}
        maybe_words = frontend.get("words") if isinstance(frontend, dict) else None
        if isinstance(maybe_words, list):
            words.extend(word for word in maybe_words if isinstance(word, dict))
    if not words:
        return
    timestamp_sidecar_path(output_path).write_text(
        json.dumps(
            {
                "provider": "volcengine_v3",
                "duration_ms": duration_ms,
                "frontend": {"words": words},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
