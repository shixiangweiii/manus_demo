
# 根据历史文档和最新源码生成对应的最新文档

本计划将基于 `sxw_aicoding/old_docs/` 下的 12 篇历史文档，结合当前最新源码实现，在 `sxw_aicoding/docs/` 目录下生成 12 篇主题对应的最新文档。

## Proposed Changes

### 文档生成总览

老文档与新文档的对应关系如下：

| 序号 | 老文档 | 新文档 | 核心主题 |
|------|--------|--------|---------|
| 1 | `codemap-v4.md` | `codemap.md` | 代码地图（基于最新源码的完整架构地图） |
| 2 | `CHANGELOG-v5.md` | `CHANGELOG.md` | 更新日志（覆盖 v1~v6 全版本演进） |
| 3 | `upgrade-plan-v3.md` | `upgrade-plan.md` | 升级计划（基于当前实现状态更新已完成/待完成项） |
| 4 | `llm-integration-v6.md` | `llm-integration.md` | LLM 集成手册（反映最新 retry 机制等 v6 特性） |
| 5 | `emergent-planning-v5.md` | `emergent-planning.md` | 隐式规划系统详解（基于最新 EmergentPlannerAgent 源码） |
| 6 | `planning-gap-analysis.md` | `planning-gap-analysis.md` | 差距分析（更新已弥补的差距和剩余差距） |
| 7 | `hybrid-plan-routing-v4.md` | `hybrid-plan-routing.md` | 混合规划路由（含 v5 emergent 路由扩展） |
| 8 | `dynamic-features-v1-vs-v2.md` | `dynamic-features.md` | 动态性对比分析（覆盖 v1→v5 全版本） |
| 9 | `planning-test-scenarios-v4.md` | `planning-test-scenarios.md` | 测试用例集（含 v5 emergent 路径测试） |
| 10 | `related-papers-plan-routing.md` | `related-papers.md` | 相关论文综述（保留学术参考，更新关联说明） |
| 11 | `data-structures-and-algorithms.md` | `data-structures-and-algorithms.md` | 数据结构与算法详解（含 v3~v5 新增数据结构） |
| 12 | `emergent-planning-test-scenarios-v5.md` | `emergent-planning-test-scenarios.md` | 隐式规划测试用例集（基于最新实现更新） |

---

### 组件 1：核心架构文档

#### [NEW] [codemap.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/codemap.md)

基于最新源码生成完整代码地图，包含：
- 系统概览（含 v5 emergent 路径的 Mermaid 架构图）
- 目录布局（反映当前实际文件结构）
- 各组件详情：OrchestratorAgent、PlannerAgent（含三路由分类）、ExecutorAgent（含 v6 ReActEngine flag）、ReflectorAgent、EmergentPlannerAgent、DAGExecutor、TaskDAG、NodeStateMachine、LLMClient（含 v6 retry）、ToolRouter、各 Tool、Memory、Context、Knowledge
- 数据流图
- 关键设计模式
- 完整文件参考表

#### [NEW] [CHANGELOG.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/CHANGELOG.md)

覆盖 v1~v6 全版本演进的更新日志：
- v1: 线性规划 + 顺序执行
- v2: DAG 分层规划 + Super-step 并行
- v3: 自适应规划 + 工具路由 + 动态 DAG
- v4: 两阶段混合分类器 + 自动路由
- v5: 隐式规划（EmergentPlannerAgent + TODO 列表）
- v6: LLM 重试机制 + ReActEngine Feature Flag

---

### 组件 2：规划系统文档

#### [NEW] [emergent-planning.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/emergent-planning.md)

基于最新 `agents/emergent_planner.py` 源码的隐式规划系统详解：
- 设计理念（Claude Code 启示）
- 系统架构（含 v6 ReActEngine 可选集成）
- 核心算法（TODO 列表管理、while(tool_use) 主循环）
- 数据结构（TodoItem、TodoList、TodoStatus）
- 失败处理（mark_pending 重试机制）
- 与 DAG 规划的对比
- 性能特征

#### [NEW] [hybrid-plan-routing.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/hybrid-plan-routing.md)

混合规划路由详解，更新为三路由（simple/complex/emergent）：
- 架构总览（含 emergent 路径）
- Stage 1 规则快筛（含 _EXPLORATORY_PATTERN 和 _UNCERTAINTY_PATTERN）
- Stage 2 LLM 兜底分类
- 三路由决策逻辑
- 评分维度和关键词模式

