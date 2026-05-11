from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any

import yaml

from svf.batch import build_from_material_folder, inspect_material_folder
from svf.tts import synthesize_tts


DEFAULT_TTS_CONFIG = Path("tts.config.yaml")
PROVIDER_CHOICES = ["", "volcengine", "minimax", "qwen", "openai", "elevenlabs", "tencent", "xunfei"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-generate infoflow videos from a material folder.")
    parser.add_argument("material_root", help="素材主文件夹，包含 文案、轨道1、其他素材 等目录")
    parser.add_argument("--output-dir", default="", help="导出目录；相对路径基于素材主文件夹")
    parser.add_argument("--tts-config", default=str(DEFAULT_TTS_CONFIG), help="配音配置 YAML；默认读取当前目录 tts.config.yaml")
    parser.add_argument("--provider", default=None, choices=PROVIDER_CHOICES, help="临时覆盖配置里的配音供应商")
    parser.add_argument("--appid", default=None, help="临时覆盖火山 AppID；MiniMax 时不用")
    parser.add_argument("--token", default=None, help="临时覆盖火山 Access Token 或其他 API Key")
    parser.add_argument("--voice", action="append", default=None, help="临时覆盖音色，可重复；火山填 voice_type，MiniMax 填 voice_id")
    parser.add_argument("--voice-strategy", choices=["round_robin", "random"], default=None)
    parser.add_argument("--endpoint", default=None, help="临时覆盖 TTS 接口地址，通常留空")
    parser.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape", help="landscape=16:9 横屏；portrait=9:16 竖屏")
    parser.add_argument("--subtitle-offset", type=float, default=-0.12, help="字幕整体偏移秒数；负数表示提前显示")
    parser.add_argument("--no-render", action="store_true", help="只生成配音、字幕和 timeline，不渲染 mp4")
    parser.add_argument("--inspect", action="store_true", help="只检查素材文件夹结构")
    args = parser.parse_args()

    if args.inspect:
        info = inspect_material_folder(args.material_root)
        print(f"素材主文件夹: {info['root']}")
        print(f"文案 txt 数: {info['script_count']}")
        print(f"轨道1组数: {len(info['track1_groups'])}")
        for group in info["track1_groups"]:
            print(f"  - {group['name']}: {group['count']} 个素材")
        print(f"素材文件总数: {info['asset_count']}")
        print(f"默认输出目录: {info['output_dir']}")
        return

    tts = _resolve_tts_config(args)
    _validate_tts_config(tts, Path(args.tts_config))
    _preflight_tts(tts)

    summary = build_from_material_folder(
        Path(args.material_root),
        output_dir=args.output_dir,
        render=not args.no_render,
        voices=tts["voices"],
        voice_strategy=tts["voice_strategy"],
        tts_provider=tts["provider"],
        tts_model=tts["model"],
        tts_endpoint=tts["endpoint"],
        tts_api_key=tts["api_key"],
        resolution=(1920, 1080) if args.orientation == "landscape" else (1080, 1920),
        subtitle_offset=args.subtitle_offset,
    )
    print(f"批量生成完成: {summary['count']} 条")
    print(f"输出目录: {summary['output_dir']}")
    for item in summary["results"]:
        print(f"- {item['script']}")
        print(f"  视频: {item['output']}")
        print(f"  配音: {item.get('voice_audio', '')}")
        print(f"  轨道1组: {item.get('track1_group', '')}")


def _resolve_tts_config(args: argparse.Namespace) -> dict[str, Any]:
    raw_config = _load_tts_config(Path(args.tts_config))
    active_name = str(raw_config.get("active", "") or "").strip()
    providers = raw_config.get("providers", {})
    active_config = {}
    if active_name:
        if not isinstance(providers, dict):
            raise SystemExit("配音配置错误：providers 必须是字典")
        if active_name not in providers:
            raise SystemExit(f"配音配置错误：找不到 providers.{active_name}")
        active_config = providers.get(active_name, {})
        if not isinstance(active_config, dict):
            raise SystemExit(f"配音配置错误：找不到 providers.{active_name}")

    provider = (args.provider if args.provider is not None else active_name).strip().lower()
    provider_config = providers.get(provider, {}) if isinstance(providers, dict) and isinstance(providers.get(provider, {}), dict) else {}
    selected_config = provider_config if provider else active_config
    voices = args.voice if args.voice is not None else selected_config.get("voices", [])
    if isinstance(voices, str):
        voices = [voices]

    return {
        "provider": provider,
        "model": _coalesce(args.appid, selected_config.get("appid"), selected_config.get("model")),
        "api_key": _coalesce(args.token, selected_config.get("token"), selected_config.get("api_key")),
        "voices": [str(voice).strip() for voice in voices if str(voice).strip()],
        "voice_strategy": _coalesce(args.voice_strategy, raw_config.get("voice_strategy"), "round_robin"),
        "endpoint": _coalesce(args.endpoint, selected_config.get("endpoint"), ""),
    }


def _load_tts_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"active": "", "providers": {}, "voice_strategy": "round_robin"}
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"配音配置错误：{path} 顶层必须是字典")
    return data


def _validate_tts_config(tts: dict[str, Any], config_path: Path) -> None:
    provider = tts["provider"]
    if not provider:
        raise SystemExit(f"执行前必须先对接好 TTS：请在 {config_path} 里设置 active，不能用空 provider 生成无口播视频")
    if provider not in {"volcengine", "minimax"}:
        raise SystemExit(f"当前脚本暂未接入 {provider}，请先把 {config_path} 的 active 改成 volcengine 或 minimax")
    if not tts["api_key"]:
        raise SystemExit(f"已选择配音供应商 {provider}，但 {config_path} 里缺少 token/api_key")
    if provider == "volcengine" and not tts["model"]:
        raise SystemExit(f"火山引擎需要在 {config_path} 里填写 appid")
    if not tts["voices"]:
        raise SystemExit(f"已选择配音供应商 {provider}，但 {config_path} 里缺少 voices")


def _preflight_tts(tts: dict[str, Any]) -> None:
    voice = tts["voices"][0] if tts.get("voices") else ""
    try:
        with tempfile.TemporaryDirectory(prefix="svf_tts_preflight_") as temp_root:
            output_path = Path(temp_root) / "tts_preflight.mp3"
            synthesize_tts(
                tts["provider"],
                "hello",
                output_path,
                api_key=tts.get("api_key", ""),
                voice=voice,
                model=tts.get("model", ""),
                endpoint=tts.get("endpoint", ""),
            )
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("没有生成有效音频文件")
    except Exception as exc:
        raise SystemExit(f"TTS 预检失败，已停止生成：{exc}") from exc


def _coalesce(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


if __name__ == "__main__":
    main()
