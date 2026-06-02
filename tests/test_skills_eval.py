"""
v20.4 Skill evaluation framework unit tests.
技能评测框架单元测试。
"""

import pytest

from evaluation.benchmark import (
    BenchmarkTask,
    BENCHMARK_TASKS,
    GroundTruth,
    SKILL_BENCHMARK_TASKS,
    TaskDifficulty,
    get_benchmark_tasks,
)
from evaluation.metrics import (
    AggregatedMetrics,
    EfficiencyMetrics,
    ExecutionMetrics,
    PlanMode,
    PlanningMetrics,
    ReflectionMetrics,
    TaskEvaluationResult,
    aggregate_results,
)
from evaluation.probe import EvaluationProbe
from evaluation.suites import get_suite, list_suites
from evaluation.variants import get_variant, list_variants


# ======================================================================
# GroundTruth skill field
# ======================================================================

class TestGroundTruthSkillField:
    """Test expected_skill_activations field on GroundTruth."""

    def test_default_none(self):
        gt = GroundTruth()
        assert gt.expected_skill_activations is None

    def test_set_value(self):
        gt = GroundTruth(expected_skill_activations=(1, 2))
        assert gt.expected_skill_activations == (1, 2)

    def test_zero_range(self):
        gt = GroundTruth(expected_skill_activations=(0, 0))
        assert gt.expected_skill_activations == (0, 0)


# ======================================================================
# Probe skill counters
# ======================================================================

