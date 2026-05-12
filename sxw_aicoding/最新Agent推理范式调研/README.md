# Agent 范式调研资料索引

> 本目录保存针对 Manus Demo 升级所做的业内最新 Agent 推理/规划/执行范式调研。

## 文件说明

| 文件 | 用途 | 阅读顺序 |
|------|------|---------|
| [01-业内最新Agent范式全景调研.md](./01-业内最新Agent范式全景调研.md) | 全景综述：范式分层地图 + 10 个候选范式优先级 + 原始资料索引 | ① 先读 |
| [02-核心论文摘要卡片.md](./02-核心论文摘要卡片.md) | 12 篇关键论文的摘要卡片（方法 / 实验 / 本项目启示） | ② 按需精读 |
| [03-v9融入路线图与实验设计.md](./03-v9融入路线图与实验设计.md) | v8.1 / v9 / v10 / v11 版本升级路线图 + 对照实验设计 | ③ 实施时参考 |

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
