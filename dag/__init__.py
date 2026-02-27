"""
DAG module - Core engine for task graph execution.
DAG 模块 —— 任务图执行的核心引擎。

Components:
  - graph.py:         TaskDAG data structure and graph operations
  - state_machine.py: Node lifecycle state machine
  - executor.py:      DAG execution engine (super-step model)

模块组成：
  - graph.py:         TaskDAG 数据结构与图算法（拓扑排序、就绪检测等）
  - state_machine.py: 节点生命周期状态机（强制合法状态转移）
  - executor.py:      DAG 执行引擎（Super-step 并行执行模型）
"""

from dag.graph import TaskDAG             # 任务有向无环图
from dag.state_machine import NodeStateMachine  # 节点状态机
from dag.executor import DAGExecutor      # DAG 执行引擎
