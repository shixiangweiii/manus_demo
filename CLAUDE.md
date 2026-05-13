# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Manus Demo is a multi-agent AI system demonstrating autonomous task execution through hybrid plan routing. The system classifies tasks by complexity and routes them to one of four execution engines: simple flat planning (v1), DAG-based parallel execution (v2), emergent TODO-list planning (v5), or goal-driven planning (v8). A v7 tracing module provides OpenTelemetry-based full-lifecycle observability.

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default, supports Ollama/Qwen/etc.)
- **UI**: Rich console with event-driven rendering
- **Current version**: v8.0

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (sequential ReAct)  → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (parallel super-steps) → Reflector
  emergent:  ENABLE_GOAL_DRIVEN_PLANNER=false → EmergentPlanner (TODO scheduling + per-TODO ReAct)
             ENABLE_GOAL_DRIVEN_PLANNER=true  → GoalDrivenPlanner (goal anchoring + dynamic TODO + goal reflection)
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
│   ├── emergent_planner.py    # EmergentPlannerAgent — Claude Code style TODO-driven planning (v5)
│   └── goal_driven_planner.py # GoalDrivenPlannerAgent — goal anchoring + dynamic TODO + goal reflection (v8)
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
│   ├── templates/             # Jinja2 HTML templates for web viewer
│   │   ├── base.html          # Dark theme base layout (header, content, CSS variables)
│   │   ├── trace_list.html    # Trace list page (table of traces)
│   │   └── trace_detail.html  # Trace detail page (left-right split: tree + detail panel)
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
- **Web viewer**: `python -m tracing` starts a FastAPI server for trace visualization; detail page uses a left-right split layout (span tree on left ~380px fixed, detail panel on right fills remaining width) with independent scrolling per panel and responsive fallback to vertical on narrow screens
- **Inline tracing**: `LLMClient._start_llm_span`/`_end_llm_span` for LLM calls; `BaseTool.traced_execute` for tool calls; `@traced` decorator for general-purpose manual span creation
- **Sensitive data**: `SENSITIVE_KEYS` set in `tracing/config.py` triggers automatic redaction of api_key, token, etc.
- **Unconditional full request/response recording**: LLM span attributes (`gen_ai.prompt.content`, `gen_ai.response.content`, `gen_ai.response.tool_calls`, `gen_ai.response.finish_reason`) are always recorded when tracing is enabled — no truncation, no sanitization, not gated by `TRACING_LOG_PROMPTS`. This is a demo/tutorial project; full raw data is required for observability. Response data is extracted by `_extract_response_data()` before `_end_llm_span()` to keep `_end_llm_span` free of OpenAI SDK type assumptions.
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
| `MAX_CONTEXT_TOKENS` | `8000` | Context token limit before compression |
| `PLAN_MODE` | `auto` | `auto` / `simple` / `complex` / `emergent` |
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | v8 goal-driven engine within emergent path |
| `GOAL_REANCHOR_INTERVAL` | `5` | Iterations between goal re-anchoring |
| `GOAL_REFLECTION_INTERVAL` | `1` | Iterations between goal reflection |
| `MAX_GOAL_DRIVEN_ITERATIONS` | `60` | v8 main loop iteration cap |
| `GOAL_DRIVEN_STAGNATION_WINDOW` | `3` | Consecutive no-progress rounds before early stop |
| `EMERGENT_PLANNING_ENABLED` | `true` | Enable v5/v8 emergent planning route |
| `MAX_REACT_ITERATIONS` | `10` | ReAct loop cap per node |
| `MAX_PARALLEL_NODES` | `3` | Super-step parallelism cap |
| `DAG_SERIAL_EXECUTION` | `true` | 串行执行 DAG 节点（默认开启；设 `false` 恢复并行，并行模式通过 `create_for_node()` 实例隔离保证安全） |
| `MAX_REPLAN_ATTEMPTS` | `3` | Max reflect-fail replan cycles |
| `TOKEN_TRACKING_ENABLED` | `true` | Enable per-call token tracking |
| `LLM_RETRY_ENABLED` | `false` | Enable exponential-backoff retry |
| `ENABLE_REACT_ENGINE_V2` | `false` | Use unified ReActEngine (v6 feature flag) |
| `ADAPTIVE_PLANNING_ENABLED` | `true` | Enable runtime DAG adaptation |
| `ADAPT_PLAN_INTERVAL` | `1` | Super-steps between adaptive checks |
| `ADAPT_PLAN_MIN_COMPLETED` | `1` | Min completed actions before adaptive |
| `NODE_EXECUTION_TIMEOUT` | `300` | Per-node timeout in seconds |
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | Sandboxed file/shell working directory |
| `MAX_TODO_ITEMS` | `20` | v5 TODO list max size |
| `MAX_TODO_RETRIES` | `3` | Max retries per TODO item |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | Context usage ratio triggering TODO compression |
| `MAX_EMERGENT_OUTER_ITERATIONS` | `60` | v5 emergent main loop iteration cap |
| `TOOL_FAILURE_THRESHOLD` | `2` | v3 consecutive failures before suggesting tool switch |
| `TRACING_ENABLED` | `false` | Master switch for v7 tracing |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `TRACING_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint |
| `TRACING_SAMPLE_RATE` | `1.0` | Sampling rate (0.0–1.0) |
| `TRACING_LOG_PROMPTS` | `false` | Legacy flag (no longer gates prompt recording — prompts are always recorded when tracing enabled; kept for config backward compat) |

## Common Commands

```bash
# Install
pip install -r requirements.txt

