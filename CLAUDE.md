# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Manus Demo is a multi-agent AI system demonstrating autonomous task execution through hybrid plan routing. The system classifies tasks by complexity and routes them to one of three execution paradigms: simple flat planning (v1), DAG-based parallel execution (v2), or emergent TODO-list planning (v5). A v7 tracing module provides OpenTelemetry-based full-lifecycle observability.

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default, supports Ollama/Qwen/etc.)
- **UI**: Rich console with event-driven rendering
- **Current version**: v7.0

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (sequential ReAct)  → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (parallel super-steps) → Reflector
  emergent:  EmergentPlanner.execute() → while has_pending_todos (outer scheduling) → per-TODO ReAct (inner tool_use)
All paths → Token usage summary → Long-term memory store
All paths → TracingBridge (event-to-span, v7 OpenTelemetry)
```

### Event Multicast Pattern (Central to the System)

OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which fans out to multiple subscribers via `on_event` callback:

1. **UI renderer** (`main.py`) — Rich console output (tables, panels, trees)
2. **TracingBridge** (`tracing/bridge.py`) — Translates events into OTel Spans with parent-child hierarchy
3. **EvaluationProbe** (`evaluation/runner.py`) — Collects metrics per-phase for benchmark scoring

ExecutorAgent and ReflectorAgent do **not** emit events directly — they return results to their caller which then emits.

### Source Layout

```
manus_demo/
├── main.py                    # CLI entry (interactive / single-task / -v verbose)
├── config.py                  # All env-var-driven config (no hardcoded secrets)
├── schema.py                  # Pydantic models: TaskNode, DAGState, Plan, TodoList, LLMCallRecord, etc.
├── agents/
│   ├── base.py                # BaseAgent — think(), think_json(), think_with_tools(), message history
│   ├── orchestrator.py        # OrchestratorAgent — classify → route → execute → reflect → memory
│   ├── planner.py             # PlannerAgent — two-stage classifier + plan/DAG generation + adaptive planning
│   ├── executor.py            # ExecutorAgent — ReAct loop per step/node (legacy or ReActEngine)
│   ├── reflector.py           # ReflectorAgent — exit criteria validation + quality assessment
│   └── emergent_planner.py    # EmergentPlannerAgent — Claude Code style TODO-driven planning
├── dag/
│   ├── graph.py               # TaskDAG — graph structure, topological sort, ready-node detection, dynamic mutation
│   ├── executor.py            # DAGExecutor — super-step parallel execution loop
│   └── state_machine.py       # NodeStateMachine — enforces legal node status transitions
├── react/
│   └── engine.py              # ReActEngine — unified ReAct loop (v6 feature-flagged, ENABLE_REACT_ENGINE_V2)
├── llm/
│   └── client.py              # LLMClient — OpenAI-compatible async wrapper with retry + per-call token tracking
├── tools/
│   ├── base.py                # BaseTool ABC — name, description, parameters_schema, execute(), traced_execute()
│   ├── web_search.py          # WebSearchTool — mock search results
│   ├── code_executor.py       # CodeExecutorTool — subprocess sandbox Python execution
│   ├── file_ops.py            # FileOpsTool — sandboxed file read/write/list
│   ├── shell_tool.py          # ShellTool — sandboxed bash execution with command blacklist
│   ├── subprocess_utils.py    # Shared subprocess runner with timeout + output-size limits
│   └── router.py              # ToolRouter — per-node failure tracking, suggests alternative tools on threshold
├── tracing/                   # v7 OpenTelemetry-based full-lifecycle tracing
│   ├── __init__.py            # Lazy imports — no-ops when TRACING_ENABLED=false
│   ├── config.py              # Tracing-specific config (backend, sample rate, sensitive data patterns)
│   ├── provider.py            # TracerProvider factory with multi-backend support (console/file/rich/otlp/phoenix)
│   ├── bridge.py              # TracingBridge — subscribes to _emit events, creates parent-child OTel spans
│   ├── decorators.py          # @traced decorator + shared helpers (_truncate, _safe_set_attribute)
│   ├── spans.py               # SpanName, AttrKey, EventName, SPAN_ICONS constants
│   ├── exporters.py           # FileSpanExporter (JSON), RichConsoleExporter (tree)
│   ├── server.py              # FastAPI web viewer for trace visualization (Jinja2 templates)
│   └── __main__.py            # `python -m tracing` entry point for standalone viewer
├── memory/
│   ├── short_term.py          # ShortTermMemory — sliding-window message buffer
│   └── long_term.py           # LongTermMemory — JSON-file persistence + keyword search
├── context/
│   └── manager.py             # ContextManager — token estimation + LLM-based context compression
├── knowledge/
│   ├── retriever.py           # KnowledgeRetriever — TF-IDF + cosine similarity document retrieval
│   └── docs/                  # Knowledge base text files
├── evaluation/                # Evaluation module — benchmark 3 plan-and-execute paradigms
│   ├── metrics.py             # Core metric models + 4-dimension weighted scoring functions
│   ├── benchmark.py           # 12 benchmark tasks with ground truth
│   ├── runner.py              # EvaluationProbe (event listener) + EvaluationRunner
│   ├── report.py              # Rich console comparison report + JSON export
│   └── eval_cli.py            # CLI entry: `python -m evaluation.eval_cli`
├── tests/                     # pytest suite (mock-based, no LLM API required)
└── sxw_aicoding/docs/         # Detailed architecture docs (codemap, CHANGELOG, design docs)
```

## Key Data Models (schema.py)

| Model | Purpose |
|-------|---------|
| `Plan` / `Step` | v1 flat plan with ordered steps |
| `TaskNode` | DAG node (GOAL / SUBGOAL / ACTION) with status, exit_criteria, risk |
| `TaskEdge` | DAG edge (DEPENDENCY / CONDITIONAL / ROLLBACK) |
| `DAGState` | Centralized execution state — `node_results` dict, `get_node_context()`, `merge_result()` |
| `TodoList` / `TodoItem` | v5 emergent planning state with cycle detection |
| `StepResult` | Execution result per step/node (success, output, tool_calls_log) |
| `LLMCallRecord` | Per-LLM-call token record (call_type, prompt_summary, tokens, engine) |
| `TokenUsageSummary` | Aggregated token usage: call_records + by_engine + total |
| `Reflection` | Reflector output (passed, score, feedback, suggestions) |

## Token Tracking

Token tracking is centralized in `LLMClient`:
- `_call_records: list[LLMCallRecord]` — appended on every successful API call via `_record_call()`
- `get_call_records()` — returns a copy
- `reset_usage()` — clears for a new task
- `Orchestrator._finalize_token_usage()` — aggregates by engine and computes total from call records
- No snapshot/delta logic; safe under asyncio concurrency (list.append is atomic in single-threaded event loop)
- When the LLM provider does not return usage data (e.g., some local Ollama models), `_call_records` has entries with zero tokens and the UI displays "N/A" gracefully

## Tracing (v7)

OpenTelemetry-based tracing adds observability without modifying core execution logic:
- **TracingBridge** subscribes to the same `_emit` event callback used by the UI and evaluation probe; it translates events into OTel Spans with parent-child hierarchy
- **Zero overhead when disabled**: `tracing/__init__.py` uses conditional imports — when `TRACING_ENABLED=false`, all tracing symbols resolve to no-op stubs with no OpenTelemetry dependency loaded
- **Multi-backend**: console, file (JSON), Rich console, OTLP HTTP, Phoenix
- **Web viewer**: `python -m tracing` starts a FastAPI server for trace visualization
- **Inline tracing**: `LLMClient._start_llm_span`/`_end_llm_span` for LLM calls; `BaseTool.traced_execute` for tool calls; `@traced` decorator for general-purpose manual span creation
- **Sensitive data**: `SENSITIVE_KEYS` set in `tracing/config.py` triggers automatic redaction of api_key, token, etc.
- **LLM span lifecycle gotcha**: `_end_llm_span` reads token usage from `_call_records[-1]`, so `_record_call()` must be called before `_end_llm_span`. This is safe because in the single-threaded asyncio event loop there is no `await` between them.

## Evaluation Module

Benchmarks all three planning paradigms (simple/complex/emergent) against 12 tasks (4 easy, 4 medium, 4 hard).

**4-Dimension Weighted Scoring**:
- Planning (30%) — classification accuracy, plan structure validity, step coverage ratio, generation speed
- Execution (40%) — task success, step success rate, tool accuracy
- Efficiency (20%) — trajectory efficiency (step_success_rate / avg_iterations_per_step), token efficiency, time efficiency, replan penalty
- Reflection Accuracy (10%) — whether Reflector's pass/fail verdict matches ground truth

**Event Probe Pattern**: `EvaluationProbe` hooks into `on_event` callback without modifying core code. `EvaluationRunner` forces routing via `config.PLAN_MODE` override and sets `classification_forced` dynamically (`True` when forced, `False` when auto). Forced mode redistributes classification weight to other planning dimensions in scoring.

## Configuration (config.py)

All config via env vars / `.env` file. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API endpoint |
| `LLM_API_KEY` | — | API key (required) |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `PLAN_MODE` | `auto` | `auto` / `simple` / `complex` / `emergent` |
| `MAX_REACT_ITERATIONS` | `10` | ReAct loop cap per node |
| `MAX_PARALLEL_NODES` | `3` | Super-step parallelism cap |
| `MAX_REPLAN_ATTEMPTS` | `3` | Max reflect-fail replan cycles |
| `TOKEN_TRACKING_ENABLED` | `true` | Enable per-call token tracking |
| `LLM_RETRY_ENABLED` | `false` | Enable exponential-backoff retry |
| `ENABLE_REACT_ENGINE_V2` | `false` | Use unified ReActEngine (v6 feature flag) |
| `ADAPTIVE_PLANNING_ENABLED` | `true` | Enable runtime DAG adaptation |
| `EMERGENT_PLANNING_ENABLED` | `true` | Enable v5 emergent planning route |
| `NODE_EXECUTION_TIMEOUT` | `300` | Per-node timeout in seconds |
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | Sandboxed file/shell working directory |
| `MAX_TODO_ITEMS` | `20` | v5 TODO list max size |
| `MAX_EMERGENT_OUTER_ITERATIONS` | `60` | v5 emergent main loop iteration cap |
| `TOOL_FAILURE_THRESHOLD` | `2` | v3 consecutive failures before suggesting tool switch |
| `TRACING_ENABLED` | `false` | Master switch for v7 tracing |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `TRACING_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint |
| `TRACING_SAMPLE_RATE` | `1.0` | Sampling rate (0.0–1.0) |
| `TRACING_LOG_PROMPTS` | `false` | Record full prompt/response in spans |

