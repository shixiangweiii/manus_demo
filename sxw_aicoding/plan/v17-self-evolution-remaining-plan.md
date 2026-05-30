# v17 Self-Evolution 剩余工作实施方案（v17.3 分类器校准 + v17.4 偏好学习）

目标产物：`sxw_aicoding/plan/v17-self-evolution-remaining-plan.md`（实施时落盘）
生成日期：2026-05-29
适用阶段：v17 - Self-Evolution 收尾（依赖已完成的 v17.1/17.2 + v15 Agentic Memory + v14.6 benchmark）

---

## Context（为什么做这件事）

v17 第一版（v17.1 经验学习 + v17.2 失败反思）已实施并通过编译/冒烟：新增 `evolution/` 模块、`ExperienceLearner`、orchestrator 接线、4 个 `SELF_EVOLUTION_*` 配置、3 个事件。本方案完成 v17 路线图剩下的两个子阶段：

- **v17.3 分类器校准**：当前任务复杂度阈值硬编码在 `agents/planner.py:474-478`（`score <= -1` → simple，`score >= 2` → complex），无法基于评测结果调整。路线图要求"基于 evaluation 结果调整复杂度阈值，**只允许配置化调整，禁止静默自改代码**"。
- **v17.4 偏好学习**：HITL 的 question/answer 当前用完即弃——`tools/ask_user.py` 把答案回灌 ReAct loop 后不持久化，`orchestrator._handle_user_prompt`（`agents/orchestrator.py`）只 emit 事件。用户偏好（默认城市、输出格式、代码风格等）无法跨任务复用。

**用户已确认的决策**：
1. v17.3 只外置 **2 个决策阈值**（simple/complex），评分权重保持硬编码。
2. v17.3 用 **离线 benchmark 网格搜索 CLI**（无需 API key），产出**建议 JSON**，默认需人工应用，不自动改 config。
3. v17.4 沿用既有模式：**自动注入 + 可回滚**，LLM 提炼 opt-in + 默认确定性提炼。

---

## Step 0：记录 v17.1/17.2 实现要点到项目记忆

实施第一步先把上一版实现要点写入 `~/.claude/projects/-Users-shixiangweii-PycharmProjects-manus-demo/memory/`：

- 新增 `v17-self-evolution-implementation.md`（type: project），记录：`evolution/` 模块（ExperienceLearner 成功→procedural / 失败→failure-lesson + dedup + 避坑提示）、orchestrator `_record_outcome`/`_learn_from_task` 接线、修复 `_store_memory` 硬编码 `success=True` 的 bug、硬依赖 `AGENTIC_MEMORY_ENABLED`、默认关。链接 `[[v15-agentic-memory-implementation]]`。
- 在 `MEMORY.md` 追加一行索引。

---

## v17.3 分类器校准（外置阈值 + 离线网格搜索建议）

### 设计总览

```text
[planner.py] _rule_classify
   ├── 探索/不确定模式命中 → emergent（早返回，不参与阈值）
   └── _rule_score(task) → int  ← 评分逻辑抽成 classmethod（权重仍硬编码）
        └── 阈值判定改读 config.CLASSIFIER_SIMPLE_THRESHOLD / _COMPLEX_THRESHOLD（默认 -1 / 2，零行为变更）

[evolution/calibration.py] ClassifierCalibrator   ← 离线、无 API
   ├── 对 BENCHMARK_TASKS 逐个算 rule_score + is_emergent + expected
   ├── grid_search：遍历 (simple_t, complex_t) 最大化分类准确率（ambiguous 记为未命中）
   └── suggest()：current vs suggested 的准确率/歧义率/逐任务 diff

[python -m evolution.calibrate]  ← CLI
   ├── 打印 current/suggested 阈值 + 准确率
   ├── 写建议 JSON（不写 live config）
   └── 打印"应用方式"（要设置的环境变量），人工决定是否采纳
```

### 实施变更

**1. 配置外置（`config.py`）**
```python
# --- v17.3 Classifier Calibration ---
CLASSIFIER_SIMPLE_THRESHOLD = int(os.getenv("CLASSIFIER_SIMPLE_THRESHOLD", "-1"))   # 规则评分 <= 此值 → simple
CLASSIFIER_COMPLEX_THRESHOLD = int(os.getenv("CLASSIFIER_COMPLEX_THRESHOLD", "2"))  # 规则评分 >= 此值 → complex
```
默认值等于现有硬编码 → 不开校准时行为完全不变。

**2. `agents/planner.py` 重构（抽出可复用、可校准的评分）**
- 把 `_rule_classify`（`planner.py:412-478`）的**评分计算**（431-459 的 len/multi_step/conditional/parallel/action_verb 累加）抽成 `@classmethod _rule_score(cls, task) -> int`，复用 `cls._MULTI_STEP_PATTERN` 等已编译模式。
- 把探索/不确定检测抽成 `@classmethod _is_emergent_by_rule(cls, task) -> bool`（462-467 逻辑）。
- `_rule_classify` 改为：先 `_is_emergent_by_rule` → emergent；否则 `score = _rule_score`，用 **config 阈值** 判定 simple/complex/ambiguous。
- 新增 `@classmethod classify_by_rule(cls, task, simple_t, complex_t) -> str`：给定阈值返回 simple/complex/emergent/ambiguous，供校准器测试任意阈值（无需实例、无需 LLMClient）。
- 行为等价性：重构后默认阈值下 `_rule_classify` 输出与现状一致。

