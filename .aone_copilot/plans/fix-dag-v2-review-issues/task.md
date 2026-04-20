
# 修复任务清单

## P0-Critical 修复

- [x] **Task 1**: dag/executor.py — P0-1: `_check_exit_criteria()` 异常捕获，防止节点状态卡死 RUNNING
- [x] **Task 2**: dag/executor.py — P0-2: 超时 StepResult 补充 `step_id=node.id`
- [x] **Task 3**: dag/executor.py — P0-3: `execute()` 入口统一状态机，`dag._sm = self._sm`
- [x] **Task 4**: dag/graph.py — P0-4: `get_ready_nodes()` 防御性检查 `d in self.nodes`

## P1-Major 修复

- [x] **Task 5**: dag/graph.py — P1-1: 构建反向邻接表 `_reverse_dep_adjacency`，改写 `get_dependency_ids()`
- [x] **Task 6**: dag/graph.py — P1-1 续: `add_dynamic_node()` 维护反向邻接表
- [x] **Task 7**: dag/graph.py — P1-1 续: `add_dynamic_edge()` 维护反向邻接表
- [x] **Task 8**: dag/graph.py — P1-1 续: `remove_pending_node()` 维护反向邻接表
- [x] **Task 9**: agents/orchestrator.py — P1-2: 重规划后 `dag._sm = dag_executor._sm` 同步状态机
- [x] **Task 10**: dag/graph.py — P1-3: `from_dict()` 增加 `state_machine` 可选参数
- [x] **Task 11**: dag/executor.py — P1-4: `_complete_structural_nodes()` 终态列表补充 `FAILED`
- [x] **Task 12**: dag/state_machine.py — P1-5: FAILED 状态添加 PENDING 重试路径 + 更新注释
- [x] **Task 13**: agents/orchestrator.py — P1-6: 过滤回滚节点结果，仅保留 ACTION 节点 + 导入 NodeType
- [x] **Task 14**: schema.py — P1-7: `merge_result()` 覆盖时记录 debug 日志 + 添加 logging 导入

## P2-Minor 修复

- [x] **Task 15**: dag/graph.py — P2-1: `remove_pending_node()` 邻接表清理改用列表推导式
- [x] **Task 16**: dag/graph.py — P2-6: 顶部导入 config，移除 `save_checkpoint()` 中延迟导入
- [x] **Task 17**: dag/executor.py — P2-2 + P3-4: `_compile_output()` 拓扑排序降级 + 防御性 get
- [x] **Task 18**: agents/orchestrator.py — P2-3: `_emit()` 异常改为 `logger.debug` 记录
- [x] **Task 19**: schema.py — P2-5: `ExitCriteria` docstring 明确三种行为模式

## 验证

- [x] **Task 20**: 运行 pytest 确认无回归（26 passed, 4 failed — 失败项为第一次评审遗留，非本次引入）
- [x] **Task 21**: 运行 read_lints 检查无新增编译错误


---
生成时间: 2026/4/20 19:18:25
planId: 1f9d823a-a116-4d6d-a16e-febc6090fac0