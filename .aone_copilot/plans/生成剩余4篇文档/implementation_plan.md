
# 生成剩余4篇最新文档

已完成8篇文档，剩余4篇需要生成。每篇文档需基于对应老文档的核心主题，结合当前最新源码（v6）重新编写。

## Proposed Changes

### 文档 9：数据结构与算法详解

#### [NEW] [data-structures-and-algorithms.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/data-structures-and-algorithms.md)

- **对应老文档**: `sxw_aicoding/old_docs/data-structures-and-algorithms.md` (950行)
- **核心主题**: 系统梳理项目中用到的全部数据结构与算法，按"基础→核心→组合应用"排列
- **老文档结构**:
  - 一、基础数据结构（dict/set/有向图/多重边/有根树/快照列表/队列）
  - 二、核心算法（Kahn拓扑排序/BFS/运行时就绪发现/FSM）
  - 三、组合应用（Super-step并行/图合并局部重规划/TF-IDF文本检索）
  - 四、算法调用关系全景图
  - 五、学习路径建议
- **更新要点**:
  - 补充 v5 隐式规划引入的 TodoItem/TodoList 数据结构
  - 补充 v6 LLM retry 的指数退避算法
  - 更新三路由分类器（simple/complex/emergent）的算法描述
  - 更新所有代码引用为最新源码实现
  - 保持"大白话+专业术语"的教学风格
- **关键源码依赖**: `schema.py`, `dag/graph.py`, `dag/state_machine.py`, `dag/executor.py`, `knowledge/retriever.py`, `agents/emergent_planner.py`, `llm/client.py`, `tools/router.py`

---

### 文档 10：相关论文综述

#### [NEW] [related-papers.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/related-papers.md)

- **对应老文档**: `sxw_aicoding/old_docs/related-papers-plan-routing.md` (207行)
- **核心主题**: 汇总与混合规划路由机制相关的学术论文，按关联度分类
- **老文档结构**:
  - 一、任务/查询路由（RouteLLM/DAAO/FrugalGPT/AutoMix/Routesplain/xRouter/Router-R1/GraphPlanner）
  - 二、多Agent协作与规划
  - 三、自适应执行与容错
  - 四、知识检索与RAG
- **更新要点**:
  - 新增"五、隐式规划与涌现行为"类别，覆盖 Claude Code 风格、ReAct 循环等相关论文
  - 新增"六、LLM 可靠性与重试机制"类别
  - 更新各论文与项目最新版本（v5/v6）的关联描述
  - 保持论文格式（标题/会议/链接/核心思想/与项目关联）
- **关键源码依赖**: `agents/planner.py`（三路由分类器）, `agents/emergent_planner.py`（隐式规划）, `llm/client.py`（retry机制）

---

### 文档 11：规划测试用例集

#### [NEW] [planning-test-scenarios.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/planning-test-scenarios.md)

- **对应老文档**: `sxw_aicoding/old_docs/planning-test-scenarios-v4.md` (453行)
- **核心主题**: 提供简单/中等/困难难度的任务场景，用于手工验证规划系统
- **老文档结构**:
  - 一、如何运行这些用例
  - 二、测试维度与观测点
  - 三、用例总览（S1-S5简单/M1-M4中等/H1-H4困难）
  - 四、统一用例模板
  - 五、详细用例描述
- **更新要点**:
  - 更新 PLAN_MODE 说明，新增 `emergent` 模式
  - 更新三路由分类（simple/complex/emergent）的预期路径
  - 新增针对三路由边界的测试用例（如探索性任务应路由到 emergent）
  - 更新终端观测信号，包含 v5 TODO 列表和 v6 retry 日志
  - 更新所有配置项引用为最新 `config.py`
  - 保持用例模板格式
- **关键源码依赖**: `config.py`, `main.py`, `agents/orchestrator.py`, `agents/planner.py`

---

### 文档 12：隐式规划测试用例集

#### [NEW] [emergent-planning-test-scenarios.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/emergent-planning-test-scenarios.md)

- **对应老文档**: `sxw_aicoding/old_docs/emergent-planning-test-scenarios-v5.md` (547行)
- **核心主题**: 验证隐式规划系统的正确性、灵活性和探索能力
- **老文档结构**:
  - 隐式规划核心概念
  - 如何运行测试
  - 测试维度与观测点
  - 测试用例（基础功能验证/TODO列表动态管理/复杂探索性任务/对比测试DAG vs 隐式）
- **更新要点**:
  - 更新隐式规划核心概念，反映最新 `EmergentPlannerAgent` 实现
  - 更新 TODO 状态（PENDING/IN_PROGRESS/COMPLETED/BLOCKED）为最新 schema
  - 新增 v6 特性相关测试（LLM retry 在隐式规划中的表现）
  - 更新 `mark_pending` 重试机制的测试场景
  - 更新配置项（MAX_TODO_ITEMS/TODO_COMPRESSION_THRESHOLD 等）
  - 保持用例模板格式
- **关键源码依赖**: `agents/emergent_planner.py`, `schema.py`（TodoItem/TodoList/TodoStatus）, `config.py`

## Verification Plan

### Manual Verification
- 检查每篇生成的文档是否与老文档核心主题一一对应
- 检查文档中引用的代码片段是否与最新源码一致
- 检查文档中的版本描述是否反映了 v5/v6 的最新特性


---
生成时间: 2026/4/20 20:53:46
planId: b511238f-d565-4ad1-b80b-d70a10b354be
plan_status: review