"""
Tests for the eval set generator — validate/repair, heuristic path, LLM path.
评测集生成器测试 —— 校验修复、启发式路径、LLM 路径（假 LLM）。
"""

from __future__ import annotations

import pytest

from evaluation.metrics import TaskDifficulty
from evalplatform.generator import (
    EvalSetGenerator,
    coerce_task,
    coerce_tasks,
    extract_keywords,
    generate_heuristic_tasks,
)
from evalplatform.models import DocumentRecord, EvalSetStatus
from tests.helpers.evalplatform_fixtures import SAMPLE_MD_DOC


def _draft(**overrides):
    """A valid LLM task draft. 一条合法的 LLM 任务草稿。"""
    draft = {
        "task_id": "doc_qa_001",
        "task_description": "阅读以下片段并回答:FastAPI 基于哪些库构建?片段:FastAPI 基于 Starlette 和 Pydantic。",
        "difficulty": "easy",
        "tags": ["doc_qa", "single_step"],
        "expected_complexity": "simple",
        "expected_tools": ["execute_python"],
        "expected_subtasks": ["回答问题"],
        "success_criteria": "提到 Starlette 与 Pydantic",
        "must_include_keywords": ["Starlette", "Pydantic"],
        "must_not_include": [],
        "verifiers": [{"type": "keyword_include", "params": {"keywords": ["Starlette"]}}],
    }
    draft.update(overrides)
    return draft


# ======================================================================
# coerce_task / 单条修复
# ======================================================================

class TestCoerceTask:
    def test_valid_draft(self):
        task = coerce_task(_draft(), 0, set())
        assert task is not None
        assert task.task_id == "doc_qa_001"
        assert task.difficulty == TaskDifficulty.EASY
        assert "generated" in task.tags
        assert task.ground_truth.expected_tools == ["execute_python"]
        assert task.verifiers[0]["type"] == "keyword_include"

    def test_short_description_dropped(self):
        assert coerce_task(_draft(task_description="太短"), 0, set()) is None

    def test_non_dict_dropped(self):
        assert coerce_task("not a dict", 0, set()) is None

    def test_unknown_tool_filtered(self):
        task = coerce_task(_draft(expected_tools=["execute_python", "made_up_tool"]), 0, set())
        assert task.ground_truth.expected_tools == ["execute_python"]

    def test_infra_tags_dropped(self):
        task = coerce_task(_draft(tags=["mcp", "skill", "memory", "resume", "code"]), 0, set())
        assert set(task.tags) == {"code", "generated"}

    def test_hitl_without_responses_downgraded(self):
        task = coerce_task(_draft(tags=["hitl", "search"], expected_tools=["ask_user", "web_search"]), 0, set())
        assert "hitl" not in task.tags
        assert "ask_user" not in task.ground_truth.expected_tools

    def test_hitl_with_responses_kept(self):
        task = coerce_task(
            _draft(tags=["hitl"], simulated_responses=["上海"], expected_tools=["ask_user"]),
            0, set(),
        )
        assert "hitl" in task.tags
        assert task.ground_truth.simulated_responses == ["上海"]

    def test_bad_difficulty_defaults_medium(self):
        task = coerce_task(_draft(difficulty="impossible"), 0, set())
        assert task.difficulty == TaskDifficulty.MEDIUM

    def test_bad_complexity_cleared(self):
        task = coerce_task(_draft(expected_complexity="weird"), 0, set())
        assert task.ground_truth.expected_complexity == ""

    def test_is_attack_explicit_true(self):
        task = coerce_task(_draft(is_attack=True), 0, set())
        assert task.ground_truth.is_attack is True

    def test_is_attack_defaults_true_for_safety_tag(self):
        # review V3: safety-tagged tasks default to is_attack=True so a complied
        # attack is scored via attack_success_rate, not as a plain success
        task = coerce_task(_draft(tags=["safety"], is_attack=None), 0, set())
        assert task.ground_truth.is_attack is True

    def test_is_attack_defaults_false_for_normal_task(self):
        task = coerce_task(_draft(), 0, set())
        assert task.ground_truth.is_attack is False

    def test_is_attack_explicit_false_overrides_safety_tag(self):
        task = coerce_task(_draft(tags=["safety"], is_attack=False), 0, set())
        assert task.ground_truth.is_attack is False

    def test_task_id_sanitized_and_deduped(self):
        used: set[str] = set()
        t1 = coerce_task(_draft(task_id="My Task!"), 0, used)
        t2 = coerce_task(_draft(task_id="My Task!"), 1, used)
        assert t1.task_id == "my_task"
        assert t2.task_id == "my_task_2"

    def test_missing_id_generated(self):
        task = coerce_task(_draft(task_id=""), 4, set())
        assert task.task_id == "gen_005"

    def test_step_range_clamped(self):
        task = coerce_task(_draft(expected_step_count_range=[0, 99]), 0, set())
        assert task.ground_truth.expected_step_count_range == (1, 20)


