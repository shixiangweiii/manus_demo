"""
Tracing Web Viewer CLI - Launch the trace visualization web server.
Tracing Web 可视化 CLI —— 启动 trace 可视化 Web 服务。

Usage:
    python -m tracing                     # Default: port 8600, traces dir ./traces
    python -m tracing --port 9000         # Custom port
    python -m tracing --dir ./my_traces   # Custom traces directory

用法：
    python -m tracing                     # 默认：端口 8600，traces 目录 ./traces
    python -m tracing --port 9000         # 自定义端口
    python -m tracing --dir ./my_traces   # 自定义 traces 目录
"""

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m tracing",
        description="Manus Demo Trace Viewer - Web-based trace visualization",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8600,
        help="Port to run the web server on (default: 8600)",
    )
    parser.add_argument(
        "--dir", "-d",
        type=str,
        default="traces",
        help="Directory containing trace JSON files (default: ./traces)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind the server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open browser automatically (default: auto-open)",
    )

    args = parser.parse_args()

    traces_dir = Path(args.dir).resolve()
    if not traces_dir.exists():
        print(f"[Error] Traces directory does not exist: {traces_dir}")
        print("  Hint: Run your agent with TRACING_ENABLED=true TRACING_BACKEND=file first.")
        sys.exit(1)

    # Check for trace files
    trace_files = list(traces_dir.glob("*.json"))
    if not trace_files:
        print(f"[Warning] No trace files found in: {traces_dir}")
        print("  The viewer will start, but no traces will be displayed until files appear.")

    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║       Manus Demo - Trace Viewer                 ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Traces dir : {str(traces_dir):<35s}║")
    print(f"║  Files found: {len(trace_files):<35d}║")
    print(f"║  Server     : http://{args.host}:{args.port}/traces{' ' * (35 - len(f'http://{args.host}:{args.port}/traces'))}║")
    print(f"╚══════════════════════════════════════════════════╝")
    print()

    # Auto-open browser
    should_open = not args.no_open
    if should_open:
        import threading
        import webbrowser
        import time

        def open_browser():
            time.sleep(1.0)  # Wait for server to start
            webbrowser.open(f"http://{args.host}:{args.port}/traces")

        threading.Thread(target=open_browser, daemon=True).start()

    # Start the server
    try:
        import uvicorn
    except ImportError:
        print("[Error] uvicorn is not installed. Install with:")
        print("  pip install uvicorn[standard]")
        sys.exit(1)

    # Set traces_dir as environment variable for the server to pick up
    import os
    os.environ["_TRACING_VIEWER_DIR"] = str(traces_dir)

    uvicorn.run(
        "tracing.server:app",
        host=args.host,
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
