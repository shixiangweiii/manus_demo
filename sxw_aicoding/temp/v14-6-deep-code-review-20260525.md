# V14.6 Evaluation Harness 深度代码评审报告

> **评审日期**: 2026-05-25
> **评审对象**: V14.6 Evaluation Harness / Benchmark Expansion 实施代码
> **评审依据**: `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md` 第六章 v14.6 验收标准
> **评审范围**: `evaluation/` 模块全部源码 + `tests/test_evaluation.py` + `agents/orchestrator.py` 中 checkpoint/resume 相关接线

---

## 一、整体判断

V14.6 在结构上覆盖了 roadmap 列出的 6 个子阶段(Eval Core 解耦 / Benchmark v2 / Verifier / Baseline Gate / Reliability Metrics / Safety Seed),但**核心可靠性指标和"作为质量闸门"的能力存在硬伤**,目前仍属于"形似而神不至"。具体三类风险:

| 等级 | 数量 | 类型 |
|---|---|---|
| **P0** | 3 | 逻辑 bug / 关键能力缺位 — 直接导致评测结论失真,必须先修 |
| **P1** | 4 | 验收标准未真正达到 — 影响 v14.6 作为后续 v15+ 闸门的可信度 |
| **P2** | 5 | 工程债 / 一致性问题 — 不阻塞,但拖累后续维护 |

**关键判断**:
- ✅ Eval Core 解耦(v14.6.1)真正完成,可以离线 collection
- ✅ Benchmark 扩到 38 个,超过 30 的目标
- ⚠️ Verifier 框架建好了但**绝大多数任务没用**(仅 3 个 safety 任务用 keyword_exclude 和 must_not_include 重复)
- ⚠️ Baseline Gate 框架建好了但**没有 baseline 种子文件**,gate 永远 trivially pass
- ❌ Reliability Metrics 中 `resume_success_rate` **聚合公式有 bug**,可能产出 >1.0 的值
- ❌ Resume 任务**没有真正调用 resume harness**,跑成普通任务,reliability 指标实际无意义
- ⚠️ Safety Seed 仅 3 个任务,且全部用 keyword 检测,没有 tool-output injection 真实场景

---

## 二、P0 级问题(必须先修)

### P0-1 `resume_success_rate` 聚合公式错误

**位置**: `evaluation/metrics.py:656-660`

```python
resume_success_rate=(
    sum(1 for r in results if r.execution.task_success) / len(resume_results)
    if (resume_results := [r for r in results if r.task_id.startswith("resume_")])
    else 0.0
),
```

**问题**: 分子遍历的是**全部 `results`**,分母是 `resume_results` 长度。当有 4 个普通任务成功 + 1 个 resume 任务存在时,该指标返回 `4/1 = 4.0` —— 完全失去 rate 含义。

**正确写法**:
```python
resume_success_rate=(
    sum(1 for r in resume_results if r.execution.task_success) / len(resume_results)
    if (resume_results := [r for r in results if r.task_id.startswith("resume_")])
    else 0.0
),
```

**影响**: 这个指标是 v14.6.5 Reliability Metrics 验收的核心字段,roadmap §六明确要求"把 resume_success_rate 纳入 pass@k / reliability 报告"。当前实现一旦上报到 baseline,后续 v15+ 的 regression gate 直接是垃圾输入。

---

### P0-2 Resume 任务没有真正调用 resume harness

**位置**: `evaluation/runner.py:159-171`

```python
orchestrator = OrchestratorAgent(
    llm_client=llm_client,
    tools=self.tools,
    on_event=event_callback,
    interactive=is_hitl_task,
)
...
await orchestrator.run(task.task_description)
```

**问题**: 全模块 grep 不到任何 `resume_execute` / `TaskStateStore` / `orchestrator.resume(` 调用。这意味着即使 benchmark 中有 `task_id` 以 `resume_` 开头的任务,runner 也只是把它当成普通任务跑一遍 `run()`,**完全没有验证"中断 → checkpoint → resume → 完成"这条 v14.5 主线**。

**影响**: roadmap §五明确要求"补 task resume 的 benchmark scenario:中断、resume、HITL paused、TODO completed 不重跑、DAG super-step 后恢复"。当前 runner 没有 inject 中断点(比如 KeyboardInterrupt 模拟、特定 step 后 raise),也没有第二阶段调用 `orchestrator.resume(task_id)`。所以 v14.6.5 中的 resume reliability 指标全部是空壳。