**3. 新增 `evolution/calibration.py`：`ClassifierCalibrator`**
- 复用 `evaluation/benchmark.py:BENCHMARK_TASKS`（`task.task_description` + `task.ground_truth.expected_complexity`，48 任务：22 simple / 16 complex / 10 emergent）。
- `compute_rows() -> list[dict]`：每任务 `{task_id, expected, score, is_emergent}`（调 `PlannerAgent._rule_score` / `_is_emergent_by_rule`，纯离线）。
- `grid_search(simple_range=range(-4,1), complex_range=range(1,6)) -> dict`：对每组 `simple_t < complex_t` 用 `classify_by_rule` 预测，统计 `accuracy`（ambiguous 记未命中）+ `ambiguous_rate`；选 accuracy 最高、ambiguous_rate 次低、最接近默认的组合。
- `suggest() -> dict`：`{current:{simple,complex,accuracy,ambiguous_rate}, suggested:{...}, improved:bool, per_task:[...]}`。
- `write_suggestion(path)`：原子写建议 JSON（写到建议文件，**绝不**改 live config）。

**4. 新增 CLI `evolution/calibrate.py`（`python -m evolution.calibrate`）**
- argparse：`--output`（默认 `${MEMORY_DIR}/classifier_thresholds.suggested.json`）、`--simple-range`、`--complex-range`、`--show-per-task`。
- 用 Rich 打印 current vs suggested 准确率对比；写建议 JSON；打印应用方式：
  `若采纳：export CLASSIFIER_SIMPLE_THRESHOLD=<x> CLASSIFIER_COMPLEX_THRESHOLD=<y>`。
- **不提供 --apply 自动写 config**（满足"禁止静默自改"）。

---

## v17.4 偏好学习（HITL → FACTUAL 记忆 → 自动注入）

### 设计总览

```text
HITL 提问 (interactive)
   [orchestrator._handle_user_prompt]  捕获 question + prompt_id
        └── response_future.add_done_callback → 答案就绪时记 (question, answer) 到 self._hitl_pairs

任务结束 [orchestrator._learn_from_task]
   └── 若有 hitl_pairs → ExperienceLearner.learn_preferences(task, pairs)
        ├── LLM 模式：从 Q&A 提炼持久偏好 {preference, value} → FACTUAL 记忆
        └── 确定性模式：每条 Q&A 存为 FACTUAL "用户偏好: Q→A"
        （tag=user_preference, source=evolution, confidence 受 cap, dedup）

下次任务 [orchestrator._gather_context]
   └── ExperienceLearner.build_preference_hints() → "## 已知用户偏好" 区块（按 tag 列出，非关键词门控）
```

### 实施变更

**1. 配置（`config.py`）**
```python
SELF_EVOLUTION_PREFERENCE_ENABLED = os.getenv("SELF_EVOLUTION_PREFERENCE_ENABLED", "true").lower() == "true"
```
仅在 `SELF_EVOLUTION_ENABLED` + HITL 激活时才有意义（捕获只发生在 HITL 路径）。

**2. `evolution/models.py`**：新增 `USER_PREFERENCE_TAG = "user_preference"`。`TaskOutcome.hitl_pairs` 字段已预留，本版填充。

**3. `agents/orchestrator.py` 捕获 HITL 对**
- `__init__` 新增 `self._hitl_pairs: list[dict] = []`、`self._pending_hitl: dict[str, str] = {}`。
- `run()` / `resume()` 起始重置 `self._hitl_pairs = []`、`self._pending_hitl = {}`（紧挨现有 `_record_outcome(...)` 重置）。
- `_handle_user_prompt(question, prompt_id, response_future)`（现 `orchestrator.py` HITL 区）：仅当 `self._experience_learner and config.SELF_EVOLUTION_PREFERENCE_ENABLED` 时，记 `self._pending_hitl[prompt_id]=question` 并 `response_future.add_done_callback(partial(self._capture_hitl_answer, prompt_id))`。
- 新增 `_capture_hitl_answer(prompt_id, fut)`：`fut.cancelled()`（timeout）或异常或结果为空/`"(user cancelled)"` → 跳过；否则把答案剥掉 `"User response: "` 前缀（与 `ask_user.py:165` 返回格式一致，此处拿到的是 UI 原始答案）后 append `{"question", "answer"}` 到 `self._hitl_pairs`。

