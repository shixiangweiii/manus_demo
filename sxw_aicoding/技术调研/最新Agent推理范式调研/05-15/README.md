# 2026-05-15 Agent 推理/规划 增量调研索引

> 在 [05-13 多 Agent 专题](../05-13/) 基础上，对"近 30 天新增的 Agent 推理 / 规划论文与博客"做的一次增量补充。
> 检索通道：MCP `bailian_web_search`（内置 WebSearch 当日返回 400）。检索时间：2026-05-15。

## 文件说明

| 文件 | 用途 | 阅读顺序 |
|------|------|---------|
| [01-论文综述清单.md](./01-论文综述清单.md) | 7 篇高价值论文/综述卡片（Agentic Reasoning 综述、Efficient Agents、T3 ICLR Oral、Latent Space 综述等） | ① 先读 |
| [02-博客资料速读.md](./02-博客资料速读.md) | 8 篇本周必看博客（Lilian Weng *Why We Think*、Simon Willison、Apple PORTool、MS 红队等） | ② 通勤时刷 |
| [03-对manus_demo的启示.md](./03-对manus_demo的启示.md) | 把上述资料逐条 mapping 到 manus_demo 现有模块，给出可落地的小步改造点 | ③ 决策时看 |

## 一句话总结

本期调研没有"颠覆性范式"出现（CodeAct/LATS/Subagent 仍是 05-13 调研给出的主轴），但有三件事**值得立刻吸收到 v9.x 路线图**：

1. **T3 (ICLR 2026 Oral)** — 用 belief tracking 解释多轮 ReAct"越走越偏"，对应我们 `EmergentPlanner` 的退化场景，应加"信念漂移"诊断指标。
2. **Toward Efficient Agents（上海 AI Lab 9 校综述）** — 将记忆/工具/规划三件套统一到「成本-效果 Pareto 前沿」评估，可直接补强 `evaluation/` 第五个维度。
3. **Lilian Weng *Agent 上线前先做评估*** — 大部分 Agent 项目失败根因在缺基线，正面回应我们 `evaluation/runner.py` 的设计哲学。

## 与 05-13 调研的关系

| 维度 | 05-13 | 05-15（本期） |
|------|-------|---------------|
| 重点 | 范式全景 + 多 Agent 工程 | 推理/规划 **稳定性 & 效率** 增量 |
| 论文数 | 12 + 12 = 24 | 7（增量） |
| 主线推荐 | v9 CodeAct/LATS、v10 CoA/MUSE | v9.1 belief-deviation 诊断 + 效率 Pareto 评估 |
| 决策结论 | 单 Agent 优先；必上多 Agent 选 Orchestrator-Worker | 不引入新范式；先把现有 v8/v9 跑稳跑省 |

## 资料来源透明度声明

- 本调研所有"论文摘要"均来自检索引擎返回的二次解读片段（CSDN / 知乎 / 腾讯云 / 智源 / 新浪等），未单独打开原始 arXiv PDF；引用前请读者自行核对原文。
- 部分 arXiv ID 在搜索片段中被截断或写作 "2601.xxxxx" 形式（疑似 26 年 1 月编号），未必准确，已在卡片中以"⚠️ 编号待核"标注。
- 博客链接以 Lilian Weng / Simon Willison / Apple Research / Microsoft Research / Towards Data Science 等一手来源为准；中文聚合站点仅作辅助。
