# v17 Self-Evolution 设计方案（第一版：v17.1 经验学习 + v17.2 失败反思）

目标产物：`sxw_aicoding/plan/v17-self-evolution-plan.md`（实施时落盘）
生成日期：2026-05-29
适用阶段：v17 - Self-Evolution（P1，依赖 v14.6 评测 + v15 Agentic Memory）

---

## Context（为什么做这件事）

路线图 §9 把 v17 定位为"让系统从任务结果中提炼经验"，但**只做低风险、可回滚的 Reflexion 风格 prompt/memory 注入**：不做 RL、不更新模型参数、不自动改源码、不自动生成危险工具。

当前系统已经具备 v17 所需的全部基础设施，但没有把它们闭环起来：

- 记忆能写但不会"学"：`_store_memory()`（`agents/orchestrator.py:844`）任务结束时调用 `AgenticMemoryService.store_task_result()`，但 **`success` 永远硬编码为 `True`**（`orchestrator.py:855`），失败/best-effort 任务被当成成功记忆，且只存原始答案摘要，不提炼"为什么成功/失败"。
- 记忆能召回但不分类：`_gather_context()`（`orchestrator.py:354`）已把 top-k experiential memory 注入 context，但没有把"过往失败教训"单独框成"避坑提示"。
- 反思结果用完即弃：`Reflection`（`schema.py`，含 `passed/score/feedback/suggestions`）只用于触发 replan（`orchestrator.py:502-515`），失败原因没有被结构化沉淀成 `(task_type, failure_reason, correction)`。
- `consolidate_task()`（`memory/service.py:120`）存在但**从未被 orchestrator 自动触发**，只能靠 agent 主动调 `memory_consolidate` 工具。

v17 第一版要做的，就是把"任务结束 → 提炼经验/失败教训 → 写入可回滚记忆 → 下次相似任务自动注入"这条链路闭合。

**用户已确认的范围决策**：
1. 第一版只做 **v17.1 经验学习 + v17.2 失败反思**；v17.3 分类器校准、v17.4 偏好学习留接口、本版不实现。
2. 学到的经验 **自动注入 + 可回滚**（写入 experiential/failure-lesson 记忆，下次相似任务自动检索注入；每条带 source/confidence/task_id，可 revoke 回滚）。
3. 若将来做 v17.3：采用**外置阈值 + 建议（不自动改 planner 路由）**。

---

## 设计总览

```text
任务结束 (run/resume 收口)
   │
   ▼
[ExperienceLearner.learn_from_task(TaskOutcome)]   ← 新增 evolution/ 模块
   │  success?
   ├── 成功 → 提炼"有效做法" → experiential/procedural 记忆 (tag: evolution_experience)
   └── 失败 → 提炼 (task_type, failure_reason, correction) → failure-lesson 记忆 (tag: failure_lesson)
   │  （LLM 提炼 opt-in；默认确定性提炼自 reflection.feedback）
   │  写入前 dedup；写入后 emit 事件；全部 source="evolution"、可 revoke
   ▼
[AgenticMemoryStore]  (复用 v15，JSON + 原子写)

下次相似任务:
[_gather_context()]  (orchestrator.py:354)
   ├── 既有：experiential top-k 注入  (保持不变)
   └── 新增：failure-lesson 检索 (tag 过滤) → "## 过往失败，请规避" 区块
   ▼
注入 combined_context → Planner / Executor / Emergent 所有路径的 prompt
```

设计原则（对齐路线图风险表）：

- **默认关**：新增 `SELF_EVOLUTION_ENABLED=false`，且**硬依赖** `AGENTIC_MEMORY_ENABLED=true`（经验落在 agentic memory 上）。关掉时行为与今天完全一致。
- **只写记忆，不改代码**：v17 仅写 experiential/procedural/failure-lesson 记忆 + 注入 prompt 上下文。不动 planner 阈值、不动源码。
- **可回滚**：每条 evolution 记忆 `source="evolution"`、带 `task_id`、`confidence` 上限受控、`status` 可 REVOKED；复用现有 `memory_revoke` 工具 / `service.revoke()`。
- **防 memory poisoning**：写入前 dedup（相同 task_type + 相似 failure_reason 不重复写）；自动学习记忆 confidence 封顶（默认 0.6，与 `tools/memory_tools.py` agent 写入上限一致）。
- **验收靠评测**：必须用 v14.6 baseline compare 证明 `success_delta` / `memory_hit_rate`，不靠功能演示。

---

## 实施变更

### 1. 新增模块 `evolution/`

