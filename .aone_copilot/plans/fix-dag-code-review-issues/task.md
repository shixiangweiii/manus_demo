
# 修复 DAG 代码评审问题 — 任务清单

## P0 — 严重问题修复

- [ ] 1. `dag/graph.py`：注入 NodeStateMachine，`mark_subtree_skipped()` 和 `refresh_ready_states()` 通过状态机修改状态
- [ ] 2. `dag/graph.py`：`_validate_dag()` 校验失败时抛出 `ValueError` 而非仅记录日志
- [ ] 3. `dag/executor.py`：`_process_conditions()` 中 `target.status = NodeStatus.SKIPPED` 改为通过状态机转移
- [ ] 4. `dag/executor.py`：`_complete_structural_nodes()` 中直接赋值状态改为通过状态机转移
- [ ] 5. `dag/executor.py`：修复 `_handle_failure()` 回滚节点失败后的处理逻辑（回滚失败时原节点标记为 SKIPPED）

## P1 — 性能与健壮性

- [ ] 6. `dag/graph.py`：预构建邻接表 `_dep_adjacency`，优化 `get_downstream()` 和 `topological_sort()` 性能
- [ ] 7. `dag/graph.py`：`add_dynamic_edge()` 添加环检测，发现环时回滚操作
- [ ] 8. `dag/graph.py`：Checkpoint 数量限制（保留最近 MAX_CHECKPOINTS 个）
- [ ] 9. `dag/executor.py`：添加节点执行超时控制（`_run_node_with_timeout`）
- [ ] 10. `dag/executor.py`：增强主循环死锁检测（区分 FAILED 阻断和可能的循环依赖）
- [ ] 11. `config.py`：新增 `NODE_EXECUTION_TIMEOUT` 和 `MAX_CHECKPOINTS` 配置项

## P2 — 代码质量改进

- [ ] 12. `dag/state_machine.py`：回调异常记录 debug 日志而非静默忽略
- [ ] 13. `dag/graph.py`：修复 `topological_sort()` 入度计算冗余（`in_degree.get()` → `+=`）
- [ ] 14. `dag/graph.py`：`summary()` 按 NodeStatus 枚举顺序输出
- [ ] 15. `dag/executor.py`：`_compile_output()` 按拓扑序输出并添加节点标识
- [ ] 16. `config.py`：移除硬编码 API Key 默认值

## 验证

- [ ] 17. 运行 `python -m pytest tests/ -v` 确认无回归
- [ ] 18. 运行 `read_lints` 检查无新增编译错误


---
生成时间: 2026/4/20 17:54:38
planId: 3235042c-ff73-49f9-9325-8053200336e5