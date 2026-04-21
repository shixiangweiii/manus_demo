
# 文档评审问题修复计划

根据对评审报告的反思复核，原报告 6 个问题中有 3 个不成立（P1、P5原文误读、P6），3 个确认需要修复。

## Proposed Changes

### 组件一：版本号更新（P2）

#### [MODIFY] [main.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/main.py)
- 将欢迎横幅中的 `Manus Demo v5` 更新为 `Manus Demo v6`
- 补充 ReActEngine 和 LLM Retry 新特性说明到横幅描述中

---

### 组件二：JSON 解析方法注释修正（P3）

#### [MODIFY] [client.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/llm/client.py)
- 修正 `_parse_json` 方法的文档注释，将"处理三种常见格式"改为"处理两种常见格式"
- 移除注释中对第三种格式的提及

#### [MODIFY] [llm-integration.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/llm-integration.md)
- 在 JSON Parsing Strategy 章节中，明确标注实际实现为 **Two-strategy fallback**
- 保留花括号提取作为"未来扩展"的说明，但不再将其计入当前策略数量

---

### 组件三：CHANGELOG v6.0 章节补充（P4）

#### [MODIFY] [CHANGELOG.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/CHANGELOG.md)
- 在 v5.0 章节之前插入独立的 v6.0 章节，包含：
  1. **ReActEngine 统一引擎抽取**：说明从 `ExecutorAgent._react_loop` 和 `EmergentPlannerAgent._execute_todo` 中抽取出公共 ReAct 引擎（`react/engine.py`，219行），消除代码重复
  2. **LLM 调用重试机制**：说明指数退避重试策略、可配置参数
  3. **Feature Flags 向后兼容设计**：两个新功能默认关闭

---

### 组件四：工具描述优化（P5，可选）

#### [MODIFY] [upgrade-plan.md](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/sxw_aicoding/docs/upgrade-plan.md)
- 将"仅 3 个工具，搜索是 mock 实现"补充为"仅 3 个工具（1 个 mock: web_search + 2 个 real: execute_python, file_ops），搜索是 mock 实现"

---

## Verification Plan

### Manual Verification
- 逐一检查修改后的文件，确认内容与源码一致
- 确认 CHANGELOG.md 中 v6.0 章节的描述与 `react/engine.py`、`llm/client.py`、`config.py` 的实际实现匹配


---
生成时间: 2026/4/21 10:48:53
planId: 52ab417a-6e66-4618-82b9-a00f3f63d34f
plan_status: review