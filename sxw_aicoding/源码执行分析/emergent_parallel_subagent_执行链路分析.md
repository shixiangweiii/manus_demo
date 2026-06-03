# SUBAGENT_ENABLED=true EMERGENT_PARALLEL_TODOS=true PLAN_MODE=emergent 执行链路分析

## 命令
```bash
SUBAGENT_ENABLED=true EMERGENT_PARALLEL_TODOS=true PLAN_MODE=emergent python main.py "调研Python异步编程、Django和FastAPI三个主题，分别总结优缺点"
```

## 源码调用链路

```
main.py:main()
  ├─ sys.argv 解析 → run_single(task="调研Python异步编程...")   # main.py:1062
  │
  └─ asyncio.run(run_single(task))                             # main.py:1064
       ├─ LLMClient()                                          # main.py:855
       ├─ _build_tools() → [WebSearchTool, FetchUrlTool, ...]  # main.py:856
       └─ OrchestratorAgent.__init__()                         # main.py:858
            ├─ SUBAGENT_ENABLED=true → 创建 SubAgentTool 并追加到 tools  # orchestrator.py:141-151
            └─ TRACING 桥接 / HITL / Handoff 等初始化           # orchestrator.py:130-199
            │
            └─ orchestrator.run(task)                          # main.py:866
                 │
                 ├─ Phase 1: _gather_context()                 # orchestrator.py:479
                 │    ├─ 长期记忆/知识库检索
                 │    └─ short_term.add(user_task)
                 │
                 ├─ Phase 2: planner.classify_task(task)        # orchestrator.py:484
                 │    └─ 两阶段分类器(规则+LLM) → "emergent" (强制 by PLAN_MODE=emergent)
                 │
                 ├─ _emit("task_complexity", {"complexity":"emergent"})
                 │
                 └─ Phase 3: _execute_emergent(task, ctx, effort)  # orchestrator.py:506
                      │
                      └─ EmergentPlannerAgent.execute()         # emergent_planner.py:153
                           │
                           ├─ _init_todo_list(task, ctx)       # emergent_planner.py:188
                           │    └─ LLM 生成 TODO 列表 (3个调研主题 → 3个独立 TODO)
                           │
                           └─ _run_emergent_loop()              # emergent_planner.py:226
                                │
                                ├─ while has_pending():         # emergent_planner.py:238
                                │    │
                                │    ├─ get_ready_todos() → 3个无依赖的 TODO 全部 ready
                                │    │
                                │    ├─ 并行判定:                # emergent_planner.py:281
                                │    │   EMERGENT_PARALLEL_TODOS=true
                                │    │   && "subagent" in tools  ✓
                                │    │   && len(ready) >= 2     ✓ (3个)
                                │    │   → parallel_eligible = True
                                │    │
                                │    ├─ _execute_todos_parallel([todo1, todo2, todo3])  # emergent_planner.py:293
                                │    │    │
                                │    │    ├─ 预算检查: budget = max_calls - used_calls  # emergent_planner.py:804
                                │    │    │
                                │    │    ├─ asyncio.gather(                            # emergent_planner.py:832
                                │    │    │      _dispatch_one_subagent(todo1),
                                │    │    │      _dispatch_one_subagent(todo2),
                                │    │    │      _dispatch_one_subagent(todo3),
                                │    │    │   )
                                │    │    │
                                │    │    └─ 对每个 TODO 并发执行:
                                │    │         _dispatch_one_subagent(todo_i)   # emergent_planner.py:858
                                │    │           ├─ todo_list.mark_in_progress()
                                │    │           ├─ subagent_tool.traced_execute(task_description=...)  # emergent_planner.py:890
                                │    │           │    │
                                │    │           │    └─ SubAgentTool.execute()   # tools/subagent_tool.py:111
                                │    │           │         ├─ 预算/白名单校验 (排除 subagent → depth=1)
                                │    │           │         ├─ 创建 sandbox 子目录
                                │    │           │         ├─ SubAgent.__init__(...)  # tools/subagent_tool.py:235
                                │    │           │         │    ├─ 独立 ReActEngine
                                │    │           │         │    ├─ 独立 messages[]
                                │    │           │         │    └─ tools 不含 subagent (结构性 depth=1)
                                │    │           │         │
                                │    │           │         ├─ async with semaphore:      # tools/subagent_tool.py:252
                                │    │           │         │    │
                                │    │           │         │    └─ SubAgent.run(context="")  # agents/subagent.py:266
                                │    │           │         │         ├─ _on_react_iteration()  # 每轮 token 预算检查
                                │    │           │         │         │    └─ SubAgentTokenExhausted 异常熔断
                                │    │           │         │         │
                                │    │           │         │         ├─ ReActEngine.execute()  # react/engine.py
                                │    │           │         │         │    └─ ReAct 循环:
                                │    │           │         │         │         ├─ LLM think_with_tools()
                                │    │           │         │         │         │    ├─ web_search (Bailian/DDGS)
                                │    │           │         │         │         │    ├─ fetch_url (Bailian MCP)
                                │    │           │         │         │         │    └─ ...
                                │    │           │         │         │         ├─ tool.execute() → ToolRouter 跟踪
                                │    │           │         │         │         └─ build_convergence_hint() (≥3次搜索时)
                                │    │           │         │         │
                                │    │           │         │         └─ _summarize_result()  # 生成 SubAgentSummary
                                │    │           │         │              ├─ model_validate 兜底
                                │    │           │         │              └─ 返回 JSON 结构化摘要
                                │    │           │         │
                                │    │           │         └─ return summary_text (JSON)
                                │    │           │
                                │    │           └─ → StepResult(success=True, output=summary)
                                │    │
                                │    └─ wave_results: [(todo1, r1), (todo2, r2), (todo3, r3)]
                                │
                                ├─ 逐个处理结果:                 # emergent_planner.py:303
                                │    ├─ success → mark_completed + emit("todo_complete")
                                │    └─ failed → retry / mark_blocked
                                │
                                ├─ _update_todo_list() 周期性 review
                                │
                                └─ _compile_answer(task, all_results)  # emergent_planner.py:975
                                     └─ LLM 综合三个 TODO 结果 → 最终答案

                 ├─ Phase 4: Token 汇总                          # orchestrator.py:520
                 │    └─ _finalize_token_usage() → emit("token_usage_summary")
                 │
                 ├─ _store_memory() → 存入长期记忆              # orchestrator.py:522
                 │
                 └─ emit("task_complete", {"answer": final})    # orchestrator.py:526
```

