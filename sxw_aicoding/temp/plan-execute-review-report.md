# plan-and-execute 动态程度、规划能力边界与 Hidden Badcases 分析

基于仓库当前实现，对 `plan-and-execute` 的实际执行链路（v1/v2/v5）、动态程度与“规划能力边界”进行代码证据级分析，并系统性枚举隐藏 badcase（按严重度）。

---

## 1. 执行链路总览（v1/v2/v5）

### 1.1 v1（simple）：扁平 Plan 串行执行 + 反思重规划

**路由与入口**
- 入口在 `agents/orchestrator.py`：`OrchestratorAgent.run(task)` 先调用 `PlannerAgent.classify_task(task)`，然后：
  - `complexity == "simple"` -> `create_plan()` -> `_execute_and_reflect_simple()`
  - `complexity == "complex"` -> v2 DAG
  - 其它 -> v5（后文会指出不可达性）
- `agents/planner.py`：`classify_task()` 规则+LLM 只会返回 `"simple"` 或 `"complex"`，因此 v5 理论分支存在但运行时路由不成立（见 Hidden Badcase）。

**执行与停止条件**
- `_execute_and_reflect_simple()` 在 `agents/orchestrator.py` 内：
  - 外层重规划循环：`for attempt in range(self.max_replan + 1)`。
  - 内层遍历步骤：`for i, step in enumerate(plan.steps)`，顺序执行。
  - 每个步骤的执行由 `agents/executor.py:ExecutorAgent.execute_step()` 托管给 ReAct 循环 `_react_loop()`。

`ExecutorAgent._react_loop()`（`agents/executor.py`）的关键停止条件：
- 当 LLM 返回消息 **不包含 `tool_calls`** 时，视为步骤完成并直接返回 `StepResult(success=True, ...)`。
- 循环上限：`while iteration < self.max_iterations`，超出则返回 `success=False`。

**反思门控与重规划触发**
- 每轮步骤都执行完后调用 `agents/reflector.py:ReflectorAgent.reflect()`（v1）。
- 若 `reflection.passed == True`：编译输出并返回。
- 若 `passed == False`：
  - 若 `attempt < self.max_replan`：调用 `PlannerAgent.replan()` 生成“剩余工作”的新 plan（v1）。
  - 否则返回 best-effort 输出。

### 1.2 v2（complex）：层级 DAG + Super-step 并行执行 + 节点级 exit criteria 校验 + 失败子树重规划

**路由与入口**
- `OrchestratorAgent._execute_dag_and_reflect()`（`agents/orchestrator.py`）：
  - DAGExecutor 运行：`final_output = await dag_executor.execute(dag)`
  - 然后 `reflection = await reflector.reflect_dag(...)`
  - 若不通过：局部重规划失败子树 `planner.replan_subtree()`，并再次 `dag_executor.execute(dag)`。

**DAGExecutor 的“动态程度”核心**
- `dag/executor.py:DAGExecutor.execute(dag)`：
  - 主循环：`while not dag.is_complete():`
  - 每个 super-step：
    - `ready = dag.get_ready_nodes()`
    - 并行执行：`results = await asyncio.gather(*(self._run_node(node, dag) for node in batch))`
    - 合并到共享状态：`dag.state.merge_result(node.id, result.output)`
    - 节点级 exit criteria 校验：`_check_exit_criteria()`（必要时调用 Reflector LLM 校验）
    - 失败处理：`_handle_failure()`（rollback/skip + 子树级联跳过）
    - 条件边评估：`_process_conditions()`（基于关键词匹配）
    - v3 自适应规划：`_adapt_plan()`（必要时重塑 DAG 的 pending 部分）
    - checkpoint：`dag.save_checkpoint()`

**停止条件**
- DAG 完成：`TaskDAG.is_complete()` 当所有节点进入终态 `{COMPLETED, SKIPPED, ROLLED_BACK}`。
- “卡住”停止：若某个 super-step `ready` 为空但 DAG 未完成 -> 直接 `break`，返回 `_compile_output(dag)`（best-effort）。

