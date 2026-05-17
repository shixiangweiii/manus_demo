"""
LLM-as-Judge fallback for open-ended answer quality assessment.
LLM 兜底裁判：对开放题答案做语义评估。

When a benchmark task's must_include_keywords check fails, this judge is
invoked to assess whether the agent's answer semantically satisfies the
ground-truth success_criteria — allowing different wording, synonyms,
paraphrasing. Cost-controlled: only triggered when keyword check has
already failed AND the GT provides a rubric (success_criteria).

借鉴 Anthropic *Demystifying evals for AI agents* (2026-01): 在严格匹配失败
时使用 LLM judge 做语义兜底，避免开放题被关键词缺失误判为失败。

参考:
- Anthropic, "Demystifying evals for AI agents" (2026-01)
- LLM-as-a-Judge survey: arXiv:2306.05685 等
"""

from __future__ import annotations

import json
import logging
import re
from typing import Tuple

from llm.client import LLMClient

logger = logging.getLogger(__name__)


JUDGE_SYSTEM_PROMPT = """\
You are a strict but fair evaluator of AI agent task outcomes. Your sole \
job is to judge whether an agent's answer semantically satisfies the \
provided success criteria — even if exact keywords differ.

Be objective: a paraphrase or synonym that fully captures the required \
information should pass; a vague or off-topic answer should fail. \
Do not award partial credit — output a binary pass/fail with a confidence.
"""


def _extract_json_block(text: str) -> dict:
    """
    Tolerate ```json fences and stray prose around the JSON object.
    解析 LLM 返回的 JSON，容忍 ```json fence 和前后多余文本。
    """
    # Strip code fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first {...} block
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"could not extract JSON from judge output: {text[:200]}")


async def judge_answer_quality(
    llm_client: LLMClient,
    task: str,
    answer: str,
    success_criteria: str,
    missed_keywords: list[str],
) -> Tuple[bool, float, str]:
    """
    Assess whether the agent's answer semantically satisfies success_criteria.

    Returns:
        (passes, confidence, reasoning)
        - passes: True if the answer semantically meets the criteria
        - confidence: 0.0-1.0; runner uses >= 0.7 to accept override
        - reasoning: one-sentence rationale (for transparency)

    The judge is intentionally constrained: low-temperature, JSON-only
    output, short max_tokens. Failure to parse → return (False, 0.0, error).

    判断 agent 答案是否在语义上满足成功标准。仅在 must_include 失败时调用。
    """
    # Truncate answer to control prompt cost (3000 chars ≈ 750 tokens)
    truncated_answer = answer[:3000] + ("...[truncated]" if len(answer) > 3000 else "")

    user_prompt = (
        f"Task asked of the agent:\n{task}\n\n"
        f"Success criteria (ground truth rubric):\n{success_criteria}\n\n"
        f"Agent's answer:\n{truncated_answer}\n\n"
        f"Note: a strict keyword check missed these terms: {missed_keywords}\n\n"
        "Does the agent's answer semantically satisfy the success criteria, "
        "allowing for paraphrasing, synonyms, or different wording? "
        "Be strict — do not pass vague or partial answers.\n\n"
        "Respond with ONLY valid JSON in this exact shape:\n"
        '{"passes": true|false, "confidence": 0.0-1.0, "reasoning": "<one sentence>"}'
    )

    try:
        raw = await llm_client.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        data = _extract_json_block(raw)

        passes = bool(data.get("passes", False))
        confidence = float(data.get("confidence", 0.0))
        reasoning = str(data.get("reasoning", "")).strip()[:300]

        # Clamp confidence into valid range
        confidence = max(0.0, min(1.0, confidence))

        return passes, confidence, reasoning

    except Exception as exc:
        logger.warning("[LLMJudge] judge_answer_quality failed: %s", exc)
        return False, 0.0, f"[judge error] {exc}"
