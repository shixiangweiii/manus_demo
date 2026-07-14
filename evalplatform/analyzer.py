"""
Aggregate analyzer — cross-run statistics, findings, and optimization advice.
聚合分析器 —— 跨多次运行的统计、结论与优化建议。

Three layers / 三层：
  1. Deterministic stats（确定性统计）: per-mode/difficulty success, failure
     Pareto, token/time costs, trend across runs.
  2. Rule-based suggestions（规则建议）: evidence-driven, each mapped to a
     concrete config knob or command in this repo.
     每条建议都落到本仓库的具体配置项或命令。
  3. Optional LLM insight（可选 LLM 洞察）: degrades to empty when no LLM.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from evalplatform.models import AggregateAnalysis, EvalRunRecord, Suggestion

logger = logging.getLogger(__name__)


# ======================================================================
# Stats extraction / 统计抽取
# ======================================================================

def _iter_task_results(run: EvalRunRecord):
    """Yield (mode, result_dict) for all per-task results in a run."""
    for mode, metrics in (run.metrics_by_mode or {}).items():
        for r in metrics.get("results") or []:
            yield mode, r


def _success_value(result: dict[str, Any]) -> float:
    """
    Pass@k-aware fractional success for one stored result (review V2 fix):
    with repeat>1 the stored execution.task_success is only the LAST trial's
    snapshot — pass_at_k is the authoritative rate.
    单条结果的 pass@k 感知成功值（评审 V2 修复）：repeat>1 时存储的
    execution.task_success 只是最后一次试验的快照，pass_at_k 才是权威口径。
    """
    trial_count = int(result.get("trial_count") or 1)
    pass_at_k = result.get("pass_at_k")
    if trial_count > 1 and pass_at_k is not None:
        return float(pass_at_k)
    execution = result.get("execution") or {}
    return 1.0 if execution.get("task_success") else 0.0


def compute_stats(runs: list[EvalRunRecord]) -> dict[str, Any]:
    """Deterministic cross-run statistics. 跨运行确定性统计。"""
    per_mode: dict[str, dict[str, Any]] = {}
    per_difficulty: dict[str, list[float]] = {}
    failure_pareto: dict[str, int] = {}
    run_success_series: list[tuple[float, float, str]] = []  # (started_at, success_rate, run_id)
    total_tasks = 0
    total_success = 0.0
    token_values: list[float] = []
    judge_overrides = 0
    max_attack_success_rate = 0.0
    subagent_seen = False

    for run in runs:
        run_tasks = 0
        run_success = 0.0
        for mode, metrics in (run.metrics_by_mode or {}).items():
            bucket = per_mode.setdefault(mode, {
                "tasks": 0, "success": 0, "overall_scores": [], "planning_scores": [],
                "execution_scores": [], "efficiency_scores": [], "tokens": [],
                "classification": [], "replans": [],
            })
            n = int(metrics.get("total_tasks") or 0)
            # task_success_rate 是 pass@k 感知的均值 → sr*n 即分数化成功和，
            # 不做 round（评审 V2 修复：round 会把 0.4 压成 0、0.6 抬成 1）
            sr = float(metrics.get("task_success_rate") or 0.0)
            bucket["tasks"] += n
            bucket["success"] += sr * n
            bucket["overall_scores"].append(float(metrics.get("avg_overall_score") or 0))
            bucket["planning_scores"].append(float(metrics.get("avg_planning_score") or 0))
            bucket["execution_scores"].append(float(metrics.get("avg_execution_score") or 0))
            bucket["efficiency_scores"].append(float(metrics.get("avg_efficiency_score") or 0))
            bucket["tokens"].append(float(metrics.get("avg_total_tokens") or 0))
            bucket["classification"].append(float(metrics.get("classification_accuracy") or 0))
            bucket["replans"].append(float(metrics.get("avg_replan_count") or 0))
            judge_overrides += int(metrics.get("judge_override_count") or 0)
            max_attack_success_rate = max(
                max_attack_success_rate, float(metrics.get("attack_success_rate") or 0.0),
            )
            # 按"有任务的模式"计入，0 token 也是合法样本（truthiness 会把
            # 离线/全失败运行剔除，抬高均值）/ include legit zero-token modes
            if n:
                token_values.append(float(metrics.get("avg_total_tokens") or 0.0))
            run_tasks += n
            run_success += sr * n

            for cat, count in (metrics.get("failure_distribution") or {}).items():
                failure_pareto[cat] = failure_pareto.get(cat, 0) + int(count)

        for _mode, r in _iter_task_results(run):
            execution = r.get("execution") or {}
            diff = str(r.get("task_difficulty") or "unknown")
            per_difficulty.setdefault(diff, []).append(_success_value(r))
            if int(execution.get("subagent_calls") or 0) > 0:
                subagent_seen = True

        total_tasks += run_tasks
        total_success += run_success
        if run_tasks:
            run_success_series.append((run.started_at or run.created_at, run_success / run_tasks, run.run_id))

    # 汇总 per-mode 平均
    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    mode_summary = {
        mode: {
            "tasks": b["tasks"],
            "success_rate": (b["success"] / b["tasks"]) if b["tasks"] else 0.0,
            "avg_overall_score": _avg(b["overall_scores"]),
            "avg_planning_score": _avg(b["planning_scores"]),
            "avg_execution_score": _avg(b["execution_scores"]),
            "avg_efficiency_score": _avg(b["efficiency_scores"]),
            "avg_tokens": _avg(b["tokens"]),
            "classification_accuracy": _avg(b["classification"]),
            "avg_replan_count": _avg(b["replans"]),
        }
        for mode, b in per_mode.items()
    }

    difficulty_summary = {
        diff: {"tasks": len(flags), "success_rate": sum(flags) / len(flags)}
        for diff, flags in per_difficulty.items() if flags
    }

    # 趋势：按时间排序的运行级成功率序列
    run_success_series.sort(key=lambda t: t[0])
    trend = "insufficient_data"
    if len(run_success_series) >= 2:
        first, last = run_success_series[0][1], run_success_series[-1][1]
        if last - first > 0.05:
            trend = "improving"
        elif first - last > 0.05:
            trend = "declining"
        else:
            trend = "flat"

    return {
        "run_count": len(runs),
        "total_task_executions": total_tasks,
        "overall_success_rate": (total_success / total_tasks) if total_tasks else 0.0,
        "per_mode": mode_summary,
        "per_difficulty": difficulty_summary,
        "failure_pareto": sorted(failure_pareto.items(), key=lambda kv: -kv[1]),
        "trend": trend,
        "run_success_series": [
            {"run_id": rid, "started_at": ts, "success_rate": sr}
            for ts, sr, rid in run_success_series
        ],
        "avg_tokens_per_task": (sum(token_values) / len(token_values)) if token_values else 0.0,
        "judge_override_count": judge_overrides,
        "subagent_seen": subagent_seen,
        "max_attack_success_rate": max_attack_success_rate,
    }


# ======================================================================
# Rule-based suggestions / 规则建议
# ======================================================================

# 失败类别 → (严重级, 建议动作)。动作均指向本仓库的具体配置或命令。
_FAILURE_ADVICE: dict[str, tuple[str, str]] = {
    "tool_execution_error": (
        "warning",
        "工具执行失败占比高：检查网络依赖工具（web_search/fetch_url）的可用性与重试；"
        "对不稳定外部源任务建议移入独立 live_web 类评测集单独统计。",
    ),
    "max_iteration_exceeded": (
        "warning",
        "ReAct 迭代超限：适当提高 MAX_REACT_ITERATIONS，或降低 SEARCH_CONVERGENCE_THRESHOLD "
        "让收敛提示更早介入；同时检查任务描述是否可分解（改走 complex/emergent 路由）。",
    ),
    "llm_call_failure": (
        "critical",
        "LLM 调用失败：确认 LLM_API_KEY / LLM_BASE_URL 配置与限流状况；必要时降低并发或更换模型。",
    ),
    "parse_failure": (
        "warning",
        "LLM 输出解析失败：检查模型是否支持 JSON mode；可换用结构化输出更稳的模型（LLM_MODEL）。",
    ),
    "classification_error": (
        "warning",
        "任务路由错误：运行 python -m evolution.calibrate 离线校准 "
        "CLASSIFIER_SIMPLE_THRESHOLD / CLASSIFIER_COMPLEX_THRESHOLD。",
    ),
    "false_positive": (
        "warning",
        "反思误判通过：收紧 ReflectorAgent 的退出标准（降低 REFLECTOR_TEMPERATURE、细化成功判据）。",
    ),
    "subagent_failed": (
        "info",
        "SubAgent 失败：检查 SUBAGENT_MAX_CONCURRENT 与子任务超时设置，缩小子任务粒度。",
    ),
}


def build_suggestions(stats: dict[str, Any]) -> list[Suggestion]:
    """Evidence-driven optimization suggestions. 基于数据证据的优化建议。"""
    suggestions: list[Suggestion] = []
    overall_sr = stats.get("overall_success_rate", 0.0)
    total = stats.get("total_task_executions", 0)

    if total == 0:
        return [Suggestion(
            title="暂无可分析的评测数据",
            severity="info",
            evidence="所选运行不包含任何任务结果",
            action="先在平台上执行至少一次评测运行，再生成聚合分析。",
        )]

    # 1) 整体成功率
    if overall_sr < 0.5:
        suggestions.append(Suggestion(
            title="整体成功率过低",
            severity="critical",
            evidence=f"总体成功率 {overall_sr:.1%}（{total} 次任务执行）",
            action="优先排查基础设施：LLM 连通性、工具可用性；用 evaluation 的 smoke_reasoning "
                   "套件确认框架本身正常，再回归生成集。",
        ))
    elif overall_sr < 0.7:
        suggestions.append(Suggestion(
            title="整体成功率偏低",
            severity="warning",
            evidence=f"总体成功率 {overall_sr:.1%}",
            action="查看失败分布 Pareto，优先修复占比最高的失败类别。",
        ))

    # 2) 失败类别 Pareto → 定向建议
    for cat, count in (stats.get("failure_pareto") or [])[:3]:
        advice = _FAILURE_ADVICE.get(cat)
        if advice:
            severity, action = advice
            suggestions.append(Suggestion(
                title=f"高频失败类别：{cat}",
                severity=severity,
                evidence=f"累计出现 {count} 次",
                action=action,
            ))

    # 3) 模式对比 → 路由建议
    per_mode = stats.get("per_mode") or {}
    if len(per_mode) >= 2:
        ranked = sorted(per_mode.items(), key=lambda kv: -kv[1]["success_rate"])
        best_mode, best = ranked[0]
        worst_mode, worst = ranked[-1]
        if best["tasks"] >= 3 and best["success_rate"] - worst["success_rate"] > 0.15:
            suggestions.append(Suggestion(
                title=f"规划模式差异显著：{best_mode} 优于 {worst_mode}",
                severity="info",
                evidence=(
                    f"{best_mode} 成功率 {best['success_rate']:.1%} vs "
                    f"{worst_mode} {worst['success_rate']:.1%}"
                ),
                action=f"对该类工作负载可设置 PLAN_MODE={best_mode}，或校准分类器让 auto 路由偏向 {best_mode}。",
            ))

    # 4) 分类准确率。恒等于 0 视为"未测量"（评测运行强制规划模式时，
    #    aggregate_results 只统计非强制分类的任务，通常得 0）而非"差"。
    class_accs = [m["classification_accuracy"] for m in per_mode.values() if m["tasks"]]
    avg_class_acc = (sum(class_accs) / len(class_accs)) if class_accs else 0.0
    if 0.0 < avg_class_acc < 0.7:
        suggestions.append(Suggestion(
            title="复杂度分类准确率偏低",
            severity="warning",
            evidence=f"平均分类准确率 {avg_class_acc:.1%}",
            action="运行 python -m evolution.calibrate 生成阈值建议，并按输出手动应用 "
                   "CLASSIFIER_SIMPLE_THRESHOLD / CLASSIFIER_COMPLEX_THRESHOLD。",
        ))

    # 5) 难度短板
    per_diff = stats.get("per_difficulty") or {}
    hard = per_diff.get("hard")
    if hard and hard["tasks"] >= 2 and hard["success_rate"] < 0.5:
        action = "对 hard 任务启用委派与目标驱动：SUBAGENT_ENABLED=true"
        if not stats.get("subagent_seen"):
            action += "（当前运行未观察到任何 SubAgent 调用）"
        action += "；探索类任务可开 ENABLE_GOAL_DRIVEN_PLANNER=true。"
        suggestions.append(Suggestion(
            title="hard 难度任务成功率低",
            severity="warning",
            evidence=f"hard 成功率 {hard['success_rate']:.1%}（{hard['tasks']} 次）",
            action=action,
        ))

    # 6) Token 成本
    avg_tokens = stats.get("avg_tokens_per_task", 0.0)
    if avg_tokens > 50000:
        suggestions.append(Suggestion(
            title="单任务 Token 消耗偏高",
            severity="info",
            evidence=f"平均每任务约 {avg_tokens:.0f} tokens",
            action="降低 TOOL_RESULT_TRUNCATION_LIMIT、调低 MAX_CONTEXT_TOKENS 触发更早的上下文压缩，"
                   "或减少 MAX_REACT_ITERATIONS。",
        ))

    # 7) 重规划频率
    replans = [m["avg_replan_count"] for m in per_mode.values() if m["tasks"]]
    if replans and max(replans) > 1.0:
        suggestions.append(Suggestion(
            title="重规划次数偏多",
            severity="info",
            evidence=f"最高模式平均重规划 {max(replans):.2f} 次/任务",
            action="检查 Planner 生成计划的粒度是否过细/过粗；必要时调低 PLANNER_TEMPERATURE 提升计划稳定性。",
        ))

    # 8) 趋势
    if stats.get("trend") == "declining":
        series = stats.get("run_success_series") or []
        evidence = " → ".join(f"{p['success_rate']:.0%}" for p in series[-4:])
        suggestions.append(Suggestion(
            title="成功率呈下降趋势",
            severity="warning",
            evidence=f"最近运行成功率序列：{evidence}",
            action="怀疑回归：用 evaluation.eval_cli --save-baseline 固化基线，"
                   "后续运行 --baseline --fail-on-regression 做回归门禁。",
        ))

    # 9) 安全：攻击用例得逞
    if stats.get("max_attack_success_rate", 0.0) > 0:
        suggestions.append(Suggestion(
            title="存在攻击用例得逞",
            severity="critical",
            evidence=f"攻击成功率最高达 {stats['max_attack_success_rate']:.1%}",
            action="启用安全护栏：GUARDRAILS_ENABLED=true（工具拦截/注入中和/输出脱敏），"
                   "并用 evaluation 的 red_team 套件做 guardrails_on vs baseline A/B 验证。",
        ))

    # 10) 裁判兜底频繁 → 评测集本身的关键词标准过严
    if stats.get("judge_override_count", 0) >= max(2, total // 5):
        suggestions.append(Suggestion(
            title="LLM 裁判频繁覆盖关键词判定",
            severity="info",
            evidence=f"judge override 共 {stats['judge_override_count']} 次",
            action="生成的评测集 must_include_keywords 可能过严：重新生成评测集并在目标描述中"
                   "要求“关键词必须是答案必然包含的具体词汇”。",
        ))

    if not suggestions:
        suggestions.append(Suggestion(
            title="各项指标健康",
            severity="info",
            evidence=f"总体成功率 {overall_sr:.1%}，无显著薄弱维度",
            action="可提高评测集难度或扩大任务规模（增大生成任务数 / repeat）继续压测。",
        ))
    return suggestions


def build_findings(stats: dict[str, Any]) -> list[str]:
    """Human-readable one-line findings. 一行式结论。"""
    findings: list[str] = []
    findings.append(
        f"共分析 {stats['run_count']} 次运行、{stats['total_task_executions']} 次任务执行，"
        f"总体成功率 {stats['overall_success_rate']:.1%}。"
    )
    for mode, m in sorted((stats.get("per_mode") or {}).items()):
        findings.append(
            f"模式 {mode}：成功率 {m['success_rate']:.1%}，综合评分 {m['avg_overall_score']:.3f}，"
            f"平均 {m['avg_tokens']:.0f} tokens/任务。"
        )
    for diff, d in sorted((stats.get("per_difficulty") or {}).items()):
        findings.append(f"难度 {diff}：成功率 {d['success_rate']:.1%}（{d['tasks']} 次）。")
    pareto = stats.get("failure_pareto") or []
    if pareto:
        top = ", ".join(f"{cat}×{count}" for cat, count in pareto[:3])
        findings.append(f"失败类别 Top3：{top}。")
    trend_label = {
        "improving": "上升", "declining": "下降", "flat": "持平",
        "insufficient_data": "样本不足",
    }.get(stats.get("trend", ""), "未知")
    findings.append(f"跨运行成功率趋势:{trend_label}。")
    return findings


# ======================================================================
# Analyzer / 分析器
# ======================================================================

_LLM_INSIGHT_PROMPT = """你是智能体系统的评测分析专家。下面是一个多智能体系统（规划-执行-反思架构，
支持 simple/complex/emergent 三种规划模式）多次评测运行的聚合统计 JSON。
请给出 3-5 条最有价值的深度观察与改进方向（中文，直接输出要点列表，不要重复统计数字本身，
重点是数字背后的模式和因果假设）：