**v2 的重规划触发点**
- `OrchestratorAgent._execute_dag_and_reflect()` 在反思失败时：
  - 收集 `failed_nodes = [n for n in dag.nodes.values() if n.status == NodeStatus.FAILED]`
  - 选择第一个失败节点 `failed_nodes[0]` 重建其“父节点子树”：
    - `dag = await planner.replan_subtree(dag, failed_node_id=failed_node.id, feedback=reflection.feedback)`

### 1.3 v5（emergent）：TODO 列表隐式规划 + while(tool_use) 执行（注意路由不可达）

**入口**
- `OrchestratorAgent._execute_emergent()`（`agents/orchestrator.py`）委托给 `agents/emergent_planner.py:EmergentPlannerAgent.execute()`。

**TODO 驱动的动态执行**
- `EmergentPlannerAgent.execute()` 初始化 `TodoList`，然后：
  - `while self._todo_list.has_pending():`
    - 选择 `ready_todos = self._todo_list.get_ready_todos()`
    - 若 `ready_todos` 为空但仍有 pending -> 强制挑一个 `PENDING` TODO；否则 `break`
    - 对当前 TODO 调用 `_execute_todo()`，由 Executor-like ReAct 循环 + 工具调用组成
    - 成功：`mark_completed`
    - 失败：只记录日志/事件，不改变 TODO 状态为 `PENDING/FAILED`

**停止条件**
- 外层循环由 `has_pending()` 控制，且有 `iteration > self.max_iterations` 的整体上限。
- `_execute_todo()` 也有自己的 `while iteration < self.max_iterations` 上限；超出则返回 `success=False`。

**重要边界**
- v5 在当前代码里大概率不可达：`PlannerAgent.classify_task()` 返回值域只覆盖 `"simple"` / `"complex"`。

---

## 2. 动态程度与规划能力边界（按特性拆解）

### 2.1 并行性（动态程度高，但存在关键边界：并发串话）
- v2 使用 `asyncio.gather` 并行执行多个 ready 的 ACTION 节点（`dag/executor.py`）。
- 但 `ExecutorAgent` 以实例形式持有可变消息历史（`BaseAgent._messages`）并在 `_react_loop()` 开头调用 `self.reset()`。
- 当并行任务复用同一个 `executor_agent` 实例时，消息历史与 tool router 统计会发生竞态（见 Hidden Badcase）。

结论：并行带来的动态性是“表面实现了”，但在真实执行下规划—执行耦合的可靠性会受并发状态共享边界影响。

### 2.2 条件分支（动态程度中等，但语义实现与规划端存在错配）
- v2 条件边由 `dag/executor.py:_evaluate_condition()` 以“关键词 substring 是否包含”实现：
  - `return edge.condition.lower() in source_result.lower()`
- 规划端（`agents/planner.py:_parse_dag`）在生成 conditional edge 时，把 `edge.source` 设成 `sg_id`（SubGoal 节点 id），而 SubGoal 本身并不执行、也不会向 `dag.state.node_results` 写入输出。
- 因而 conditional edge 的 source 结果通常为空字符串，导致条件逻辑偏离预期。

结论：条件分支在 v2 中是“可观察到的动态行为”，但规划语义与执行语义并不一致，导致条件分支可能失效或反向。

### 2.3 失败恢复（回滚/跳过的动态性存在，但 rollback 受 EdgeType 生成缺失限制）
- v2 失败处理通过 `dag/executor.py:_handle_failure()`：
  - 仅当存在 `EdgeType.ROLLBACK` 边时才会执行 rollback 节点
  - 无 rollback edge 则失败节点会被直接标记为 `SKIPPED`，并对其下游依赖子树级联跳过
- 但 planner 在 `_parse_dag()` 中只记录了 `TaskNode.rollback_action`（节点字段），未生成 `EdgeType.ROLLBACK` 边。

结论：planner 生成的“失败回滚语义”在 v2 运行时并未落到执行图的 rollback edge 上，导致回滚动态性边界被显著收缩。

### 2.4 自适应规划（v3：动态 DAG 变更能力存在，但安全边界不完整）
- DAGExecutor 在超步间可调用 `planner.adapt_plan()` 并应用：
  - `remove_pending_node` / `modify_node` / `add_dynamic_node + add_dynamic_edge`
- DAG 图结构的 acyclicity 约束没有在运行时严格保证（见无环检测）。