镜像 `dag/` / `checkpoint/` / `memory/` 的"每能力一模块"约定，便于独立测试与特性开关。

**`evolution/models.py`**（新增）

- `TaskOutcome`（pydantic）：一次任务结束时的事实快照
  - `task: str`、`task_id: str`、`complexity: str`（simple/complex/emergent/goal_driven）
  - `success: bool`、`final_answer: str`
  - `reflection_feedback: str`、`reflection_score: float`（来自 `Reflection`，无则空）
  - `trajectory: list[StepResult]`（执行轨迹，用于提炼"做了什么"）
- 常量：`EXPERIENCE_TAG = "evolution_experience"`、`FAILURE_LESSON_TAG = "failure_lesson"`
- metadata 约定键：`task_type`、`failure_reason`、`correction`、`evolution_version`
- **不新增 `MemoryKind` 枚举值**：经验用 `EXPERIENTIAL`/`PROCEDURAL`（路线图 §7 已把经验映射到 experiential），失败教训用 `EXPERIENTIAL` + `FAILURE_LESSON_TAG` 区分，避免 schema churn。

**`evolution/learner.py`**（新增）`ExperienceLearner`

- `__init__(self, llm_client, memory_service)`：注入共享 `LLMClient` 与 `AgenticMemoryService`（不自建）。
- `async def learn_from_task(self, outcome: TaskOutcome) -> list[AgenticMemoryRecord]`
  - **成功路径**：提炼一条简洁"有效做法"。
    - LLM 模式（`SELF_EVOLUTION_LLM_EXTRACTION=true`）：用一次 `chat_json`（`caller_tag="ExperienceLearner"`）从 task+trajectory 提炼 `{summary, procedural_steps, tags}`，写 `PROCEDURAL` 记忆。
    - 确定性模式（默认）：复用 `AgenticMemoryService.consolidate_task(task_id, notes=reflection_feedback)`（`memory/service.py:120`，已能从该 task 的 experiential 记录合成 procedural），**这是已有逻辑的首次自动触发点**。
  - **失败路径**：提炼结构化 `(task_type, failure_reason, correction)`。
    - LLM 模式：从 `reflection_feedback` + trajectory 提炼三元组。
    - 确定性模式：`task_type`=complexity，`failure_reason`=reflection_feedback 截断，`correction`=reflection.suggestions 首条（无则空）。
    - 写入 `EXPERIENTIAL` 记忆：`tags=[FAILURE_LESSON_TAG, task_type]`，`metadata={failure_reason, correction, task_type}`，`source="evolution"`，`confidence≤SELF_EVOLUTION_CONFIDENCE_CAP`。
  - **写入前 dedup**：`memory_service.search()` 用 `MemorySearchQuery(query=failure_reason, tags=[FAILURE_LESSON_TAG])`（复用 `agentic_store.py` 的 tag 过滤 + 评分），命中高分近重复则跳过或仅更新 `access_count`。
  - 每写一条 emit 事件（见 §3）。
- `def build_avoidance_hints(self, task: str) -> str`
  - 用 `MemorySearchQuery(query=task, tags=[FAILURE_LESSON_TAG], top_k=SELF_EVOLUTION_MAX_HINTS, min_confidence=...)` 检索失败教训，格式化为：
    ```
    ## 过往失败教训（请主动规避 / Past failures to avoid）
    - [task_type] 失败原因: <failure_reason> → 建议做法: <correction>
    ```
  - 返回空串表示无相关教训（不污染 context）。

### 2. Orchestrator 接线（`agents/orchestrator.py`）

- **构造函数**（`__init__`，约 line 211-240 附近）：在 agentic memory 初始化块之后，新增
  ```python
  self._experience_learner = None
  if config.SELF_EVOLUTION_ENABLED and self._agentic_memory_service is not None:
      from evolution.learner import ExperienceLearner
      self._experience_learner = ExperienceLearner(self.llm_client, self._agentic_memory_service)
  ```
  关闭或无 agentic memory 时 `_experience_learner` 为 None，全部逻辑短路。

- **执行结果回传（success 信号穿透）**：当前 `run()` 只拿到 `final_answer: str`，拿不到真实 success/reflection。新增三个实例字段，在 `run()` 开头重置，在各路径返回前由小助手 `_record_outcome(success, reflection, results)` 写入：
  - `self._last_success: bool`
  - `self._last_reflection: Reflection | None`
  - `self._last_trajectory: list[StepResult]`
  - 落点：
    - simple 路径 `_execute_and_reflect_simple`（`orchestrator.py:391`）：`reflection.passed` 即 success；best-effort 返回时 success=False。
    - DAG 路径 `_execute_dag_and_reflect`（`orchestrator.py:702`）：同理用最后一次 `reflection.passed`。
    - emergent/goal-driven `_execute_emergent`（`orchestrator.py:622`）：`success = not blocked_todos`（该处已计算 `blocked_todos`）。

