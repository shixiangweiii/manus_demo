# v4 混合规划路由：两阶段分类器 + v1/v2 自动选择

> 本文档说明 v4 新增的混合规划路由机制：通过两阶段分类器（规则快筛 + LLM 兜底）
> 自动判断任务复杂度，简单任务走 v1 扁平计划路径（省 token、低延迟），
> 复杂任务走 v2 DAG 分层路径（支持并行、回滚、自适应）。

---

## 背景：为什么需要混合路由？

### 问题

v2/v3 的 DAG 规划虽然强大，但对**所有任务**都生成三层层级计划（Goal → SubGoals → Actions），
存在两个明显的效率问题：

1. **Token 浪费**：像「帮我搜索今天的天气」这样的简单任务，生成完整 DAG 需要消耗 500-1000 tokens 的规划 prompt，而一个 2 步的扁平计划只需 ~200 tokens。
2. **延迟开销**：DAG 规划 prompt 更长，LLM 响应也更长，简单任务白白多等 1-2 秒。

### 目标

让系统像人一样「看菜下饭」：
- **简单任务** → 快速生成扁平计划，立即执行（v1 路径）
- **复杂任务** → 投入更多 token 生成分层 DAG，支持并行和容错（v2 路径）

### 设计依据

参考两篇 ICLR 2025 论文的核心工程思想：

- **DAAO**（Difficulty-Aware Agentic Orchestration）：用 VAE 估计查询难度，动态调整工作流深度和模型选择。核心洞察——**先低成本估难度，再分配资源**。
- **RouteLLM**（Learning to Route LLMs from Preference Data）：训练轻量路由器决定将查询发送到强模型还是弱模型，实现 2x 成本降低。核心洞察——**不是所有查询都需要最强的处理方式**。

我们将这两个思想简化为适合 demo 规模的实现：**规则快筛 + LLM 兜底**。

---

## 架构总览

```
User Task
    │
    ▼
┌──────────────────────────────┐
│  classify_task()              │
│                               │
│  ┌─────────────────────────┐  │
│  │ Stage 1: _rule_classify │  │  ← 零成本, < 1ms
│  │ 基于文本特征打分          │  │
│  └──────────┬──────────────┘  │
│             │                  │
│      ┌──────┴──────┐          │
│      │             │          │
│   definite     ambiguous      │
│   (60-70%)    (30-40%)        │
│      │             │          │
│      │  ┌──────────▼────────┐ │
│      │  │Stage 2: _llm_classify│ ← ~60 tokens, 0.3s
│      │  │极简 LLM 分类        │ │
│      │  └──────────┬────────┘ │
│      │             │          │
│      ▼             ▼          │
│   "simple"    or  "complex"   │
└──────┬─────────────┬──────────┘
       │             │
       ▼             ▼
  create_plan()   create_dag()
  v1 扁平计划      v2 分层 DAG
       │             │
       ▼             ▼
  Sequential     DAGExecutor
  Execution      Parallel Super-steps
       │             │
       ▼             ▼
  reflect()      reflect_dag()
  v1 反思          v2 全量反思
```

---

## Stage 1：规则快筛 `_rule_classify()`

### 原理

基于任务文本的**确定性特征**进行加权打分，无需任何 LLM 调用。

### 评分维度

| 维度 | 倾向 simple (减分) | 倾向 complex (加分) |
|------|-------------------|-------------------|
| **文本长度** | < 30 字符: -2 分<br>< 60 字符: -1 分 | > 200 字符: +2 分<br>> 120 字符: +1 分 |
| **多步指示词** | 无匹配: 0 分 | 1 个: +1 分<br>>= 2 个: +3 分 |
| **条件/分支词** | 无匹配: 0 分 | 有匹配: +2 分 |
| **并行需求词** | 无匹配: 0 分 | 有匹配: +2 分 |
| **动作动词数** | <= 1 个: -1 分 | 2 个: +1 分<br>>= 3 个: +2 分 |

### 关键词模式

**多步指示词**（中英双语）：
```
然后、接着、之后、随后、再、首先…然后、第X步
first、then、next、finally、after that、step N、afterwards、subsequently、followed by
```

**条件/分支词**：
```
如果、假如、若是、取决于、根据…决定、分情况
if、depending、based on、whether、in case、when…then
```

**并行需求词**：
```
同时、并行、另外、此外、与此同时、一方面…另一方面
meanwhile、simultaneously、in parallel、additionally、also…and
```

**动作动词**：
```
搜索、查找、分析、计算、生成、创建、编写、下载、保存、对比、总结、翻译、转换、部署、测试、爬取、整理、汇总、调研
search、find、analyze、calculate、generate、create、write、download、save、compare、summarize、translate、build、deploy、test、scrape、crawl、collect、research
```

### 判定阈值

- **score <= -2** → `"simple"`（强简单信号，直接走 v1）
- **score >= 3** → `"complex"`（强复杂信号，直接走 v2）
- **-2 < score < 3** → `"ambiguous"`（需要 Stage 2 LLM 裁决）

### 示例

