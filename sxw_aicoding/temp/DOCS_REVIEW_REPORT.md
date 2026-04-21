# 文档评审报告 — sxw_aicoding/docs/

> 评审日期：2026-04-21
> 评审范围：sxw_aicoding/docs/ 下全部 12 个文档
> 评审方法：逐一对比文档描述与实际源码实现

---

## 总体评价

文档整体质量很高，架构描述、设计理念和核心代码逻辑基本准确。
共发现 **6 个问题**，其中 1 个高严重度、2 个中严重度、3 个低严重度。
另有 6 个文档经验证与源码完全一致。

---

## 问题清单

### P1（高）— codemap.md 文件行数全部过时

| 项目 | 详情 |
|------|------|
| 文档 | `codemap.md` |
| 位置 | 全部组件的行数标注 |
| 问题 | 文档标注了每个文件的精确行数（如 orchestrator.py: 432 lines, planner.py: 884 lines），但实际源码行数与文档标注有 **30-50% 偏差** |
| 影响 | 读者根据文档行数去定位代码会产生误导 |
| 建议 | 要么定期同步行数，要么移除精确行数改为"约 XXX 行"，或完全删除该列 |

---

### P2（中）— main.py 欢迎横幅版本号滞后

| 项目 | 详情 |
|------|------|
| 文档 | `main.py` 源码 + `CHANGELOG.md` |
| 位置 | `main.py:329` 欢迎横幅 |
| 问题 | 欢迎横幅显示 **"Manus Demo v5"**，但代码已包含 v6.0 特性：ReActEngine Feature Flag (`react/engine.py`)、LLM 重试机制 (`llm/client.py`)、配置项 v6 flags (`config.py:73-79`) |
| 影响 | 用户交互界面显示的版本号与实际功能版本不一致 |
| 建议 | 横幅更新为 "Manus Demo v6"，并补充 ReActEngine 和 LLM Retry 新特性说明 |

---

### P3（中）— llm-integration.md JSON 解析策略描述不准确

| 项目 | 详情 |
|------|------|
| 文档 | `llm-integration.md` |
| 位置 | "JSON Parsing Strategy" 章节 |
| 问题 | 文档声称 **三种** 回退策略：直接解析、代码块提取、花括号提取（brace extraction）。但 `llm/client.py:196-225` 的 `_parse_json` 只实现了 **两种**：直接 `json.loads()` 和 Markdown 代码块正则提取。**不存在第三种 brace extraction 策略** |
| 影响 | 读者误以为系统有更强的 JSON 容错能力 |
| 建议 | 修正为 "Two-strategy fallback"，或补充实现 brace extraction |

---

### P4（低）— CHANGELOG.md v6.0 未提及 ReActEngine 抽取

| 项目 | 详情 |
|------|------|
| 文档 | `CHANGELOG.md` |
| 位置 | v6.0 章节 |
| 问题 | v6.0 部分描述了 LLM retry 和 `ENABLE_REACT_ENGINE_V2` Feature Flag，但 **未说明 `ReActEngine` 是从 `ExecutorAgent._react_loop` 和 `EmergentPlannerAgent._execute_todo` 中抽取出来的公共引擎**（`react/engine.py`，219 行）。这是 v6 的重要架构改进（消除代码重复） |
| 影响 | 读者不了解 v6 的架构改进全貌 |
| 建议 | 在 v6.0 条目中补充 ReActEngine 抽取的说明 |

---

### P5（低）— upgrade-plan.md 工具数量描述不精确

| 项目 | 详情 |
|------|------|
| 文档 | `upgrade-plan.md` |
| 位置 | "Current status review" 部分 |
| 问题 | 文档说 "only 3 mock tools"，但实际验证：`WebSearchTool` 返回 mock 结果，`CodeExecutorTool` 和 `FileOpsTool` 均为真实执行工具。应为 **"1 mock + 2 real tools"** |
| 影响 | 轻微，不影响整体理解 |
| 建议 | 修正为 "1 mock tool (web_search) + 2 real tools (execute_python, file_ops)" |

---

### P6（低）— hybrid-plan-routing.md 路由图 emergent 路径标注不够清晰

| 项目 | 详情 |
|------|------|
| 文档 | `hybrid-plan-routing.md` |
| 位置 | 路由流程图 |
| 问题 | `orchestrator.py:158-171` 中 classify_task 返回 "emergent" 时走 `else` 分支（非 simple 也非 complex 的都走 emergent）。文档路由图应明确标注这个 else 分支就是 emergent 路径，当前表述不够直接 |
| 影响 | 轻微，路由行为实际正确 |
| 建议 | 在路由图中明确标注 emergent 路径对应 else 分支 |

---

## 验证通过的文档

以下文档经验证与源码完全一致，内容准确：

| 文档 | 验证要点 |
|------|----------|
| `data-structures-and-algorithms.md` | Kahn 算法、BFS、状态机转移表、TF-IDF 实现均与源码一致 |
| `emergent-planning.md` | 核心算法流程、数据结构、温度参数（0.3）、_compile_answer 简单汇总逻辑均正确 |
| `emergent-planning-test-scenarios.md` | 测试用例期望行为、mark_pending 重试机制、TODO 数据结构均正确 |
| `planning-test-scenarios.md` | 路由测试用例、_EXPLORATORY_PATTERN 匹配行为与源码一致 |
| `dynamic-features.md` | v1-v5 版本间能力对比表准确，v5 不支持 Partial Replanning 描述正确 |
| `planning-gap-analysis.md` | 14 个 Gap 分析准确，标注 "Partially mitigated" 的 3 项与源码状态一致 |
| `related-papers.md` | 论文列表与设计决策映射合理，未发现过时内容 |

---

## 问题汇总

| # | 文档 | 问题 | 严重度 | 建议行动 |
|---|------|------|--------|----------|
| 1 | codemap.md | 所有文件行数标注过时（偏差 30-50%） | **高** | 更新或删除行数标注 |
| 2 | main.py + CHANGELOG | 欢迎横幅仍显示 v5 | **中** | 更新为 v6 |
| 3 | llm-integration.md | JSON 解析策略说三种，实际只有两种 | **中** | 修正为两种 |
| 4 | CHANGELOG.md | v6.0 未提及 ReActEngine 抽取 | 低 | 补充说明 |
| 5 | upgrade-plan.md | "3 mock tools" 描述不精确 | 低 | 修正为 1 mock + 2 real |
| 6 | hybrid-plan-routing.md | emergent 路由路径标注不够清晰 | 低 | 优化流程图 |
