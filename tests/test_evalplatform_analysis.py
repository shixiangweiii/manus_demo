"""
Tests for single-run reporting + cross-run aggregate analysis.
单次运行报告与跨运行聚合分析测试。
"""

from __future__ import annotations

from evaluation.metrics import TaskDifficulty
from evalplatform.analyzer import (
    ReportAnalyzer,
    build_suggestions,
    compute_stats,
)
from evalplatform.models import EvalSetStatus, GeneratedEvalSet
from evalplatform.reporting import build_report
from tests.helpers.evalplatform_fixtures import (
    make_passk_result,
    make_run,
    make_run_from_results,
)


# ======================================================================
# Single-run report / 单次运行报告
# ======================================================================

class TestBuildReport:
    def test_basic_report(self):
        run = make_run("run_1", [True, True, False])
        evalset = GeneratedEvalSet(
            evalset_id="es_test", name="示例评测集", doc_filename="guide.md",
            status=EvalSetStatus.READY,
        )
        report = build_report(run, evalset)
        assert report.run_id == "run_1"
        assert report.mode_summaries[0]["total_tasks"] == 3
        assert len(report.per_task_rows) == 3
        # markdown 关键章节
        assert "# 评测报告" in report.markdown
        assert "## 模式概要" in report.markdown
        assert "## 逐任务结果" in report.markdown
        assert "guide.md" in report.markdown

    def test_weak_dimensions_flagged(self):
        run = make_run("run_2", [False, False, False, True])
        report = build_report(run)
        assert any("任务成功率偏低" in w for w in report.weak_dimensions)
        assert "## 薄弱维度" in report.markdown

    def test_failed_run_shows_error(self):
        run = make_run("run_3", [True])
        from evalplatform.models import RunStatus
        run.status = RunStatus.FAILED
        run.error = "LLM connection refused"
        report = build_report(run)
        assert any("失败结束" in h for h in report.highlights)
        assert "LLM connection refused" in report.markdown

    def test_best_worst_highlighted(self):
        run = make_run("run_4", [True, False])
        report = build_report(run)
        assert any(h.startswith("最佳任务") for h in report.highlights)
        assert any(h.startswith("最差任务") for h in report.highlights)

    def test_forced_mode_classification_shown_as_dash(self):
        # review V4b: forced-mode runs have classification_accuracy==0.0 (not
        # measured) — render "-" in the mode table, never a misleading "0.0%"
        run = make_run("run_c", [True, True])
        report = build_report(run)
        assert report.mode_summaries[0]["classification_measured"] is False
        # the mode-summary row renders the classification column as "-"
        summary_rows = [
            ln for ln in report.markdown.splitlines()
            if ln.startswith("| simple |")
        ]
        assert summary_rows and " | - | " in summary_rows[0]

    def test_no_reflection_no_weak_warning(self):
        # review V4a: reflection_accuracy==0.0 with no observed reflection must
        # NOT be flagged as a weak dimension
        run = make_run("run_r", [True, True, True])
        report = build_report(run)
        assert not any("反思准确率偏低" in w for w in report.weak_dimensions)


# ======================================================================
# Aggregate stats / 聚合统计
# ======================================================================

class TestComputeStats:
    def test_totals_and_per_mode(self):
        runs = [
            make_run("r1", [True, True], started_at=100.0),
            make_run("r2", [True, False], mode="complex", started_at=200.0),
        ]
        stats = compute_stats(runs)
        assert stats["run_count"] == 2
        assert stats["total_task_executions"] == 4
        assert stats["overall_success_rate"] == 0.75
        assert set(stats["per_mode"]) == {"simple", "complex"}
        assert stats["per_mode"]["simple"]["success_rate"] == 1.0
        assert stats["per_mode"]["complex"]["success_rate"] == 0.5

    def test_per_difficulty(self):
        runs = [make_run("r1", [True, False], difficulty=TaskDifficulty.HARD)]
        stats = compute_stats(runs)
        assert stats["per_difficulty"]["hard"]["tasks"] == 2
        assert stats["per_difficulty"]["hard"]["success_rate"] == 0.5

    def test_trend_improving_and_declining(self):
        improving = [
            make_run("r1", [False, False], started_at=100.0),
            make_run("r2", [True, True], started_at=200.0),
        ]
        declining = [
            make_run("r3", [True, True], started_at=100.0),
            make_run("r4", [False, False], started_at=200.0),
        ]
        assert compute_stats(improving)["trend"] == "improving"
        assert compute_stats(declining)["trend"] == "declining"
        assert compute_stats([make_run("r5", [True])])["trend"] == "insufficient_data"

    def test_failure_pareto(self):
        runs = [make_run("r1", [False, False], failure_category="tool_execution_error")]
        stats = compute_stats(runs)
        assert stats["failure_pareto"][0][0] == "tool_execution_error"

    def test_passk_success_not_rounded(self):
        # review V2a: repeat>1 fractional pass@k must NOT round to 0/1 per mode
        run = make_run_from_results("rk", [
            make_passk_result("h1", passes=2, trials=3, last_trial_success=False),
        ])
        stats = compute_stats([run])
        # pass@k = 0.667 → mode success_rate ≈ 0.667, not round(0.667)=1 → 100%
        assert abs(stats["per_mode"]["simple"]["success_rate"] - 2 / 3) < 1e-6
        assert abs(stats["overall_success_rate"] - 2 / 3) < 1e-6

    def test_passk_difficulty_matches_mode(self):
        # review V2b: per_difficulty must use pass@k, not last-trial task_success
        run = make_run_from_results("rk2", [
            make_passk_result("h1", passes=2, trials=3, last_trial_success=False,
                              difficulty=TaskDifficulty.HARD),
        ])
        stats = compute_stats([run])
        # last trial failed, but pass@k=0.667 → difficulty rate must agree, not 0%
        assert abs(stats["per_difficulty"]["hard"]["success_rate"] - 2 / 3) < 1e-6

    def test_zero_token_mode_counted(self):
        # review V13: a legit 0-token mode is included in avg_tokens_per_task
        run = make_run("rz", [True, True], tokens=0)
        stats = compute_stats([run])
        assert stats["avg_tokens_per_task"] == 0.0


