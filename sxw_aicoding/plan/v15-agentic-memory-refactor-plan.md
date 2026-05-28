# v15 Agentic Memory 重构实施计划

目标产物：`sxw_aicoding/plan/v15-agentic-memory-refactor-plan.md`  
生成日期：2026-05-26  
适用阶段：v15 - Agentic Memory 重构

---

## Summary

v15 的目标是把当前 `LongTermMemory` 的单文件关键词检索，升级为轻量、可评测、可回滚的 Agentic Memory。第一版不引入向量数据库、不更新模型参数、不做自动代码生成；重点是结构化记忆、可控检索、任务后巩固、Memory as Tool，以及和现有 evaluation matrix 打通。

实施原则：

- 默认保持旧行为不变，新增 `AGENTIC_MEMORY_ENABLED=false` 作为 opt-in 开关。
- 存储继续用本地 JSON，先不引入 SQLite/pgvector，避免依赖膨胀。
- Checkpoint 仍只负责恢复执行；Memory 只负责跨任务学习和召回。
- Memory 写入必须带来源、task_id、confidence、status，支持 revoke，提前防 memory poisoning。
- v15 完成标准必须由 `memory_agentic` 评测 suite 证明，而不是只看功能演示。

---

## Implementation Changes

### 1. Memory Core

新增 `memory/models.py` 和 `memory/agentic_store.py`。

核心模型：

- `AgenticMemoryRecord`
  - 字段：`id`, `kind`, `content`, `summary`, `tags`, `task_id`, `session_id`, `source`, `confidence`, `importance`, `status`, `created_at`, `updated_at`, `last_accessed_at`, `access_count`, `metadata`, `links`
  - `kind`: `factual`, `experiential`, `working`, `procedural`
  - `status`: `active`, `revoked`
- `MemorySearchQuery`
  - 字段：`query`, `kind`, `tags`, `top_k`, `min_confidence`, `include_revoked`
- `MemorySearchResult`
  - 字段：`record`, `score`, `score_breakdown`, `matched_terms`
- `AgenticMemoryStore`
  - 本地存储路径：`${MEMORY_DIR}/agentic_memory/memories.json`
  - 提供 `add`, `search`, `get`, `list`, `revoke`, `update_access_stats`, `clear`
  - 所有写入使用临时文件 + atomic replace
  - 兼容旧 `MemoryEntry`：新增 migration helper，把旧 `memory.json` 转为 `kind=experiential` 记录，但不删除旧文件

检索先实现 keyword 版本，预留 embedding 字段但不启用向量检索。中文检索要支持字符 bigram，英文检索支持 word token。

排序权重固定为：

| 信号 | 权重 |
| --- | ---: |
| keyword match | 0.50 |
| tag match | 0.15 |
| confidence | 0.15 |
| recency | 0.10 |
| importance | 0.05 |
| link bonus | 0.05 |

### 2. Memory Service And Tools

新增 `memory/service.py`：

- `AgenticMemoryService.search_for_task(task, top_k=3)`
- `AgenticMemoryService.format_context(results)`
- `AgenticMemoryService.store_task_result(task, answer, task_id, success, metadata)`
- `AgenticMemoryService.consolidate_task(...)`
- `AgenticMemoryService.revoke(memory_id, reason)`

新增工具模块 `tools/memory_tools.py`：

- `memory_search`
  - 参数：`query`, `kind?`, `tags?`, `top_k?`
  - 返回结构化 JSON 字符串，包含 memory id、summary、confidence、score
- `memory_store`
  - 参数：`kind`, `content`, `summary?`, `tags?`
  - agent 工具写入的 confidence 上限固定为 `0.6`
- `memory_consolidate`
  - 参数：`task_id?`, `notes?`
  - 用于把当前任务经验巩固成 experiential/procedural memory
- `memory_revoke`
  - 参数：`memory_id`, `reason`
  - 只允许 revoke，不做物理删除

新增配置项：

```bash
AGENTIC_MEMORY_ENABLED=false
MEMORY_TOOLS_ENABLED=false
MEMORY_MIN_CONFIDENCE=0.35
MEMORY_SEARCH_TOP_K=3
MEMORY_LLM_CONSOLIDATION_ENABLED=false
```

### 3. Orchestrator Integration

在 `OrchestratorAgent` 中做兼容接线：

- 当 `AGENTIC_MEMORY_ENABLED=false`：继续使用现有 `LongTermMemory`，行为不变。
- 当 `AGENTIC_MEMORY_ENABLED=true`：
  - `_gather_context()` 改用 `AgenticMemoryService.search_for_task()` 生成 `=== Agentic Memory ===` 上下文。
  - 继续保留 `KnowledgeRetriever` 上下文，不混入 memory store。
  - 任务成功后调用 `store_task_result()` 写 experiential memory。
  - 发出事件：`memory_search_start`, `memory_search_result`, `memory_store`, `memory_revoke`, `memory_consolidate`。