- **修正 `_store_memory()`（`orchestrator.py:844`）**：把硬编码 `success=True`（line 855）改为 `success=self._last_success`。这样失败/best-effort 任务在 agentic memory 中的 `confidence/importance` 才正确（`store_task_result` 已对 success=False 降权，`service.py:109-110`）。

- **新增 `_learn_from_task(...)`**：在 `run()` 的 Phase 4（`orchestrator.py:340` `_store_memory` 调用之后）与 `resume()`（`orchestrator.py:1124` 之后）各调用一次：
  ```python
  if self._experience_learner is not None:
      outcome = TaskOutcome(task=..., task_id=self._current_task_id, complexity=self._active_complexity,
                            success=self._last_success, final_answer=final_answer,
                            reflection_feedback=..., reflection_score=..., trajectory=self._last_trajectory)
      try:
          await self._experience_learner.learn_from_task(outcome)
      except Exception:
          logger.debug("[Orchestrator] Self-evolution learn failed", exc_info=True)  # 学习失败不影响主流程
  ```
  失败容错：包 try/except，遵循"UI/observability 异常不影响主流程"的既有约定。

- **注入避坑提示（`_gather_context()`，`orchestrator.py:354`）**：在既有 agentic memory 注入块之后追加
  ```python
  if self._experience_learner is not None:
      hints = self._experience_learner.build_avoidance_hints(task)
      if hints:
          combined += f"{hints}\n\n"
          self._emit("avoidance_hints_injected", {"task": task[:80]})
  ```
  `combined_context` 已流向所有规划/执行路径的 prompt，无需改 `prompt_utils`。

### 3. 配置（`config.py`）

新增（默认全关，向后兼容）：

| 变量 | 默认 | 用途 |
|---|---|---|
| `SELF_EVOLUTION_ENABLED` | `false` | v17 主开关（需 `AGENTIC_MEMORY_ENABLED=true`，否则告警并保持关闭） |
| `SELF_EVOLUTION_LLM_EXTRACTION` | `false` | 用 LLM 提炼经验/失败三元组；关则走确定性提炼 |
| `SELF_EVOLUTION_MAX_HINTS` | `3` | 单次注入的失败避坑提示上限 |
| `SELF_EVOLUTION_CONFIDENCE_CAP` | `0.6` | 自动学习记忆 confidence 上限（防 poisoning，与 agent 写入上限一致） |

检索 `min_confidence` 复用 `MEMORY_MIN_CONFIDENCE`，top_k 复用 `MEMORY_SEARCH_TOP_K`。CLAUDE.md 配置表同步补这几行。

### 4. 事件（多播：UI / Tracing / EvaluationProbe）

新增事件类型（沿用 `_emit` 多播，`orchestrator.py:889`）：

- `experience_learned`：`{task_id, kind, summary}`
- `failure_lesson_stored`：`{task_id, task_type, failure_reason, correction}`
- `avoidance_hints_injected`：`{task, count}`

`main.py` 的 `on_event` 加 Rich 渲染分支；`tracing/bridge.py` 若按事件名映射 span，补这三类（不强制）。

### 5. 评测（v14.6 闭环，路线图 §9 硬验收）

- **新增 evolution suite**（`evaluation/suites.py`，复用 `EVALUATION_SUITES` 结构）：`evolution` suite，含 2-3 组"双跑"场景：
  - 第一跑：构造一个易失败任务（如缺前置信息），产生 failure-lesson。
  - 第二跑：相似任务，验证避坑提示注入后是否改善。
- **报告维度**：复用 `evaluation/baseline.py` 的 `compare_baseline`（`success_rate_delta`/`token`/`time`）。新增聚合指标（`evaluation/metrics.py`）：`memory_hit_rate`（注入命中相关记忆的任务占比）、`avoidance_injected_count`。验收要求报告 `success_delta` 为非负且无 token/time 显著回归。
- **离线单测**：`tests/test_self_evolution.py`（新增），mock `LLMClient` + 临时 `MEMORY_DIR`：
  - 成功任务 → 写 experiential/procedural 记忆。
  - 失败任务 → 写 failure-lesson，metadata 含 `failure_reason/correction`。
  - dedup：相同失败不重复写。
  - `build_avoidance_hints` 命中并正确格式化。
  - 开关关闭时零副作用（行为与今天一致）。

