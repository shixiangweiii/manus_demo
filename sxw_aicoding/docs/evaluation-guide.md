# Manus Demo - 评测模块功能说明与使用指南

> **版本**: v8.0（覆盖 v8 GoalDriven / v9 SubAgent / v13 HITL；Pass^k + LLM-as-Judge）
> **更新日期**: 2026-05-16
> **目的**: 介绍评测模块的整体设计思路、架构、指标体系、使用方法和扩展指南，帮助新加入的开发人员快速上手

> **v8.0 关键升级**（相对 v7.0）：
> - **事件覆盖扩展**：Probe 新增 14+ 个事件分支（HITL/SubAgent/GoalDriven/DAG 细节）
> - **新指标维度**：ExecutionMetrics 加 SubAgent / HITL / DAG 字段；EfficiencyMetrics 加 SubAgent token / HITL 等待时间；PlanningMetrics 加 goal anchor 计数；ReflectionMetrics 加 stagnation 检测
> - **新失败类别**：`subagent_failed` / `hitl_timeout` / `hitl_cancelled` / `goal_stagnation` / `todo_blocked`
> - **SubAgent 评分纳入**：`compute_execution_score` 在 SubAgent 触发时切换权重（45/25/15/15）
> - **新 benchmark 任务**：HITL/SubAgent/GoalDriven 各 2 任务（共 +6，总数 12→18）
> - **TauBench 风格 Pass^k**：`--repeat k` 单任务重跑 k 次，输出 `pass_at_k = successes/k`
> - **Anthropic 风格 LLM-as-Judge**：`must_include_keywords` 失败时 LLM 兜底语义评估
> - **SimulatedUser**：HITL 评测的脚本化用户输入模拟器，让 ask_user 任务可在无人工介入下完成

---

## 目录