**4. `evolution/learner.py` 提炼 + 召回**
- `async def learn_preferences(self, task, hitl_pairs) -> list[AgenticMemoryRecord]`：
  - LLM 模式（`SELF_EVOLUTION_LLM_EXTRACTION`）：一次 `chat_json`（`caller_tag="ExperienceLearner"`）从 pairs 提炼 `[{preference, value}]`，逐条写 FACTUAL。
  - 确定性模式：每条 pair 写一条 FACTUAL，summary=`偏好: {answer}`、content 含 Q&A。
  - 统一：`tags=[USER_PREFERENCE_TAG]`、`kind=FACTUAL`、`source=EVOLUTION_SOURCE`、`confidence=min(0.6, cap)`、metadata 记 `question/answer`。写入前 dedup（复用 `_is_duplicate_failure` 同款逻辑，泛化为 `_is_duplicate(query, tag)`）。
  - emit `preference_learned`。
- `def build_preference_hints(self, task) -> str`：偏好多为全局（如默认城市），关键词检索会漏召回 → 改用 `self._memory._store.list_records(kind=MemoryKind.FACTUAL)` 过滤 `USER_PREFERENCE_TAG` + `status=ACTIVE`，按 importance/recency 取前 `SELF_EVOLUTION_MAX_HINTS`，格式化为"## 已知用户偏好（请遵循）"。（`orchestrator` 已有 `self._agentic_memory_service._store.xxx` 直接访问先例。）

**5. `agents/orchestrator.py` 注入与学习**
- `_gather_context()`：在避坑提示块之后追加 preference hints 注入（`build_preference_hints`），emit `preference_hints_injected`。
- `_learn_from_task()`：在 `learn_from_task(outcome)` 之后，若 `self._hitl_pairs` 且偏好开关开 → `await self._experience_learner.learn_preferences(task, self._hitl_pairs)`，同样 try/except 容错。

**6. `main.py` 事件渲染**：新增 `preference_learned` / `preference_hints_injected` 两个 Rich 分支（与 v17.1/17.2 事件同款 dim 渲染）。

---

## 复用清单

| 复用对象 | 位置 | 用途 |
|---|---|---|
| `PlannerAgent._MULTI_STEP_PATTERN` 等已编译模式 | `agents/planner.py:284-318` | rule_score 复用，避免重复正则 |
| `BENCHMARK_TASKS` + `ground_truth.expected_complexity` | `evaluation/benchmark.py:89` | 校准 ground truth（离线） |
| `ExperienceLearner` dedup / `_safe_chat_json` / `_emit` | `evolution/learner.py` | 偏好提炼复用同款 dedup + LLM 容错 |
| `AgenticMemoryStore.list_records(kind=...)` | `memory/agentic_store.py` | 偏好按 tag 列举（非关键词门控） |
| `AgenticMemoryService.add_record` / `revoke` | `memory/service.py:45,199` | 偏好写入 / 回滚 |
| `_gather_context` 注入点 | `agents/orchestrator.py` | 偏好/避坑提示注入（无需改 prompt_utils） |
| `response_future.add_done_callback` | asyncio | 在 orchestrator 侧捕获 HITL 答案，零侵入 ask_user.py / main.py |
| `TaskOutcome.hitl_pairs`（已预留） | `evolution/models.py` | 偏好数据载体 |

---

## 验证方法（端到端，本版不写单测/不跑评测，仅确保编译 + 冒烟）

1. **静态编译**：`python3 -m py_compile config.py agents/planner.py agents/orchestrator.py evolution/models.py evolution/learner.py evolution/calibration.py evolution/calibrate.py main.py`
2. **导入冒烟**：`python3 -c "from agents.planner import PlannerAgent; from evolution.calibration import ClassifierCalibrator; from agents.orchestrator import OrchestratorAgent; print('ok')"`
3. **分类器行为等价**：默认阈值下抽样若干任务，确认重构前后 `_rule_classify` 输出一致（人工 spot-check 几条）。
4. **校准 CLI（离线，无 API）**：`python -m evolution.calibrate --show-per-task` → 打印 current/suggested 阈值 + 准确率，生成建议 JSON。
5. **偏好学习冒烟**（确定性模式，临时 MEMORY_DIR，无 API）：构造 hitl_pairs（如 Q="哪个城市?" A="上海"）→ `learn_preferences` 写 FACTUAL → `build_preference_hints` 召回并格式化。
6. **默认零副作用**：`SELF_EVOLUTION_ENABLED=false` + 默认阈值，确认现有行为不变。
7. **文档同步**：`CLAUDE.md` 配置表新增 3 个变量、Common Commands 加 `python -m evolution.calibrate`、模块角色补 calibration、关键实现注记补 v17.3/17.4。

---

## 不在本版范围

- 评分权重（5 个）的外置与校准——仅外置 2 个决策阈值。
- 校准自动写 live config / `--apply`——禁止静默自改，只产出建议。
- 消费 evaluation 结果 JSON 的加权校准——本版用离线 benchmark 网格搜索。
- 评测 suite / baseline 对比 / 单元测试——用户后续整体进行。
- 向量检索、RL、模型参数更新、自动改源码。
