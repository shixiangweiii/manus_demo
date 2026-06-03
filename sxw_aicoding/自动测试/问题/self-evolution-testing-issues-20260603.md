# 4.5 自演化 Self-Evolution 实测问题报告

**日期**: 2026-06-03  
**测试人**: opencode 自动化验证  
**测试环境**: macOS, Python 3.12, .venv, DeepSeek API + Bailian MCP  
**测试范围**: operations-manual.md §4.5 "自演化 Self-Evolution" 全部示例命令及验证要点  

---

## 测试结果总览

| 测试 Case | 描述 | 结果 | 备注 |
|-----------|------|------|------|
| Case 1 | 基本自演化 — 失败任务 → Failure lesson stored | ✅ 通过 | 失败任务正确触发 `🧠 Failure lesson stored` |
| Case 1b | 基本自演化 — 成功任务 → Experience learned | ✅ 通过 | 成功任务正确触发 `🧠 Experience learned` |
| Case 6 | 类似任务重跑 → 避坑提示注入 | ✅ 通过 | 正确显示 `🧭 Past-failure avoidance hints injected` |
| Case 2 | LLM 辅助经验提炼 | ✅ 通过 | `SELF_EVOLUTION_LLM_EXTRACTION=true` 成功触发 LLM 提炼，产出更精炼的摘要 |
| Case 3 | 避坑提示上限调整 `SELF_EVOLUTION_MAX_HINTS=5` | ⚠️ 部分通过 | 功能正常，但文档命令缺少必需的 `AGENTIC_MEMORY_ENABLED=true` |
| Case 4 | 偏好学习 `SELF_EVOLUTION_PREFERENCE_ENABLED=true` + `HITL_ENABLED=true` | ⚠️ 未完全验证 | 单任务模式下 HITL 被抑制，偏好学习需要在交互模式中才能触发 |
| Case 5 | 分类器校准 `python -m evolution.calibrate --show-per-task` | ✅ 通过 | 离线运行正常，产出建议 JSON |
| Case 5b | 校准自定义搜索范围 | ❌ 失败 | 文档命令 `--simple-range -3:1` 在 shell 中报错 |

---

## 发现的问题

### 问题 1 [P1·文档]: 校准命令 `--simple-range` 参数格式导致 shell 解析失败

**文档命令**:
```bash
python -m evolution.calibrate \
  --simple-range -3:1 \
  --complex-range 1:5 \
  -o calibration_result.json
```

**实际执行**:
```
python -m evolution.calibrate: error: argument --simple-range: expected one argument
```

**原因**: `-3:1` 以 `-` 开头，argparse 将其误认为另一个 flag 而非 `--simple-range` 的值。shell 也可能将 `-3` 视为选项前缀。

**修复方案**: 文档应使用引号包裹负数范围参数：
```bash
python -m evolution.calibrate \
  --simple-range="-3:1" \
  --complex-range="1:5" \
  -o calibration_result.json
```

**代码位置**: `evolution/calibrate.py:27-36`（`_parse_range()`）  
**影响**: 用户直接复制文档命令会立即报错，无法完成校准操作。

---

### 问题 2 [P1·文档]: 自演化示例 3、4 缺少必需的 `AGENTIC_MEMORY_ENABLED=true`

**文档示例 3**（调整避坑提示注入上限）:
```bash
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_MAX_HINTS=5 \
python main.py
```

**文档示例 4**（启用用户偏好学习）:
```bash
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py
```

**实际执行**:
```
WARNING [Orchestrator] SELF_EVOLUTION_ENABLED but AGENTIC_MEMORY_ENABLED is off — self-evolution disabled
```

**原因**: 自演化的硬依赖是 `AGENTIC_MEMORY_ENABLED=true`（ExperienceLearner 需要 AgenticMemoryService）。文档仅示例 1 和示例 2 正确设置了此变量，示例 3、4 均遗漏。

**修复方案**: 示例 3 和示例 4 应补充 `AGENTIC_MEMORY_ENABLED=true`：
```bash
# 示例 3
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_MAX_HINTS=5 \
python main.py

# 示例 4
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py
```

**代码位置**: `agents/orchestrator.py:359-363`（warning 分支）  
**影响**: 用户复制文档命令后自演化静默失效，只有 WARNING 日志提示，UI 无任何自演化相关输出。