- `main.py` 工具注册：
  - `MEMORY_TOOLS_ENABLED=true` 时追加 memory tools。
  - SubAgent 默认不继承 `memory_store` / `memory_revoke`，只允许 `memory_search`，避免子任务污染全局记忆。

### 4. Evaluation And Reports

扩展 evaluation：

- 新增 benchmark tag：`memory`
- 新增 suite：`memory_agentic`
- 新增 variant：`agentic_memory_on`
  - `AGENTIC_MEMORY_ENABLED=true`
  - `MEMORY_TOOLS_ENABLED=true`
- `EvaluationRunner` 对 memory suite 使用临时 `MEMORY_DIR`，同一 variant/mode 内保持任务顺序和同一 memory store，避免污染用户真实记忆。
- `EvaluationProbe` 新增指标：
  - `memory_search_count`
  - `memory_hit_count`
  - `memory_store_count`
  - `memory_revoke_count`
  - `memory_false_positive_count`
  - `memory_context_tokens`
- `AggregatedMetrics` 新增：
  - `memory_hit_rate`
  - `memory_false_positive_rate`
  - `avg_memory_context_tokens`

新增至少 6 个 memory benchmark：

| Task ID | 目标 |
| --- | --- |
| `memory_fact_write_001` | 写入用户偏好事实 |
| `memory_fact_recall_001` | 后续任务必须召回该偏好 |
| `memory_experience_write_001` | 完成一次文件/代码任务并沉淀经验 |
| `memory_experience_recall_001` | 类似任务验证是否召回经验 |
| `memory_correction_001` | 撤销或修正错误事实 |
| `memory_poisoning_001` | 尝试注入恶意记忆，验证不应被高置信召回 |

报告里必须展示：

- `success_delta`
- `token_delta`
- `memory_hit_rate`
- `memory_false_positive_rate`

评测命令：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite memory_agentic \
  --variants react_auto_baseline agentic_memory_on \
  --repeat 3
```

---

## Test Plan

### 单元测试

新增 `tests/test_agentic_memory.py`：

- record model 默认值和枚举校验
- JSON store load/save/revoke/atomic write
- legacy `MemoryEntry` migration
- 中文 bigram + 英文 token 检索排序
- confidence/status 过滤
- access_count / last_accessed_at 更新

新增 `tests/test_memory_tools.py`：

- 四个 memory tools 的 schema
- store/search/revoke/consolidate 正常路径
- `memory_store` confidence 上限
- revoked memory 默认不可搜索

新增 `tests/test_orchestrator_memory.py`：

- feature flag off 时仍走旧 `LongTermMemory`
- feature flag on 时调用 `AgenticMemoryService`
- memory events 被 emit
- SubAgent 工具白名单默认不含写入/撤销工具

新增 `tests/test_evaluation_memory.py`：

- `memory_agentic` suite 的 task id 全部存在
- runner 对 memory suite 使用临时 MEMORY_DIR
- memory metrics 聚合正确

### 验证命令

```bash
python3 -m py_compile \
  memory/models.py \
  memory/agentic_store.py \
  memory/service.py \
  tools/memory_tools.py \
  agents/orchestrator.py \
  evaluation/benchmark.py \
  evaluation/metrics.py \
  evaluation/probe.py \
  evaluation/suites.py \
  evaluation/variants.py
```

```bash
python3 -m pytest \
  tests/test_agentic_memory.py \
  tests/test_memory_tools.py \
  tests/test_orchestrator_memory.py \
  tests/test_evaluation_memory.py \
  -q -o asyncio_mode=auto
```

```bash
python3 -m evaluation.reasoning_matrix --dry-run --suite memory_agentic
```

```bash
python3 -m evaluation.reasoning_matrix \
  --suite memory_agentic \
  --variants react_auto_baseline agentic_memory_on \
  --modes simple emergent \
  --repeat 1
