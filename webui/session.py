"""
WebSession + SessionManager: config snapshot lifecycle, orchestrator
factory, single-flight run lock, HITL prompt registry, trace-id capture.
WebSession 与 SessionManager：配置快照生命周期、orchestrator 工厂、
单飞运行锁、HITL prompt 注册表、trace_id 捕获。

Knows orchestrator + config, but NOT HTTP (api.py/ws.py adapt).
懂 orchestrator 与 config，但不懂 HTTP（由 api.py/ws.py 适配）。

Config timing (settled in plan): apply at session creation, restore at
session close/replace/app-shutdown — OrchestratorAgent.__init__ reads
feature flags for tool self-injection, so per-run apply would desync
the tool set from the panel. Single session + serial runs make the
module-global mutation race-free.
配置时机（方案已定）：会话创建时 apply、会话关闭/替换/应用退出时
restore —— OrchestratorAgent.__init__ 读取特性开关做工具自注入，
按运行 apply 会使工具集与面板脱节。单会话 + 串行运行保证全局
mutation 无竞争。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from webui import config_schema
from webui.events import EventBridge
from webui.serializer import truncate_str

logger = logging.getLogger(__name__)

ANSWER_MAX = 20000  # run_finished.answer 上限（最终交付物，放宽于普通字段）
CANCEL_SENTINEL = "(user cancelled)"  # 与 main.py / tools/ask_user.py 的哨兵一致


class BusyError(RuntimeError):
    """已有任务在运行。 A run is already in progress."""


class NoSessionError(RuntimeError):
    """尚未创建会话。 No session created yet."""


@dataclass
class RunContext:
    run_id: str
    kind: str                       # "run" | "resume"
    task_text: str                  # 任务文本或 resume 的 task_id
    task: asyncio.Task | None = None
    trace_id: str | None = None


@dataclass
class WebSession:
    session_id: str
    overrides: dict[str, Any]
    originals: dict[str, Any]
    orchestrator: Any
    tools: list[Any]
    created_at: float = field(default_factory=time.time)
    turn_count: int = 0
    # prompt_id → (future, question)
    pending_prompts: dict[str, tuple[asyncio.Future, str]] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overrides": dict(self.overrides),
            "turn_count": self.turn_count,
            "created_at": self.created_at,
        }


async def _default_orchestrator_factory(event_bridge: EventBridge) -> tuple[Any, list[Any]]:
    """Mirror run_interactive's construction (main.py:822-828).
    镜像 run_interactive 的构造方式（main.py:822-828）。

    Importing main is deliberate for v1: its module level is
    definition-only (verified), and _build_tools/_build_agentic_service
    are the proven helpers shared with the CLI path.
    v1 刻意 import main：其模块级仅有定义（已核实），且
    _build_tools/_build_agentic_service 是与 CLI 路径共享的成熟助手。
    """
    from agents.orchestrator import OrchestratorAgent
    from llm.client import LLMClient
    from main import _build_agentic_service, _build_tools

    llm_client = LLMClient()
    tools = await _build_tools()
    agentic_service = _build_agentic_service(llm_client)
    orchestrator = OrchestratorAgent(
        llm_client=llm_client,
        tools=tools,
        on_event=event_bridge.on_event,
        interactive=True,  # Web 端可收集输入 → HITL 可用 / web can collect input
        agentic_memory_service=agentic_service,
    )
    return orchestrator, tools


class SessionManager:
    """Singleton on app.state; holds at most ONE WebSession.
    挂在 app.state 的单例；最多持有一个 WebSession。"""

    def __init__(
        self,
        event_bridge: EventBridge,
        orchestrator_factory: Callable[[EventBridge], Awaitable[tuple[Any, list[Any]]]] | None = None,
    ) -> None:
        self.session: WebSession | None = None
        self._event_bridge = event_bridge
        self._orchestrator_factory = orchestrator_factory or _default_orchestrator_factory
        self._run_lock = asyncio.Lock()
        self._current_run: RunContext | None = None
        self._run_counter = 0
        # 装配 bridge 注入点 / wire bridge injection points
        event_bridge.set_run_id_provider(
            lambda: self._current_run.run_id if self._current_run else None
        )
        event_bridge.set_prompt_hook(self._register_prompt)
        event_bridge.set_event_observer(self._maybe_capture_trace_id)

    # ------------------------------------------------------------------
    # 状态 / state
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._current_run is not None

    @property
    def current_run_id(self) -> str | None:
        return self._current_run.run_id if self._current_run else None

    def pending_prompt(self) -> dict[str, Any] | None:
        """首个未决 HITL 提问（供 state 快照，刷新后恢复输入框）。
        First unresolved HITL prompt (for the state snapshot)."""
        if self.session is None:
            return None
        for prompt_id, (future, question) in self.session.pending_prompts.items():
            if not future.done():
                import config

                return {
                    "prompt_id": prompt_id,
                    "question": question,
                    "timeout_seconds": config.HITL_USER_INPUT_TIMEOUT,
                }
        return None

    def state_snapshot(self) -> dict[str, Any]:
        """WS `state` 全量快照。 Full `state` snapshot for WS clients."""
        return {
            "type": "state",
            "session": self.session.describe() if self.session else None,
            "running": self.is_running,
            "run_id": self.current_run_id,
            "pending_prompt": self.pending_prompt(),
            "seq": self._event_bridge.current_seq,
        }

    # ------------------------------------------------------------------
    # 会话生命周期 / session lifecycle
    # ------------------------------------------------------------------

    async def create_session(self, overrides: dict[str, Any]) -> WebSession:
        """关旧会话（恢复配置）→ apply overrides → 构建 orchestrator。
        Close old session (restore config) → apply → build orchestrator."""
        if self.is_running:
            raise BusyError("任务运行中，无法变更会话")
        if self.session is not None:
            await self.close_session()

        originals = config_schema.apply(overrides)
        if overrides:
            logger.info("[webui] 会话配置覆盖 / session overrides applied: %s", overrides)
        try:
            orchestrator, tools = await self._orchestrator_factory(self._event_bridge)
        except Exception:
            config_schema.restore(originals)  # 构建失败即还原 / restore on failure
            raise

        self.session = WebSession(
            session_id=f"s-{uuid.uuid4().hex[:8]}",
            overrides=overrides,
            originals=originals,
            orchestrator=orchestrator,
            tools=tools,
        )
        return self.session

    async def close_session(self) -> None:
        if self.is_running:
            raise BusyError("任务运行中，无法关闭会话")
        if self.session is None:
            return
        config_schema.restore(self.session.originals)
        if self.session.originals:
            logger.info("[webui] 会话配置已还原 / session overrides restored")
        self.session = None

    async def shutdown(self) -> None:
        """App shutdown hook: best-effort restore, no run check.
        应用退出钩子：尽力还原配置，不做运行检查。"""
        if self.session is not None:
            config_schema.restore(self.session.originals)
            self.session = None

    # ------------------------------------------------------------------
    # 运行 / runs
    # ------------------------------------------------------------------

    def start_run(self, text: str) -> str:
        return self._start(kind="run", text=text)

    def start_resume(self, task_id: str) -> str:
        return self._start(kind="resume", text=task_id)

    def _start(self, kind: str, text: str) -> str:
        if self.session is None:
            raise NoSessionError("请先创建会话（配置面板 → 应用并新建会话）")
        if self.is_running:
            raise BusyError("已有任务在运行，请等待完成")

        self._run_counter += 1
        run = RunContext(run_id=f"r-{self._run_counter}", kind=kind, task_text=text)
        self._current_run = run  # 先占位，agent_event 才能带上 run_id
        run.task = asyncio.create_task(self._execute(run))
        return run.run_id

    async def _execute(self, run: RunContext) -> None:
        session = self.session
        assert session is not None
        async with self._run_lock:
            self._event_bridge.emit_system({
                "type": "run_started",
                "run_id": run.run_id,
                "session_id": session.session_id,
                "kind": run.kind,
                "task": run.task_text,
                "overrides": dict(session.overrides),
                "ts": time.time(),
            })
            status = "completed"
            answer: str | None = None
            error: str | None = None
            try:
                if run.kind == "run":
                    answer = await session.orchestrator.run(run.task_text)
                else:
                    answer = await session.orchestrator.resume(run.task_text)
            except ValueError as exc:
                # resume: checkpoint 缺失/已完成 → 聊天级错误而非 500
                # resume: missing/completed checkpoint → chat-level error
                status, error = "failed", str(exc)
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                logger.exception("[webui] run %s failed", run.run_id)

            trace_ref = await self._capture_trace_ref(run)

            if answer is not None:
                answer, _ = truncate_str(answer, ANSWER_MAX)
            self._event_bridge.emit_system({
                "type": "run_finished",
                "run_id": run.run_id,
                "status": status,
                "answer": answer,
                "error": error,
                "trace": trace_ref,
                "ts": time.time(),
            })
            session.turn_count += 1
            self._current_run = None

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def _register_prompt(self, prompt_id: str, future: asyncio.Future, question: str) -> None:
        """EventBridge 在序列化前调用（Future 会被序列化剥离）。
        Called by EventBridge BEFORE serialization strips the Future."""
        session = self.session
        if session is None:
            return
        session.pending_prompts[prompt_id] = (future, question)
        # 超时/取消后自动清理 / auto-cleanup once resolved by any path
        future.add_done_callback(
            lambda _f: session.pending_prompts.pop(prompt_id, None)
        )

    def resolve_prompt(self, prompt_id: str, text: str) -> bool:
        """Resolve a pending ask_user Future (first writer wins, mirrors
        main.py's done() guards). 解决未决提问（先写者赢）。"""
        if self.session is None:
            return False
        entry = self.session.pending_prompts.get(prompt_id)
        if entry is None:
            return False
        future, _question = entry
        if future.done():
            return False
        future.set_result(text)
        return True

    def cancel_prompt(self, prompt_id: str) -> bool:
        return self.resolve_prompt(prompt_id, CANCEL_SENTINEL)

    # ------------------------------------------------------------------
    # trace 关联 / trace correlation
    # ------------------------------------------------------------------

    def _maybe_capture_trace_id(self, _event: str) -> None:
        """Lazily read the root span's trace_id from the live TracingBridge.
        从运行中的 TracingBridge 惰性读取根 span 的 trace_id。

        Multicast calls webui BEFORE the bridge (orchestrator.py:135-136),
        so the span exists on every event after task_start. Private-attr
        peek is confined to this one method and fully getattr-guarded.
        multicast 先调 webui 再调 bridge，因此 task_start 之后的每个事件
        span 都已存在。私有属性窥视仅限本方法且全程 getattr 守卫。"""
        run = self._current_run
        if run is None or run.trace_id is not None or self.session is None:
            return
        try:
            bridge = getattr(self.session.orchestrator, "_tracing_bridge", None)
            span = getattr(bridge, "_root_span", None)
            if span is None:
                return
            ctx = span.get_span_context()
            run.trace_id = format(ctx.trace_id, "032x")
        except Exception:
            pass  # tracing 关闭/异常 → 静默降级 / degrade silently

    async def _capture_trace_ref(self, run: RunContext) -> dict[str, str] | None:
        """Flush spans then build the trace link (file backend only).
        刷盘 span 后构造 trace 链接（仅 file 后端）。"""
        import config

        if run.trace_id is None or not getattr(config, "TRACING_ENABLED", False):
            return None
        if getattr(config, "TRACING_BACKEND", "") != "file":
            return None
        try:
            from opentelemetry import trace as otel_trace

            provider = otel_trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                # BatchSpanProcessor 5s 刷盘节奏 → 显式 flush（同步调用进线程）
                # explicit flush (sync call moved to a thread)
                await asyncio.to_thread(provider.force_flush, 3000)
        except Exception:
            logger.debug("[webui] trace force_flush failed", exc_info=True)
        return {"trace_id": run.trace_id, "url": f"/traces/{run.trace_id}"}
