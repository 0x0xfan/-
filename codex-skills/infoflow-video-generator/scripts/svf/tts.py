from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


MINIMAX_DEFAULT_ENDPOINT = "https://api.minimax.chat/v1/t2a_v2"
MINIMAX_DEFAULT_MODEL = "speech-2.8-hd"
VOLCENGINE_DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"
VOLCENGINE_DEFAULT_CLUSTER = "volcano_tts"
VOLCENGINE_DEFAULT_MODEL = "seed-tts-1.1"


def synthesize_tts(
    provider: str,
    text: str,
    output_path: str | Path,
    api_key: str,
    voice: str,
    model: str = "",
    endpoint: str = "",
) -> Path:
    provider = provider.strip().lower()
    if provider == "minimax":
        return synthesize_minimax(text, output_path, api_key, voice, model=model, endpoint=endpoint)
    if provider in {"volcengine", "volc", "doubao"}:
        return synthesize_volcengine(text, output_path, api_key, voice, appid=model, endpoint=endpoint)
    raise ValueError(f"暂不支持的配音供应商：{provider}")


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
) -> Path:
    if not appid:
        raise ValueError("火山引擎 AppID 不能为空")
    if not token:
        raise ValueError("火山引擎 Access Token 不能为空")
    if not voice_type:
        raise ValueError("火山引擎 voice_type 不能为空")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
