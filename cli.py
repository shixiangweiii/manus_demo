"""Command-line host for the unified agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging

from console import ConsoleRenderer, console, setup_logging
from core.events import EventBus
from core.models import Effort, EngineKind, ExecutorKind
from core.settings import get_settings
from rich.panel import Panel
from runtime.factory import build_runtime


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        choices=[kind.value for kind in EngineKind if kind != EngineKind.WORKFLOW],
        default=None,
        help="Orchestration engine (default: settings.toml)",
    )
    parser.add_argument(
        "--executor",
        choices=[kind.value for kind in ExecutorKind],
        default=None,
        help="Per-action executor (default: settings.toml)",
    )
    parser.add_argument(
        "--effort",
        choices=[effort.value for effort in Effort],
        default=None,
        help="Reasoning effort (default: settings.toml)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Local playground for comparing agent orchestration engines",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_parser = subparsers.add_parser("chat", help="Start an interactive session")
    _add_run_options(chat_parser)

    run_parser = subparsers.add_parser("run", help="Execute one task")
    run_parser.add_argument("task", help="Task text")
    _add_run_options(run_parser)

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Execute a deterministic workflow JSON file",
    )
    workflow_parser.add_argument("path", help="Path to workflow specification")

    subparsers.add_parser(
        "mcp-server",
        help="Expose configured local tools through the MCP server",
    )

    subparsers.add_parser("tasks", help="List retained checkpoint records")
    resume_parser = subparsers.add_parser("resume", help="Resume a semantic checkpoint")
    resume_parser.add_argument("task_id")
    return parser


async def _runtime(interactive: bool):
    settings = get_settings()
    events = EventBus()
    renderer = ConsoleRenderer()
    events.subscribe(renderer)
    runtime = await build_runtime(settings, events, interactive=interactive)
    return runtime, renderer


def _overrides(args: argparse.Namespace) -> dict:
    values = {
        "engine": args.engine,
        "executor": args.executor,
        "effort": args.effort,
    }
    return {name: value for name, value in values.items() if value is not None}


async def _run_command(args: argparse.Namespace) -> None:
    if args.command == "tasks":
        from checkpoint.store import RuntimeCheckpointStore

        records = RuntimeCheckpointStore().list_tasks()
        if not records:
            console.print("[dim]No checkpoint records found.[/dim]")
            return
        for record in records:
            console.print(
                f"{record.task_id}  {record.state.value:<10}  "
                f"{record.engine.value}/{record.executor.value}/{record.effort.value}  "
                f"{record.task[:80]}"
            )
        return

    if args.command == "mcp-server":
        settings = get_settings()
        runtime = await build_runtime(settings, EventBus(), interactive=False)
        from tools.mcp.server import MCPServerWrapper

        caps = settings.capabilities
        server = MCPServerWrapper(
            runtime.context.tools.values(),
            memory_service=runtime.context.agentic_memory_service,
            host=caps.mcp_server_host,
            port=caps.mcp_server_port,
            llm_client=(
                runtime.context.llm_client
                if caps.mcp_server_expose_agent
                else None
            ),
            expose_agent=caps.mcp_server_expose_agent,
        )
        try:
            if caps.mcp_server_transport == "stdio":
                await server.run_stdio()
            else:
                await server.run_streamable_http()
        finally:
            await runtime.aclose()
        return

    runtime, renderer = await _runtime(
        interactive=args.command in {"chat", "resume"}
    )
    try:
        if args.command == "run":
            await runtime.run(args.task, _overrides(args))
            return
        if args.command == "workflow":
            from workflow.loader import load_workflow_spec

            await runtime.run_workflow(load_workflow_spec(args.path))
            return
        if args.command == "resume":
            await runtime.resume(args.task_id)
            return

        console.print(
            Panel(
                "Available engines: sequential, dag, todo, goal\n"
                "Type a task, or type quit to exit.",
                title="[bold blue]Manus Demo[/bold blue]",
            )
        )
        while True:
            try:
                task = console.input("[bold blue]You > [/bold blue]").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if task.lower() in {"quit", "exit", "q"}:
                break
            if task:
                try:
                    await runtime.run(task, _overrides(args))
                except Exception:
                    logging.exception("Task failed")
    finally:
        await renderer.aclose()
        await runtime.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    stdio_server = (
        args.command == "mcp-server"
        and get_settings().capabilities.mcp_server_transport == "stdio"
    )
    setup_logging(args.verbose, use_stderr=stdio_server)
    try:
        asyncio.run(_run_command(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise SystemExit(1) from exc
    finally:
        from tracing import shutdown_tracing

        shutdown_tracing()