### 6. v17.3 / v17.4 接口预留（本版不实现）

- **v17.3 分类器校准**：在文档中记录方向——把 `planner.py:474-478` 的硬编码阈值（`score<=-1` simple / `score>=2` complex）外置到 `config.py` 或 `classifier_thresholds.json`；校准流程读 `evaluation/metrics.py` 已有的 `classification_accuracy`（`metrics.py:612`、`probe.py:513` 已按 benchmark `expected_complexity` 算准确率）产出**建议阈值 JSON**，默认需人工应用，禁止静默自改。本版只在 plan 文档留此 section，不写代码。
- **v17.4 偏好学习**：`TaskOutcome` 预留 `hitl_pairs` 字段（本版不填充）。来源是 `tools/ask_user.py` 的 question+answer——当前**未被持久化**（答案仅回灌 ReAct loop，`orchestrator.py:1229` `_handle_user_prompt`）。将来需在该处捕获 (question, answer) 写入 FACTUAL 记忆。本版仅注释标注捕获点。

---

## 复用清单（避免造新轮子）

| 复用对象 | 位置 | 用途 |
|---|---|---|
| `AgenticMemoryService.consolidate_task` | `memory/service.py:120` | 成功路径确定性经验合成（首次自动触发） |
| `AgenticMemoryService.store_task_result` | `memory/service.py:90` | 任务结果记忆（修正 success 传参） |
| `AgenticMemoryService.search` / `add_record` / `revoke` | `memory/service.py:36,45,199` | 失败教训 dedup / 写入 / 回滚 |
| `AgenticMemoryStore.search`（tag 过滤 + 6 因子评分） | `memory/agentic_store.py:175` | 避坑提示检索 |
| `MemorySearchQuery(tags=...)` | `memory/models.py:62` | tag 维度检索失败教训 |
| `_gather_context` 注入点 | `orchestrator.py:354` | 避坑提示注入 context（无需改 prompt_utils） |
| `Reflection`（passed/score/feedback/suggestions） | `schema.py` | 确定性失败原因/纠正来源 |
| `compare_baseline` / `EVALUATION_SUITES` | `evaluation/baseline.py`, `evaluation/suites.py` | v17 验收对比 + suite |
| `classification_accuracy` 指标 | `evaluation/metrics.py:612` | v17.3 校准数据源（本版仅预留） |
| `memory_revoke` 工具 | `tools/memory_tools.py` | 人工回滚误学记忆 |

---

## 验证方法（端到端）

1. **静态编译**：`python3 -m py_compile evolution/models.py evolution/learner.py agents/orchestrator.py config.py`
2. **离线单测**：`python3 -m pytest tests/test_self_evolution.py tests/test_agentic_memory.py -v -o asyncio_mode=auto`（无 API key 可跑，mock LLM + 临时 MEMORY_DIR）
3. **开关回归（确认零副作用）**：默认配置跑既有用例 `python3 -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py`，确认 `SELF_EVOLUTION_ENABLED=false` 时行为不变。
4. **双跑手测**（需 API key）：
   ```bash
   AGENTIC_MEMORY_ENABLED=true SELF_EVOLUTION_ENABLED=true python main.py "<易失败任务>"   # 第一跑，产生 failure-lesson
   AGENTIC_MEMORY_ENABLED=true SELF_EVOLUTION_ENABLED=true python main.py "<相似任务>"     # 第二跑，观察 avoidance_hints_injected 事件 + 改善
   ```
   检查 `~/.manus_demo/agentic_memory/memories.json` 出现 `source="evolution"`、`tags` 含 `failure_lesson` 的记录。
5. **评测对比**：
   ```bash
   python -m evaluation.eval_cli --suite evolution --output /tmp/evo.json
   python -m evaluation.eval_cli --suite evolution --baseline evaluation/baselines/v14_6_initial.json --fail-on-regression
   ```
   验收：`success_delta ≥ 0`，无 token/time 显著回归，`memory_hit_rate` 报告可见。
6. **回滚验证**：用 `memory_revoke` 撤销一条误学记忆，确认第二跑不再注入。

---

## 不在本版范围

- v17.3 分类器阈值的实际外置与校准代码（仅留方向）。
- v17.4 偏好学习的 HITL 捕获与 FACTUAL 写入（仅留 `hitl_pairs` 字段与捕获点注释）。
- 向量检索 / embedding（继续用 v15 keyword 检索）。
- RL、模型参数更新、自动代码生成、自动改 planner 路由——路线图明确禁止。