## 关键设计要点

| 环节 | 机制 | 源码位置 |
|------|------|---------|
| **强制路由** | `PLAN_MODE=emergent` 经 `config.py` 注入，`classify_task` 直接返回 `"emergent"` | `config.py` / `agents/planner.py` |
| **并行触发条件** | `EMERGENT_PARALLEL_TODOS` + `SUBAGENT_ENABLED` + ≥2 个 ready TODO | `emergent_planner.py:281` |
| **并发控制** | `asyncio.gather` + `SUBAGENT_MAX_CONCURRENT` 信号量限流 | `emergent_planner.py:832` / `subagent_tool.py:252` |
| **隔离保障** | 独立 messages / 独立 ReActEngine / 独立 sandbox 目录 | `agents/subagent.py` / `subagent_tool.py:225` |
| **结构性 depth=1** | `_BLOCKED_TOOLS` 始终排除 `subagent`，子代无法递归派生 | `subagent_tool.py:170` |
| **Token 熔断** | `_on_react_iteration` 每轮检查累积 tokens，超限抛 `SubAgentTokenExhausted` | `agents/subagent.py` |
| **摘要返回** | `_summarize_result()` 生成 `SubAgentSummary` JSON（accomplished/findings/issues/artifacts） | `agents/subagent.py` |

## 执行流程总结

三个主题（Python异步、Django、FastAPI）会被 `_init_todo_list` 拆成 3 个无依赖 TODO，命中并行分支后**同时** spawn 3 个隔离 SubAgent 并发调研，各自调用 `web_search` / `fetch_url` 收集信息，完成后汇总到一个 LLM 综合答案。

### 核心并发路径
```
EmergentPlannerAgent._run_emergent_loop()
  → _execute_todos_parallel([todo1, todo2, todo3])
    → asyncio.gather(
        _dispatch_one_subagent(todo1) → SubAgent-1.run() → ReActEngine
        _dispatch_one_subagent(todo2) → SubAgent-2.run() → ReActEngine
        _dispatch_one_subagent(todo3) → SubAgent-3.run() → ReActEngine
      )
```

### 隔离边界
- **上下文隔离**: 每个 SubAgent 拥有独立的 `messages[]` 列表
- **工具隔离**: SubAgent 工具白名单排除 `subagent`，结构性禁止递归派生
- **文件系统隔离**: 每个 SubAgent 运行在独立 sandbox 子目录 (`subagent_1`, `subagent_2`, `subagent_3`)
- **Token 预算隔离**: 每个 SubAgent 独立计算 token 消耗，超限触发熔断

## 子智能体实际收到的信息

### 关键代码位置
`tools/subagent_tool.py:253` 硬编码 `context=""`：
```python
async with self._semaphore:
    result: SubAgentResult = await subagent.run(context="")  # ← 空字符串
```

### 信息传递矩阵

| 来源 | 是否传递 | 内容 | 源码位置 |
|------|---------|------|---------|
| **System Prompt** | ✅ 有 | `SUBAGENT_SYSTEM_PROMPT` + v12 日期/时间注入 (`inject_context=True`) | `agents/subagent.py:146-151` |
| **task_description** | ✅ 有 | `TODO {id}: {description}` + 已完成依赖的 result（如有） | `emergent_planner.py:865-873` |
| **父级 _gather_context() 结果** | ❌ 无 | 长期记忆检索、知识库、经验避坑提示、用户偏好等均不传递 | — |
| **父级对话历史 messages[]** | ❌ 无 | 独立 messages 列表（防上下文泄漏的设计） | `agents/subagent.py:159-167` |
| **context 参数** | ❌ 空 | 硬编码为 `""` | `tools/subagent_tool.py:253` |

### 设计取舍分析

**优势 (Anti-Pattern #2 防御)**
- 结构性防止**上下文泄漏**，避免父级庞大的上下文撑爆子智能体的 token 窗口
- 子智能体完全隔离，不受父级历史对话干扰，专注执行分配的单一子任务

**代价**
- 子智能体**丢失父级收集的上下文**（相关记忆、知识库结果、历史失败经验等）
- 多个 SubAgent 可能**重复搜索**相同信息（因缺乏共享上下文协调）
- 缺少父级方向指引，可能导致搜索偏离预期

### 潜在改进方向
如需在隔离与上下文之间平衡，可考虑：
1. 将父级 `combined_context` 的**压缩摘要**传入 `context` 参数（需控制 token 规模）
2. 在 `task_description` 中注入关键背景信息（已部分实现：传递已完成依赖的 result）
3. 引入**共享知识库**机制，让 SubAgent 可查询父级已发现的知识（不传递 messages，但共享检索结果）