**修复方向**(粗略): 在 `BenchmarkTask` 增加 `resume_scenario` 字段(描述在哪个边界中断、哪个边界恢复),`evaluate_task` 检测到 resume 任务时分两阶段执行,中间通过注入 `_emit` hook 或 monkeypatch 抛 `_TestInterrupt`,然后注入相同 `task_state_store` 实例调用 `resume()`,聚合两阶段的 probe 结果。

---

### P0-3 `checkpoint_avg_count` 字段定义后从未赋值

**位置**: `evaluation/metrics.py:339`

```python
checkpoint_avg_count: float = 0.0           # Resume 任务平均 checkpoint 数
```

**问题**: 定义在 `AggregatedMetrics` 但 `aggregate_results()` 全函数内没有任何 `checkpoint_avg_count=` 赋值;probe 也没有从 `TaskStateStore` 读取 checkpoint 计数的逻辑。

**影响**: roadmap §六明确要求"在 evaluation report 中显示 checkpoint count、resume count、paused count"。当前字段只是占位,所有报告都打 0.0,看不出 checkpoint 行为是否健康。

**修复方向**: probe 在 task 完成后从 `TaskStateStore.list_checkpoints(task_id)` 读取,聚合到 `AggregatedMetrics`。

---

## 三、P1 级问题(影响 v14.6 作为后续闸门的可信度)

### P1-1 缺少 baseline 种子文件 — Regression Gate 永远 trivially pass

**位置**: `evaluation/baselines/`(目录不存在)+ `evaluation/baseline.py:128-133`

```python
missing = baseline_modes - current_modes
if baseline_modes and missing:
    ...
```

**问题**:
1. roadmap §六明确产物:`evaluation/baselines/v14_6_initial.json`。当前**目录都没建**。
2. `compare_baselines` 中 `missing` 的判断是 `baseline_modes and missing`,即空 baseline(没有任何 mode)直接绕过缺失检测,导致空 baseline 永远 trivially pass。

**影响**: v14.6 验收标准里"后续 v15/v17/v18/v19 的 roadmap 验收必须引用 v14.6 baseline"完全无法成立 —— 因为 baseline 还不存在,而代码也容许把空 baseline 当成"全部通过"。

**修复**: 立即在最小可信任务子集(比如 easy + 1-2 medium,共 8-10 个)上跑一次 simple/complex 双 mode,固化为 `evaluation/baselines/v14_6_initial.json`,并 commit 到仓库;`baseline.py` 至少改成"baseline 中有 modes 但 current 缺失才告警,baseline 为空时显式提示需要生成种子"。

---

### P1-2 Verifier 框架建好但绝大多数任务没用

**位置**: `evaluation/benchmark.py` 全文 38 个任务 vs `evaluation/verifiers.py:9 个 verifier 类型`

**问题**: 38 个 BenchmarkTask 中,只有 3 个 safety 任务挂了 `verifiers=[...]`,而且全部是 `keyword_exclude`,与 `must_not_include` **完全功能重复**。其它 35 个任务的成功判定仍然落在:
- `must_include_keywords` 的 substring 匹配
- LLM-as-Judge fallback

这正是 roadmap §六批评的"keyword-only oracle 假阴性/假阳性"问题,verifier 模块本来就是为了解决它。**目前等于建了厨房没做饭**。

**应优先用 verifier 替代关键词的任务类型**:
- file_ops 类(easy_005 写文件、easy_007 README) → `file_exists` + `file_contains`
- code 类(easy_004 求和、medium_003 排序) → `numeric_range` 或 `regex_match` 抓数值
- search 类带具体数字(hard_002 城市人口) → `numeric_range` 容差判定
- DAG 输出 JSON 报告 → `json_field`

**影响**: Verifier 框架的存在没有兑现 roadmap "减少 keyword-only 假阴性/假阳性" 的承诺,后续 v15 memory 评测要靠 verifier 判断"召回的记忆是不是对的",现在没有先例可参考。

---

### P1-3 Runner 没有注入 TaskStateStore — 污染本地用户目录

**位置**: `evaluation/runner.py:159-164`

```python
orchestrator = OrchestratorAgent(
    llm_client=llm_client,
    tools=self.tools,
    on_event=event_callback,
    interactive=is_hitl_task,
)
```

**问题**: 没有传 `task_state_store=`,Orchestrator 会落到默认 `~/.manus_demo/checkpoints`。后果:
- 评测产生的 checkpoint 污染用户真实 checkpoint 目录
- 多次评测/CI 跑会互相干扰
- 想做 P0-2 中的 resume scenario 验证时,因为没有可控 store,二阶段 resume 拿不到一阶段的 checkpoint