---

### 问题 3 [P2·文档]: `MEMORY_TOOLS_ENABLED=true` 对自演化并非必需

**文档示例 1**:
```bash
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
python main.py
```

文档注释称"必须先启用 Agentic Memory"，但命令中同时设置了 `MEMORY_TOOLS_ENABLED=true`。

**实际验证**: 在仅设置 `AGENTIC_MEMORY_ENABLED=true` + `SELF_EVOLUTION_ENABLED=true`（不带 `MEMORY_TOOLS_ENABLED`）的条件下，自演化核心功能（经验学习、失败反思、避坑提示注入、偏好提示注入）全部正常工作。

**原因**: `MEMORY_TOOLS_ENABLED` 仅控制将 `memory_search`/`memory_store`/`memory_update`/`memory_revoke` 四个工具注册到 ReAct 工具列表，供 LLM 在执行时主动查询/写入记忆。自演化的核心路径（ExperienceLearner 写入 → build_avoidance_hints/build_preference_hints 注入）直接使用 `AgenticMemoryService`，不需要 LLM 通过工具间接操作。

**修复方案**: 文档应明确说明：
- `MEMORY_TOOLS_ENABLED=true` 是可选的，仅当希望 LLM 在 ReAct 循环中主动使用 memory 工具时才需要。
- 自演化核心功能只需 `AGENTIC_MEMORY_ENABLED=true` + `SELF_EVOLUTION_ENABLED=true`。

---

### 问题 4 [P2·功能]: 单任务模式下 HITL 被抑制，偏好学习无法触发

**文档示例 4**（偏好学习）的命令行形式为单任务模式：
```bash
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py
```

**实际执行**:
```
INFO [Orchestrator] HITL configured but suppressed (non-interactive mode)
```

**原因**: `ask_user` 工具在非交互模式（单任务命令行）下被禁用（orchestrator.py suppress HITL）。偏好学习依赖 HITL 问答对（`_hitl_pairs`），只有在交互模式中用户实际回答 ask_user 问题时才能收集。

**修复方案**: 文档应在偏好学习示例后注明必须使用交互模式：
```bash
# 偏好学习必须在交互模式下使用
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py   # 进入交互模式，然后输入任务
```

**代码位置**: `agents/orchestrator.py` HITL suppress logic  
**影响**: 用户按文档命令运行单任务模式后不会看到 `User preference learned` 输出。

---

### 问题 5 [P3·UI]: 成功任务也输出 "No independent steps remaining after failure, breaking early"

**观察**: 在所有成功任务（Reflector Verdict: PASSED）的执行日志中，都出现了：
```
INFO [Orchestrator] No independent steps remaining after failure, breaking early
```

**原因**: 这是 `_execute_simple_plan()` / `_execute_dag()` 中在每轮 step 完成后检查剩余步骤的通用 break 逻辑。即使所有步骤成功完成且无"failure"，此日志仍会打印。

**影响**: 
- 对用户造成误导：看到 "failure" 可能以为任务失败。
- 对日志分析和问题排查造成干扰。

**建议修复**: 
1. 区分 "所有步骤完成" 与 "有步骤失败后提前终止" 两种情况。
2. 成功完成时改为 `INFO [Orchestrator] All steps completed`。
3. 失败提前终止时才使用 "after failure"措辞。

**代码位置**: `agents/orchestrator.py` 中 `_execute_simple_plan` 和 DAGExecutor 相关逻辑。

---

### 问题 6 [P3·文档]: 验证要点描述不完整

**文档验证要点**:
- 失败任务后出现 `Failure lesson stored: ...`
- 下次类似任务出现 `Past-failure avoidance hints injected`
- HITL 交互后出现 `User preference learned: ...`

**遗漏**:
- 文档未提及成功任务的验证要点：`Experience learned: ...`（实际 UI 中以 `🧠 Experience learned` 显示）。
- 文档未提及 `preference_hints_injected`（已知用户偏好注入）的验证要点。
- 文档未提及分类器校准的完整验证要点（建议 JSON 输出、`--show-per-task` 表格、APPLY 提示）。

---

## 功能验证通过项

