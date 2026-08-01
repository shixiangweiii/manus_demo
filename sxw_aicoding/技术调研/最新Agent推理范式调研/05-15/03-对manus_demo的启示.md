# 对 manus_demo 的启示与落地建议

> 本文把 [01-论文综述清单](./01-论文综述清单.md) 与 [02-博客资料速读](./02-博客资料速读.md) 的发现，逐条 mapping 到现有代码模块，给出**最小可落地**的小步改造建议。
> 推进哲学：**不引入新范式，先把现有 v8/v9 跑稳跑省**。

---

## 一、按优先级排序的 4 个落地点

| # | 名称 | 优先级 | 投入 | 触达模块 | 来源 |
|---|------|--------|------|---------|------|
| A | Belief-Deviation 早停诊断 | ⭐⭐⭐ | 2-3 天 | `react/engine.py` | T3 (ICLR 2026 Oral) |
| B | 成本-效果 Pareto 评估 | ⭐⭐⭐ | 1-2 天 | `evaluation/runner.py` | Toward Efficient Agents 综述 |
| C | 轨迹聚类 / 异常模式识别 | ⭐⭐ | 3-5 天 | `tracing/` 新增子模块 | Anthropic Clio 解读 |
| D | PLAN_MODE × 五分类法 mapping 文档 | ⭐ | 半天 | `sxw_aicoding/docs/` | Planning Survey 五分类 |

---

## 二、详细落地方案

### A. Belief-Deviation 早停诊断（来源：T3 ICLR 2026 Oral）

**问题**: 当前 `EmergentPlanner` 在长链路下会"重复调用同一工具 / 忽略历史 observation"，仅靠 `MAX_REACT_ITERATIONS=10` 兜底过于粗糙。

**最小实现**（不做 RL 训练）:

```python
# react/engine.py 内伪代码
class ReActEngine:
    async def execute(self, ...):
        belief_history: list[str] = []   # 每轮 LLM 对"剩余子目标"的简短判断
        for i in range(max_iterations):
            ...
            # 在 think_with_tools 之后，增量记录 belief
            belief_now = self._extract_belief(response)  # 让 LLM 输出 belief 字段
            belief_history.append(belief_now)

            # 检测是否 belief-trapped
            if self._is_belief_trapped(belief_history, window=3):
                self._emit("belief_deviation_detected", {"history": belief_history})
                break  # 提前终止，交还给上层重新规划
```

**判定规则建议**:
- 连续 3 轮 belief 文本相似度 > 0.9（用 embedding 或简单字符串 SimHash）
- 或 belief 中包含 "stuck" / "无法" / "重复" 等关键词

**评估**:
- 在 `evaluation/runner.py` 里增加一项 `belief_deviation_caught` 指标，统计触发次数
- 跑 12 任务 benchmark，观察成功率和 token 消耗变化

**产出**: 一个新事件 `belief_deviation_detected` + 一个 `evaluation` 维度 + README 一段说明

---

### B. 成本-效果 Pareto 评估（来源：Toward Efficient Agents 综述）

**问题**: 现有 4 维评分把 Efficiency 压成一个标量，看不出"换成 simple 模式能省多少 token、损失多少质量"。

**最小实现**:

```python
# evaluation/runner.py 增加方法
def cost_effectiveness_curve(self, results: list[BenchmarkResult]) -> dict:
    """每种 PLAN_MODE 在(总 token, 总成功率)平面上是一个点。"""
    pareto_points = []
    for mode in ["simple", "complex", "emergent"]:
        mode_results = [r for r in results if r.mode == mode]
        avg_tokens = sum(r.tokens for r in mode_results) / len(mode_results)
        success_rate = sum(r.success for r in mode_results) / len(mode_results)
        pareto_points.append({
            "mode": mode,
            "avg_tokens": avg_tokens,
            "success_rate": success_rate,
            "tokens_per_success": avg_tokens / max(success_rate, 1e-6),
        })
    return {"points": pareto_points, "frontier": self._compute_pareto_frontier(pareto_points)}
```