结论：自适应规划能“修改图”，但缺少对图性质（无环）与语义连通性的约束，导致规划能力边界在异常/极端情况下会迅速变差。

### 2.5 隐式规划（v5：TODO 动态性高，但状态机/终止行为边界不稳）
- v5 的动态性来自 TODO 动态增删、并由 LLM 随工具调用推断下一步。
- 但 `_execute_todo()` 在失败时不会把 TODO 状态从 `IN_PROGRESS` 回退到可重试的 `PENDING`，外层循环会在 ready 为空时直接 break（见 Hidden Badcase）。

结论：隐式规划的动态性高，但状态转移不闭合导致规划—执行收敛性边界较差。

---

## 3. Hidden Badcases（按严重度排序）

### Critical 级

1. **并发串话（并行 super-step 复用同一个 ExecutorAgent 实例导致竞态）**
   - 触发条件：
     - v2 中同一 super-step 多个 ACTION 节点 ready；
     - `DAGExecutor.execute()` 使用 `asyncio.gather` 并行调用 `_executor_agent.execute_node(...)`；
     - 这些并行调用复用同一个 `OrchestratorAgent.executor_agent` 实例。
   - 代码证据：
     - `dag/executor.py:DAGExecutor.execute()` 并行：`asyncio.gather(*(self._run_node(node, dag) ...))`
     - `agents/executor.py:ExecutorAgent._react_loop()` 开头：`self.reset()` 且共享 `self._messages` / `self.tool_router` 统计。
   - 影响：
     - 不同节点的对话历史、工具调用观察值混入同一条 ReAct 消息流；
     - 结果错误但仍可能通过“exit criteria LLM 判断”或默认通过门控。
   - 现有测试覆盖现状：
     - `tests/test_dag_capabilities.py` 用了 `AsyncMock` 替代 executor agent，未覆盖真实并发串话。

2. **silent success：工具返回 `Error:` 字符串时被当作成功**
   - 触发条件：
     - `CodeExecutorTool.execute()` 或 `FileOpsTool.execute()` 内部捕获异常并返回 `"Error: ..."` 字符串，而不是抛异常；
     - executor 端只在 `tool.execute` 抛异常时记录 failure；并且 ReAct “是否成功”由 LLM 是否停止 tool_calls 决定。
   - 代码证据：
     - `agents/executor.py:_react_loop()` 在工具执行后：只要无异常就 `self.tool_router.record_success(...)`；
     - 工具实现返回错误字符串：
       - `tools/code_executor.py`：`except Exception as exc: return f"Error executing code: {exc}"`
       - `tools/file_ops.py`：多处 `return "Error: ..."`（read/write/list 异常均返回字符串）
     - executor 成功条件：`if not response_msg.tool_calls: success=True`
   - 影响：
     - tool router 的失败计数/切换提示几乎无法触发；
     - 节点可能在语义失败情况下被标记为 COMPLETED。
   - 现有测试覆盖现状：
     - v2 executor 在测试中被 mock 掉，未验证真实工具错误返回路径。

3. **v5 隐式规划：失败 TODO 的状态机不闭合导致提前终止（IN_PROGRESS 卡死）**
   - 触发条件：
     - `_execute_todo()` 返回 `success=False`；
     - 外层在 `result.success == False` 时没有把该 TODO 从 `IN_PROGRESS` 回退/标记为 `PENDING/FAILED`；
     - 下一轮 `get_ready_todos()` 只返回 `PENDING` TODO，可能空；
     - 若没有其它 pending，会 `break`，任务未完成就停止。
   - 代码证据：
     - `agents/emergent_planner.py:_execute_todo()`：失败不修改 TODO 状态，只返回 StepResult；
     - `agents/emergent_planner.py:execute()`：失败分支只 emit todo_failed，不做状态回退；
     - `schema.py:TodoList.get_ready_todos()`：只挑 `TodoStatus.PENDING`。
   - 影响：
     - 隐式规划在失败情况下难以恢复，收敛性差。
   - 现有测试覆盖现状：
     - emergent planning 测试以导入/初始化为主，未覆盖失败场景的状态机路径。

### High 级

