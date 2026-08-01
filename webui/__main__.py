"""Launch the local WebUI using settings.toml defaults."""

from __future__ import annotations

import argparse

from core.settings import get_settings
from webui.app import create_app


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m webui",
        description="Manus Demo local debugging web UI / 本地调试 Web 界面",
    )
    parser.add_argument("--host", default=settings.webui.host, help="Bind host")
    parser.add_argument("--port", type=int, default=settings.webui.port, help="Bind port")
    parser.add_argument(
        "--traces-dir",
        default=settings.tracing.output_dir,
        help="Trace files directory for the embedded viewer",
    )
    args = parser.parse_args()

    from tracing.server import configure_traces_dir

    configure_traces_dir(args.traces_dir)
    import uvicorn

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