## Common Commands

```bash
# Install
pip install -r requirements.txt

# Run (interactive)
python main.py

# Run (single task)
python main.py "task description"

# Run (verbose)
python main.py -v

# Force planning mode
PLAN_MODE=simple python main.py "task"
PLAN_MODE=complex python main.py "task"
PLAN_MODE=emergent python main.py "task"

# Tracing viewer (v7)
python -m tracing                    # Start web viewer on http://localhost:8000

# All tests (no API key needed, mock-based)
python -m pytest tests/ -v

# Individual test suites
python -m pytest tests/test_dag_capabilities.py -v      # DAG planning, parallel exec, conditional/rollback, adaptive
python -m pytest tests/test_emergent_planning.py -v     # v5 TODO list management, EmergentPlanner
python -m pytest tests/test_tracing.py -v               # v7 OTel tracing bridge, spans, exporters
python -m pytest tests/test_evaluation.py -v            # Evaluation metrics, scoring, probe
python -m pytest tests/test_concurrent_execution.py -v  # asyncio.gather DAG parallelism
python -m pytest tests/test_cycle_detection.py -v       # DAG cycle detection
python -m pytest tests/test_llm_integration.py -v       # LLMClient retry, token tracking
python -m pytest tests/test_optimizations.py -v         # Performance optimizations
python -m pytest tests/test_real_tools.py -v            # Real tool execution (sandbox)
python -m pytest tests/test_shell_tool.py -v            # ShellTool blacklist, sandbox

# Run a single test
python -m pytest tests/test_dag_capabilities.py::test_topological_sort -v

# Evaluation CLI (requires LLM_API_KEY)
python -m evaluation.eval_cli --dry-run                    # Show benchmark tasks
python -m evaluation.eval_cli --difficulty easy --modes simple  # Quick smoke test
python -m evaluation.eval_cli --output results.json        # Full eval with JSON export

# Syntax check modified files
python3 -m py_compile schema.py llm/client.py agents/orchestrator.py
```

