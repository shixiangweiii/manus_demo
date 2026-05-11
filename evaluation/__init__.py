"""
Evaluation module for Manus Demo's three plan-and-execute paradigms.

Provides benchmark tasks, metric collection, and comparative reporting for:
  - v1 simple:  flat plan -> sequential execution
  - v2 complex: hierarchical DAG -> parallel super-step execution
  - v5 emergent: Claude Code-style TODO list planning

Manus Demo 三种规划执行范式的评测模块。

参考来源：
  - AgentBench (ICLR 2024): multi-environment LLM-as-Agent benchmark
  - AgentEval (ACL 2026): DAG-structured step-level evaluation with error propagation
  - Odysseys: Trajectory Efficiency = rubric_score / num_steps
  - SWE-bench: execution-based verification
  - GeoAgentBench: Parameter Execution Accuracy (PEA)
"""