4. **门控 default-pass：Reflector 在异常/解析失败时默认通过，抑制重规划**
   - 触发条件：
     - Reflector 的 `think_json` 抛异常、解析失败；
     - 或 JSON 输出缺字段导致 `data.get(..., True)` 默认值放大。
   - 代码证据：
     - `agents/reflector.py:validate_exit_criteria()` except -> `return True`
     - `agents/reflector.py:reflect_dag()` except -> `reflection = Reflection(passed=True, ...)`
   - 影响：
     - 执行失败/语义不满足可能被直接判为通过；
     - Orchestrator 的重规划触发被系统性削弱。
   - 现有测试覆盖现状：
     - `tests/test_dag_capabilities.py` mock 了 `validate_exit_criteria = True`，未覆盖异常/失败判定路径。

5. **DAG 卡住提前 break：无 ready 节点但 DAG 未完成时直接退出 best-effort**
   - 触发条件：
     - 图存在依赖阻塞（例如动态变更引入环或条件跳过导致链路断裂）；
     - 或条件边/失败边组合导致剩余节点永远无法就绪；
     - 即 `dag.get_ready_nodes()` 返回空但 `not dag.is_complete()`。
   - 代码证据：
     - `dag/executor.py:DAGExecutor.execute()`：`if not ready: break`，随后返回 `_compile_output(dag)`。
     - `_compile_output()` 只汇总 `ACTION + COMPLETED` 的结果，失败/未执行节点的信息会被弱化。
   - 影响：
     - 系统可能输出部分结果，且随后 reflector 若 default-pass 也会进一步“放行”。
   - 现有测试覆盖现状：
     - 测试未覆盖 “ready 为空但未完成” 的卡住路径。

6. **conditional/rollback 语义错配：planner 生成方式与 executor 评估方式不一致**
   - 条件错配：
     - `agents/planner.py:_parse_dag()` conditional edge：`source=sg_id`（SubGoal），但 SubGoal 不执行，不写入 `dag.state.node_results`。
     - `dag/executor.py:_evaluate_condition()` 使用 `dag.state.node_results.get(edge.source, "")` 做关键词匹配。
     - 结果：条件判断通常基于空字符串，条件语义偏离规划意图。
   - 回滚错配：
     - planner 只设置 `TaskNode.rollback_action` 字段，没有生成 `EdgeType.ROLLBACK` 边；
     - executor 的 rollback 仅基于 `EdgeType.ROLLBACK` 边执行（`dag/graph.py:get_rollback_targets()`）。
   - 影响：
     - 条件分支可能被错误跳过或永远不触发；
     - rollback 可能在真实 planner 输出中完全不可用。
   - 现有测试覆盖现状：
     - conditional/rollback 测试使用手工构建 DAG，并未验证 planner 的语义输出是否匹配执行器。

7. **v5 不可达（路由分支存在但 classifier 从不产生该分支所需取值）**
   - 触发条件：
     - 任何常规 task 调用。
   - 代码证据：
     - `agents/planner.py:classify_task()` 只返回 `"simple"` 或 `"complex"`；
     - `agents/orchestrator.py:run()` 只有在 `else:` 才走 v5，但该 else 理论上不触发。
   - 影响：
     - emergent planning 功能在运行中可能永远不会被启用，形成“dead code”。
   - 现有测试覆盖现状：
     - emergent 测试主要是导入/属性存在，不验证路由可达性。

### Medium 级

8. **无环检测缺失：DAG 只校验端点存在，不校验 acyclicity**
   - 触发条件：
     - planner 或 v3 adaptive planning 动态加入的 edges 形成环；
     - 或 replan_subtree 合并新边产生环。
   - 代码证据：
     - `dag/graph.py:TaskDAG._validate_dag()`：仅检查 edges 端点是否存在，不检查环。
     - 若形成环，`get_ready_nodes()` 可能永远无 ready -> `DAGExecutor.execute()` break 输出 best-effort（见 badcase 5）。
   - 现有测试覆盖现状：
     - 测试未覆盖动态加边引入环/死锁。