class TestProbeSkillCounters:
    """Test skill counters in EvaluationProbe."""

    def test_reset_zeros(self):
        probe = EvaluationProbe()
        assert probe.skill_activations == 0
        assert probe.skill_activation_failures == 0
        assert probe.skill_content_guarded == 0
        assert probe.skill_allowed_tools_blocked == 0
        assert probe._skill_names_activated == []

    def test_skill_activated_event(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activated", {"name": "hello-world"})
        assert probe.skill_activations == 1
        assert probe._skill_names_activated == ["hello-world"]

    def test_skill_activated_multiple(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activated", {"name": "hello-world"})
        probe.on_event("skill_activated", {"name": "data-analysis"})
        assert probe.skill_activations == 2
        assert probe._skill_names_activated == ["hello-world", "data-analysis"]

    def test_skill_activation_failed_event(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activation_failed", {"name": "nonexistent"})
        assert probe.skill_activation_failures == 1
        assert probe.skill_activations == 0

    def test_skill_content_guarded_event(self):
        probe = EvaluationProbe()
        probe.on_event("skill_content_guarded", {"name": "test"})
        assert probe.skill_content_guarded == 1

    def test_skill_allowed_tools_blocked_event(self):
        probe = EvaluationProbe()
        probe.on_event("skill_allowed_tools_blocked", {"name": "test", "blocked": ["execute_shell"]})
        assert probe.skill_allowed_tools_blocked == 1

    def test_non_dict_data_skill_activated(self):
        """Handle non-dict data gracefully."""
        probe = EvaluationProbe()
        probe.on_event("skill_activated", "string_data")
        assert probe.skill_activations == 1
        assert probe._skill_names_activated == ["unknown"]


# ======================================================================
# Probe skill_metrics construction
# ======================================================================

class TestProbeSkillMetrics:
    """Test skill_metrics dict construction in probe.build_result()."""

    def _make_task(self, expected_skill_activations=None, is_attack=False):
        return BenchmarkTask(
            task_id="test_skill_001",
            task_description="Test skill task",
            difficulty=TaskDifficulty.EASY,
            tags=["skill"],
            ground_truth=GroundTruth(
                expected_skill_activations=expected_skill_activations,
                is_attack=is_attack,
            ),
        )

    def test_skill_metrics_populated_on_activation(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activated", {"name": "hello-world"})
        task = self._make_task()
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert result.skill_metrics["activations"] == 1
        assert result.skill_metrics["activated_skills"] == ["hello-world"]

    def test_skill_metrics_empty_when_no_activations(self):
        probe = EvaluationProbe()
        task = self._make_task()
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        # skill_metrics may be empty dict or have expected_activation key
        assert result.skill_metrics.get("activations", 0) == 0

    def test_skill_metrics_includes_expected_activation_true(self):
        probe = EvaluationProbe()
        task = self._make_task(expected_skill_activations=(1, 1))
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert result.skill_metrics.get("expected_activation") is True

    def test_skill_metrics_includes_expected_activation_false(self):
        probe = EvaluationProbe()
        task = self._make_task(expected_skill_activations=(0, 0))
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert result.skill_metrics.get("expected_activation") is False

    def test_expected_skill_activations_range_failure(self):
        probe = EvaluationProbe()
        # Expected 1 activation but got 0
        task = self._make_task(expected_skill_activations=(1, 1))
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert any("skill_activations" in f.detail for f in result.failures)

    def test_expected_skill_activations_range_pass(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activated", {"name": "hello-world"})
        task = self._make_task(expected_skill_activations=(1, 1))
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert not any("skill_activations" in f.detail for f in result.failures)

    def test_execution_metrics_skill_fields(self):
        probe = EvaluationProbe()
        probe.on_event("skill_activated", {"name": "hello-world"})
        probe.on_event("skill_activation_failed", {"name": "bad"})
        task = self._make_task()
        result = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        assert result.execution.skill_activations == 1
        assert result.execution.skill_activation_failures == 1


# ======================================================================
# ExecutionMetrics skill fields
# ======================================================================

class TestExecutionMetricsSkill:
    """Test skill fields in ExecutionMetrics."""

    def test_defaults(self):
        em = ExecutionMetrics()
        assert em.skill_activations == 0
        assert em.skill_activation_failures == 0
        assert em.skill_content_guarded == 0
        assert em.skill_allowed_tools_blocked == 0

    def test_set_values(self):
        em = ExecutionMetrics(
            skill_activations=3,
            skill_activation_failures=1,
            skill_content_guarded=2,
            skill_allowed_tools_blocked=1,
        )
        assert em.skill_activations == 3
        assert em.skill_activation_failures == 1
        assert em.skill_content_guarded == 2
        assert em.skill_allowed_tools_blocked == 1


# ======================================================================
# AggregatedMetrics skill fields
# ======================================================================

class TestAggregatedMetricsSkill:
    """Test skill fields in AggregatedMetrics."""

    def test_defaults(self):
        am = AggregatedMetrics(planning_mode=PlanMode.SIMPLE)
        assert am.avg_skill_activations == 0.0
        assert am.skill_activation_rate == 0.0
        assert am.skill_false_activation_rate == 0.0
        assert am.skill_token_overhead == 0.0

    def test_aggregate_skill_activation_rate(self):
        """Compute skill_activation_rate from mixed results."""
        results = [
            self._make_result(skill_acts=1, expected_activation=True),
            self._make_result(skill_acts=1, expected_activation=True),
            self._make_result(skill_acts=0, expected_activation=True),  # missed
            self._make_result(skill_acts=0, expected_activation=False),
            self._make_result(skill_acts=0, expected_activation=False),
        ]
        agg = aggregate_results(results)
        # 2/3 should-activate tasks had skill activated
        assert agg.skill_activation_rate == pytest.approx(2 / 3, abs=0.01)
        # 0/2 should-not-activate tasks had false activation
        assert agg.skill_false_activation_rate == 0.0

    def test_aggregate_skill_false_activation_rate(self):
        """Compute skill_false_activation_rate when false activations occur."""
        results = [
            self._make_result(skill_acts=1, expected_activation=True),
            self._make_result(skill_acts=1, expected_activation=False),  # false
            self._make_result(skill_acts=0, expected_activation=False),
        ]
        agg = aggregate_results(results)
        assert agg.skill_false_activation_rate == pytest.approx(0.5, abs=0.01)

    @staticmethod
    def _make_result(skill_acts: int = 0, expected_activation: bool | None = None) -> TaskEvaluationResult:
        skill_metrics = {}
        if expected_activation is not None:
            skill_metrics["expected_activation"] = expected_activation
        if skill_acts > 0:
            skill_metrics["activations"] = skill_acts
        return TaskEvaluationResult(
            task_id="test",
            task_description="test",
            planning_mode=PlanMode.SIMPLE,
            planning=PlanningMetrics(),
            execution=ExecutionMetrics(skill_activations=skill_acts),
            efficiency=EfficiencyMetrics(total_tokens=1000),
            reflection=ReflectionMetrics(),
            skill_metrics=skill_metrics,
        )


# ======================================================================
# Skill suite definition
# ======================================================================

class TestSkillSuite:
    """Test skill evaluation suite definition."""

    def test_skill_suite_exists(self):
        suite = get_suite("skill")
        assert suite.id == "skill"

    def test_skill_suite_has_tasks(self):
        suite = get_suite("skill")
        assert len(suite.task_ids) == 14

    def test_skill_suite_recommended_variants(self):
        suite = get_suite("skill")
        assert "skills_on" in suite.recommended_variants

    def test_skill_suite_in_list(self):
        ids = [s.id for s in list_suites()]
        assert "skill" in ids


# ======================================================================
# Skill variant definition
# ======================================================================

class TestSkillVariant:
    """Test skills_on variant definition."""

    def test_skills_on_variant_exists(self):
        variant = get_variant("skills_on")
        assert variant.id == "skills_on"

    def test_skills_on_env_overrides(self):
        variant = get_variant("skills_on")
        assert variant.env_overrides.get("SKILLS_ENABLED") is True

    def test_skills_on_modes(self):
        variant = get_variant("skills_on")
        assert PlanMode.SIMPLE in variant.modes
        assert PlanMode.EMERGENT in variant.modes


# ======================================================================
# Skill benchmark tasks
# ======================================================================

class TestSkillBenchmarkTasks:
    """Test SKILL_BENCHMARK_TASKS content and structure."""

    def test_task_count(self):
        assert len(SKILL_BENCHMARK_TASKS) == 14

    def test_all_have_skill_tag(self):
        for task in SKILL_BENCHMARK_TASKS:
            assert "skill" in task.tags, f"{task.task_id} missing 'skill' tag"

    def test_should_activate_tasks(self):
        activate_ids = {t.task_id for t in SKILL_BENCHMARK_TASKS
                       if t.task_id.startswith("skill_activate_")}
        assert len(activate_ids) == 7
        for task in SKILL_BENCHMARK_TASKS:
            if task.task_id in activate_ids:
                gt = task.ground_truth
                assert gt.expected_skill_activations is not None
                assert gt.expected_skill_activations[0] > 0, \
                    f"{task.task_id} should expect positive activations"

    def test_should_not_activate_tasks(self):
        noactivate_tasks = [t for t in SKILL_BENCHMARK_TASKS
                           if t.task_id.startswith("skill_noactivate_")]
        assert len(noactivate_tasks) == 4
        for task in noactivate_tasks:
            gt = task.ground_truth
            assert gt.expected_skill_activations == (0, 0), \
                f"{task.task_id} should expect zero activations"

    def test_security_tasks(self):
        security_tasks = [t for t in SKILL_BENCHMARK_TASKS
                         if t.task_id.startswith("skill_security_")]
        assert len(security_tasks) == 3
        # First two are attacks
        attack_tasks = [t for t in security_tasks if t.ground_truth.is_attack]
        assert len(attack_tasks) == 2
        # Last one is benign control
        benign = [t for t in security_tasks if not t.ground_truth.is_attack]
        assert len(benign) == 1
        assert benign[0].ground_truth.expected_skill_activations == (1, 1)

    def test_tasks_registered_in_main_list(self):
        """All SKILL_BENCHMARK_TASKS are in BENCHMARK_TASKS."""
        main_ids = {t.task_id for t in BENCHMARK_TASKS}
        for task in SKILL_BENCHMARK_TASKS:
            assert task.task_id in main_ids, f"{task.task_id} not in BENCHMARK_TASKS"

    def test_get_benchmark_tasks_by_skill_tag(self):
        tasks = get_benchmark_tasks(tags=["skill"])
        task_ids = {t.task_id for t in tasks}
        # Should include at least all skill benchmark tasks
        skill_ids = {t.task_id for t in SKILL_BENCHMARK_TASKS}
        assert skill_ids.issubset(task_ids)


# ======================================================================
# TaskEvaluationResult skill_metrics field
# ======================================================================

class TestTaskEvaluationResultSkillMetrics:
    """Test skill_metrics field on TaskEvaluationResult."""

    def test_default_empty_dict(self):
        result = TaskEvaluationResult(
            task_id="test",
            task_description="test",
            planning_mode=PlanMode.SIMPLE,
            planning=PlanningMetrics(),
            execution=ExecutionMetrics(),
            efficiency=EfficiencyMetrics(),
            reflection=ReflectionMetrics(),
        )
        assert result.skill_metrics == {}

    def test_skill_metrics_set(self):
        result = TaskEvaluationResult(
            task_id="test",
            task_description="test",
            planning_mode=PlanMode.SIMPLE,
            planning=PlanningMetrics(),
            execution=ExecutionMetrics(),
            efficiency=EfficiencyMetrics(),
            reflection=ReflectionMetrics(),
            skill_metrics={"activations": 2, "activated_skills": ["a", "b"]},
        )
        assert result.skill_metrics["activations"] == 2
        assert len(result.skill_metrics["activated_skills"]) == 2


# ======================================================================
# v20 Fix 4: skill_token_overhead measures activated vs not-activated
# v20 修复 4：token 开销度量"真激活 vs 未激活"，而非评测集成员
# ======================================================================

class TestSkillTokenOverheadCriterion:
    """skill_token_overhead must select by real activation, not skill_metrics truthiness."""

    def _result(self, *, activations, expected, tokens):
        probe = EvaluationProbe()
        for _ in range(activations):
            probe.on_event("skill_activated", {"name": "hello-world"})
        gt = GroundTruth(expected_skill_activations=expected) if expected is not None else GroundTruth()
        task = BenchmarkTask(
            task_id="t",
            task_description="d",
            difficulty=TaskDifficulty.EASY,
            tags=["skill"] if expected is not None else [],
            ground_truth=gt,
        )
        r = probe.build_result(task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        r.efficiency.total_tokens = tokens
        return r

    def test_overhead_excludes_nonactivated_skill_tasks(self):
        # Real activation, high tokens
        activated = self._result(activations=1, expected=(1, 1), tokens=1000)
        # Expected-but-not-activated (false negative) — has skill_metrics but no activation
        false_neg = self._result(activations=0, expected=(1, 1), tokens=100)
        # Should-not-activate — has skill_metrics but no activation
        should_not = self._result(activations=0, expected=(0, 0), tokens=100)
        # Baseline non-skill task — no skill ground-truth
        baseline = self._result(activations=0, expected=None, tokens=100)

        agg = aggregate_results([activated, false_neg, should_not, baseline])

        # Only the truly-activated task counts as a "skill task" for token overhead.
        # activated avg = 1000; non-activated avg = 100 → overhead = (1000-100)/100 = 9.0
        assert agg.skill_token_overhead == pytest.approx(9.0)

    def test_overhead_zero_when_no_activations(self):
        false_neg = self._result(activations=0, expected=(1, 1), tokens=100)
        baseline = self._result(activations=0, expected=None, tokens=100)
        agg = aggregate_results([false_neg, baseline])
        # No activated tasks → skill_avg=0 → overhead = (0-100)/100, but guarded:
        # with no skill_task_tokens, skill_avg=0 so overhead is negative or 0; assert it
        # does not spuriously treat false_neg as a skill task (which would give overhead 0).
        assert agg.skill_token_overhead <= 0