```

---

## Implementation Order

### v15.1 Schema + Store

- 增加 memory models、JSON store、legacy migration、核心单元测试。
- 不接 Orchestrator，不改运行行为。

交付物：

- `memory/models.py`
- `memory/agentic_store.py`
- `tests/test_agentic_memory.py`

验收：

- store/revoke/search/migration 单测通过。
- 旧 `memory.json` 不被删除、不被覆盖。

### v15.2 Retrieval API

- 实现 bilingual keyword scorer、confidence/status filtering、格式化上下文。
- 增加 search ranking 测试。

交付物：

- `memory/service.py`
- search scoring helper
- ranking tests

验收：

- 中文/英文任务都能召回预期 memory。
- revoked 或低 confidence memory 默认不进入上下文。

### v15.3 Memory Tools

- 实现 `memory_search/store/consolidate/revoke`。
- 接入 tool schema 和 traced execution。
- 默认不注册到主流程，先测工具本身。

交付物：

- `tools/memory_tools.py`
- `tests/test_memory_tools.py`

验收：

- tool schema 与 OpenAI function calling 兼容。
- `memory_store` 写入 confidence 不超过 `0.6`。
- `memory_revoke` 后默认搜索不到对应记录。

### v15.4 Orchestrator Opt-in

- 增加 `AGENTIC_MEMORY_ENABLED` 路径。
- 旧 `LongTermMemory` 保留为 fallback。
- 增加 memory events 和任务后 consolidation。

交付物：

- `agents/orchestrator.py` 接线
- `main.py` memory tools 注册
- `config.py` / `.env.example` 配置

验收：

- flag off：现有测试和行为不变。
- flag on：任务开始前搜索 agentic memory，任务完成后写入 experiential memory。
- SubAgent 默认只能搜索 memory，不能写入/撤销。

### v15.5 Evaluation Integration

- 新增 memory benchmark、suite、variant、probe metrics、report 聚合。
- memory suite 必须使用隔离临时 `MEMORY_DIR`。

交付物：

- `evaluation/benchmark.py`
- `evaluation/suites.py`
- `evaluation/variants.py`
- `evaluation/probe.py`
- `evaluation/metrics.py`
- `tests/test_evaluation_memory.py`

验收：

- `python3 -m evaluation.reasoning_matrix --dry-run --suite memory_agentic` 可列出 memory tasks。
- `memory_agentic` smoke 可跑，并在报告里显示 memory 指标。

### v15.6 Docs And Acceptance

- 更新评测手册和 roadmap v15 状态。
- 生成一次 `memory_agentic` smoke 结果。
- 如果 `agentic_memory_on` 没有改善成功率或明确降低重复任务成本，不把 v15 标记为完成。

交付物：

- `sxw_aicoding/评测/评测快速上手手册.md` 更新 memory suite 用法
- `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md` 更新 v15 实施状态
- `evaluation/results/<run_id>/variant_comparison.md`

验收：

- 文档中包含 memory suite 快速运行命令。
- 报告中能比较 baseline 和 `agentic_memory_on`。

---

## Acceptance Criteria

v15 完成必须同时满足：

- `AGENTIC_MEMORY_ENABLED=false` 时，现有主流程行为不变。
- `AGENTIC_MEMORY_ENABLED=true` 时，能按 task 召回、注入、写入结构化 memory。
- Memory 记录具备来源、task_id、confidence、status，可 revoke。
- Memory tools 能被主 agent 使用，但 SubAgent 默认无写入/撤销权限。
- `memory_agentic` suite 可 dry-run 和 smoke-run。
- 报告包含 `memory_hit_rate`、`memory_false_positive_rate`、`success_delta`、`token_delta`。
- v15 的最终结论基于 evaluation 结果，而不是主观演示。

---

## Assumptions

- v15 第一版使用 JSON 存储，不使用 SQLite、pgvector 或外部 embedding 服务。
- 默认配置不改变当前主流程行为；新 memory 通过 feature flag 开启。
- Memory 和 checkpoint 严格分工：checkpoint 用于恢复，memory 用于学习。
- LLM consolidation 默认关闭，先用 deterministic consolidation，避免额外 token 成本和不可测漂移。
- Memory poisoning 在 v15 做最小防护：source/confidence/status/revoke/filter；完整 red-team 放到 v19。

---

## Risks And Mitigations

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Memory poisoning | 错误或恶意内容被后续任务召回 | source/confidence/status/revoke，低 confidence 默认不注入 |
| Token 成本上升 | memory context 过长导致成本增加 | `MEMORY_SEARCH_TOP_K`、`MEMORY_MIN_CONFIDENCE`、context token 指标 |
| 与 ContextManager 双重压缩 | 信息被过度摘要或重复注入 | working memory 仍复用 ContextManager，不另建压缩链路 |
| 与 checkpoint 语义混淆 | 恢复状态和学习经验混在一起 | checkpoint 不进入 memory store，memory 不用于 resume |
| 评测污染真实记忆 | benchmark 结果写入用户实际 memory | memory suite 使用临时 `MEMORY_DIR` |
| SubAgent 污染全局 memory | 子任务误写长期记忆 | SubAgent 默认只允许 `memory_search` |

---

## Quick Commands

开发验证：

```bash
python3 -m pytest tests/test_agentic_memory.py tests/test_memory_tools.py -q -o asyncio_mode=auto
```

评测 dry-run：

```bash
python3 -m evaluation.reasoning_matrix --dry-run --suite memory_agentic
```

最小真实评测：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite memory_agentic \
  --variants react_auto_baseline agentic_memory_on \
  --modes simple \
  --repeat 1
```

主评测：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite memory_agentic \
  --variants react_auto_baseline agentic_memory_on \
  --repeat 3
```