9. **partial replan 的 node_results 污染：被移除节点的旧结果可能仍进入 reflect_dag 输入**
   - 触发条件：
     - 执行局部重规划（`planner.replan_subtree` / `_merge_dags`）。
   - 代码证据：
     - `agents/planner.py:_merge_dags()`：`result_dag.state.node_results = dict(old_dag.state.node_results)`（未 prune 被移除节点的 results）；
     - `agents/orchestrator.py:_node_to_result()`：对不存在 node 的条目会返回 `success=False`，但输出仍会被传入 reflector。
   - 影响：
     - reflector 的评估输入包含“已移除节点的历史输出”，可能误导通过/失败判断。

---

## 4. 测试覆盖缺口（现有 tests 覆盖了什么、没覆盖什么）

### 4.1 已覆盖（但通常是 mock 级别）
- `tests/test_dag_capabilities.py` 覆盖了：
  - DAG 的分层结构、拓扑性质、并行 ready detection（不涉及真实 executor 并发状态）
  - DAGExecutor super-step 并行机制（但 executor_agent 被 mock）
  - 条件分支 + rollback（通过手工构建 DAG 并 mock reflector 的 `validate_exit_criteria=True`）
  - v3 动态 DAG 变更（一般也通过 mock planner）
  - ToolRouter 的失败阈值提示逻辑（若有的话属单元级覆盖）

### 4.2 未覆盖（导致上文 Hidden Badcases 真实风险无法被测试拦截）
- 并发串话：未使用真实 `ExecutorAgent` 并行执行（`AsyncMock` 替代导致竞态无法出现）。
- 工具失败字符串路径：未调用真实 `tools/code_executor.py` / `tools/file_ops.py` 并验证 executor/reflector 对 `"Error: ..."` 的行为。
- Reflector 异常路径：未覆盖 `validate_exit_criteria` / `reflect_dag` 的 LLM 异常与 JSON 解析失败；因此无法验证 default-pass 的风险。
- DAG 卡住路径：未覆盖 `ready` 为空但 DAG 未完成 -> break 的场景。
- 图性质约束：未覆盖无环检测缺失导致的死锁/就绪永远为空。
- planner 生成语义一致性：未验证 planner 生成的 conditional/rollback edges 与 DAGExecutor 的评估逻辑是否匹配（尤其 rollback edge 从未生成）。
- v5：测试主要覆盖导入/初始化，不覆盖失败状态机、路由可达性、或端到端 emergent planning 的收敛性。

---

## 5. 建议的改进方向（不在本次交付中修改代码）

若要显著提升系统可靠性，优先级建议：

1. **修复并发串话**
   - 方案 A：v2 并行时为每个节点创建独立 executor 实例（隔离消息历史）。
   - 方案 B：把 ReAct 状态从实例变量迁移到局部变量（每个 `_react_loop` 持有自己的 messages）。
   - 方案 C：对 ExecutorAgent 做并发锁（但会降低并行收益）。

2. **统一工具失败语义（让 executor 能识别 Error strings）**
   - 方案：工具返回结果统一成结构化错误（异常或带错误码字段），executor 基于该结构判定失败，而不是仅凭无异常。
   - 同时让 `StepResult.success` 与“语义成功”绑定（例如：如果最后工具输出以 `Error:` 开头，则 success=False 或至少影响 exit criteria）。

3. **收紧 Reflector default-pass**
   - 异常/解析失败时应触发失败路径（例如返回 passed=False + 原因），从而引导重规划，而不是默认通过。

4. **补齐 DAG 安全约束**
   - 在图构造/动态变更时加入 cycle detection；
   - 或在 DAGExecutor 中加入“死锁/阻塞判定”并触发局部重规划。

5. **修复 planner conditional/rollback 生成**
   - conditional edge：让 `source` 指向会产生日结果的 ACTION 节点（或明确让 structural 节点也产生日结果/状态）。
   - rollback：让 planner 在输出中真正生成 `EdgeType.ROLLBACK` 边，而不是仅填 `TaskNode.rollback_action` 文本字段。

6. **修复 v5 状态机**
   - TODO 失败时应回退为 `PENDING` 或标记 `FAILED` 并允许恢复/重试；
   - `get_ready_todos()` 应支持 `IN_PROGRESS` 的可恢复策略（或外层循环逻辑需要重构）。

7. **验证 v5 路由可达性**
   - 修改分类器返回值域或 Orchestrator routing 条件，使 v5 真能被启用（否则功能测试价值为零）。