## Dependencies

- `openai` — AsyncOpenAI client
- `pydantic` — Data models with validation
- `rich` — Terminal UI (tables, panels, trees)
- `python-dotenv` — `.env` file loading
- `opentelemetry-api` / `opentelemetry-sdk` / `opentelemetry-exporter-otlp` — v7 tracing
- `fastapi` / `uvicorn` / `jinja2` — v7 trace web viewer
- `pytest` / `pytest-asyncio` — Testing (optional)

## Code Conventions

- **All agents except OrchestratorAgent inherit `BaseAgent`** which provides `think()`, `think_json()`, `think_with_tools()` and manages message history; OrchestratorAgent does not inherit BaseAgent (no own LLM message history) — it composes sub-agents and shares a single `LLMClient` instance
- **All tools inherit `BaseTool`** with `name`, `description`, `parameters_schema`, `execute()`, and `to_openai_tool()`
- **Async throughout** — all LLM calls and tool executions are `async def`
- **Event-driven UI** — OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which forwards to the `on_event` callback in `main.py`; ExecutorAgent and ReflectorAgent do not emit events directly
- **Pydantic models** for data structures, but LLM message passing uses raw `list[dict[str, Any]]` (OpenAI API compatibility)
- **Chinese + English bilingual comments** — most modules have dual-language docstrings
- **Feature flags** — v6 capabilities (LLM retry, ReActEngine) default to disabled (`false`); v3/v5 features (adaptive planning, emergent planning) default to enabled (`true`); v7 tracing defaults to disabled (`false`)
- **Token tracking centralized** — only `LLMClient` and `OrchestratorAgent` manage token usage; individual execution agents (Executor, EmergentPlanner, Reflector, Planner) have no token tracking code

