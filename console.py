"""Rich terminal rendering for structured runtime events."""

from __future__ import annotations

import asyncio
import logging

from core.events import RuntimeEvent
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

console = Console()


def setup_logging(verbose: bool = False, *, use_stderr: bool = False) -> None:
    log_console = Console(stderr=True) if use_stderr else console
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=log_console, show_path=False, rich_tracebacks=True)],
    )
    for name in ("httpx", "openai", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class ConsoleRenderer:
    """Translate stable event names into concise local feedback."""

    def __init__(self) -> None:
        self._input_tasks: set[asyncio.Task] = set()

    def __call__(self, event: RuntimeEvent) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.name == "task_started":
            console.print(
                f"[dim]engine={event.engine} "
                f"effort={payload.get('effort', 'auto')}[/dim]"
            )
        elif event.name == "action_started":
            action = payload.get("action", {})
            console.print(f"[cyan]→[/cyan] {action.get('description', '')[:160]}")
        elif event.name == "task_completed":
            success = payload.get("success") is not False
            console.print(
                Panel(
                    str(payload.get("output", "")),
                    title=(
                        "[bold green]Final Answer[/bold green]"
                        if success
                        else "[bold red]Task Unsuccessful[/bold red]"
                    ),
                    border_style="green" if success else "red",
                )
            )
        elif event.name == "task_failed":
            console.print(f"[red]{payload.get('error', 'Task failed')}[/red]")
        elif event.name == "ask_user_prompt":
            self._collect_user_input(payload)

    def _collect_user_input(self, payload: dict) -> None:
        future = payload.get("response_future")
        if future is None:
            return
        question = str(payload.get("question", ""))
        console.print(Panel(question, title="[bold magenta]Agent Asks[/bold magenta]"))

        async def collect() -> None:
            try:
                answer = await asyncio.to_thread(console.input, "[bold magenta]You > [/bold magenta]")
            except (EOFError, KeyboardInterrupt):
                answer = "(user cancelled)"
            if future.done():
                logging.getLogger(__name__).info(
                    "Ignoring terminal response received after the prompt closed"
                )
                return
            future.set_result(answer.strip() or "(no response)")

        task = asyncio.create_task(collect())
        self._input_tasks.add(task)

        def finish(done: asyncio.Task) -> None:
            self._input_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                logging.getLogger(__name__).exception("Terminal input task failed")

        task.add_done_callback(finish)

    async def aclose(self) -> None:
        """Cancel any outstanding terminal input collectors."""
        tasks = tuple(self._input_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
