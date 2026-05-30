# v18.5 多智能体评测脚手架（设计 + 实施记录）

生成日期：2026-05-29
适用阶段：v18.5 - Multi-Agent Evaluation（v18 收尾；依赖 v18.1/18.2/18.3/18.4 + v14.6 评测框架）

---

## Context

路线图 §10 v18.5：新增协作任务集，衡量多智能体协作收益，与 single-agent baseline 对比，报告成功率/token/耗时/委派成功率；handoff+ask_user 必须显式配置并进入 HITL tag 评测。本轮只搭**评测脚手架**（任务/suite/variant/指标/探针/对比口径），不跑评测、不写单测（沿用既有约定）。

设计取向：**镜像现有 SubAgent 指标范式**，把 SubAgent / Handoff / Remote SubAgent 统一为"委派（delegation）"，最小侵入扩展现有评测链路。

---

## 实施变更

### 1. `evaluation/metrics.py`
- `ExecutionMetrics` 新增 `handoff_calls`/`handoff_success_count`、`remote_subagent_calls`/`remote_subagent_success_count`（镜像 subagent）。
- `compute_execution_score` 泛化为 **delegation-aware**：`delegation_calls = subagent + handoff + remote`，触发时 45/25/15 + 15%×委派成功率；未触发时保持原 50/30/20（**向后兼容**）。
- `AggregatedMetrics` 新增 `avg_handoff_calls`/`avg_handoff_success_rate`/`avg_remote_subagent_*`；`aggregate_results` 仅在发生委派的任务上求均值。
- `TaskEvaluationResult` 新增 `handoff_metrics` 钻取字典（镜像 `subagent_metrics`）。

### 2. `evaluation/probe.py`
- reset 新增 handoff/remote 计数与结果列表。
- on_event 捕获 `handoff_start/complete/failed`、`remote_subagent_start/complete/failed`。
- build_result 计算 `ho_calls/ho_success`、`rsa_calls/rsa_success` → ExecutionMetrics；构建 `handoff_metrics`；校验 `expected_handoff_calls` 区间（镜像 subagent）。

### 3. `evaluation/benchmark.py`
- `GroundTruth` 新增 `expected_handoff_calls: tuple[int,int] | None`。
- 新增 `MULTI_AGENT_BENCHMARK_TASKS`（multi_agent_001/002/003，tags 含 `multi_agent`+`handoff`），extend 到主列表。

### 4. `evaluation/suites.py`
- 新增 `multi_agent` suite（3 个新任务 + 2 个 subagent 任务），`recommended_variants=[react_auto_baseline, handoff_on, subagent_on]`，`default_modes=[EMERGENT]`。

### 5. `evaluation/variants.py`
- 新增 `handoff_on` 变体（`HANDOFF_ENABLED=true`，`baseline_variant=react_auto_baseline`）。

### 6. `evaluation/runner.py`
- `is_handoff_task = "handoff" in tags` → 激活 `config.HANDOFF_ENABLED`；若同时是 `hitl` 任务则 `HANDOFF_ALLOW_ASK_USER=True`（路线图要求显式配置）。capture/restore originals。

### 7. `evaluation/compare_variants.py`
- summary row + CSV 新增 `avg_subagent_*` / `avg_handoff_*`，让 multi-agent vs baseline 对比报告含委派成功率。

---

## 复用

`compute_execution_score` 委派加权框架（原 subagent 逻辑）、probe subagent 事件/计数/校验范式、benchmark GroundTruth 区间校验、suites/variants 注册结构、compare_variants build_summary/CSV、baseline.compare_baseline（success/token/time delta，无需改）。

---

## 验证（已通过；本轮不跑评测/不写单测）

- `py_compile` / `compileall evaluation` 全绿；evaluation 包离线可导入（无 openai）。
- 委派加权评分：handoff 2 调用/1 成功 → exec score 0.925；无委派 → 1.0（向后兼容）。
- aggregate：avg_handoff_calls=2.0、success_rate=0.5。
- probe：handoff 事件 → handoff_calls/success + handoff_metrics 正确。
- `apply_variant(handoff_on)` 翻转并恢复 `HANDOFF_ENABLED`。
- `eval_cli --dry-run` 列出 multi_agent_001/002/003。
- 待用户后续：带 API key 跑 `python -m evaluation.eval_cli --suite multi_agent`（或 matrix）做 handoff_on vs baseline ROI 对比。

---

## 不在本版范围

- 真正运行评测 / baseline 落盘 / 回归 gate —— 用户后续整体进行。
- Remote SubAgent 评测变体（需 live agent server）——指标已埋点，变体后置。
- 单元测试。