#### [NEW] [planning-gap-analysis.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/planning-gap-analysis.md)

更新差距分析，标注已弥补的差距：
- 维度一：规划范式（v5 隐式规划已部分弥补差距 1）
- 维度二：CodeAct（仍为差距）
- 维度三：上下文管理（部分改进）
- 维度四：工具生态（仍为 mock，差距 5/6 未弥补）
- 维度五：错误恢复（v3 工具路由部分弥补）
- 当前状态总结和剩余升级方向

---

### 组件 3：技术深度文档

#### [NEW] [dynamic-features.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/dynamic-features.md)

v1→v2→v3→v4→v5 全版本动态性对比分析：
- v1 静态瓶颈
- v2 六大动态性提升（运行时就绪发现、并行执行、条件分支、失败感知+回滚、局部重规划、状态机）
- v3 三项新动态能力（超步间自适应、工具路由、DAG 运行时变更）
- v4 混合路由的动态性
- v5 隐式规划的动态性（TODO 列表动态演化、无预定义结构）
- 总结对照表

#### [NEW] [data-structures-and-algorithms.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/data-structures-and-algorithms.md)

数据结构与算法详解，新增 v3~v5 内容：
- 基础数据结构（dict、set、DAG、多重边、树、队列）
- 核心算法（Kahn 拓扑排序、BFS、就绪节点发现、FSM）
- 组合应用（Super-step 并行、图合并/局部重规划、TF-IDF）
- v3 新增：图运行时变更、工具路由/熔断器
- v5 新增：TODO 列表数据结构、依赖检查算法
- 算法调用关系全景图

#### [NEW] [llm-integration.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/llm-integration.md)

LLM 集成手册，反映最新实现：
- LLM 集成概述（架构图含 EmergentPlanner）
- 环境配置（含 v6 Feature Flags）
- OpenAI SDK 使用模式
- LLMClient API 参考（含 retry 机制详解）
- 各 Agent 的 LLM 调用模式
- 故障排查

---

### 组件 4：升级与参考文档

#### [NEW] [upgrade-plan.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/upgrade-plan.md)

升级计划，更新已完成/待完成状态：
- 当前架构回顾（v6 状态）
- 升级方向一：真实工具生态（仍为 P1）
- 升级方向二：动态自适应规划（已完成 ✅）
- 升级方向三：流式交互与 Human-in-the-Loop（待完成）
- 升级方向四：增强沙箱环境（待完成）
- 升级方向五：多模型路由（待完成）
- 升级方向六：可观测性（待完成）
- 升级方向七：记忆增强（待完成）
- 更新后的优先级矩阵

#### [NEW] [related-papers.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/related-papers.md)

相关论文综述，保留学术参考并更新与当前实现的关联说明：
- 任务/查询路由（RouteLLM、DAAO 等）
- 自适应推理深度
- 涌现规划相关研究
- 更新各论文与当前 v5/v6 实现的关联度

---

### 组件 5：测试文档

#### [NEW] [planning-test-scenarios.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/planning-test-scenarios.md)

测试用例集，扩展为三路由覆盖：
- 运行方式（含 PLAN_MODE=emergent）
- 测试维度（含 emergent 路径观测点）
- 简单/中等/困难用例（更新预期路由含 emergent）
- 新增 emergent 专属用例

#### [NEW] [emergent-planning-test-scenarios.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/emergent-planning-test-scenarios.md)

隐式规划测试用例集，基于最新实现更新：
- 核心概念（含 mark_pending 重试机制）
- 运行方式
- 基础功能验证
- TODO 列表动态管理
- 复杂探索性任务
- 对比测试

## Verification Plan

### Manual Verification

- 逐一检查 12 篇新文档是否已在 `sxw_aicoding/docs/` 目录下生成
- 验证每篇新文档的核心主题与对应老文档一致
- 验证新文档中引用的源码路径、类名、方法名与当前最新源码一致
- 验证新文档中的代码片段与当前源码匹配
- 验证 Mermaid 图表语法正确


---
生成时间: 2026/4/20 20:05:20
planId: 7891e603-f29a-407a-886b-379b3432e47e
plan_status: review