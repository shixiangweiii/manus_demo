# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Manus Demo is a multi-agent AI system demonstrating autonomous task execution through hybrid plan routing. The system classifies tasks by complexity and routes them to one of four execution engines: simple flat planning (v1), DAG-based parallel execution (v2), emergent TODO-list planning (v5), or goal-driven planning (v8). A v9 SubAgent mechanism allows any ReAct-loop agent to spawn isolated sub-agents for complex subtasks. A v7 tracing module provides OpenTelemetry-based full-lifecycle observability.

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default, supports Ollama/Qwen/etc.)
- **UI**: Rich console with event-driven rendering
- **Current version**: v9.0

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (sequential ReAct)  → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (parallel super-steps) → Reflector
  emergent:  ENABLE_GOAL_DRIVEN_PLANNER=false → EmergentPlanner (TODO scheduling + per-TODO ReAct)
             ENABLE_GOAL_DRIVEN_PLANNER=true  → GoalDrivenPlanner (goal anchoring + dynamic TODO + goal reflection)
All paths → Token usage summary → Long-term memory store
All paths → TracingBridge (event-to-span, v7 OpenTelemetry)
Any ReAct path (when SUBAGENT_ENABLED=true) → can call "subagent" tool → SubAgent (depth=1, isolated context, summary-only return)
```

### Entry Point & Interactive Mode

- **`main.py`** parses `sys.argv` directly (not `argparse`). Verbose flag: `"--verbose" in sys.argv or "-v" in sys.argv`. Positional args joined as task string after filtering flags.
- **Interactive mode** (`run_interactive()`) creates one `LLMClient` + `OrchestratorAgent` instance for the entire session — long-term memory accumulates across multiple tasks within a session. Quit commands: "quit", "exit", "q".
- **Base tools** registered in `main.py`: `WebSearchTool`, `CodeExecutorTool`, `FileOpsTool`, `ShellTool`. `SubAgentTool` is injected by `OrchestratorAgent.__init__()` when `SUBAGENT_ENABLED=true`, not in main.py's tool list.
- **`on_event` callback** in `main.py` handles 30+ event types with Rich console rendering. All event rendering logic lives here.
- **Logging suppression**: `setup_logging()` sets `httpx`, `openai`, `httpcore` loggers to WARNING level; `OtelDetachFilter` suppresses OTel detach errors.

## Key Schema Enums

- **NodeStatus** (7 states): `PENDING → READY → RUNNING → COMPLETED | FAILED | SKIPPED | ROLLED_BACK`
- **NodeType**: `GOAL / SUBGOAL / ACTION`
- **EdgeType**: `DEPENDENCY / CONDITIONAL / ROLLBACK`
- **StepStatus**: `PENDING / RUNNING / COMPLETED / FAILED / SKIPPED`
- **ExitCriteria** has 3-tier behavior: LLM validation (default), direct `result.success` check, or skip entirely
- **TodoStatus**: `PENDING / IN_PROGRESS / COMPLETED / BLOCKED`
- **SubAgentStatus**: `PENDING / RUNNING / COMPLETED / FAILED / TIMED_OUT`
- **GoalAction**: `EXECUTE_TODO / REPLAN / COMPLETE`

## Module Roles

- **`agents/`** — OrchestratorAgent (compose + route, no BaseAgent inheritance), PlannerAgent (two-stage classifier + plan/DAG), ExecutorAgent (ReAct loop), ReflectorAgent (exit criteria + quality), EmergentPlannerAgent (v5 TODO-driven), GoalDrivenPlannerAgent (v8 goal anchoring), SubAgent (v9 depth=1 isolated sub-agent), prompt_utils (system prompt composition + SubAgent tool-selection guidance)
- **`dag/`** — TaskDAG (graph + topological sort + ready-node detection + dynamic mutation), DAGExecutor (super-step parallel loop), NodeStateMachine (enforces legal status transitions)
- **`react/`** — ReActEngine (unified ReAct loop, v6 feature-flagged via `ENABLE_REACT_ENGINE_V2`)
- **`llm/`** — LLMClient (OpenAI-compatible async wrapper with retry + centralized per-call token tracking)
- **`tools/`** — BaseTool ABC, WebSearchTool, CodeExecutorTool, FileOpsTool, ShellTool, SubAgentTool (v9 meta-tool), ToolRouter (per-node failure tracking), subprocess_utils (shared sandbox runner)
- **`tracing/`** — OpenTelemetry observability: TracingBridge (event→span), provider (TracerProvider setup), spans (span creation helpers), multi-backend exporters, FastAPI web viewer with Jinja2 templates (left-right split detail page), @traced decorator
- **`memory/`** — ShortTermMemory (sliding-window), LongTermMemory (JSON-file persistence + keyword search)
- **`context/`** — ContextManager (token estimation + LLM-based compression with safe split boundary)
- **`knowledge/`** — KnowledgeRetriever (TF-IDF + cosine similarity)
- **`evaluation/`** — Benchmark 3 paradigms with 4-dimension weighted scoring (Planning 30% / Execution 40% / Efficiency 20% / Reflection 10%)

### Event Multicast Pattern (Central to the System)

OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which fans out to multiple subscribers via `on_event` callback:

1. **UI renderer** (`main.py`) — Rich console output (tables, panels, trees)
2. **TracingBridge** (`tracing/bridge.py`) — Translates events into OTel Spans with parent-child hierarchy
3. **EvaluationProbe** (`evaluation/runner.py`) — Collects metrics per-phase for benchmark scoring

ExecutorAgent and ReflectorAgent do **not** emit events directly — they return results to their caller which then emits.

## Key Dependencies

`openai` (LLM API), `pydantic` (data models), `rich` (console UI), `python-dotenv` (env loading), `ddgs>=8.0.0` (web search, v10), `opentelemetry-api/sdk>=1.27.0` + `opentelemetry-exporter-otlp>=1.27.0` (tracing), `fastapi>=0.100.0` + `uvicorn>=0.20.0` + `jinja2>=3.1.0` (tracing web viewer). Test deps: `pytest`, `pytest-asyncio`.

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

# SubAgent (v9)
SUBAGENT_ENABLED=true PLAN_MODE=emergent python main.py "multi-step research task"

# Tracing viewer (v7)
python -m tracing                    # Start web viewer on http://localhost:8000

# All tests (no API key needed, mock-based)
python -m pytest tests/ -v

# Run a single test
python -m pytest tests/test_dag_capabilities.py::test_topological_sort -v

# Evaluation CLI (requires LLM_API_KEY)
python -m evaluation.eval_cli --dry-run                    # Show benchmark tasks
python -m evaluation.eval_cli --difficulty easy --modes simple  # Quick smoke test
python -m evaluation.eval_cli --output results.json        # Full eval with JSON export

# Syntax check modified files
python3 -m py_compile schema.py llm/client.py agents/orchestrator.py agents/subagent.py tools/subagent_tool.py
```