# Configure (copy and edit with your API key)
cp .env.example .env

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
python -m pytest tests/test_emergent_simple.py -v       # v5 emergent planning simple scenarios
python -m pytest tests/test_goal_driven_planner.py -v   # v8 goal-driven planning, milestones, stagnation
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
- **Replan edge pattern** — `_parse_dag()` may produce edges referencing nodes outside its parsed set (LLM references old DAG completed nodes); these are filtered and stored in `_filtered_edges`; `_merge_dags()` reconstructs valid ones after merge
- **OTel detach convention** — all `otel_context.detach()` calls in `tracing/bridge.py` are unprotected by try/except (OTel library catches ValueError internally); logging suppression is handled centrally by `OtelDetachFilter` in `main.py`, not at each call site

## Important Design Decisions

1. **Four routing paths**: Tasks classified by a two-stage hybrid classifier (rules fast-filter ~60-70% of tasks, LLM fallback for ambiguous ones); unknown classifications fall back to the complex DAG path; emergent path further splits into v5 (TODO-driven) or v8 (goal-driven) based on `ENABLE_GOAL_DRIVEN_PLANNER`
2. **DAG concurrency**: `asyncio.gather` runs ready nodes in parallel within each super-step (when `DAG_SERIAL_EXECUTION=false`); each parallel node gets an independent `ExecutorAgent` instance via `create_for_node()` to avoid `_messages` race conditions; `DAGState.node_results` uses per-node dict keys to avoid write conflicts
3. **State machine enforcement**: `NodeStateMachine.transition()` validates all status changes against `VALID_TRANSITIONS` table; raises on illegal transitions
4. **Centralized LLM client**: All agents share one `LLMClient` instance; token tracking is accumulated there, not in individual agents
5. **Sandbox security**: `ShellTool` runs in `SANDBOX_DIR` with command blacklists and stripped env vars; `CodeExecutorTool` uses subprocess with timeout and output size limits; both share `subprocess_utils.run_with_limits()`
6. **Checkpoint**: `TaskDAG.save_checkpoint()` snapshots full DAG state at key milestones (each super-step, after adaptive planning) for debugging
7. **Evaluation via event probe**: `EvaluationProbe` hooks into `on_event` callback without modifying core code; forced routing via `config.PLAN_MODE` override with `classification_forced` flag to correctly handle scoring weights
8. **Tracing via event bridge**: `TracingBridge` hooks into the same `on_event` callback as the UI and evaluation; it creates OTel Spans with parent-child hierarchy matching the task execution flow; exception-safe — tracing errors never affect main execution
9. **Context compression boundary safety**: `ContextManager._find_safe_split()` ensures the split between old and recent messages never breaks `tool_calls` structural groups (assistant+tool_responses must stay together), preventing orphaned tool messages that would cause LLM API 400 errors
10. **DAG dataflow dependencies**: `PLANNER_SYSTEM_PROMPT` Rule 6 requires actions to express cross-subgoal dataflow dependencies (e.g., `act_2_1.dependencies = ["act_1_1"]`); `_parse_dag()` automatically infers subgoal-level dependencies from cross-subgoal action deps — no code restriction on dep_id scope
11. **Replan robustness**: `_parse_dag()` filters orphan edges (source/target not in parsed nodes) and stores them in `TaskDAG._filtered_edges`; `_merge_dags()` reconstructs these cross-DAG edges after merge when both endpoints exist in the merged node set
12. **OTel context detach handling**: `OtelDetachFilter` in `main.py` suppresses OTel `Failed to detach context` ERROR tracebacks by downgrading the log record to INFO in-place — avoids misleading stack traces during concurrent asyncio DAG execution

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
- `推理引擎类型环境变量配置.md` — Complete guide on PLAN_MODE / ENABLE_GOAL_DRIVEN_PLANNER routing and env var configuration per engine type
- `related-papers.md` — Research papers referenced in system design
- `planning-gap-analysis.md` — Gap analysis for planning paradigms