| 任务 | 长度分 | 多步 | 条件 | 并行 | 动词 | 总分 | 判定 |
|------|--------|------|------|------|------|------|------|
| "搜索今天天气" | -2 | 0 | 0 | 0 | -1 | **-3** | simple |
| "计算 1+1" | -2 | 0 | 0 | 0 | -1 | **-3** | simple |
| "搜索 Python 教程然后总结保存到文件" | -1 | +1 | 0 | 0 | +2 | **+2** | ambiguous → LLM |
| "先调研市场数据，然后分析趋势，最后生成可视化报告并部署到网站" | +2 | +3 | 0 | 0 | +2 | **+7** | complex |

---

## Stage 2：LLM 分类 `_llm_classify()`

### 触发条件

仅当 Stage 1 返回 `"ambiguous"` 时触发（约 30-40% 的请求）。

### Prompt 设计

极简化，输入约 60 tokens：

```
Classify as "simple" or "complex":
- simple: single clear action, 1-2 steps, no parallel/conditional needs
- complex: multi-phase, 3+ steps, parallel work, conditional logic, or research+analysis

Task: {task}

JSON: {"complexity": "simple"|"complex", "reason": "..."}
```

### 关键参数

- `temperature=0.0`：确保确定性输出，同一输入始终得到同一分类
- 失败时默认降级为 `"complex"`：安全兜底，宁可多花 token 也不要误判

---

## v1 简单路径 vs v2 复杂路径

### 执行流程对比

| 维度 | v1 Simple Path | v2 Complex Path |
|------|---------------|-----------------|
| **规划** | 2-6 步扁平列表 | 三层 DAG (Goal → SubGoals → Actions) |
| **执行** | 顺序逐步执行 | Super-step 并行执行 |
| **反思** | `reflect()` 整体评估 | `reflect_dag()` + 逐节点 exit criteria |
| **重规划** | `replan()` 全量重规划 | `replan_subtree()` 局部重规划 |
| **Token 消耗** | 低（~200 tokens 规划） | 高（~500-1000 tokens 规划） |
| **延迟** | 快（规划 0.5-1s） | 慢（规划 1-3s） |
| **适用场景** | 单目标、线性流程 | 多目标、并行、条件分支 |

### Orchestrator 路由逻辑

```python
# agents/orchestrator.py - run() 方法
complexity = await self.planner.classify_task(task)

if complexity == "simple":
    plan = await self.planner.create_plan(task, context)
    answer = await self._execute_and_reflect_simple(task, plan, context)
else:
    dag = await self.planner.create_dag(task, context)
    answer = await self._execute_dag_and_reflect(dag)
```

---

## 配置项

### PLAN_MODE

在 `config.py` 中新增：

```python
PLAN_MODE = os.getenv("PLAN_MODE", "auto")
```

| 值 | 行为 |
|----|------|
| `"auto"` | 默认值。两阶段混合分类器自动判断 |
| `"simple"` | 强制使用 v1 扁平计划路径（跳过分类） |
| `"complex"` | 强制使用 v2 DAG 路径（跳过分类） |

用法：
```bash
# 强制简单模式（调试/测试）
PLAN_MODE=simple python main.py "我的任务"

# 强制复杂模式
PLAN_MODE=complex python main.py "我的任务"

# 自动模式（默认）
python main.py "我的任务"
```

---

## UI 展示

v4 在 CLI 中新增了以下可视化：

### 分类结果展示

任务提交后，立即显示分类结果：
```
  Task complexity: Simple (v1 flat plan)
```
或
```
  Task complexity: Complex (v2 DAG)
```

### v1 扁平计划表格

当走 simple 路径时，以表格形式展示计划：
```
┌──────────────────────────────────┐
│        Simple Plan (v1)          │
├──────┬───────────────────┬───────┤
│ Step │ Description       │ Deps  │
├──────┼───────────────────┼───────┤
│ 1    │ Search for X      │ -     │
│ 2    │ Summarize results │ 1     │
└──────┴───────────────────┴───────┘
```

### v2 DAG 树形可视化

当走 complex 路径时，保持原有的 Rich Tree 展示（与 v3 一致）。

---

## 修改文件总结

| 文件 | 变更内容 |
|------|---------|
| [`config.py`](../config.py) | 新增 `PLAN_MODE` 配置项 |
| [`agents/planner.py`](../agents/planner.py) | 新增 `classify_task()`、`_rule_classify()`、`_llm_classify()` 分类方法；恢复 v1 的 `create_plan()`、`replan()`、`_parse_plan()` 方法；新增 `SIMPLE_PLANNER_SYSTEM_PROMPT` |
| [`agents/orchestrator.py`](../agents/orchestrator.py) | `run()` 中加入分类路由；新增 `_execute_and_reflect_simple()` 和 `_compile_answer()` 方法 |
| [`main.py`](../main.py) | 新增 `task_complexity`、`plan`、`step_start`/`step_complete`/`step_failed` 事件处理；更新欢迎信息 |

---

## 版本演进线

```
v1  线性计划 + 顺序执行 + 全量重规划
     │
     ▼
v2  DAG 分层规划 + Super-step 并行 + 局部重规划 + 节点状态机
     │
     ▼
v3  自适应规划 + 工具路由 + DAG 动态增删改
     │
     ▼
v4  两阶段混合分类器 + v1/v2 自动路由
    （简单任务省 token，复杂任务用 DAG）
```