**修复**: runner 应在每次评测开始前创建临时 `TaskStateStore(checkpoint_dir=tempfile.mkdtemp())`(或测试 fixture 提供),inject 到 Orchestrator,并在 evaluate_mode 结束后清理。

---

### P1-4 verifier / baseline 完全没有单元测试

**位置**: `tests/` 目录

**问题**:
- `tests/test_evaluation.py` 51 passed,但 grep 不到任何 `run_verifiers`、`save_baseline`、`compare_baselines` 引用
- 没有 `tests/test_verifiers.py` 文件
- 没有 `tests/test_baseline.py` 文件

**影响**: V14.6 三个 P0 子能力(verifier 注册、baseline diff、regression gate 阈值边界)全部没有回归保护。后续任意一次重构或者添加新 verifier 类型,出问题不会被测试发现。这与 roadmap §六的验收标准"`tests/test_evaluation.py` 在无 API key、无 OpenAI SDK 的环境下也能 collection 并通过纯单元部分"相比,虽然形式上 collection 通过了,但**纯单元测试根本没覆盖到 v14.6 新增的代码**。

**修复**: 至少补:
- `test_verifiers.py`: 9 个类型各一个 happy path + 1-2 个 edge case(空 spec、None 路径、非法 regex、composite 嵌套)
- `test_baseline.py`: save/load roundtrip、阈值刚好踩边界、空 baseline、mode 缺失、JSON schema 兼容

---

## 四、P2 级问题(工程债 / 一致性)

### P2-1 `--dry-run` 没有显示 verifier 列

**位置**: `evaluation/eval_cli.py:81 show_benchmark_tasks`

roadmap §六验收要求"`--dry-run` 能列出 30+ benchmark,并显示 difficulty/tag/expected mode/**verifier 类型**"。当前 dry-run 只显示前四列,没有 verifier 列。后果:用户看不出"哪些任务依赖 deterministic verifier、哪些还在裸关键词模式",不利于推动 P1-2 的迁移工作。

### P2-2 默认工具列表硬编码,与 main.py 重复

**位置**: `evaluation/runner.py:94-103` vs `main.py` 工具构造

两处独立 import 6 个 tool 类。一旦后续(v16 MCP)在 main.py 加新 tool,evaluation 这边漏改 → 评测的工具集和真实运行不一致,所有工具相关任务的成功率就失去意义。建议抽 `tools/registry.py:get_default_tools()` 共用。

### P2-3 BENCHMARK_TAGS 列表包含未使用 tag

**位置**: `evaluation/benchmark.py` BENCHMARK_TAGS 常量

`memory`、`rollback` 在 38 个任务中都没有任务使用,仅是 placeholder。要么补任务要么删 tag,避免 dry-run 输出和 tag matrix 报告里出现"该 tag 永远 0 个任务"的空列。

### P2-4 safety 任务 `keyword_exclude` 与 `must_not_include` 完全冗余

**位置**: `evaluation/benchmark.py` safety_001/002/003

```python
must_not_include=[...],
verifiers=[{"type": "keyword_exclude", "params": {"keywords": [...]}}],
```

两套机制查同一组关键词 —— 调试时不清楚谁先生效。建议统一到 verifier 一侧,`must_not_include` 用于纯关键词过滤,verifier 留给结构化检查;safety_001 真正应该用的是 `regex_match`(检测命令注入模式)而不是简单 keyword。

### P2-5 LLM-as-Judge fallback 对 verifier 失败任务也会触发

**位置**: `evaluation/runner.py:_maybe_apply_llm_judge` (211)

当前逻辑只检查 `must_include_keywords` missed,不区分这次失败是 keyword miss 还是 verifier 失败。意味着即使 verifier 给出确定性 false(比如 file_exists 失败),judge 还可能把它翻成 success。这削弱了 verifier 的"确定性"承诺。

**修复**: 在 probe 上区分 `failed_by_verifier` vs `failed_by_keyword`,judge 仅在后者触发。

---

## 五、其他观察

1. **`run_verifiers()` 边界**: 当 `actionable` 全空(全部 verifier passed=None,比如 file 都不存在)时,`all_passed=False`(`verifiers.py:420`),probe 据此把任务标记失败 —— 这与 "verifier 不可判定,应回落到关键词" 的预期不符。建议改为 `all_passed=None` 并在 probe 里识别为"verifier 跳过,转 keyword 路径"。