# ======================================================================
# Suggestions / 优化建议
# ======================================================================

class TestSuggestions:
    def test_empty_data(self):
        suggestions = build_suggestions(compute_stats([]))
        assert suggestions[0].title == "暂无可分析的评测数据"

    def test_low_success_rate_critical(self):
        stats = compute_stats([make_run("r1", [False] * 5)])
        suggestions = build_suggestions(stats)
        assert any(s.severity == "critical" and "成功率过低" in s.title for s in suggestions)

    def test_failure_category_advice(self):
        stats = compute_stats([make_run("r1", [False, False, False], failure_category="max_iteration_exceeded")])
        suggestions = build_suggestions(stats)
        matched = [s for s in suggestions if "max_iteration_exceeded" in s.title]
        assert matched and "MAX_REACT_ITERATIONS" in matched[0].action

    def test_mode_gap_suggestion(self):
        runs = [
            make_run("r1", [True] * 4, mode="simple"),
            make_run("r2", [False, False, True, True], mode="emergent"),
        ]
        suggestions = build_suggestions(compute_stats(runs))
        assert any("规划模式差异显著" in s.title and "PLAN_MODE=simple" in s.action for s in suggestions)

    def test_hard_difficulty_suggestion(self):
        stats = compute_stats([make_run("r1", [False, False, True], difficulty=TaskDifficulty.HARD)])
        suggestions = build_suggestions(stats)
        assert any("hard 难度" in s.title and "SUBAGENT_ENABLED" in s.action for s in suggestions)

    def test_declining_trend_suggestion(self):
        runs = [
            make_run("r1", [True, True], started_at=100.0),
            make_run("r2", [False, False], started_at=200.0),
        ]
        suggestions = build_suggestions(compute_stats(runs))
        assert any("下降趋势" in s.title for s in suggestions)

    def test_attack_success_suggestion(self):
        stats = compute_stats([make_run("r1", [True])])
        stats["max_attack_success_rate"] = 0.5
        suggestions = build_suggestions(stats)
        assert any("攻击用例得逞" in s.title and "GUARDRAILS_ENABLED" in s.action for s in suggestions)

    def test_healthy_metrics(self):
        stats = compute_stats([make_run("r1", [True] * 5)])
        suggestions = build_suggestions(stats)
        assert any("指标健康" in s.title for s in suggestions)


# ======================================================================
# Analyzer end-to-end / 分析器端到端
# ======================================================================

class _FakeLLM:
    model = "fake"

    async def chat(self, messages, **kwargs):
        return "1. 洞察一\n2. 洞察二"


class TestReportAnalyzer:
    async def test_analyze_without_llm(self):
        runs = [make_run("r1", [True, False]), make_run("r2", [True, True])]
        analysis = await ReportAnalyzer().analyze(runs)
        assert analysis.run_ids == ["r1", "r2"]
        assert analysis.findings and analysis.suggestions
        assert analysis.llm_insight == ""
        assert "# 聚合评测分析" in analysis.markdown
        assert "## 优化建议" in analysis.markdown
        assert "LLM 深度洞察" not in analysis.markdown

    async def test_analyze_with_llm_insight(self):
        analysis = await ReportAnalyzer(llm_client=_FakeLLM()).analyze(
            [make_run("r1", [True])], use_llm=True,
        )
        assert "洞察一" in analysis.llm_insight
        assert "## LLM 深度洞察" in analysis.markdown

    async def test_llm_failure_degrades_gracefully(self):
        class _Broken:
            async def chat(self, *a, **k):
                raise RuntimeError("down")

        analysis = await ReportAnalyzer(llm_client=_Broken()).analyze(
            [make_run("r1", [True])], use_llm=True,
        )
        assert analysis.llm_insight == ""
        assert analysis.suggestions  # 确定性部分不受影响