class TestVerifierValidation:
    def test_invalid_regex_dropped(self):
        task = coerce_task(_draft(verifiers=[{"type": "regex_match", "params": {"pattern": "("}}]), 0, set())
        assert task.verifiers == []

    def test_unknown_type_dropped(self):
        task = coerce_task(_draft(verifiers=[{"type": "numeric_range", "params": {"min": 1}}]), 0, set())
        assert task.verifiers == []

    def test_absolute_path_dropped(self):
        task = coerce_task(
            _draft(verifiers=[{"type": "file_exists", "params": {"path": "/etc/passwd"}}]), 0, set(),
        )
        assert task.verifiers == []

    def test_parent_traversal_dropped(self):
        task = coerce_task(
            _draft(verifiers=[{"type": "file_exists", "params": {"path": "../out.txt"}}]), 0, set(),
        )
        assert task.verifiers == []

    def test_relative_path_kept(self):
        task = coerce_task(
            _draft(verifiers=[{"type": "file_contains", "params": {"path": "out.md", "content": "ok"}}]),
            0, set(),
        )
        assert task.verifiers[0]["params"]["path"] == "out.md"

    def test_keyword_singular_normalized(self):
        task = coerce_task(
            _draft(verifiers=[{"type": "keyword_include", "params": {"keyword": "hi"}}]), 0, set(),
        )
        assert task.verifiers[0]["params"]["keywords"] == ["hi"]

    def test_composite_filters_children(self):
        task = coerce_task(_draft(verifiers=[{
            "type": "composite_and",
            "params": {"verifiers": [
                {"type": "file_exists", "params": {"path": "/abs"}},       # 无效子项
                {"type": "keyword_include", "params": {"keywords": ["x"]}},
            ]},
        }]), 0, set())
        children = task.verifiers[0]["params"]["verifiers"]
        assert len(children) == 1 and children[0]["type"] == "keyword_include"

    def test_composite_all_invalid_dropped(self):
        task = coerce_task(_draft(verifiers=[{
            "type": "composite_or",
            "params": {"verifiers": [{"type": "file_exists", "params": {"path": "/abs"}}]},
        }]), 0, set())
        assert task.verifiers == []


class TestCoerceTasks:
    def test_dedup_by_description(self):
        payload = {"tasks": [_draft(), _draft(task_id="other_id")]}
        assert len(coerce_tasks(payload)) == 1

    def test_bad_payload_returns_empty(self):
        assert coerce_tasks({"tasks": "oops"}) == []
        assert coerce_tasks(None) == []


# ======================================================================
# Heuristic path / 启发式路径
# ======================================================================

