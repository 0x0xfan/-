from __future__ import annotations

import argparse
from pathlib import Path

from svf.batch import build_from_material_folder
from svf.gui import run_gui
from svf.pipeline import build_project
from svf.sample_project import create_sample_project


def main() -> None:
    parser = argparse.ArgumentParser(description="Short Video Factory 第一版批量短视频生成工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("create-sample", help="创建一个可测试的示例项目")
    sample.add_argument("project_dir", help="示例项目目录")

    build = sub.add_parser("build", help="根据配置生成 timeline、字幕和视频")
    build.add_argument("config", help="config.yaml 路径")
    build.add_argument("--no-render", action="store_true", help="只生成 timeline/srt，不渲染视频")
    build.add_argument("--seed", type=int, default=42, help="随机种子")

    gui = sub.add_parser("gui", help="启动本地可视化操作界面")
    gui.add_argument("--host", default="127.0.0.1", help="监听地址")
    gui.add_argument("--port", type=int, default=8765, help="监听端口")
    gui.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")

    batch = sub.add_parser("batch", help="从素材主文件夹批量生成视频")
    batch.add_argument("material_root", help="素材主文件夹，里面包含 文案 和 轨道1 等子文件夹")
    batch.add_argument("--output-dir", default="", help="导出目录；相对路径会基于素材主文件夹")
    batch.add_argument("--no-render", action="store_true", help="只生成 timeline/srt，不渲染视频")
    batch.add_argument("--seed", type=int, default=42, help="随机种子")
    batch.add_argument("--voice", action="append", default=[], help="可重复填写多个音色，批量时按顺序轮换")
    batch.add_argument("--voice-strategy", choices=["round_robin", "random"], default="round_robin", help="音色分配策略")
    batch.add_argument("--tts-provider", default="", help="配音供应商，例如 minimax/volcengine/qwen/openai")
    batch.add_argument("--tts-model", default="", help="配音模型或应用标识")
    batch.add_argument("--tts-endpoint", default="", help="自定义配音接口地址")
    batch.add_argument("--tts-api-key", default="", help="配音 API Key，不会写入输出文件")
    batch.add_argument("--orientation", choices=["landscape", "portrait"], default="landscape", help="视频方向：landscape=16:9, portrait=9:16")
    batch.add_argument("--subtitle-offset", type=float, default=0.0, help="字幕整体偏移秒数；正数表示延后显示")

    args = parser.parse_args()
    if args.command == "create-sample":
        create_sample_project(Path(args.project_dir))
        print(f"示例项目已创建：{args.project_dir}")
    elif args.command == "build":
        timeline = build_project(args.config, seed=args.seed, render=not args.no_render)
        print(f"生成完成：{timeline['output_video']}")
    elif args.command == "gui":
        run_gui(host=args.host, port=args.port, open_browser=not args.no_open)
    elif args.command == "batch":
        summary = build_from_material_folder(
            args.material_root,
            output_dir=args.output_dir,
            seed=args.seed,
            render=not args.no_render,
            voices=args.voice,
            voice_strategy=args.voice_strategy,
            tts_provider=args.tts_provider,
            tts_model=args.tts_model,
            tts_endpoint=args.tts_endpoint,
            tts_api_key=args.tts_api_key,
            resolution=(1920, 1080) if args.orientation == "landscape" else (1080, 1920),
            subtitle_offset=args.subtitle_offset,
        )
        print(f"批量生成完成：{summary['count']} 条")


if __name__ == "__main__":
    main()
