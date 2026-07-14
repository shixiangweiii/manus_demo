"""
WebUI entry point: `python -m webui`.
WebUI 入口：`python -m webui`。

Env bootstrap MUST happen before any project import:
- tracing/config.py captures TRACING_* at import time;
- tracing/__init__.py picks real classes vs no-op stubs at FIRST import
  based on config.TRACING_ENABLED.
So we set env defaults here, then let uvicorn import `webui.app` lazily
via the import-string + factory form.
env 引导必须先于任何项目模块 import：
- tracing/config.py 在 import 时捕获 TRACING_*；
- tracing/__init__.py 在首次 import 时根据 config.TRACING_ENABLED
  选择真实实现或 no-op stub。
因此先在这里写 env 默认值，再通过 uvicorn 的 import-string + factory
形式惰性导入 `webui.app`。
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    # 1. Tracing defaults (setdefault: user can still disable explicitly).
    #    tracing 默认开启（setdefault：用户仍可显式关闭）。
    os.environ.setdefault("TRACING_ENABLED", "true")
    os.environ.setdefault("TRACING_BACKEND", "file")

    parser = argparse.ArgumentParser(
        prog="python -m webui",
        description="Manus Demo local debugging web UI / 本地调试 Web 界面",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8700, help="Bind port (default: 8700)")
    parser.add_argument(
        "--traces-dir",
        default="traces",
        help="Trace files directory for the embedded viewer (default: ./traces)",
    )
    parser.add_argument(
        "--llm-api-key",
        default="",
        metavar="KEY",
        help="LLM API key（写入进程环境变量，优先于 .env；适合临时 key 调试。"
             "注意：命令行参数会出现在 ps/shell 历史中，长期使用请写 .env）",
    )
    args = parser.parse_args()

    # LLM key：必须在 config import（load_dotenv）之前写入环境变量。
    # load_dotenv 默认不覆盖已存在的 env，因此命令行参数优先于 .env。
    # Must be set BEFORE config import (load_dotenv). load_dotenv does not
    # override existing env vars, so the CLI argument wins over .env.
    if args.llm_api_key:
        os.environ["LLM_API_KEY"] = args.llm_api_key

    # 2. Trace viewer dir — tracing/server.py reads this env live per request.
    #    trace 查看目录 —— tracing/server.py 每次请求实时读取该 env。
    os.environ.setdefault("_TRACING_VIEWER_DIR", args.traces_dir)

    # 3. Import uvicorn only now; webui.app is imported by uvicorn (factory).
    #    此时才 import uvicorn；webui.app 由 uvicorn 按 factory 形式导入。
    import uvicorn

    uvicorn.run(
        "webui.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
