# 修复任务清单

## P0-Critical 修复

- [ ] **Task 1**: dag/state_machine.py — 问题 E: RUNNING 状态添加 SKIPPED 转移路径
- [ ] **Task 2**: dag/state_machine.py — 问题 E: 更新转移图注释

## P1-High 修复

- [ ] **Task 3**: dag/executor.py — 问题 E 优化: 简化 `_complete_structural_nodes` RUNNING→SKIPPED 为直接转移
- [ ] **Task 4**: dag/executor.py — 问题 H: `_handle_failure` 回滚成功判断排除未执行的回滚节点

## P2-Medium 修复

- [ ] **Task 5**: agents/planner.py — 问题 I: `_merge_dags` 继承旧 DAG 的 checkpoints

## 验证

- [ ] **Task 6**: 运行 pytest 确认无回归
- [ ] **Task 7**: 运行 read_lints 检查无新增编译错误

---
生成时间: 2026/4/20 19:57:36
planId: f32dac19-6967-4490-9218-2905200ff4a8