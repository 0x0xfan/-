from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from svf.batch import build_from_material_folder
from svf.batch import SCRIPT_DIR_NAME
from svf.generated_visuals import openai_image_api_key, openai_image_model
from svf.renderer import render_video
from svf.tts import synthesize_tts


DEFAULT_TTS_CONFIG = Path("tts.config.yaml")
PROVIDER_CHOICES = ["", "volcengine", "minimax", "edge", "windows", "qwen", "openai", "elevenlabs", "tencent", "xunfei"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build infoflow videos, cache used remote assets locally, then render from cache.")
    parser.add_argument("material_root", help="素材主文件夹，至少包含 文案/")
    parser.add_argument("--asset-root", default="", help="素材资源根目录；为空时默认等于 material_root")
    parser.add_argument("--work-output", required=True, help="工作输出目录，保存 timeline/字幕/配音/自动素材")
    parser.add_argument("--final-dir", required=True, help="最终 mp4 导出目录")
    parser.add_argument("--cache-dir", default="", help="本地素材缓存目录；默认在 work-output/asset_cache")
    parser.add_argument("--tts-config", default=str(DEFAULT_TTS_CONFIG), help="配音配置 YAML")
    parser.add_argument("--provider", default=None, choices=PROVIDER_CHOICES, help="临时覆盖配音供应商")
    parser.add_argument("--appid", default=None, help="临时覆盖火山 AppID 或模型标识")
    parser.add_argument("--token", default=None, help="临时覆盖 Access Token / API Key")
    parser.add_argument("--voice", action="append", default=None, help="临时覆盖音色，可重复")
    parser.add_argument("--voice-strategy", choices=["round_robin", "random"], default=None)
    parser.add_argument("--speed-ratio", type=float, default=None, help="override TTS speed ratio")
    parser.add_argument("--endpoint", default=None, help="临时覆盖 TTS 接口地址")
    parser.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape")
    parser.add_argument("--subtitle-offset", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generate-object-images", action="store_true", help="按规则生成物体配图")
    parser.add_argument("--render-workers", type=int, default=1, help="parallel mp4 render workers; 2 or 3 is usually safe for 1080p")
    parser.add_argument("--render-chunk-seconds", type=float, default=8.0, help="seconds per render chunk; larger is faster but uses more memory")
    parser.add_argument("--skip-existing", action="store_true", help="skip readable mp4 files that already exist in final-dir")
    args = parser.parse_args()

    material_root = Path(args.material_root).resolve()
    asset_root = Path(args.asset_root).resolve() if str(args.asset_root).strip() else material_root
    work_output = Path(args.work_output).resolve()
    final_dir = Path(args.final_dir).resolve()
    cache_dir = Path(args.cache_dir).resolve() if str(args.cache_dir).strip() else work_output / "asset_cache"

    work_output.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    status_path = work_output / "render_status.json"
    _write_status(status_path, "running", final_dir=final_dir)

    build_root, pre_skipped = _material_root_for_missing_outputs(
        material_root,
        final_dir,
        skip_existing=args.skip_existing,
    )
    if args.skip_existing and pre_skipped and not _has_script_files(build_root):
        manifest = {
            "material_root": str(material_root),
            "build_root": str(build_root),
            "asset_root": str(asset_root),
            "work_output": str(work_output),
            "cache_dir": str(cache_dir),
            "final_dir": str(final_dir),
            "rendered_mp4": [],
            "skipped_existing_mp4": pre_skipped,
            "cached_asset_count": 0,
            "render_workers": max(1, int(args.render_workers or 1)),
            "render_chunk_seconds": max(3.0, float(args.render_chunk_seconds or 8.0)),
            "tts_provider": "",
            "generate_object_images": args.generate_object_images,
            "openai_image_key_provided": False,
            "summary": {"count": 0, "results": []},
        }
        (work_output / "cache_render_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_status(status_path, "done", final_dir=final_dir, rendered_mp4=[], skipped_mp4=pre_skipped)
        print(f"Work output: {work_output}")
        print(f"Cache dir: {cache_dir}")
        print(f"Final dir: {final_dir}")
        print("Cached assets: 0")
        for path in pre_skipped:
            print(f"SKIPPED: {path}")
        return

    tts = _resolve_tts_config(args)
    _validate_tts_config(tts, Path(args.tts_config))
    _preflight_tts(tts)

    try:
        summary = build_from_material_folder(
            material_root=build_root,
            output_dir=work_output,
            asset_root=asset_root,
            seed=args.seed,
            render=False,
            voices=tts["voices"],
            voice_strategy=tts["voice_strategy"],
            tts_provider=tts["provider"],
            tts_model=tts["model"],
            tts_endpoint=tts["endpoint"],
            tts_api_key=tts["api_key"],
            tts_speed_ratio=tts["speed_ratio"],
            tts_api_version=tts["api_version"],
            tts_resource_id=tts["resource_id"],
            tts_sample_rate=tts["sample_rate"],
            image_api_key=openai_image_api_key(tts["raw_config"]) if args.generate_object_images else "",
            image_model=openai_image_model(tts["raw_config"]),
            generate_object_images=args.generate_object_images,
            resolution=(1920, 1080) if args.orientation == "landscape" else (1080, 1920),
            subtitle_offset=args.subtitle_offset,
        )

        mapping = _cache_used_assets(summary, asset_root, cache_dir)
        render_jobs = _prepare_render_jobs(
            summary,
            mapping,
            final_dir,
            render_chunk_seconds=args.render_chunk_seconds,
            skip_existing=args.skip_existing,
        )
        rendered, render_skipped = _render_timelines(render_jobs, max_workers=args.render_workers)
        skipped = [*pre_skipped, *render_skipped]

        manifest = {
            "material_root": str(material_root),
            "build_root": str(build_root),
            "asset_root": str(asset_root),
            "work_output": str(work_output),
            "cache_dir": str(cache_dir),
            "final_dir": str(final_dir),
            "rendered_mp4": rendered,
            "skipped_existing_mp4": skipped,
            "cached_asset_count": len(mapping),
            "render_workers": max(1, int(args.render_workers or 1)),
            "render_chunk_seconds": max(3.0, float(args.render_chunk_seconds or 8.0)),
            "tts_provider": tts["provider"],
            "generate_object_images": args.generate_object_images,
            "openai_image_key_provided": bool(openai_image_api_key(tts["raw_config"])),
            "summary": summary,
        }
        (work_output / "cache_render_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_status(status_path, "done", final_dir=final_dir, rendered_mp4=rendered, skipped_mp4=skipped)

        print(f"Work output: {work_output}")
        print(f"Cache dir: {cache_dir}")
        print(f"Final dir: {final_dir}")
        print(f"Cached assets: {len(mapping)}")
        for path in rendered:
            print(f"MP4: {path}")
        for path in skipped:
            print(f"SKIPPED: {path}")
    except Exception as exc:
        _write_status(status_path, "failed", final_dir=final_dir, error=str(exc))
        raise


def _cache_used_assets(summary: dict[str, Any], asset_root: Path, cache_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in summary.get("results", []):
        timeline_path = Path(str(item["timeline"]))
        if not timeline_path.exists():
            continue
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        for path in _iter_referenced_asset_paths(timeline):
            cached = _cache_one_asset(path, asset_root, cache_dir)
            if cached:
                mapping[path] = cached
    return mapping


def _material_root_for_missing_outputs(material_root: Path, final_dir: Path, *, skip_existing: bool) -> tuple[Path, list[str]]:
    if not skip_existing:
        return material_root, []
    script_dir = material_root / SCRIPT_DIR_NAME
    if not script_dir.exists():
        return material_root, []

    missing: list[Path] = []
    skipped: list[str] = []
    for script_path in sorted(path for path in script_dir.glob("*.txt") if path.is_file()):
        output_path = final_dir / f"{script_path.stem}.mp4"
        if _is_readable_mp4(output_path):
            skipped.append(str(output_path))
        else:
            missing.append(script_path)

    if not skipped or missing == sorted(path for path in script_dir.glob("*.txt") if path.is_file()):
        return material_root, skipped
    if not missing:
        return _temporary_script_root(material_root, []), skipped
    return _temporary_script_root(material_root, missing), skipped


def _has_script_files(material_root: Path) -> bool:
    script_dir = material_root / SCRIPT_DIR_NAME
    return script_dir.exists() and any(path.is_file() for path in script_dir.glob("*.txt"))


def _temporary_script_root(material_root: Path, script_paths: list[Path]) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="svf_missing_scripts_"))
    dst_script_dir = temp_dir / SCRIPT_DIR_NAME
    dst_script_dir.mkdir(parents=True, exist_ok=True)
    for script_path in script_paths:
        shutil.copy2(script_path, dst_script_dir / script_path.name)
    return temp_dir


def _iter_referenced_asset_paths(timeline: dict[str, Any]):
    bgm = str(timeline.get("bgm_audio") or "").strip()
    if bgm:
        yield bgm
    for effect in timeline.get("sound_effects", []) or []:
        path = str(effect.get("path") or "").strip()
        if path:
            yield path
    for clip in timeline.get("base_clips", []) or []:
        path = str(clip.get("asset") or "").strip()
        if path:
            yield path
    for segment in timeline.get("segments", []) or []:
        asset = segment.get("track2_asset") or {}
        path = str(asset.get("path") or "").strip()
        if path:
            yield path
        for nested in _iter_nested_asset_paths(asset):
            yield nested


def _iter_nested_asset_paths(asset: dict[str, Any]):
    for item in asset.get("sequence_assets", []) or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path:
            yield path
    for path in asset.get("revenue_paths", []) or []:
        text = str(path or "").strip()
        if text:
            yield text


def _cache_one_asset(path_str: str, asset_root: Path, cache_dir: Path) -> str | None:
    if not path_str or path_str.startswith("generated://"):
        return None
    src = Path(path_str)
    if not src.exists():
        return None
    try:
        rel = src.relative_to(asset_root)
    except ValueError:
        try:
            rel = src.resolve().relative_to(asset_root.resolve())
        except Exception:
            return None
    dst = cache_dir / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return str(dst)


def _rewrite_timeline_paths(timeline: dict[str, Any], mapping: dict[str, str]) -> None:
    bgm = str(timeline.get("bgm_audio") or "")
    if bgm in mapping:
        timeline["bgm_audio"] = mapping[bgm]
    for effect in timeline.get("sound_effects", []) or []:
        path = str(effect.get("path") or "")
        if path in mapping:
            effect["path"] = mapping[path]
    for clip in timeline.get("base_clips", []) or []:
        path = str(clip.get("asset") or "")
        if path in mapping:
            clip["asset"] = mapping[path]
    for segment in timeline.get("segments", []) or []:
        asset = segment.get("track2_asset") or {}
        path = str(asset.get("path") or "")
        if path in mapping:
            asset["path"] = mapping[path]
            asset["rel_dir"] = str(Path(mapping[path]).parent)
        _rewrite_nested_asset_paths(asset, mapping)


def _rewrite_nested_asset_paths(asset: dict[str, Any], mapping: dict[str, str]) -> None:
    for item in asset.get("sequence_assets", []) or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path in mapping:
            item["path"] = mapping[path]
            item["rel_dir"] = str(Path(mapping[path]).parent)
    if isinstance(asset.get("revenue_paths"), list):
        asset["revenue_paths"] = [mapping.get(str(path or ""), str(path or "")) for path in asset.get("revenue_paths", [])]


def _prepare_render_jobs(
    summary: dict[str, Any],
    mapping: dict[str, str],
    final_dir: Path,
    *,
    render_chunk_seconds: float,
    skip_existing: bool,
) -> list[dict[str, str | bool]]:
    jobs: list[dict[str, str | bool]] = []
    chunk_seconds = max(3.0, float(render_chunk_seconds or 8.0))
    for item in summary["results"]:
        timeline_path = Path(item["timeline"])
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        _rewrite_timeline_paths(timeline, mapping)
        output_path = final_dir / Path(item["output"]).name
        timeline["output_video"] = str(output_path)
        timeline["render_chunk_seconds"] = chunk_seconds
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        jobs.append(
            {
                "timeline_path": str(timeline_path),
                "output_path": str(output_path),
                "skip": bool(skip_existing and _is_readable_mp4(output_path)),
            }
        )
    return jobs


def _render_timelines(jobs: list[dict[str, str | bool]], max_workers: int = 1) -> tuple[list[str], list[str]]:
    rendered: list[str] = []
    skipped: list[str] = []
    pending: list[dict[str, str | bool]] = []
    for job in jobs:
        output_path = str(job["output_path"])
        if job.get("skip"):
            skipped.append(output_path)
        else:
            pending.append(job)

    workers = max(1, int(max_workers or 1))
    if workers == 1 or len(pending) <= 1:
        for job in pending:
            timeline_path = Path(str(job["timeline_path"]))
            output_path = Path(str(job["output_path"]))
            _render_one_timeline_in_subprocess(timeline_path, output_path)
            rendered.append(str(output_path))
        return rendered, skipped

    with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as executor:
        future_to_job = {
            executor.submit(
                _render_one_timeline_in_subprocess,
                Path(str(job["timeline_path"])),
                Path(str(job["output_path"])),
            ): job
            for job in pending
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            future.result()
            rendered.append(str(job["output_path"]))
    return rendered, skipped


def _is_readable_mp4(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return False
    if result.returncode:
        return False
    values = [part.strip() for part in result.stdout.strip().split(",")]
    return len(values) >= 3 and all(values[:2]) and bool(values[2])


def _render_one_timeline_in_subprocess(timeline_path: Path, output_path: Path) -> None:
    runner = PROJECT_ROOT / "scripts" / "render_single_timeline.py"
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(runner), str(timeline_path), str(output_path)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"单条时间线渲染失败：{timeline_path.name}\n{detail[-4000:]}")


def _write_status(
    status_path: Path,
    status: str,
    *,
    final_dir: Path,
    rendered_mp4: list[str] | None = None,
    skipped_mp4: list[str] | None = None,
    error: str = "",
) -> None:
    payload = {
        "status": status,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "final_dir": str(final_dir),
        "rendered_mp4": rendered_mp4 or [],
        "skipped_mp4": skipped_mp4 or [],
        "error": error,
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_tts_config(args: argparse.Namespace) -> dict[str, Any]:
    raw_config = _load_tts_config(Path(args.tts_config))
    active_name = str(raw_config.get("active", "") or "").strip()
    providers = raw_config.get("providers", {})
    active_config = {}
    if active_name:
        if not isinstance(providers, dict) or active_name not in providers:
            raise SystemExit(f"配音配置错误：找不到 providers.{active_name}")
        active_config = providers.get(active_name, {})
        if not isinstance(active_config, dict):
            raise SystemExit(f"配音配置错误：providers.{active_name} 必须是字典")

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
        "speed_ratio": _coalesce_float(args.speed_ratio, selected_config.get("speed_ratio")),
        "api_version": _coalesce(selected_config.get("api_version"), selected_config.get("version"), ""),
        "resource_id": _coalesce(selected_config.get("resource_id"), selected_config.get("resourceId"), ""),
        "sample_rate": _coalesce_int(selected_config.get("sample_rate")),
        "raw_config": raw_config,
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
    if provider == "edge":
        return
    if provider == "windows":
        return
    if provider not in {"volcengine", "minimax"}:
        raise SystemExit(f"当前脚本暂未接入 {provider}，请先把 {config_path} 的 active 改成 volcengine 或 minimax")
    if not tts["api_key"]:
        raise SystemExit(f"已选择配音供应商 {provider}，但 {config_path} 里缺少 token/api_key")
    if provider == "volcengine" and not tts["model"]:
        raise SystemExit(f"火山引擎需要在 {config_path} 里填写 appid")
    if not tts["voices"]:
        raise SystemExit(f"已选择配音供应商 {provider}，但 {config_path} 里缺少 voices")


def _preflight_tts(tts: dict[str, Any]) -> None:
    output_name = "tts_preflight.mp3"
    voice = tts["voices"][0] if tts.get("voices") else ""
    kwargs = {
        "api_key": tts.get("api_key", ""),
        "voice": voice,
        "model": tts.get("model", ""),
        "endpoint": tts.get("endpoint", ""),
    }
    if tts.get("speed_ratio") is not None:
        kwargs["speed_ratio"] = tts["speed_ratio"]
    if tts.get("api_version"):
        kwargs["api_version"] = tts["api_version"]
    if tts.get("resource_id"):
        kwargs["resource_id"] = tts["resource_id"]
    if tts.get("sample_rate"):
        kwargs["sample_rate"] = tts["sample_rate"]
    try:
        with tempfile.TemporaryDirectory(prefix="svf_tts_preflight_") as temp_root:
            output_path = Path(temp_root) / output_name
            synthesize_tts(tts["provider"], "hello", output_path, **kwargs)
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


def _coalesce_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            raise SystemExit(f"TTS config error: speed_ratio must be a number, got {value!r}")
    return None


def _coalesce_int(*values: Any) -> int | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            raise SystemExit(f"TTS config error: sample_rate must be an integer, got {value!r}")
    return None


if __name__ == "__main__":
    os.environ.setdefault("SVF_VIDEO_ENCODER", "auto")
    main()