## Important Design Decisions

1. **Three routing paths**: Tasks classified by a two-stage hybrid classifier (rules fast-filter ~60-70% of tasks, LLM fallback for ambiguous ones); unknown classifications fall back to the complex DAG path
2. **DAG concurrency**: `asyncio.gather` runs ready nodes in parallel within each super-step; `DAGState.node_results` uses per-node dict keys to avoid write conflicts
3. **State machine enforcement**: `NodeStateMachine.transition()` validates all status changes against `VALID_TRANSITIONS` table; raises on illegal transitions
4. **Centralized LLM client**: All agents share one `LLMClient` instance; token tracking is accumulated there, not in individual agents
5. **Sandbox security**: `ShellTool` runs in `SANDBOX_DIR` with command blacklists and stripped env vars; `CodeExecutorTool` uses subprocess with timeout and output size limits; both share `subprocess_utils.run_with_limits()`
6. **Checkpoint**: `TaskDAG.save_checkpoint()` snapshots full DAG state at key milestones (each super-step, after adaptive planning) for debugging
7. **Evaluation via event probe**: `EvaluationProbe` hooks into `on_event` callback without modifying core code; forced routing via `config.PLAN_MODE` override with `classification_forced` flag to correctly handle scoring weights
8. **Tracing via event bridge**: `TracingBridge` hooks into the same `on_event` callback as the UI and evaluation; it creates OTel Spans with parent-child hierarchy matching the task execution flow; exception-safe — tracing errors never affect main execution

## Documentation

Detailed design docs live in `sxw_aicoding/docs/`:
- `codemap.md` — Full component reference with method signatures and data flow diagrams
- `CHANGELOG.md` — Version history v1→v7 with per-feature breakdown
- `data-structures-and-algorithms.md` — Schema, graph algorithms, state machine details
- `dynamic-features.md` — v1→v5 dynamic capability comparison
- `emergent-planning.md` — v5 emergent planning system design
- `hybrid-plan-routing.md` — v4 two-stage classifier design
- `llm-integration.md` — v6 LLM retry + ReActEngine design
- `upgrade-plan.md` — v6 upgrade plan with completion status
- `evaluation-guide.md` — Evaluation module design, metrics system, usage guide, and extension instructions
- `tracing-design.md` — v7 tracing architecture and design rationale
- `tracing-guide.md` — v7 tracing usage guide and extension instructions
