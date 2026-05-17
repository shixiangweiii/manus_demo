"""
SimulatedUser — TauBench-style scripted user for HITL benchmark evaluation.
模拟用户 —— 为 HITL 评测任务提供脚本化的用户回答。

When a benchmark task is tagged "hitl", the runner can't pump real user input
into the ask_user tool's asyncio.Future. SimulatedUser provides predefined
responses (from GroundTruth.simulated_responses) that auto-resolve the Future,
so evaluation runs fully unattended.

设计要点：
- 借鉴 TauBench 的 user simulator 思路
- 每次 ask_user 触发时按 FIFO 弹出一个预设回答
- 回答用完后兜底返回 "I don't know"，让 LLM 自主推理
- 拦截器在 EvaluationRunner 中以 on_event 装饰器形式接入

参考: τ-bench (Sierra Research, 2024) — A Benchmark for Tool-Agent-User Interaction
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SimulatedUser:
    """
    Scripted user that answers ask_user prompts during evaluation.
    按脚本回答 ask_user 提问的模拟用户。

    Usage in EvaluationRunner:
        sim = SimulatedUser(task.ground_truth.simulated_responses or [])
        def intercept(event, data):
            if event == "ask_user_prompt":
                response = sim.respond(data.get("question", ""))
                data["response_future"].set_result(response)
            probe.on_event(event, data)
        orchestrator = OrchestratorAgent(on_event=intercept, interactive=True, ...)
    """

    FALLBACK_RESPONSE = "I don't know — please proceed with your best judgment."

    def __init__(self, responses: list[str] | None = None):
        self._responses: list[str] = list(responses) if responses else []
        self._calls: int = 0

    def respond(self, question: str) -> str:
        """
        Return the next scripted response, or a fallback when exhausted.

        Returns the FIFO-popped response; if no more scripted responses,
        returns FALLBACK_RESPONSE so the LLM gracefully proceeds autonomously
        (matching real user behavior when they don't have the answer).
        """
        self._calls += 1
        if self._responses:
            answer = self._responses.pop(0)
            logger.info(
                "[SimulatedUser] Call #%d, scripted response: %s (question: %s)",
                self._calls, answer[:80], question[:80],
            )
            return answer
        logger.info(
            "[SimulatedUser] Call #%d, no more scripted responses, returning fallback (question: %s)",
            self._calls, question[:80],
        )
        return self.FALLBACK_RESPONSE

    @property
    def call_count(self) -> int:
        """Number of times respond() has been invoked."""
        return self._calls

    @property
    def remaining(self) -> int:
        """Number of scripted responses still queued."""
        return len(self._responses)
