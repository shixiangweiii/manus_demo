# CLAUDE.md — Manus Demo Project Context

## Project Overview

Manus Demo is a multi-agent AI system demonstrating autonomous task execution through hybrid plan routing. The system classifies tasks by complexity and routes them to one of three execution paradigms: simple flat planning (v1), DAG-based parallel execution (v2), or emergent TODO-list planning (v5).

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default, supports Ollama/Qwen/etc.)
- **UI**: Rich console with event-driven rendering
- **Current version**: v6.0

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (sequential ReAct)  → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (parallel super-steps) → Reflector
  emergent:  EmergentPlanner.execute() → while(tool_use) loop + TODO list
All paths → Token usage summary → Long-term memory store
```

## Source Layout

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
│   ├── base.py                # BaseTool ABC — name, description, parameters_schema, execute()
│   ├── web_search.py          # WebSearchTool — mock search results
│   ├── code_executor.py       # CodeExecutorTool — subprocess sandbox Python execution
│   ├── file_ops.py            # FileOpsTool — sandboxed file read/write/list
│   ├── shell_tool.py          # ShellTool — sandboxed bash execution with command blacklist
│   ├── subprocess_utils.py    # Shared subprocess runner with timeout + output-size limits
│   └── router.py              # ToolRouter — per-node failure tracking, suggests alternative tools on threshold
├── memory/
│   ├── short_term.py          # ShortTermMemory — sliding-window message buffer
│   └── long_term.py           # LongTermMemory — JSON-file persistence + keyword search
├── context/
│   └── manager.py             # ContextManager — token estimation + LLM-based context compression
├── knowledge/
│   ├── retriever.py           # KnowledgeRetriever — TF-IDF + cosine similarity document retrieval
│   └── docs/                  # Knowledge base text files
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

## Token Tracking (current design)

Token tracking is centralized in `LLMClient`:
- `_call_records: list[LLMCallRecord]` — appended on every successful API call via `_record_call()`
- `get_call_records()` — returns a copy
- `reset_usage()` — clears for a new task
- `Orchestrator._finalize_token_usage()` — aggregates by engine and computes total from call records
- No snapshot/delta logic; safe under asyncio concurrency (list.append is atomic in single-threaded event loop)

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

# Tests (no API key needed)
python -m pytest tests/test_dag_capabilities.py -v
python -m pytest tests/test_emergent_planning.py -v
python -m pytest tests/ -v

# Syntax check modified files
python3 -m py_compile schema.py llm/client.py agents/orchestrator.py
```

## Dependencies

- `openai` — AsyncOpenAI client
- `pydantic` — Data models with validation
- `rich` — Terminal UI (tables, panels, trees)
- `python-dotenv` — `.env` file loading
- `pytest` / `pytest-asyncio` — Testing (optional)

## Code Conventions

- **All agents except OrchestratorAgent inherit `BaseAgent`** which provides `think()`, `think_json()`, `think_with_tools()` and manages message history; OrchestratorAgent is a standalone coordinator
- **All tools inherit `BaseTool`** with `name`, `description`, `parameters_schema`, `execute()`, and `to_openai_tool()`
- **Async throughout** — all LLM calls and tool executions are `async def`
- **Event-driven UI** — OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which forwards to the `on_event` callback in `main.py`; ExecutorAgent and ReflectorAgent do not emit events directly
- **Pydantic models** for data structures, but LLM message passing uses raw `list[dict[str, Any]]` (OpenAI API compatibility)
- **Chinese + English bilingual comments** — most modules have dual-language docstrings
- **Feature flags** — v6 capabilities (LLM retry, ReActEngine) default to disabled (`false`); v3/v5 features (adaptive planning, emergent planning) default to enabled (`true`)
- **Token tracking centralized** — only `LLMClient` and `OrchestratorAgent` manage token usage; individual execution agents (Executor, EmergentPlanner, Reflector, Planner) have no token tracking code

## Important Design Decisions

1. **Three routing paths**: Tasks classified by a two-stage hybrid classifier (rules fast-filter ~60-70% of tasks, LLM fallback for ambiguous ones)
2. **DAG concurrency**: `asyncio.gather` runs ready nodes in parallel within each super-step; `DAGState.node_results` uses per-node dict keys to avoid write conflicts
3. **State machine enforcement**: `NodeStateMachine.transition()` validates all status changes against `VALID_TRANSITIONS` table; raises on illegal transitions
4. **Centralized LLM client**: All agents share one `LLMClient` instance; token tracking is accumulated there, not in individual agents
5. **Sandbox security**: `ShellTool` runs in `SANDBOX_DIR` with command blacklists and stripped env vars; `CodeExecutorTool` uses subprocess with timeout and output size limits; both share `subprocess_utils.run_with_limits()`
6. **Checkpoint**: `TaskDAG.save_checkpoint()` snapshots full DAG state after each super-step for debugging

## Documentation

Detailed design docs live in `sxw_aicoding/docs/`:
- `codemap.md` — Full component reference with method signatures and data flow diagrams
- `CHANGELOG.md` — Version history v1→v6 with per-feature breakdown
- `data-structures-and-algorithms.md` — Schema, graph algorithms, state machine details
- `dynamic-features.md` — v1→v5 dynamic capability comparison
- `emergent-planning.md` — v5 emergent planning system design
- `hybrid-plan-routing.md` — v4 two-stage classifier design
- `llm-integration.md` — v6 LLM retry + ReActEngine design
- `upgrade-plan.md` — v6 upgrade plan with completion status