2. **HITL paused checkpoint 的 disk I/O 在 hot path**: `agents/orchestrator.py:1193` 每次 `ask_user_prompt` 同步写 checkpoint。量小没问题,但 evaluate 时 SimulatedUser 会快速回答,N 次 ask 串行 N 次 disk I/O,影响 efficiency 指标的稳定性。可以考虑在 SimulatedUser 路径下让 checkpoint 异步或合并。

3. **`compute_overall_score` 权重写死**: `metrics.py` 中 30/40/20/10 的 4 维加权是常量。后续 v15+ 引入 memory/safety 维度时,只能改源码 —— 未来该改成 dataclass 配置,baseline 本身记录用了哪个权重表,避免不同版本不可比。

---

## 六、推荐修复顺序

| # | 任务 | 类型 | 估时 |
|---|---|---|---|
| 1 | 修 `resume_success_rate` 公式(P0-1) | 1 行代码 | 5 分钟 |
| 2 | 补 `tests/test_verifiers.py` + `tests/test_baseline.py`(P1-4) | 测试 | 0.5 天 |
| 3 | 生成 `evaluation/baselines/v14_6_initial.json` 种子 + 修 baseline 空检测(P1-1) | 配置 + 1 处代码 | 0.5 天 |
| 4 | 给 5-8 个高价值任务加 verifier(P1-2)— file_ops/code/numeric 优先 | 配置 | 1 天 |
| 5 | runner 注入临时 TaskStateStore(P1-3)+ 实现 resume scenario harness(P0-2)+ 接入 checkpoint_avg_count(P0-3) | 实现 | 1.5 天 |
| 6 | dry-run 加 verifier 列、工具注册表抽公共、删除未用 tag、合并冗余 must_not_include(P2-1/2/3/4) | 工程债 | 0.5 天 |
| 7 | judge fallback 区分失败原因(P2-5) | 实现 | 0.5 天 |

**合计**: 4-5 个工作日。**前 3 项必须在进入 v15 Memory 之前完成**,否则 v15 没有可信 baseline 可比。

---

## 七、验收复核(对照 roadmap §六验收标准)

| 验收项 | 当前状态 | 备注 |
|---|---|---|
| `tests/test_evaluation.py` 在无 OpenAI SDK 环境下 collection 并通过 | ✅ 通过(51 passed) | 但未覆盖 v14.6 新代码 |
| `eval_cli --dry-run` 列出 30+ task 并显示 difficulty/tag/expected mode/**verifier 类型** | ⚠️ 部分通过 | 缺 verifier 列 |
| `eval_cli --tasks easy_002 --modes simple --output /tmp/eval.json` 能跑最小 smoke | ✅ 可跑 | 无 API key 报错清晰 |
| 增加 `--baseline ... --fail-on-regression` 命令 | ✅ 已实现 | 但无 baseline 种子文件 |
| 报告新增 tag 维度聚合(HITL/SubAgent/GoalDriven/Resume/Safety) | ✅ render_tag_matrix 已实现 | Resume tag 实际无意义(P0-2) |
| 后续 v15+ 验收必须引用 v14.6 baseline | ❌ 不可执行 | baseline 种子缺失 |
| Reliability Metrics 接入(pass@k / resume_success_rate / checkpoint_count) | ❌ 严重缺陷 | P0-1/P0-3 |
| Deterministic Verifier 减少假阳性/假阴性 | ⚠️ 框架在,实际未启用 | P1-2 |

---

## 八、结论

V14.6 的**结构性工作做得不错** —— Eval Core 解耦真正落地,benchmark 扩到 38 个,verifier / baseline / tag matrix / pass@k / SimulatedUser 都搭好了骨架。但**关键的可信度兑现没有完成**:

1. Resume 评测整条链路没接通(P0-2),配套指标公式还有 bug(P0-1, P0-3)
2. Verifier 没真正用起来(P1-2),baseline gate 没有种子(P1-1)
3. 新增能力没有单元测试保护(P1-4)

按当前状态进入 v15 Memory 评测,**很可能复制 v14.6 的"形式完成"问题**:写一堆 memory 任务但没有可对比 baseline,改完 memory 实现也分不清是真变好还是评测器抖动。

**强烈建议**:在 v15 启动前完成 P0-1/P0-3 + P1-1/P1-4(2-3 天工作量),把 v14.6 baseline 种子文件 commit 到仓库,作为后续所有架构实验的入门门槛。P0-2 和 P1-2 可以排在 v14.6.7 fix pass 中并行推进。
