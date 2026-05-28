"""
Tests for v15 Agentic Memory evaluation integration.
v15 结构化记忆评测集成测试。
"""

import pytest

from evaluation.benchmark import get_benchmark_tasks
from evaluation.suites import get_suite, resolve_suite_task_ids
from evaluation.variants import get_variant


# ======================================================================
# Benchmark task existence
# ======================================================================

class TestMemoryBenchmarkTasks:
    EXPECTED_TASK_IDS = [
        "memory_fact_write_001",
        "memory_fact_recall_001",
        "memory_experience_write_001",
        "memory_experience_recall_001",
        "memory_correction_001",
        "memory_poisoning_001",
    ]

    def test_all_memory_tasks_exist(self):
        """All memory benchmark task IDs exist in BENCHMARK_TASKS."""
        all_tasks = get_benchmark_tasks()
        all_ids = {t.task_id for t in all_tasks}
        for tid in self.EXPECTED_TASK_IDS:
            assert tid in all_ids, f"Missing task: {tid}"

    def test_memory_tasks_have_memory_tag(self):
        """All memory benchmark tasks have 'memory' tag."""
        all_tasks = {t.task_id: t for t in get_benchmark_tasks()}
        for tid in self.EXPECTED_TASK_IDS:
            task = all_tasks[tid]
            assert "memory" in [tag.lower() for tag in task.tags], \
                f"Task {tid} missing 'memory' tag"


class TestMemorySuite:
    def test_memory_agentic_suite_exists(self):
        """memory_agentic suite is registered."""
        suite = get_suite("memory_agentic")
        assert suite.id == "memory_agentic"

    def test_memory_suite_has_expected_tasks(self):
        """memory_agentic suite references all 6 memory tasks."""
        suite = get_suite("memory_agentic")
        task_ids = resolve_suite_task_ids(suite)
        for tid in [
            "memory_fact_write_001",
            "memory_fact_recall_001",
            "memory_experience_write_001",
            "memory_experience_recall_001",
            "memory_correction_001",
            "memory_poisoning_001",
        ]:
            assert tid in task_ids, f"Suite missing task: {tid}"

    def test_memory_suite_validates(self):
        """memory_agentic suite has no unknown task references."""
        from evaluation.suites import validate_suite_tasks
        suite = get_suite("memory_agentic")
        validate_suite_tasks(suite)  # Should not raise


class TestMemoryVariant:
    def test_agentic_memory_on_variant_exists(self):
        """agentic_memory_on variant is registered."""
        variant = get_variant("agentic_memory_on")
        assert variant.id == "agentic_memory_on"

    def test_variant_enables_memory(self):
        """agentic_memory_on variant enables memory config."""
        variant = get_variant("agentic_memory_on")
        assert variant.env_overrides.get("AGENTIC_MEMORY_ENABLED") is True
        assert variant.env_overrides.get("MEMORY_TOOLS_ENABLED") is True


class TestMemoryMetrics:
    def test_aggregated_metrics_has_memory_fields(self):
        """AggregatedMetrics has v15 memory fields."""
        from evaluation.metrics import AggregatedMetrics, PlanMode
        m = AggregatedMetrics(planning_mode=PlanMode.SIMPLE)
        assert hasattr(m, "memory_search_count")
        assert hasattr(m, "memory_hit_count")
        assert hasattr(m, "memory_store_count")
        assert hasattr(m, "memory_revoke_count")
        assert hasattr(m, "memory_hit_rate")
        assert hasattr(m, "memory_false_positive_rate")
        assert hasattr(m, "avg_memory_context_tokens")

    def test_probe_has_memory_counters(self):
        """EvaluationProbe has v15 memory counters."""
        from evaluation.probe import EvaluationProbe
        p = EvaluationProbe()
        assert hasattr(p, "memory_search_count")
        assert hasattr(p, "memory_hit_count")
        assert hasattr(p, "memory_store_count")
        assert hasattr(p, "memory_revoke_count")

    def test_probe_handles_memory_events(self):
        """EvaluationProbe correctly counts memory events."""
        from evaluation.probe import EvaluationProbe
        p = EvaluationProbe()

        p.on_event("memory_search_start", {})
        assert p.memory_search_count == 1

        p.on_event("memory_search_result", {"count": 3})
        assert p.memory_hit_count == 3

        p.on_event("memory_store", {})
        assert p.memory_store_count == 1

        p.on_event("memory_revoke", {})
        assert p.memory_revoke_count == 1