**输出**:
- JSON 文件附加 `cost_effectiveness` 字段
- README 增加"如何选 PLAN_MODE"决策树（基于实测数据）

**长期价值**: 当未来加入 v9.x / v10 模式时，可一目了然看是否帕累托劣解（dominated）。

---

### C. 轨迹聚类 / 异常模式识别（来源：Anthropic Clio / Petri / Bloom 路线）

**问题**: v7 tracing 已能完整记录轨迹，但**只能人肉读 span**；当 trace 数量过百，没人会看。

**最小实现**:

```
tracing/analysis/  # 新建子模块
├── __init__.py
├── extractor.py   # 从 trace JSON 提取关键序列：tool 调用模式、迭代次数、子目标完成度
├── clusterer.py   # 用简单 TF-IDF + KMeans 把轨迹聚成 K 类
└── reporter.py    # 输出 markdown 报告：每类一个代表性 trace + 计数
```

**用法**:
```bash
python -m tracing.analysis cluster --input traces/ --k 5 --output report.md
```

**第一阶段目标**: 能识别三类异常
1. **重复型**：同一 tool 连续调用 ≥ 3 次且无新参数
2. **失败循环**：同一 error message 出现 ≥ 2 次
3. **上下文爆炸**：单条 LLM call 的 prompt token 超过模型上限的 80%

---

### D. PLAN_MODE × 五分类法 mapping 文档（来源：Planning Survey）

**问题**: 我们 README 用 v1/v2/v5/v8 这种"版本号"称呼，对外人不友好。

**实现**: 在 `sxw_aicoding/docs/` 加一篇 `planning-paradigm-mapping.md`：

| PLAN_MODE | 项目内部命名 | Planning Survey 五分类 | 代表论文 |
|-----------|-------------|-----------------------|----------|
| `simple` | v1 Flat Plan | **任务分解**（Task Decomposition）的最简形式 | HuggingGPT, ReAct |
| `complex` | v2 DAG | **任务分解** + **外部模块**（DAG 调度器）| LLM Compiler, ADaPT |
| `emergent` (`ENABLE_GOAL_DRIVEN_PLANNER=false`) | v5 Emergent TODO | **计划选择**（动态生成 + 选择 TODO）| Plan-and-Solve |
| `emergent` (`ENABLE_GOAL_DRIVEN_PLANNER=true`) | v8 Goal-Driven | **反思** + **记忆**（goal anchoring + reflection 闭环）| ReflAct, Reflexion |
| 任何路径 + `SUBAGENT_ENABLED=true` | v9 SubAgent | 跨分类（多 Agent + 工具粒度）| Claude Code Subagent |

加上一段："本表参考《Understanding the Planning of LLM Agents: A Survey》的五分类法。"

---

## 三、不建议跟进的方向

| 方向 | 原因 |
|------|------|
| Latent Space 推理（论文 04） | 与当前 token-level prompt 路线正交，需要训练或改模型；教学 demo 不合适 |
| Model-native paradigm（PORTool RL / Memory-R1） | 需要 RL 训练基础设施，远超 demo 范围；建议留作 v11+ 占位 |
| 大规模多 Agent 网络（MS 红队场景） | 05-13 调研已结论"单 Agent 优先"，本期博客也印证；保持 depth=1 SubAgent 即可 |

---

## 四、本期调研之后的两件"不写代码也要做"的事

1. **README 显性声明评估弱点**：当前 LLM-as-judge，没有人工 ground truth。Lilian Weng *Agent 评估* 一文是对此最有力的推动。
2. **README 加 disclaimer**：本项目生成的代码与建议须人工审阅，不可"vibe accept"。Simon Willison 的 normalization-of-deviance 论点是依据。

---

## 五、四件事的预期收益矩阵

```
              低投入                                高投入
  高收益  │  D mapping 文档              A belief-deviation 诊断
         │  B Pareto 评估
         │
  低收益  │  README disclaimer           C 轨迹聚类（v1）
         │
```

**首推顺序**: D（半天）→ B（1-2 天）→ A（2-3 天）→ C（3-5 天，作为 v9.x 阶段实验）。