{stats_json}
"""


class ReportAnalyzer:
    """Aggregate multiple runs into an AggregateAnalysis.
    将多次运行聚合为一份分析报告。"""

    def __init__(self, llm_client: Any | None = None):
        self._llm_client = llm_client

    async def analyze(
        self,
        runs: list[EvalRunRecord],
        use_llm: bool = False,
    ) -> AggregateAnalysis:
        stats = compute_stats(runs)
        analysis = AggregateAnalysis(
            run_ids=[r.run_id for r in runs],
            stats=stats,
            findings=build_findings(stats),
            suggestions=build_suggestions(stats),
        )

        if use_llm and stats.get("total_task_executions"):
            analysis.llm_insight = await self._llm_insight(stats)

        analysis.markdown = render_analysis_markdown(analysis)
        return analysis

    async def _llm_insight(self, stats: dict[str, Any]) -> str:
        """Optional LLM deep-dive; failures degrade to empty string.
        可选 LLM 深度分析；失败降级为空串，不影响确定性结果。"""
        import json
        try:
            if self._llm_client is None:
                from llm.client import LLMClient
                self._llm_client = LLMClient()
            slim = {k: v for k, v in stats.items() if k != "run_success_series"}
            prompt = _LLM_INSIGHT_PROMPT.format(
                stats_json=json.dumps(slim, ensure_ascii=False, indent=1),
            )
            return await self._llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=1200,
                caller_tag="evalplatform.analyzer",
            )
        except Exception as exc:
            logger.warning("[ReportAnalyzer] LLM insight failed: %s", exc)
            return ""


def render_analysis_markdown(analysis: AggregateAnalysis) -> str:
    """Render the aggregate analysis as Markdown. 渲染聚合分析 Markdown。"""
    stats = analysis.stats
    lines: list[str] = []
    lines.append("# 聚合评测分析")
    lines.append("")
    lines.append(f"- **分析 ID**：`{analysis.analysis_id}`")
    lines.append(f"- **覆盖运行**：{', '.join(f'`{r}`' for r in analysis.run_ids) or '-'}")
    lines.append(f"- **生成时间**：{datetime.fromtimestamp(analysis.created_at).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    lines.append("## 结论")
    lines.append("")
    for f in analysis.findings:
        lines.append(f"- {f}")
    lines.append("")

    per_mode = stats.get("per_mode") or {}
    if per_mode:
        lines.append("## 模式对比")
        lines.append("")
        lines.append("| 模式 | 任务数 | 成功率 | 综合评分 | 规划 | 执行 | 效率 | 分类准确率 | 平均Token |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for mode, m in sorted(per_mode.items()):
            lines.append(
                f"| {mode} | {m['tasks']} | {m['success_rate']:.1%} | {m['avg_overall_score']:.3f} "
                f"| {m['avg_planning_score']:.3f} | {m['avg_execution_score']:.3f} "
                f"| {m['avg_efficiency_score']:.3f} | {m['classification_accuracy']:.1%} "
                f"| {m['avg_tokens']:.0f} |"
            )
        lines.append("")

    series = stats.get("run_success_series") or []
    if len(series) >= 2:
        lines.append("## 成功率趋势")
        lines.append("")
        for p in series:
            when = datetime.fromtimestamp(p["started_at"]).strftime("%m-%d %H:%M") if p["started_at"] else "-"
            bar = "█" * int(round(p["success_rate"] * 20))
            lines.append(f"- `{p['run_id']}` {when}  {bar} {p['success_rate']:.1%}")
        lines.append("")

    pareto = stats.get("failure_pareto") or []
    if pareto:
        lines.append("## 失败类别 Pareto")
        lines.append("")
        for cat, count in pareto:
            lines.append(f"- `{cat}`：{count} 次")
        lines.append("")

    lines.append("## 优化建议")
    lines.append("")
    icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
    for s in analysis.suggestions:
        lines.append(f"### {icon.get(s.severity, '🔵')} {s.title}")
        lines.append("")
        lines.append(f"- **证据**：{s.evidence}")
        lines.append(f"- **建议**：{s.action}")
        lines.append("")

    if analysis.llm_insight:
        lines.append("## LLM 深度洞察")
        lines.append("")
        lines.append(analysis.llm_insight.strip())
        lines.append("")

    lines.append("---")
    lines.append("*manus_demo 评测平台 · 聚合分析*")
    return "\n".join(lines)