- [1. 为什么需要评测模块](#1-为什么需要评测模块)
- [2. 整体设计思路](#2-整体设计思路)
- [3. 架构概览](#3-架构概览)
- [4. 模块结构与文件说明](#4-模块结构与文件说明)
- [5. 指标体系详解](#5-指标体系详解)
- [6. 基准任务集](#6-基准任务集)
- [7. 快速开始：5 分钟上手](#7-快速开始5-分钟上手)
- [8. 命令行参数详解](#8-命令行参数详解)
- [9. 报告输出说明](#9-报告输出说明)
- [10. 实战用例](#10-实战用例)
- [11. 扩展指南](#11-扩展指南)
- [12. 学术参考来源](#12-学术参考来源)

---

## 1. 为什么需要评测模块

Manus Demo 实现了三种 plan-and-execute 规划执行范式：

| 范式 | 版本 | 核心思路 | 适用场景 |
|------|------|---------|---------|
| **Simple** | v1 | 扁平 Plan→Step → 顺序 ReAct 执行 | 单步、简单任务 |
| **Complex** | v2 | 层次化 DAG→TaskNode → 超步并行执行 | 多步骤、有依赖关系的任务 |
| **Emergent** | v5 | Claude Code 风格 TodoList → while(tool_use) 循环 | 探索性、迭代优化型任务 |

三种范式各有优劣，但没有量化的评测手段就无法回答：

- 哪种范式在什么类型任务上表现更好？
- 规划阶段的分类准确率如何？
- 执行阶段的工具使用精度怎样？
- 重规划是帮助了执行还是浪费了 Token？

**评测模块的目标**：在不修改核心执行代码的前提下，对三种范式进行系统化的基准测试和量化对比。

---

## 2. 整体设计思路

### 2.1 零侵入式设计

评测模块的核心设计原则是 **零侵入**——不修改 `agents/`、`dag/`、`react/` 等核心模块的任何代码。

实现方式：

```
OrchestratorAgent 已有事件机制：
  self._emit(event, data) → on_event 回调

评测模块挂载 EvaluationProbe 到这个回调：
  OrchestratorAgent(on_event=probe.on_event)
  → probe 被动接收所有事件
  → 解析事件数据，填充指标字段
  → 任务结束后调用 probe.build_result() 生成评测结果
```

这意味着：
- 核心代码零改动
- 评测模块可以随时启用/禁用
- 事件数据是只读的（probe 不修改 data）

### 2.2 强制路由与动态分类检测

评测需要控制变量——让同一个任务分别以三种模式运行，才能做公平对比。

实现方式是临时覆写 `config.PLAN_MODE`：

```python
# evaluation/runner.py
original_plan_mode = config.PLAN_MODE
config.PLAN_MODE = mode.value           # 强制设为 simple/complex/emergent
try:
    await orchestrator.run(task_description)  # 以指定模式运行
finally:
    config.PLAN_MODE = original_plan_mode     # 恢复原配置
```

`classification_forced` 标志在 `task_start` 事件中根据当前 `config.PLAN_MODE` 动态设置：
- `PLAN_MODE != "auto"` → `classification_forced = True`（评测时使用，分类权重重新分配）
- `PLAN_MODE == "auto"` → `classification_forced = False`（正常自动分类，分类权重参与评分）

### 2.3 四维度加权评分

每个任务产生一个 0-1 的综合评分，由四个维度加权合成：

```
overall = 0.30 × planning + 0.40 × execution + 0.20 × efficiency + 0.10 × reflection_accuracy
```

---

## 3. 架构概览

```mermaid
graph TB
    subgraph "评测流程"
        CLI["eval_cli.py<br/>命令行入口"]
        Runner["EvaluationRunner<br/>评测执行器"]
        Probe["EvaluationProbe<br/>事件探针"]
        Orch["OrchestratorAgent<br/>核心编排器"]
    end

    subgraph "数据定义"
        Benchmark["benchmark.py<br/>12 个基准任务"]
        Metrics["metrics.py<br/>指标模型 + 评分函数"]
    end

    subgraph "输出"
        Report["report.py<br/>Rich 报告 + JSON"]
    end

    CLI -->|"--modes --difficulty"| Runner
    Runner -->|"创建 Probe"| Probe
    Runner -->|"config.PLAN_MODE"| Orch
    Probe -->|"on_event 回调"| Orch
    Benchmark -->|"任务列表"| Runner
    Metrics -->|"build_result()"| Probe
    Metrics -->|"aggregate_results()"| Runner
    Report -->|"render_full_report()"| Runner

    style CLI fill:#e1f5ff
    style Probe fill:#fff3cd
    style Report fill:#d4edda
```

### 执行流程

```
1. eval_cli 解析命令行参数
2. EvaluationRunner 按指定的 modes 和 tasks 循环：
   对每个 (task, mode) 组合：
     a. 创建 EvaluationProbe
     b. 强制设置 config.PLAN_MODE
     c. 创建 OrchestratorAgent(on_event=probe.on_event)
     d. 执行任务：await orchestrator.run(task_description)
     e. probe 通过事件回调收集数据
     f. probe.build_result() → TaskEvaluationResult
3. aggregate_results() 聚合同一 mode 下的所有结果 → AggregatedMetrics
4. render_full_report() 输出对比报告
```

---

## 4. 模块结构与文件说明

```
evaluation/
├── __init__.py        # 模块入口，列出学术参考来源
├── metrics.py         # 核心指标模型 + 评分函数（475 行）
├── benchmark.py       # 12 个基准任务定义（311 行）
├── runner.py          # EvaluationProbe + EvaluationRunner（570 行）
├── report.py          # Rich 控制台报告 + JSON 导出（309 行）
└── eval_cli.py        # CLI 命令行入口（186 行）

tests/
└── test_evaluation.py # 51 个单元测试，全部基于 mock（无需 API Key）
```

### 各文件核心内容

| 文件 | 关键类/函数 | 职责 |
|------|------------|------|
| `metrics.py` | `PlanningMetrics`, `ExecutionMetrics`, `EfficiencyMetrics`, `ReflectionMetrics`, `TaskEvaluationResult`, `AggregatedMetrics` | Pydantic 数据模型 |
| | `compute_planning_score()`, `compute_execution_score()`, `compute_efficiency_score()`, `compute_overall_score()` | 评分计算函数 |
| | `aggregate_results()` | 多任务聚合 |
| `benchmark.py` | `BenchmarkTask`, `GroundTruth` | 任务定义模型 |
| | `get_benchmark_tasks()` | 获取/筛选任务列表 |
| | `BENCHMARK_TASKS` | 12 个预定义任务的列表 |
| `runner.py` | `EvaluationProbe` | 事件探针，拦截 Orchestrator 事件流 |
| | `EvaluationRunner` | 评测执行器，编排多任务×多模式运行 |
| `report.py` | `render_comparison_table()`, `render_mode_detail()`, `render_summary_tree()`, `export_json()` | 报告渲染 |
| `eval_cli.py` | `main()` | CLI 参数解析和入口 |

---

## 5. 指标体系详解

### 5.1 规划质量（Planning Score）

评估规划阶段的分类准确性和计划质量。

**自动模式**（`classification_forced=False`）下的权重：

| 子指标 | 权重 | 说明 |
|--------|------|------|
| 分类准确性 | 40% | 任务被路由到正确的范式（simple/complex/emergent） |
| 计划结构有效性 | 30% | DAG 无环 / 步骤结构完整 |
| 步骤覆盖率 | 20% | 计划覆盖了 benchmark 预期的子任务比例 |
| 生成速度 | 10% | 线性衰减：0 ms 满分，≥10 s 零分 |

**强制模式**（`classification_forced=True`，评测时使用）下的权重（分类权重重新分配）：

| 子指标 | 权重 |
|--------|------|
| 计划结构有效性 | 50% |
| 步骤覆盖率 | 35% |
| 生成速度 | 15% |

**步骤覆盖率的中英文匹配**：先按空白/标点分词做英文 token 匹配，未命中时再用中文 2-gram 滑动窗口匹配（解决中文不分词导致覆盖率恒为 0 的问题）。

相关源码：`evaluation/metrics.py` → `compute_planning_score()`，`evaluation/runner.py` → `build_result()` 中的 step_coverage 计算

### 5.2 执行质量（Execution Score）

| 子指标 | 权重 | 说明 |
|--------|------|------|
| 任务成功 | 50% | 任务是否最终完成 |
| 步骤成功率 | 30% | `completed / total_steps` |
| 工具准确率 | 20% | `successful_tool_calls / total_tool_calls`（无工具调用时给 10% 中性分） |

相关源码：`evaluation/metrics.py` → `compute_execution_score()`

### 5.3 效率指标（Efficiency Score）

| 子指标 | 权重 | 说明 |
|--------|------|------|
| 轨迹效率 | 40% | 基于每步平均 ReAct 迭代次数（`iters_per_step`）与理想值 1 的偏离程度，公式 `max(0, 1 - (iters_per_step - 1) / 9)` |
| Token 效率 | 30% | 对数归一化：1000 token 优秀，50000+ 较差 |
| 时间效率 | 20% | <5s 优秀，>120s 较差 |
| 重规划惩罚 | 10% | 0 次满分，每次扣 33%，最多扣完 |

**ReAct 迭代计数**：每步完成时计入 `tool_calls_log 数 + 1`（+1 是最终 answer-only 迭代），确保不同模式的迭代计数语义一致。

**执行耗时测量**：`execution_time_ms` 仅测量从 "Executing" 阶段到任务完成的时间，不含 context gathering、classification、planning 等准备阶段。

相关源码：`evaluation/metrics.py` → `compute_efficiency_score()`

### 5.4 反思准确性（Reflection Accuracy）

反思（Reflector）判定结果与实际任务结果的吻合度。

| 概念 | 说明 |
|------|------|
| `reflection_observed` | 是否实际观测到 reflection 事件（emergent 模式成功路径不触发 reflection） |
| `benchmark_task_success` | 基于 benchmark GT + `must_include_keywords` + `must_not_include` 判定的任务成功 |
| 反思准确率 | `reflection_passed == benchmark_task_success` 时为 1.0 |
| False Positive | 反思判定通过，但实际不达标 |
| False Negative | 反思判定拒绝，但实际已达标 |
| `reflection_coverage_rate` | 多少任务实际观测到了 reflection 事件（聚合指标） |

**重要**：聚合统计中，FP/FN 率只计算 `reflection_observed=True` 的结果，避免 emergent 模式的 FN 膨胀。

相关源码：`evaluation/metrics.py` → `aggregate_results()`

### 5.5 失败分类体系

12 种失败类别，覆盖规划、执行、反思、系统四个层面：

| 类别 | 层面 | 说明 |
|------|------|------|
| `classification_error` | 规划 | 任务被路由到错误的范式 |
| `plan_structure_invalid` | 规划 | 计划结构无效（如 DAG 环依赖） |
| `plan_incomplete` | 规划 | 计划不完整，遗漏关键步骤 |
| `tool_selection_error` | 执行 | 选错工具 |
| `tool_parameter_error` | 执行 | 工具参数错误 |
| `tool_execution_error` | 执行 | 工具执行异常 |
| `max_iteration_exceeded` | 执行 | 超过最大迭代次数 |
| `node_timeout` | 执行 | 节点执行超时 |
| `false_positive` | 反思 | 反思通过但实际不达标 |
| `false_negative` | 反思 | 反思拒绝但实际已达标 |
| `llm_call_failure` | 系统 | LLM 调用失败 |
| `parse_failure` | 系统 | LLM 输出解析失败 |

---

## 6. 基准任务集

### 6.1 任务总览

共 12 个基准任务，覆盖 3 个难度等级、4 种工具组合：

| Task ID | 难度 | 期望分类 | 期望工具 | 必含关键词 | 子任务数 |
|---------|------|---------|---------|-----------|---------|
| `easy_001` | easy | simple | `web_search` | 天气 | 0 |
| `easy_002` | easy | simple | `execute_python` | 3628800 | 0 |
| `easy_003` | easy | simple | `file_ops` | test_output.txt | 0 |
| `easy_004` | easy | simple | `shell` | 文件, file | 0 |
| `medium_001` | medium | complex | `web_search`, `execute_python` | Python, JavaScript, 列表, list | 2 |
| `medium_002` | medium | complex | `web_search`, `execute_python` | Tokyo, population, density | 3 |
| `medium_003` | medium | complex | `file_ops`, `execute_python` | CSV, 平均, avg | 3 |
| `medium_004` | medium | complex | `execute_python`, `file_ops` | mean, standard deviation, 平均 | 3 |
| `hard_001` | hard | complex | `web_search`, `execute_python`, `file_ops` | AI, 报告, report | 5 |
| `hard_002` | hard | emergent | `web_search`, `execute_python`, `shell` | Flask, FastAPI, Django | 4 |
| `hard_003` | hard | emergent | `execute_python`, `web_search` | 分类, classifier, class | 4 |
| `hard_004` | hard | complex | `execute_python`, `file_ops` | 学生, 成绩, CSV, student | 5 |

### 6.2 任务验证机制

每个任务的 Ground Truth 包含多种验证维度：

```python
class GroundTruth(BaseModel):
    expected_complexity: str                  # 期望路由分类
    expected_step_count_range: tuple[int, int]  # 步骤数合理范围
    expected_tools: list[str]                 # 期望使用的工具
    expected_subtasks: list[str]              # 期望覆盖的子任务描述
    must_include_keywords: list[str]          # 输出必须包含的关键词
    must_not_include: list[str]               # 输出不应包含的内容
```

**验证逻辑**（`runner.py` → `build_result()`）：

1. **`must_include_keywords`**：答案中必须包含所有指定关键词（不区分大小写）
2. **`must_not_include`**：答案中不得包含任何禁止关键词
3. **`expected_subtasks`**：用于计算步骤覆盖率，英文按空白/标点分词匹配，中文用 2-gram 滑动窗口匹配
4. **任务成功判定** = `final_answer 非空` + `不含"无法完成"` + `通过 must_include 检查` + `通过 must_not_include 检查`

---

## 7. 快速开始：5 分钟上手

### 7.1 前提条件

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM API Key（评测需要真实 LLM 调用）
#    方式 A: 环境变量
export LLM_API_KEY="your-api-key"
#    方式 B: 编辑 .env 文件
```

`.env` 配置示例（支持任何 OpenAI 兼容 API）：

```env
# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=deepseek-chat

# 或 通义千问
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_API_KEY=your-api-key-here
# LLM_MODEL=qwen-turbo

# 或 Ollama (本地)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
# LLM_MODEL=llama3
```

### 7.2 不需要 API Key 的开发操作

以下操作完全离线，适合熟悉模块结构和验证代码：

```bash
# 查看基准任务列表（确认任务加载正常）
python -m evaluation.eval_cli --dry-run

# 运行全部 51 个 mock-based 单元测试
python -m pytest tests/test_evaluation.py -v

# 只跑评分计算测试（验证评分逻辑正确性）
python -m pytest tests/test_evaluation.py -v -k "TestPlanningScore or TestExecutionScore"

# 只跑事件探针测试
python -m pytest tests/test_evaluation.py -v -k "TestProbe"

# 语法检查
python3 -m py_compile evaluation/runner.py evaluation/metrics.py
```

### 7.3 新手评测路径（由浅入深）

#### 路径一：快速验证环境（1 分钟）

```bash
# 先跑一个最简单的任务（easy_001 + simple 模式）
# 只有 1 个任务 × 1 种模式 = 1 次执行，快速验证 API 和配置正确
python -m evaluation.eval_cli --tasks easy_001 --modes simple --verbose
```

**预期结果**：
- 如果看到 `Task easy_001 (simple): score=...` 说明环境配置成功
- `--verbose` 会输出详细的调试信息（如 LLM 调用日志、事件流）
- 耗时约 10-30 秒（取决于 LLM 响应速度）

**常见问题**：
- `401 Unauthorized` → API Key 无效或过期
- `Connection refused` → `LLM_BASE_URL` 配置错误
- `ModuleNotFoundError` → 未执行 `pip install -r requirements.txt`

#### 路径二：单模式全量评测（5 分钟）

```bash
# 只跑 simple 模式，所有 12 个任务
python -m evaluation.eval_cli --modes simple --output simple_mode.json
```

**适用场景**：
- 验证 `simple` 模式在各类任务上的整体表现
- 收集基线数据，用于后续对比

**结果解读**：
```
对比总表中关注：
  - Task Success Rate → 整体成功率
  - Avg Overall Score → 平均综合评分
  - Classification Accuracy → 自动分类准确率（simple 模式强制时 N/A）
```

#### 路径三：双模式对比评测（10 分钟）

```bash
# 对比 simple 和 complex 两种模式在所有任务上的表现
python -m evaluation.eval_cli --modes simple complex --output compare_sc.json
```

**输出重点**：
- 对比总表中会高亮更优的值（绿色 + ★）
- 各难度成功率表显示 easy/medium/hard 的差异
- 失败分布表揭示两种模式的失败模式差异

#### 路径四：完整评测（15-20 分钟）

```bash
# 全部 12 个任务 × 3 种模式 = 36 次执行
python -m evaluation.eval_cli --output full_eval_$(date +%Y%m%d_%H%M).json
```

**输出文件**：
- 控制台：Rich 格式的对比报告
- JSON：`full_eval_20260115_1430.json`（包含所有原始数据，可二次分析）

### 7.4 按场景分类的评测命令

#### 场景 1：开发调试 —— 验证 DAG 并行执行改动

```bash
# 开发修改了 dag/executor.py 后，快速验证 complex 模式在 easy 任务上的表现
python -m evaluation.eval_cli --modes complex --difficulty easy --verbose

# 只跑单个复杂任务，观察节点并行执行效果
python -m evaluation.eval_cli --modes complex --tasks medium_003 --verbose
```

#### 场景 2：性能对比 —— 评估新模型效果

```bash
# 先用当前模型跑一轮，保存结果
python -m evaluation.eval_cli --output baseline.json

# 切换模型后（修改 LLM_MODEL）再跑一轮
# export LLM_MODEL="qwen-turbo"
python -m evaluation.eval_cli --output new_model.json

# 手动对比两个 JSON 文件的差异（后续可写脚本自动对比）
```

#### 场景 3：聚焦高难度 —— 探索 emergent 模式优势

```bash
# hard 难度任务最能体现 emergent 模式的价值
python -m evaluation.eval_cli --difficulty hard --modes complex emergent --output emergent_vs_complex.json
```

#### 场景 4：回归测试 —— 验证 Planner 分类器改动

```bash
# 将 PLAN_MODE 设为 auto，测试自动分类准确率
# 在 .env 中设置：PLAN_MODE=auto
python -m evaluation.eval_cli --output auto_routing.json
# 关注对比表中的 "Classification Accuracy" 列
```

#### 场景 5：快速冒烟测试 —— CI/CD 集成

```bash
# 在 CI 中只跑 easy 难度（最快）
python -m evaluation.eval_cli --difficulty easy --modes simple --output smoke_test.json

# 或只跑单个任务作为基本可用性验证
python -m evaluation.eval_cli --tasks easy_002 --modes simple
```

### 7.5 结果解读速查

评测完成后，控制台会输出以下报告。以下是对各报告的快速解读指南：

**对比总表**：
```
Metric                          SIMPLE    COMPLEX   EMERGENT
Task Success Rate / 任务成功率   75.0%     66.7%     58.3%    ← 看哪个模式更稳
Overall Score / 综合评分          0.682     0.645     0.610    ← 综合表现
Planning Score / 规划评分         0.850     0.800     0.750    ← 分类+计划质量
Execution Score / 执行评分        0.720     0.680     0.650    ← 任务执行能力
Efficiency Score / 效率评分       0.450     0.420     0.380    ← 资源消耗效率
```

**各难度成功率表**：
```
Difficulty / 难度   SIMPLE    COMPLEX   EMERGENT
easy                 100.0%    100.0%    75.0%    ← easy 任务通常全部成功
medium                75.0%     50.0%     50.0%    ← medium 开始出现差异
hard                  50.0%     50.0%     50.0%    ← hard 最能体现模式差异
```

**失败分布表**：
```
Failure Category / 失败类别      SIMPLE    COMPLEX   EMERGENT
tool_execution_error                   2         3         4    ← 工具执行错误最多
parse_failure                          0         1         2    ← 解析失败
max_iteration_exceeded                 1         0         1    ← 超过最大迭代次数
```

### 7.6 进阶技巧

#### 技巧 1：结合 `PLAN_MODE` 环境变量强制路由

```bash
# 强制以 simple 模式运行（忽略分类器决策）
PLAN_MODE=simple python -m evaluation.eval_cli --tasks hard_001

# 强制以 auto 模式运行（测试分类器准确性）
PLAN_MODE=auto python -m evaluation.eval_cli --difficulty medium
```

#### 技巧 2：使用 `timeout` 防止评测挂死

```bash
# 限制单次评测在 10 分钟内完成
timeout 600 python -m evaluation.eval_cli --output results.json
```

#### 技巧 3：重定向日志到文件

```bash
# 将 verbose 日志输出到文件，便于排查问题
python -m evaluation.eval_cli --verbose --output results.json 2>&1 | tee eval.log
```

#### 技巧 4：筛选特定失败类别的任务

```bash
# 先导出 JSON，然后用 jq 筛选出包含 tool_execution_error 的任务
python -m evaluation.eval_cli --output results.json
# jq ' .modes.simple.per_task_results[] | select(any(.failures[]?; .category == "tool_execution_error")) | .task_id ' results.json
```

#### 技巧 5：多次评测取平均值

```bash
# 运行 3 次取平均（消除随机性）
for i in {1..3}; do
    python -m evaluation.eval_cli --output run_${i}.json
done
# 后续可写脚本从 3 个 JSON 中计算平均值和标准差
```

### 7.7 常见错误排查

| 错误现象 | 可能原因 | 解决方案 |
|---------|---------|---------|
| `ModuleNotFoundError: No module named 'evaluation'` | 未安装依赖或未在正确目录执行 | 确认在项目根目录执行 `pip install -r requirements.txt` |
| `401 Unauthorized` | API Key 无效或过期 | 检查 `LLM_API_KEY` 是否正确 |
| `Connection refused` | LLM 服务不可用 | 检查 `LLM_BASE_URL` 和网络连接 |
| 评测结果全为 0 分 | 事件探针未正确挂载 | 确认 `config.PLAN_MODE` 未在运行中被意外修改 |
| 某个模式评测极慢 | 任务复杂度高或 LLM 响应慢 | 用 `--difficulty easy` 缩小范围，或检查 LLM 服务状态 |
| JSON 文件为空 | 输出路径权限问题 | 检查 `--output` 指定的目录是否有写入权限 |
| 评分异常偏高/偏低 | `classification_forced` 标志未正确设置 | 确认 `config.PLAN_MODE` 在评测前已正确覆盖 |

### 7.8 快速开始检查清单

首次使用评测模块时，建议按以下顺序执行：

- [ ] 1. 安装依赖：`pip install -r requirements.txt`
- [ ] 2. 配置 API Key：在 `.env` 中填写 `LLM_API_KEY`
- [ ] 3. 验证离线操作：`python -m evaluation.eval_cli --dry-run`
- [ ] 4. 运行单元测试：`python -m pytest tests/test_evaluation.py -v`
- [ ] 5. 单任务快速验证：`python -m evaluation.eval_cli --tasks easy_001 --modes simple`
- [ ] 6. 单模式全量评测：`python -m evaluation.eval_cli --modes simple --output baseline.json`
- [ ] 7. 完整评测对比：`python -m evaluation.eval_cli --output full_eval.json`
- [ ] 8. 查看报告并分析结果

---

---

## 8. 命令行参数详解

```
python -m evaluation.eval_cli [OPTIONS]
```

| 参数 | 缩写 | 说明 | 默认值 | 示例 |
|------|------|------|--------|------|
| `--modes` | - | 指定规划模式 | 全部（simple, complex, emergent） | `--modes simple complex` |
| `--difficulty` | - | 按难度筛选任务 | 全部 | `--difficulty easy` |
| `--tasks` | - | 指定任务 ID | 全部 | `--tasks easy_001 easy_002` |
| `--output` | `-o` | 导出结果到 JSON 文件 | 仅控制台 | `--output results.json` |
| `--dry-run` | - | 展示基准任务但不执行 | - | `--dry-run` |
| `--verbose` | `-v` | 启用调试日志 | - | `--verbose` |

---

## 9. 报告输出说明

### 9.1 控制台报告

评测完成后自动输出三部分：

**1) 对比总表**（`render_comparison_table`）

三种模式并排对比，最优值标绿带星号。包含指标：

- 任务成功率、综合评分
- 规划评分、执行评分、效率评分
- 分类准确率、计划有效率、步骤覆盖率
- 步骤成功率、工具准确率、ReAct 迭代数
- Token 消耗、执行耗时、重规划次数
- 反思准确率、反思覆盖率、误判通过率、误判拒绝率

**2) 各难度成功率表**

展示 easy / medium / hard 三个难度等级在每种模式下的成功率。

**3) 失败分布表**

按 `FailureCategory` 统计各模式下的失败事件分布。

**4) 各模式详细报告**（`render_mode_detail`）

每个模式一个表格，展示每个具体任务的 ID、难度、成功与否、各维度评分、Token 消耗、步骤完成情况。

**5) 树形总结**（`render_summary_tree`）

用 Rich Tree 渲染模式级别的摘要。

### 9.2 JSON 导出

使用 `--output results.json` 导出结构化数据：

```json
{
  "timestamp": "2026-05-12T15:30:00",
  "modes": {
    "simple": {
      "planning_mode": "simple",
      "total_tasks": 12,
      "task_success_rate": 0.75,
      "avg_overall_score": 0.682,
      "avg_planning_score": 0.85,
      "avg_execution_score": 0.72,
      "avg_efficiency_score": 0.45,
      "reflection_accuracy": 0.83,
      "reflection_coverage_rate": 1.0,
      "false_positive_rate": 0.08,
      "false_negative_rate": 0.0,
      "per_task_results": [...]
    },
    "complex": {...},
    "emergent": {...}
  }
}
```

---

## 10. 实战用例

### 用例 1：快速验证开发改动

开发修改了 `dag/executor.py` 的并行逻辑后，快速验证 complex 模式没退化：

```bash
python -m evaluation.eval_cli --modes complex --difficulty easy --verbose
```

只跑 4 个 easy 任务 × complex 模式，几分钟出结果。

### 用例 2：对比 simple vs complex

想看 simple 和 complex 在中等难度任务上的差异：

```bash
python -m evaluation.eval_cli --modes simple complex --difficulty medium --output medium_compare.json
```

4 个 medium 任务 × 2 种模式 = 8 次执行，结果导出到 JSON 供分析。

### 用例 3：聚焦单个任务的跨模式对比

只看 `hard_002`（框架调研）这个任务在三种模式下的表现差异：

```bash
python -m evaluation.eval_cli --tasks hard_002 --verbose
```

1 个任务 × 3 种模式 = 3 次执行，适合深入调试某个具体任务。

### 用例 4：导出完整评测数据

生成包含所有细节的 JSON 文件，供后续数据分析或绘图：

```bash
python -m evaluation.eval_cli --output full_eval_$(date +%Y%m%d).json
```

### 用例 5：查看 benchmark 任务定义（开发新任务前）

在开发新 benchmark 任务前，先看看现有任务的结构：

```bash
python -m evaluation.eval_cli --dry-run
```

### 用例 6：运行单元测试验证

修改了评测代码后运行测试确认没有破坏：

```bash
# 全部评测测试
python -m pytest tests/test_evaluation.py -v

# 只跑探针相关的测试
python -m pytest tests/test_evaluation.py -v -k "TestProbe"

# 只跑评分计算的测试
python -m pytest tests/test_evaluation.py -v -k "TestPlanningScore or TestExecutionScore"

# 只跑中文步骤覆盖率测试
python -m pytest tests/test_evaluation.py -v -k "chinese_step_coverage"
```

---

## 11. 扩展指南

### 11.1 添加新的基准任务

在 `evaluation/benchmark.py` 的 `BENCHMARK_TASKS` 列表中新增：

```python
BenchmarkTask(
    task_id="medium_005",                           # 唯一 ID，格式：{难度}_{序号}
    task_description="你的任务描述",                  # 传给 Agent 的 prompt
    difficulty=TaskDifficulty.MEDIUM,                # easy / medium / hard
    tags=["search", "code"],                         # 标签，用于筛选
    ground_truth=GroundTruth(
        expected_complexity="complex",               # 期望路由分类
        expected_step_count_range=(2, 5),            # 步骤数合理范围
        expected_tools=["web_search", "execute_python"],  # 期望使用的工具
        expected_subtasks=[                          # 期望覆盖的子任务
            "第一步描述",
            "第二步描述",
        ],
        success_criteria="成功标准的自然语言描述",
        must_include_keywords=["关键词1", "关键词2"],    # 输出必须包含
        must_not_include=["禁止词"],                     # 输出不应包含
    ),
),
```

**注意事项**：
- `task_id` 不能与现有任务重复
- `must_include_keywords` 必须填写（测试 `test_all_tasks_have_must_include` 会验证）
- `expected_subtasks` 支持中英文匹配——英文按空格/标点分词，中文用 2-gram 滑动窗口
- 添加后运行 `python -m evaluation.eval_cli --dry-run` 确认加载正常

### 11.2 添加新的事件监听

如果核心模块新增了事件类型（如 `"custom_plan_adaptation"`），在 `EvaluationProbe._handle_event()` 中添加处理分支：

```python
# evaluation/runner.py → EvaluationProbe._handle_event()
elif event == "custom_plan_adaptation":
    self.replan_count += 1
    # 解析 data 中的信息
```

**注意**：避免在 `event == "phase"` 文本匹配和同名事件中重复计数同一件事。

### 11.3 添加新的失败类别

1. 在 `evaluation/metrics.py` 的 `FailureCategory` 枚举中添加新值
2. 在 `EvaluationProbe._handle_event()` 中对应的失败场景创建 `FailureRecord`

### 11.4 调整评分权重

评分权重在各 `compute_*_score()` 函数中硬编码。如需调整：

```python
# evaluation/metrics.py → compute_overall_score()
# 当前权重：30% planning + 40% execution + 20% efficiency + 10% reflection
score = 0.3 * planning + 0.4 * execution + 0.2 * efficiency
score += 0.1 * reflection.reflection_accuracy
```

### 11.5 为新任务编写测试

遵循 `tests/test_evaluation.py` 中的模式：

```python
def test_your_new_task():
    tasks = get_benchmark_tasks(task_ids=["your_task_id"])
    assert len(tasks) == 1
    task = tasks[0]
    assert task.ground_truth.must_include_keywords  # 有验证关键词
    # 可以验证更多 GT 属性
```

---

## 12. 学术参考来源

评测模块的设计参考了以下学术工作：

| 来源 | 借鉴点 |
|------|--------|
| **AgentBench** (ICLR 2024) | 多环境 LLM-as-Agent 基准测试框架 |
| **AgentEval** (ACL 2026) | DAG 结构化的步骤级评估，带误差传播 |
| **Odysseys** | 轨迹效率指标 = rubric_score / num_steps |
| **SWE-bench** | 基于执行的验证（而非字符串匹配） |
| **WorkBench** | 结果导向的评测理念 |
| **SLATE** | 多有效轨迹的认可（不追求单一标准答案） |
| **Plan-RewardBench** | 规划质量的量化评估 |
| **GeoAgentBench** | 参数执行准确率（PEA） |
| **ELHPlan** | 计划步骤的有效性评估 |

完整引用列表见 `evaluation/__init__.py`。

---

## 附录：关键配置项

评测运行时涉及的主要环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | — | API Key（评测必须） |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API 端点 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `MAX_REACT_ITERATIONS` | `10` | 单节点 ReAct 循环上限 |
| `MAX_PARALLEL_NODES` | `3` | DAG 超步并行上限 |
| `MAX_REPLAN_ATTEMPTS` | `3` | 最大重规划次数 |
| `NODE_EXECUTION_TIMEOUT` | `300` | 节点超时时间（秒） |
| `TOKEN_TRACKING_ENABLED` | `true` | Token 追踪开关 |

评测运行时这些配置会被记录到 `TaskEvaluationResult.config_snapshot` 中，便于回溯每次评测的运行时环境。

---

## 13. v8 引擎覆盖扩展（v9 SubAgent / v13 HITL / v8 GoalDriven）

### 13.1 事件覆盖矩阵

v7 Probe 覆盖 17 个事件；v8 扩展到 31 个事件（新增 14 个）：

| 事件源 | 新增事件 | Probe 处理 |
|--------|----------|------------|
| `tools/ask_user.py` (v13 HITL) | `ask_user_prompt` | `hitl_calls++` + 记录 prompt 开始时间 |
| | `ask_user_response` | 累计 `hitl_total_wait_ms` |
| | `ask_user_timeout` | `hitl_timeout_count++` + 添加 `HITL_TIMEOUT` failure |
| | `ask_user_cancelled` | `hitl_cancelled_count++` + 添加 `HITL_CANCELLED` failure |
| `agents/subagent.py` (v9) | `subagent_start` | `_subagent_starts++`（含启动后失败的情况） |
| | `subagent_iteration` | 静默（仅用于 tracing） |
| | `subagent_limit_exceeded` | 添加 `SUBAGENT_FAILED` failure |
| `agents/goal_driven_planner.py` (v8) | `goal_anchor` | `goal_anchor_count++` |
| | `goal_reanchor` | `goal_reanchor_count++` |
| | `goal_reflection` | `goal_reflection_count++` |
| | `stagnation_detected` | `stagnation_detected=True` + `GOAL_STAGNATION` failure |
| | `goal_drift_alert` | 静默（信息性） |
| `dag/executor.py` (v2) | `node_rollback` | `dag_rollback_count++` |
| | `condition_evaluated` | `condition_eval_count++` |
| | `execution_error` | 添加 `TOOL_EXECUTION_ERROR` failure |
| `agents/emergent_planner.py` / `goal_driven_planner.py` | `todo_failed` | `steps_failed++`（之前漏掉） |

### 13.2 SubAgent 评分纳入

`compute_execution_score` 在 `subagent_calls > 0` 时切换权重：

```
原 (无 SubAgent)：   task_success 50% + step_success 30% + tool_accuracy 20%
v8 (有 SubAgent)：   task_success 45% + step_success 25% + tool_accuracy 15% + subagent_success_rate 15%
```

向后兼容：现有 12 任务从未调用 SubAgent，分数不变；新 `subagent_*` 任务会受 SubAgent 成功率影响。

### 13.3 GroundTruth 新字段

```python
class GroundTruth(BaseModel):
    expected_hitl_calls: tuple[int, int] | None = None       # 期望 ask_user 调用次数区间
    expected_subagent_calls: tuple[int, int] | None = None   # 期望 SubAgent 调用次数区间
    expected_goal_features: list[str] | None = None          # 期望出现的 goal-driven 事件
    simulated_responses: list[str] | None = None             # HITL 任务的预设用户回答
```

Runner 在 `build_result` 中校验实际值是否在区间内，否则添加 `PLAN_INCOMPLETE` failure record。

### 13.4 新 benchmark 任务

| ID | 难度 | 触发特性 | 预期工具 |
|----|------|----------|---------|
| `hitl_easy_001` | easy | HITL | get_user_location + ask_user + web_search |
| `hitl_hard_001` | hard | HITL | ask_user + web_search |
| `subagent_easy_001` | easy | SubAgent | subagent + web_search |
| `subagent_hard_001` | hard | SubAgent | subagent + shell + file_ops |
| `goal_easy_001` | easy | GoalDriven | execute_python |
| `goal_hard_001` | hard | GoalDriven | execute_python |

`tag` 字段驱动 Runner 自动设置环境变量：
- `tags=["hitl"]`  → `HITL_ENABLED=True` + `interactive=True` + 注入 SimulatedUser
- `tags=["subagent"]` → `SUBAGENT_ENABLED=True`
- `tags=["goal_driven"]` → `ENABLE_GOAL_DRIVEN_PLANNER=True`

执行完毕后所有 env 在 `finally` 中恢复。

---

## 14. Pass^k 可靠性（TauBench 风格）

### 14.1 使用

```bash
# 默认 k=1，等价于原行为
python -m evaluation.eval_cli --tasks easy_001 --modes simple

# k=3：每个 (task, mode) 跑 3 次，输出 pass_at_k = successes/3
python -m evaluation.eval_cli --tasks easy_001 --modes simple --repeat 3
```

### 14.2 输出字段

`TaskEvaluationResult` 新增 3 个字段：
- `trial_count: int` — 实际重跑次数 k
- `trial_results: list[bool]` — 每次试验的 task_success
- `pass_at_k: float | None` — successes / k（k=1 时为 None）

`AggregatedMetrics` 新增聚合：
- `avg_pass_at_k` — 模式下所有任务的平均 pass^k
- `pass_at_k_std` — pass^k 标准差，反映稳定性

### 14.3 推荐用法

- **调试性能优化**：`--repeat 3` 验证改动是否提升了稳定性（而不只是单次幸运）
- **CI 冒烟**：`--repeat 1`（默认）保持快速
- **完整发布前评测**：`--repeat 5` 跑可靠性基线

### 14.4 报告呈现

启用 `--repeat > 1` 时报告会自动新增列：
- 对比总表：`Avg Pass^k` + `Pass^k Std Dev`
- 各模式详表：Per-task 行的 `Pass^k` 列显示 `successes/k (XX%)`

---

## 15. LLM-as-Judge 兜底（Anthropic 风格）

### 15.1 触发条件

仅在满足以下**全部**条件时启用：
- `must_include_keywords` 中至少一个关键词缺失
- GT 提供了 `success_criteria`
- 最终答案非空

### 15.2 判定流程

```
Agent answer + Task + Success criteria + Missed keywords
  ↓
LLM Judge (low temp, JSON-only, 500 tokens)
  ↓
{"passes": bool, "confidence": 0-1, "reasoning": "..."}
  ↓
若 passes=True 且 confidence ≥ 0.7
  ↓
覆盖 task_success=True; 记录 judge_overrode=True + judge_reasoning
重算 execution_score 和 overall_score
```

### 15.3 成本

- 单次 judge 调用约 500 input tokens + 100 output tokens
- 估算开销：失败率 ~30% × 600 tokens × N tasks ≈ 评测总开销 +5%
- 不会对成功任务额外调用

### 15.4 透明性

- `result.judge_overrode: bool` — judge 是否覆盖了关键词失败
- `result.judge_reasoning: str` — judge 的一句话理由（即使未覆盖也会记录 `[declined, conf=0.X] ...`）
- 报告中 Per-task 表条件显示 `Judge` 列（Y/-）

### 15.5 关闭

无显式开关。判定条件天然限制了调用频率：移除 GT 的 `success_criteria` 即可禁用 judge 兜底。

---

## 16. SimulatedUser（HITL 评测的关键支撑）

### 16.1 为什么需要

HITL 任务的 `ask_user` 工具会暂停 ReAct 循环等待用户输入。评测无法人工介入，需要脚本化回答。

### 16.2 工作机制

```
ask_user_prompt 事件（携带 response_future）
  ↓
EvaluationRunner 的 event_callback 拦截
  ↓
SimulatedUser.respond(question) → 返回预设回答（FIFO 弹出）
  ↓
response_future.set_result(answer) — 直接 resolve Future
  ↓
ReAct 循环恢复，与正常运行流程完全一致
```

注意：拦截器**先 resolve future**，**再让 probe 处理事件**。这样：
- LLM 立刻拿到答案
- Probe 正确计算 `hitl_calls` / `hitl_total_wait_ms`

### 16.3 GroundTruth 配置

```python
GroundTruth(
    simulated_responses=["上海", "中山公园附近"],  # 按 FIFO 顺序回答
    expected_hitl_calls=(1, 3),  # 期望 1-3 次 ask_user
    ...
)
```

预设回答用完后，SimulatedUser 兜底返回 `"I don't know — please proceed with your best judgment."`，让 LLM 自主推理。

---

## 17. Wave-7：SubAgent token 单一数据源（v9.1）

### 17.1 问题背景

v9.0 / v8.0 评测中 `subagent_total_tokens` 是从 `subagent_complete` 事件的 `tokens_used` 字段累加得到的。这意味着评测的 SubAgent token 数据源 **不是** LLMClient 的权威记录,而是 SubAgent 派生流程在事件中的二次报告。这造成两个问题：

1. **双数据源不一致**：UI 显示的 token 表（来自 `LLMClient._call_records`）与评测里的 SubAgent token（来自事件聚合）原则上应一致，但任意一边的统计漏洞都会让两数对不上
2. **失败 SubAgent 的 token 难统计**：如果 SubAgent 中途崩溃没发出 `subagent_complete`,事件路径会丢这部分 token

### 17.2 Wave-7 改造

`evaluation/runner.py` 在 `EvaluationProbe.on_event` 里多收一个事件处理：

```python
elif event == "token_usage_summary":
    summary: TokenUsageSummary = data
    self.total_tokens = summary.total.total_tokens
    # Wave-7: 直接从 by_caller 视图聚合 SubAgent token
    self.subagent_tokens_from_caller = sum(
        usage.total_tokens
        for caller, usage in summary.by_caller.items()
        if caller.startswith("SubAgent")
    )
```

`finalize` 时：

```python
sa_total_tokens_from_events = sum(int(r.get("tokens_used", 0) or 0) for r in self.subagent_results)
# 优先 caller_tag,事件聚合 fallback
sa_total_tokens = self.subagent_tokens_from_caller or sa_total_tokens_from_events
```

### 17.3 数据源单一化的好处

- **与 UI / trace 数字必然一致**：所有 SubAgent token 视图（UI by_caller 表、trace `gen_ai.caller`、评测 `subagent_total_tokens`）都从同一个 `LLMCallRecord.caller_tag` 聚合，没有"为什么三处不一致"的调试场景
- **崩溃 SubAgent 也能统计**：哪怕 SubAgent 没发 `subagent_complete` 事件，只要它发起过 LLM 调用，`LLMCallRecord` 必然存在,token 不丢
- **Pre-Wave-6 trace 文件兼容**：如果加载的是 Wave-6 之前的旧 trace（无 caller_tag），`self.subagent_tokens_from_caller=0`，自动 fallback 到事件聚合 —— 老评测 JSON 不会失效

### 17.4 评测使用上没有变化

用户视角没有变化 —— 还是同样的 `--modes` / `--difficulty` / `--repeat`。只是输出更可信。

---

## 学术参考补充

| 来源 | 借鉴点 |
|------|--------|
| **τ-bench / TauBench** (Sierra Research, 2024) | Pass^k 可靠性度量；用户模拟器接入 HITL 评测 |
| **GAIA** (Meta, 2023) | 分级难度（Level 1-3）的实际任务设计原则 |
| **Anthropic *Demystifying evals for AI agents*** (2026-01) | Task→Trial→Transcript→Outcome→Grader 评测流水线；LLM-as-Judge 作为开放题兜底 |