### ✅ 失败经验学习 (Failure lesson stored)
- 测试命令: `AGENTIC_MEMORY_ENABLED=true MEMORY_TOOLS_ENABLED=true SELF_EVOLUTION_ENABLED=true PLAN_MODE=simple MAX_REPLAN_ATTEMPTS=0 MAX_REACT_ITERATIONS=1 python main.py "请帮我连接到火星上的WiFi网络并下载最新版本的操作系统更新"`
- 结果: Reflector verdict=NEEDS REWORK → `🧠 Failure lesson stored: 任务完全未执行成功...`
- 记忆写入: `experiential` kind, tags=[`failure_lesson`, task_type]

### ✅ 成功经验学习 (Experience learned)
- 测试命令: 同上配置，任务 `"2+2等于几"`
- 结果: Reflector verdict=PASSED → `🧠 Experience learned: 任务执行结果正确...`
- 记忆写入: `procedural` kind, tags=[`evolution_experience`, task_type]

### ✅ LLM 辅助经验提炼 (SELF_EVOLUTION_LLM_EXTRACTION)
- 测试命令: `AGENTIC_MEMORY_ENABLED=true SELF_EVOLUTION_ENABLED=true SELF_EVOLUTION_LLM_EXTRACTION=true`
- 成功路径: LLM 提炼出更精炼的摘要（如"使用Python进行简单算术计算是可靠的方法"），而非确定性的 trajectory 拼接
- 失败路径: LLM 提炼失败时自动回退到确定性摘要，不中断主流程

### ✅ 避坑提示注入 (avoidance_hints_injected)
- 先执行失败任务（火星WiFi），再执行类似任务
- 第二次任务在 Gathering context 阶段显示: `🧭 Past-failure avoidance hints injected`
- 提示内容格式: `## 过往失败教训（请主动规避 / Past failures to avoid）` + 每条 reason + correction

### ✅ 分类器校准 (ClassifierCalibrator)
- `python -m evolution.calibrate --show-per-task` 正常运行
- 产出 Rich 表格 + per-task rule score breakdown
- 建议写入 `${MEMORY_DIR}/classifier_thresholds.suggested.json`
- APPLY 提示正确打印（`export CLASSIFIER_SIMPLE_THRESHOLD=0 CLASSIFIER_COMPLEX_THRESHOLD=1`）

### ✅ dedup 防刷屏
- 重复失败任务不会写入多条相同教训（`_DEDUP_SCORE_THRESHOLD=0.6`）
- 日志: `[ExperienceLearner] skip duplicate failure lesson`

### ✅ 自演化学习失败不影响主流程
- `_learn_from_task()` 被 `try/except` 包裹，所有异常仅记 DEBUG 日志
- 主任务结果不受影响

---

## 建议的文档修正

### §4.5 命令修正

```bash
# 1. 启用自演化（AGENTIC_MEMORY 是硬依赖，MEMORY_TOOLS 是可选）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
python main.py

# 若希望 LLM 在 ReAct 中主动使用 memory 工具，额外设置：
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
python main.py

# 2. 启用 LLM 辅助经验提炼（✅ 原文档正确）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_LLM_EXTRACTION=true \
python main.py

# 3. 调整避坑提示注入上限（⚠️ 补充 AGENTIC_MEMORY_ENABLED）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_MAX_HINTS=5 \
python main.py

# 4. 启用用户偏好学习（⚠️ 补充 AGENTIC_MEMORY_ENABLED + 交互模式说明）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py   # 必须在交互模式下使用
```

### §4.5 分类器校准命令修正

```bash
# 注意：负数范围参数必须用引号包裹，否则 argparse/shell 会误解析
python -m evolution.calibrate \
  --simple-range="-3:1" \
  --complex-range="1:5" \
  -o calibration_result.json
```

### §4.5 验证要点补充

```
**验证要点**：
- 失败任务后出现 `Failure lesson stored: ...` 或 `🧠 Failure lesson stored`
- 成功任务后出现 `Experience learned: ...` 或 `🧠 Experience learned`
- 下次类似任务出现 `Past-failure avoidance hints injected` 或 `🧭 Past-failure avoidance hints injected`
- 偏好学习仅在交互模式中生效，交互后出现 `User preference learned: ...` 或 `🧠 User preference learned`
- 偏好提示注入出现 `Known user preferences injected` 或 `🧭 Known user preferences injected`
- 分类器校准输出 accuracy/ambiguous 表格 + APPLY 提示
```