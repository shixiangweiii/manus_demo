"""
Evaluation platform CLI.
评测平台命令行。

Usage / 用法：
  python -m evalplatform serve [--host 127.0.0.1] [--port 8720]
  python -m evalplatform upload <file> [--title T]
  python -m evalplatform generate --doc <doc_id> [--goal "..."] [--num-tasks 6] [--heuristic]
  python -m evalplatform run --evalset <id> [--modes simple complex emergent] [--repeat 1]
  python -m evalplatform report --run <id> [--output out.md]
  python -m evalplatform analyze [--runs id1 id2 ...] [--llm] [--output out.md]
  python -m evalplatform list [documents|evalsets|runs|analyses]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _store():
    from evalplatform.store import EvalPlatformStore
    return EvalPlatformStore()


# ----------------------------------------------------------------------
# Sub-commands / 子命令
# ----------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn
    uvicorn.run(
        "evalplatform.server:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
    )


def cmd_upload(args: argparse.Namespace) -> None:
    from evalplatform.document import DocumentIngestError, ingest_document
    path = Path(args.file).expanduser()
    if not path.is_file():
        print(f"文件不存在：{path}")
        sys.exit(1)
    try:
        doc = ingest_document(path.name, path.read_bytes(), _store(), title=args.title)
    except DocumentIngestError as exc:
        print(f"上传失败：{exc}")
        sys.exit(1)
    print(f"文档已入库：{doc.doc_id}（{doc.filename}，{doc.char_count} 字符）")


def cmd_generate(args: argparse.Namespace) -> None:
    from evalplatform.generator import EvalSetGenerator
    store = _store()
    doc = store.get_document(args.doc)
    if doc is None:
        print(f"文档不存在：{args.doc}（先执行 upload）")
        sys.exit(1)

    async def _run():
        generator = EvalSetGenerator()
        evalset = await generator.generate(
            doc, target_goal=args.goal, num_tasks=args.num_tasks,
            use_llm=not args.heuristic, name=args.name,
        )
        store.save_evalset(evalset)
        return evalset

    evalset = asyncio.run(_run())
    print(f"评测集：{evalset.evalset_id}  状态={evalset.status.value}  生成方式={evalset.generator}")
    if evalset.generation_error:
        print(f"说明：{evalset.generation_error}")
    for t in evalset.tasks:
        print(f"  - {t.task_id} [{t.difficulty.value}] {t.task_description[:60].replace(chr(10), ' ')}…")


def cmd_run(args: argparse.Namespace) -> None:
    from evalplatform.executor import EvalSetExecutor, clamp_repeat
    from evalplatform.models import EvalRunRecord, EvalSetStatus
    from evalplatform.reporting import build_report

    store = _store()
    evalset = store.get_evalset(args.evalset)
    if evalset is None:
        print(f"评测集不存在：{args.evalset}")
        sys.exit(1)
    if evalset.status != EvalSetStatus.READY:
        print(f"评测集状态为 {evalset.status.value}，无法运行")
        sys.exit(1)

    # 与 server 同口径钳制 repeat（评审 V6 修复：CLI 无上限会放大真实 LLM 成本）
    run = EvalRunRecord(
        evalset_id=evalset.evalset_id,
        evalset_name=evalset.name,
        doc_id=evalset.doc_id,
        modes=args.modes,
        repeat=clamp_repeat(args.repeat),
    )
    store.save_run(run)

    async def _run():
        executor = EvalSetExecutor()
        await executor.execute(evalset, run, on_progress=store.save_run)

    asyncio.run(_run())
    store.save_run(run)
    report = build_report(run, evalset)
    store.save_report(report)
    print(f"运行完成：{run.run_id}  状态={run.status.value}")
    print(f"报告：{store.report_markdown_path(run.run_id)}")


def cmd_report(args: argparse.Namespace) -> None:
    store = _store()
    report = store.get_report(args.run)
    if report is None:
        print(f"报告不存在：{args.run}")
        sys.exit(1)
    if args.output:
        Path(args.output).expanduser().write_text(report.markdown, encoding="utf-8")
        print(f"报告已写入：{args.output}")
    else:
        print(report.markdown)


def cmd_analyze(args: argparse.Namespace) -> None:
    from evalplatform.analyzer import ReportAnalyzer
    from evalplatform.models import RunStatus

    store = _store()
    if args.runs:
        runs = []
        for rid in args.runs:
            run = store.get_run(rid)
            if run is None:
                print(f"运行不存在：{rid}")
                sys.exit(1)
            runs.append(run)
    else:
        runs = [r for r in store.list_runs() if r.status == RunStatus.COMPLETED]
    if not runs:
        print("没有可分析的已完成运行")
        sys.exit(1)

    analysis = asyncio.run(ReportAnalyzer().analyze(runs, use_llm=args.llm))
    store.save_analysis(analysis)
    if args.output:
        Path(args.output).expanduser().write_text(analysis.markdown, encoding="utf-8")
        print(f"分析已写入：{args.output}")
    else:
        print(analysis.markdown)


def cmd_list(args: argparse.Namespace) -> None:
    store = _store()
    kind = args.kind
    if kind in ("documents", "all"):
        print("== 文档 ==")
        for d in store.list_documents():
            print(f"  {d.doc_id}  {d.filename}  {d.char_count} 字符")
    if kind in ("evalsets", "all"):
        print("== 评测集 ==")
        for e in store.list_evalsets():
            print(f"  {e.evalset_id}  [{e.status.value}]  {e.name}（{len(e.tasks)} 任务，{e.generator}）")
    if kind in ("runs", "all"):
        print("== 运行 ==")
        for r in store.list_runs():
            print(f"  {r.run_id}  [{r.status.value}]  {r.evalset_name}  modes={','.join(r.modes)}")
    if kind in ("analyses", "all"):
        print("== 分析 ==")
        for a in store.list_analyses():
            print(f"  {a.analysis_id}  覆盖 {len(a.run_ids)} 次运行，{len(a.suggestions)} 条建议")


# ----------------------------------------------------------------------
# Entry / 入口
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import config  # 触发 .env 加载 / trigger dotenv load

    parser = argparse.ArgumentParser(
        prog="python -m evalplatform",
        description="Manus Demo 评测平台：文档 → 评测集 → 执行 → 报告 → 聚合分析",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="启动 Web 平台")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=config.EVAL_PLATFORM_PORT)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("upload", help="上传文档")
    p.add_argument("file")
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("generate", help="从文档生成评测集")
    p.add_argument("--doc", required=True, help="文档 ID")
    p.add_argument("--goal", default="", help="评测目标描述")
    p.add_argument("--num-tasks", type=int, default=config.EVAL_PLATFORM_DEFAULT_NUM_TASKS)
    p.add_argument("--name", default="", help="评测集名称")
    p.add_argument("--heuristic", action="store_true", help="离线启发式生成（不调 LLM）")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("run", help="执行评测集")
    p.add_argument("--evalset", required=True, help="评测集 ID")
    p.add_argument("--modes", nargs="+", default=["simple"],
                   choices=["simple", "complex", "emergent"])
    p.add_argument("--repeat", type=int, default=1, help="pass^k 重跑次数（上限 5，与 Web 端一致）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="查看/导出单次运行报告")
    p.add_argument("--run", required=True, help="运行 ID")
    p.add_argument("--output", "-o", default="")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("analyze", help="聚合分析多次运行并给出优化建议")
    p.add_argument("--runs", nargs="*", default=[], help="运行 ID 列表（默认全部已完成）")
    p.add_argument("--llm", action="store_true", help="附加 LLM 深度洞察")
    p.add_argument("--output", "-o", default="")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("list", help="列出平台数据")
    p.add_argument("kind", nargs="?", default="all",
                   choices=["all", "documents", "evalsets", "runs", "analyses"])
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