class TestHeuristic:
    def _doc(self) -> DocumentRecord:
        return DocumentRecord(
            doc_id="doc_abcd1234", filename="guide.md", title="FastAPI 指南",
            content=SAMPLE_MD_DOC, char_count=len(SAMPLE_MD_DOC),
        )

    def test_keyword_extraction_bilingual(self):
        keywords = extract_keywords(SAMPLE_MD_DOC)
        assert any(k.lower() == "fastapi" for k in keywords)

    def test_generates_requested_count(self):
        tasks = generate_heuristic_tasks(self._doc(), 4)
        assert 1 <= len(tasks) <= 4
        ids = [t.task_id for t in tasks]
        assert len(ids) == len(set(ids))

    def test_tasks_are_self_contained(self):
        # 任务描述内嵌文档片段（被测 agent 看不到原文档）
        for task in generate_heuristic_tasks(self._doc(), 5):
            assert "文档片段" in task.task_description

    def test_file_task_has_sandbox_safe_verifier(self):
        tasks = generate_heuristic_tasks(self._doc(), 6)
        file_tasks = [t for t in tasks if "doc_apply" in t.tags]
        assert file_tasks, "应包含文件产出任务"
        verifier = file_tasks[0].verifiers[0]
        assert verifier["type"] == "composite_and"
        paths = [c["params"]["path"] for c in verifier["params"]["verifiers"] if "path" in c["params"]]
        assert paths and all(not p.startswith("/") for p in paths)


# ======================================================================
# LLM path with fake client / 假 LLM 客户端
# ======================================================================

class _FakeLLM:
    def __init__(self, payloads=None, error: Exception | None = None):
        self._payloads = list(payloads or [])
        self._error = error
        self.model = "fake-model"
        self.calls = 0

    async def chat_json(self, messages, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._payloads.pop(0) if self._payloads else {"tasks": []}


class TestEvalSetGenerator:
    @pytest.fixture
    def doc(self) -> DocumentRecord:
        return DocumentRecord(
            doc_id="doc_x", filename="guide.md", title="指南",
            content=SAMPLE_MD_DOC, char_count=len(SAMPLE_MD_DOC),
        )

    async def test_llm_success(self, doc):
        llm = _FakeLLM(payloads=[{"tasks": [_draft()]}])
        generator = EvalSetGenerator(llm_client=llm)
        evalset = await generator.generate(doc, num_tasks=3)
        assert evalset.status == EvalSetStatus.READY
        assert evalset.generator == "llm"
        assert evalset.generation_model == "fake-model"
        assert len(evalset.tasks) == 1
        assert evalset.generation_error == ""

    async def test_llm_retry_then_success(self, doc):
        llm = _FakeLLM(payloads=[{"tasks": []}, {"tasks": [_draft()]}])
        generator = EvalSetGenerator(llm_client=llm)
        evalset = await generator.generate(doc)
        assert evalset.status == EvalSetStatus.READY
        assert evalset.generator == "llm"
        assert llm.calls == 2

    async def test_llm_failure_falls_back_to_heuristic(self, doc):
        llm = _FakeLLM(error=RuntimeError("api down"))
        generator = EvalSetGenerator(llm_client=llm)
        evalset = await generator.generate(doc, num_tasks=3)
        assert evalset.status == EvalSetStatus.READY
        assert evalset.generator == "heuristic"
        assert "api down" in evalset.generation_error
        assert evalset.tasks

    async def test_explicit_heuristic_no_llm_call(self, doc):
        llm = _FakeLLM()
        generator = EvalSetGenerator(llm_client=llm)
        evalset = await generator.generate(doc, use_llm=False, num_tasks=2)
        assert llm.calls == 0
        assert evalset.generator == "heuristic"
        assert evalset.generation_error == ""
        assert evalset.status == EvalSetStatus.READY

    async def test_metadata_recorded(self, doc):
        generator = EvalSetGenerator(llm_client=_FakeLLM(payloads=[{"tasks": [_draft()]}]))
        evalset = await generator.generate(doc, target_goal="考察文档理解", num_tasks=5, name="我的评测集")
        assert evalset.name == "我的评测集"
        assert evalset.target_goal == "考察文档理解"
        assert evalset.requested_num_tasks == 5
        assert evalset.doc_id == "doc_x"
