# Agent 范式调研资料索引

> 本目录保存针对 Manus Demo 升级所做的业内最新 Agent 推理/规划/执行范式调研。

## 文件说明

| 文件 | 用途 | 阅读顺序 |
|------|------|---------|
| [01-业内最新Agent范式全景调研.md](./01-业内最新Agent范式全景调研.md) | 全景综述：范式分层地图 + 10 个候选范式优先级 + 原始资料索引 | ① 先读 |
| [02-核心论文摘要卡片.md](./02-核心论文摘要卡片.md) | 12 篇关键论文的摘要卡片（方法 / 实验 / 本项目启示） | ② 按需精读 |
| [03-v9融入路线图与实验设计.md](./03-v9融入路线图与实验设计.md) | v8.1 / v9 / v10 / v11 版本升级路线图 + 对照实验设计 | ③ 实施时参考 |
| [04-多Agent架构全景与工业旗舰实践.md](./04-多Agent架构全景与工业旗舰实践.md) | **多 Agent 专题**：Anthropic / Magentic-One / Cognition / Cursor / LangGraph 等 8 大旗舰系统架构对比 + 5 大拓扑矩阵 | ④ 多 Agent 方向先读 |
| [05-多Agent学术前沿论文卡片.md](./05-多Agent学术前沿论文卡片.md) | **多 Agent 专题**：12 篇协作层论文（Magentic-One / MAS 失败学 / AFlow / MoA / Debate-or-Vote 等） | ⑤ 按需精读 |
| [06-多Agent反模式与选型决策指南.md](./06-多Agent反模式与选型决策指南.md) | **多 Agent 专题**：10 大反模式 + 单/多 Agent 决策树 + 拓扑选择矩阵 + 12 项上线 Checklist | ⑥ 决策时必看 |

## 一图流速览

```
                 ┌─── CodeAct   (执行层革命, 2024 ICML)       → v9-A
                 │
                 ├─── LATS MCTS (推理层搜索, 2024 ICML)        → v9-B
   业内最新范式 ──┤
                 ├─── Chain-of-Agents (协作层, Google 2024)   → v10-B
                 │
                 ├─── MUSE (自进化, 2025.10)                  → v10-C
                 │
                 ├─── Claude Code Subagent (工程最佳实践)      → v10-A
                 │
                 ├─── Self-Critique Paradox (Snorkel 2025)    → v8.1 快速修补
                 │
                 ├─── DeepSeek-R1 / o1 (Model-native)        → v11 对照组
                 │
                 └─── ReflAct (EMNLP 2025)                    ← v8 已融入 ✓
```

## 关键结论三句话

1. **当前项目已融入 ReflAct（v8），但还缺 CodeAct / LATS / Subagent / 自进化记忆四大前沿范式**。
2. **推荐按 v8.1 → v9 → v10 → v11 四步推进**，每步引入 1-3 个范式 + 对照实验。
3. **v11 必须加 Model-native 对照组**（接 R1/o3），否则教学 Demo 与业界脱节。

## 原始论文与资料快速入口

### 必读综述
- Beyond Pipelines Survey (2025.10): https://arxiv.org/abs/2510.16720
- Self-Evolving Agents Survey (2025.07): https://arxiv.org/html/2507.21046v4

### v9 核心论文
- CodeAct (ICML 2024): https://machinelearning.apple.com/research/codeact
- LATS (ICML 2024): https://arxiv.org/abs/2310.04406

### v10 核心论文
- Chain-of-Agents (NeurIPS 2024): https://arxiv.org/abs/2406.02818
- MUSE (2025.10): https://arxiv.org/abs/2510.08002
- Memp (2025.08): https://arxiv.org/html/2508.06433v2

### v11 对照组
- DeepSeek-R1 (2025.01): https://arxiv.org/abs/2501.12948

### 工程参考
- Claude Code Architecture: https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- OpenManus (MetaGPT): https://github.com/FoundationAgents/OpenManus

## 与二次评审的联动

本调研与先前 v8 评审互补：

| 评审 / 调研 | 关注点 | 产出位置 |
|-----------|-------|---------|
| 工业视角评审 | v8 上线风险 | `sxw_aicoding/temp/v8-goal-driven-planner-code-review.md` |
| 研究视角复核 | v8 学术思想落地度 | `sxw_aicoding/temp/v8-goal-driven-planner-review-v2-research-lens.md` |
| **本调研** | **下一批可融入的前沿范式** | **本目录** |

**路径建议**:
1. 先按 "v2 研究视角复核" 第六节必修 5 项加固 v8
2. 再按本目录 `03-v9融入路线图.md` 推进 v9/v10/v11
3. 上多 Agent 前必读 `06-多Agent反模式与选型决策指南.md`，完成 12 项上线 Checklist

---

## 2026-05-13 多 Agent 专题增补

在原有 3 份通用调研基础上，新增 3 份**多 Agent / Subagent 架构**聚焦文档：

```
                 ┌─── 04 工业旗舰实践 ───┐
  多 Agent 专题 ─┤    8 大系统 / 5 大拓扑矩阵 │
                 ├─── 05 学术论文卡片 ───┤
                 │    12 篇协作层论文     │
                 └─── 06 反模式与决策 ────┘
                      10 反模式 + 决策树
```

**阅读顺序建议**：04 了解现状 → 05 了解前沿 → 06 做决策。

**关键结论速览**：
1. **行业共识正在回摆**：2025 下半年以来 Cognition / Anthropic 公开讨论"**慎用多 Agent**"；**单 Agent 能解决就不上多 Agent** 已成主流原则
2. **若必须多 Agent，首选 Orchestrator-Worker + depth=1 Subagent**（Claude Code / Anthropic Research 风格），最克制也最稳健
3. **不推荐 Debate、Network Handoff、多层 Hierarchical**（分别踩 Self-Critique Paradox / 死循环 / 角色爆炸）
4. **v8 反模式自检结论**：当前架构无高风险，只需 v8.1 修补 `_goal_reflect` 的 Self-Critique Paradox

