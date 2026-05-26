"""
Evaluation module for Manus Demo's three plan-and-execute paradigms.

Provides benchmark tasks, metric collection, and comparative reporting for:
  - v1 simple:  flat plan -> sequential execution
  - v2 complex: hierarchical DAG -> parallel super-step execution
  - v5 emergent: Claude Code-style TODO list planning

v14.6 (current): Evaluation Harness upgrade — probe extracted to standalone module,
deterministic verifiers, baseline/regression gate, expanded benchmark dataset.

Module structure (v14.6):
  - benchmark.py   — Task definitions + ground truth (no runtime deps)
  - metrics.py     — Score computation models (no runtime deps)
  - probe.py       — EvaluationProbe event interceptor (no runtime deps)
  - verifiers.py   — Deterministic outcome verifiers (no runtime deps)
  - baseline.py    — Baseline management + regression gate (no runtime deps)
  - suites.py      — Named benchmark task suites for matrix runs
  - variants.py    — Named config bundles for engine/effort comparisons
  - reasoning_matrix.py — Variant × mode matrix CLI
  - compare_variants.py — Matrix summary/Markdown/CSV reporting
  - report.py      — Rich console + JSON report (rich + metrics only)
  - runner.py      — Orchestrates execution (requires runtime: agents, llm, tools)
  - eval_cli.py    — CLI entry point (lazy-imports runner)
  - user_simulator.py — HITL simulated user (no runtime deps)
  - llm_judge.py   — LLM-as-Judge fallback (requires llm runtime)

Manus Demo 三种规划执行范式的评测模块。

参考来源：
  - AgentBench (ICLR 2024): multi-environment LLM-as-Agent benchmark
  - AgentEval (ACL 2026): DAG-structured step-level evaluation with error propagation
  - Odysseys: Trajectory Efficiency = rubric_score / num_steps
  - SWE-bench: execution-based verification
  - GeoAgentBench: Parameter Execution Accuracy (PEA)
  - τ-bench / TauBench (Sierra Research, 2024): Pass^k reliability metric,
    tool-agent-user multi-turn interaction (inspired SimulatedUser)
  - GAIA (Meta, 2023): tiered-difficulty real-world task benchmark
  - Anthropic *Demystifying evals for AI agents* (2026-01):
    task → trial → transcript → outcome → grader pipeline; LLM-as-Judge
    as fallback for open-ended answer evaluation
"""