## Key Configuration

All config via env vars / `.env` file (see `config.py` for full list). Most commonly needed:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_API_KEY` | — | API key (required) |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API endpoint (supports Ollama/Qwen/etc.) |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `PLAN_MODE` | `auto` | `auto` / `simple` / `complex` / `emergent` |
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | v8 goal-driven engine within emergent path |
| `SUBAGENT_ENABLED` | `false` | v9 SubAgent master switch |
| `TRACING_ENABLED` | `false` | Master switch for v7 tracing |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `MAX_REACT_ITERATIONS` | `10` | ReAct loop cap per node |
| `DAG_SERIAL_EXECUTION` | `true` | Serial DAG execution (default; set `false` for parallel) |
| `EMERGENT_PLANNING_ENABLED` | `true` | Enable v5/v8 emergent planning route |

## Code Conventions

- **All agents except OrchestratorAgent inherit `BaseAgent`** which provides `think()`, `think_json()`, `think_with_tools()` and manages message history; OrchestratorAgent does not inherit BaseAgent (no own LLM message history) — it composes sub-agents and shares a single `LLMClient` instance
- **All tools inherit `BaseTool`** with `name`, `description`, `parameters_schema`, `execute()`, and `to_openai_tool()`
- **Async throughout** — all LLM calls and tool executions are `async def`
- **Event-driven UI** — OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which forwards to the `on_event` callback in `main.py`; ExecutorAgent and ReflectorAgent do not emit events directly
- **Pydantic models** for data structures, but LLM message passing uses raw `list[dict[str, Any]]` (OpenAI API compatibility)
- **Chinese + English bilingual comments** — most modules have dual-language docstrings
- **Feature flags** — v6 capabilities (LLM retry, ReActEngine) default to disabled (`false`); v3/v5 features (adaptive planning, emergent planning) default to enabled (`true`); v7 tracing defaults to disabled (`false`); v9 SubAgent defaults to disabled (`false`)
- **Token tracking centralized** — only `LLMClient` and `OrchestratorAgent` manage token usage; individual execution agents (Executor, EmergentPlanner, Reflector, Planner) have no token tracking code
- **OTel detach convention** — all `otel_context.detach()` calls in `tracing/bridge.py` are unprotected by try/except (OTel library catches ValueError internally); logging suppression is handled centrally by `OtelDetachFilter` in `main.py`, not at each call site

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
- **Env var naming discrepancy**: The env var `TRACING_MAX_ATTR_LENGTH` maps to Python attribute `TRACING_MAX_ATTRIBUTE_LENGTH` — `.env.example` uses the shorter form, `config.py` reads it with the shorter key but exposes the longer attribute name.

## SubAgent (v9)

Multi-agent mechanism following the Claude Code Subagent pattern. When `SUBAGENT_ENABLED=true`, the `subagent` tool is injected into the ExecutorAgent's tool list; the LLM can then decide to call it during ReAct loops.

**Key design principles**:
- **depth=1**: SubAgent's tool whitelist structurally excludes the `subagent` tool — it cannot spawn further SubAgents
- **Independent context**: SubAgent has its own `ReActEngine` instance, own messages list, own system prompt
- **Summary-only return**: Parent receives `SubAgentSummary` JSON (accomplished/findings/issues/artifacts/tool_calls_summary), never the full conversation history
- **Token budget circuit breaker**: `SubAgentTokenExhausted` exception raised via `on_iteration` callback when cumulative tokens exceed `SUBAGENT_MAX_TOKENS_PER_CALL`
- **Sandbox isolation**: Optional per-SubAgent sandbox subdirectory to prevent dual-write conflicts
- **Artifacts extraction**: `_extract_artifacts_from_log` uses `file_ops` tool's actual parameter name `filename` (not `path`/`file_path`) and filters only `action="write"` operations; shell-created files cannot be statically detected (best-effort)

**Anti-pattern defenses**:
- #2 Context leak: independent messages list, only structured summary returned
- #3 Depth=1: structural enforcement via tool whitelist filtering
- #4 Dual-write: sandbox directory isolation
- #5 Self-critique: all summary paths go through LLM reflection (no short-path bypass with `issues=""`); `_summarize_result` has 3 fallback levels: model_validate → unexpected structure → generation failed
- #6 Summary loss: `SubAgentSummary` structured artifact + full `tool_calls_log` preserved in `SubAgentResult`
- #8 Token explosion: per-call token budget + call count limit (`SUBAGENT_MAX_CALLS_PER_TASK`)

**Integration flow**:
1. `OrchestratorAgent.__init__()` — if `SUBAGENT_ENABLED`, creates `SubAgentTool` and appends to tools list
2. LLM calls `subagent(task_description, tool_whitelist?)` during ReAct
3. `SubAgentTool.execute()` — validates whitelist, creates isolated sandbox dir, spawns `SubAgent` with `context=""` (not `task_description` — avoids P0 double-write where task desc would appear twice in the ReAct prompt)
4. `SubAgent.run()` — runs `ReActEngine.execute()` with `on_iteration=self._on_react_iteration` for token budget checking
5. Returns `SubAgentResult.summary_text` (JSON string) to parent; `SubAgentTool.reset_task_state()` called at task boundary

**Event keys** (emitted by SubAgent, consumed by UI/TracingBridge/EvaluationProbe): `subagent_start`, `subagent_complete`, `subagent_failed`, `subagent_timed_out`, `subagent_limit_exceeded`. All event dicts use `"iterations_used"` key (not `"iterations"`).

## Evaluation Module

Benchmarks all three planning paradigms (simple/complex/emergent) against 12 tasks (4 easy, 4 medium, 4 hard).

**4-Dimension Weighted Scoring**:
- Planning (30%) — classification accuracy, plan structure validity, step coverage ratio, generation speed
- Execution (40%) — task success, step success rate, tool accuracy
- Efficiency (20%) — trajectory efficiency (step_success_rate / avg_iterations_per_step), token efficiency, time efficiency, replan penalty
- Reflection Accuracy (10%) — whether Reflector's pass/fail verdict matches ground truth

**Event Probe Pattern**: `EvaluationProbe` hooks into `on_event` callback without modifying core code. `EvaluationRunner` forces routing via `config.PLAN_MODE` override and sets `classification_forced` dynamically (`True` when forced, `False` when auto). Forced mode redistributes classification weight to other planning dimensions in scoring.

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
13. **SubAgent depth=1 enforcement**: Structural, not just prompt-based — `SubAgentTool` always filters "subagent" from the tool whitelist before passing to `SubAgent`, so the LLM literally cannot call it
14. **ReActEngine on_iteration callback**: `execute()` accepts `on_iteration: Callable[[int, list[ToolCallRecord]], None]` invoked after each iteration; used by `SubAgent._on_react_iteration()` for token budget checking (can raise to abort); `StepResult.iterations_completed` populated from the iteration counter
15. **SubAgent token accounting**: Uses record index range (`records[_records_before:]`) throughout both `_on_react_iteration` and `run()` — no delta method, no `_get_total_tokens()` helper; `_records_before` is initialized to 0 in `__init__` and set to `len(llm_client.get_call_records())` at the start of each `run()`
16. **SubAgent token budget scope**: The per-call token budget covers only the ReAct main loop; the summarize step (one additional LLM call, `max_tokens=1500`) is excluded from the budget check by design, as it occurs after the loop completes and before returning

## Documentation

Detailed design docs live in `sxw_aicoding/docs/` (codemap, CHANGELOG, per-feature design docs, evaluation guide, tracing design/guide, env var configuration guide, related papers, planning gap analysis).