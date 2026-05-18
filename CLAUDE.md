# CLAUDE.md

## Project Overview

Multi-agent AI system with hybrid plan routing. Tasks classified by complexity → one of four engines: simple flat planning (v1), DAG-based parallel (v2), emergent TODO-list (v5), or goal-driven (v8). Supports SubAgent spawning (v9), OTel tracing (v7), and HITL ask_user (v13).

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default)
- **UI**: Rich console, event-driven
- **Version**: v13.0 + Wave-1..7 SubAgent overhaul

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (ReActEngine)      → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (super-steps)   → Reflector
  emergent:  ENABLE_GOAL_DRIVEN_PLANNER=false → EmergentPlanner (TODO + per-TODO ReAct)
             ENABLE_GOAL_DRIVEN_PLANNER=true  → GoalDrivenPlanner (goal anchoring + dynamic TODO)
All paths → Token usage → Long-term memory → TracingBridge (OTel)
ReAct loops → subagent tool (depth=1, isolated, summary-only)
ReAct loops → ask_user tool (HITL, asyncio.Future bridge, interactive-only)
```

## Entry Point

- **`main.py`** parses `sys.argv` directly. `"--verbose"` / `"-v"` for debug. Positional args joined as task.
- **Interactive** (`run_interactive()`): one `OrchestratorAgent(interactive=True)` for session, memory accumulates.
- **Single-task** (`run_single()`): `OrchestratorAgent(interactive=False)` — HITL double-gated off.
- **Base tools** in `main.py`: `WebSearchTool`, `FetchUrlTool`, `UserLocationTool`, `CodeExecutorTool`, `FileOpsTool`, `ShellTool`. `SubAgentTool` injected when `SUBAGENT_ENABLED=true`. `AskUserTool` when `HITL_ENABLED=true AND interactive=True`.
- **`on_event` callback**: 30+ event types with Rich rendering. `OtelDetachFilter` suppresses OTel detach errors.

## Key Enums (schema.py)

- **NodeStatus**: `PENDING → READY → RUNNING → COMPLETED | FAILED | SKIPPED | ROLLED_BACK`
- **NodeType**: `GOAL / SUBGOAL / ACTION`
- **EdgeType**: `DEPENDENCY / CONDITIONAL / ROLLBACK`
- **StepStatus**: `PENDING / RUNNING / COMPLETED / FAILED / SKIPPED`
- **TodoStatus**: `PENDING / IN_PROGRESS / COMPLETED / BLOCKED`
- **SubAgentStatus**: `PENDING / RUNNING / COMPLETED / FAILED / TIMED_OUT`
- **GoalAction**: `EXECUTE_TODO / REPLAN / COMPLETE`

## Module Roles

- **`agents/`** — OrchestratorAgent (compose+route, no BaseAgent), PlannerAgent (classifier+plan/DAG), ExecutorAgent (delegates to ReActEngine), ReflectorAgent (exit criteria), EmergentPlannerAgent (v5 TODO), GoalDrivenPlannerAgent (v8 goal), SubAgent (v9 depth=1), prompt_utils (system prompt composition + context injection + convergence hints)
- **`dag/`** — TaskDAG, DAGExecutor (super-step parallel), NodeStateMachine
- **`react/`** — ReActEngine (canonical loop, concurrent tool_calls), tool_call_helpers (`attribute_caller`/`classify_result`/`truncate_for_llm` — shared by all 3 ReAct loops)
- **`llm/`** — LLMClient (async wrapper, centralized token tracking, `caller_tag` per-call attribution)
- **`tools/`** — BaseTool ABC, WebSearchTool (Bailian MCP + DDGS fallback), FetchUrlTool, UserLocationTool, CodeExecutorTool, FileOpsTool, ShellTool, SubAgentTool, AskUserTool, ToolRouter, BailianMCPClient
- **`tracing/`** — TracingBridge (event→span), FastAPI web viewer, multi-backend exporters
- **`memory/`** — ShortTermMemory (sliding-window), LongTermMemory (JSON-file)
- **`context/`** — ContextManager (token estimation + LLM-based compression with safe split)
- **`knowledge/`** — KnowledgeRetriever (TF-IDF + cosine)
- **`evaluation/`** — 12 tasks, 4-dimension weighted scoring (Planning 30% / Execution 40% / Efficiency 20% / Reflection 10%)

## Event Multicast

OrchestratorAgent, EmergentPlannerAgent, DAGExecutor emit via `self._emit(event, data)` → fans out to:
1. **UI** (main.py Rich console)
2. **TracingBridge** (OTel spans)
3. **EvaluationProbe** (metrics)

ExecutorAgent and ReflectorAgent do NOT emit — they return results to callers.

## Common Commands

```bash
pip install -r requirements.txt
cp .env.example .env

python main.py                          # Interactive
python main.py "task description"       # Single task
python main.py -v                       # Verbose

PLAN_MODE=simple|complex|emergent python main.py "task"
SUBAGENT_ENABLED=true python main.py "task"
HITL_ENABLED=true python main.py        # Interactive only
python -m tracing                       # Web viewer localhost:8000

python -m pytest tests/ -v -o asyncio_mode=auto
python -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py

python -m evaluation.eval_cli --dry-run
python -m evaluation.eval_cli --difficulty easy --modes simple
python -m evaluation.eval_cli --output results.json

python3 -m py_compile schema.py llm/client.py agents/orchestrator.py react/engine.py
```

## Key Configuration

All via env vars / `.env` (see `config.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_API_KEY` | — | API key (required) |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API endpoint |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `DASHSCOPE_API_KEY` | — | Bailian MCP key (absent → DDGS fallback) |
| `PLAN_MODE` | `auto` | `auto` / `simple` / `complex` / `emergent` |
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | v8 within emergent path |
| `SUBAGENT_ENABLED` | `false` | v9 master switch |
| `HITL_ENABLED` | `false` | v13 master switch (auto-suppressed in single-task) |
| `HITL_MAX_PROMPTS_PER_TASK` | `5` | Per-task ask_user cap |
| `HITL_USER_INPUT_TIMEOUT` | `120` | Seconds to wait for user input |
| `TRACING_ENABLED` | `false` | v7 tracing switch |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `MAX_REACT_ITERATIONS` | `10` | ReAct loop cap |
| `MAX_CONTEXT_TOKENS` | `16000` | Context compression threshold |
| `TOOL_RESULT_TRUNCATION_LIMIT` | `2000` | Max chars in tool messages to LLM |
| `SEARCH_CONVERGENCE_THRESHOLD` | `3` | Web search call count for convergence hints |
| `DAG_SERIAL_EXECUTION` | `true` | Set `false` for parallel |
| `EMERGENT_PLANNING_ENABLED` | `true` | Enable v5/v8 route |

## Code Conventions

- **OrchestratorAgent** composes sub-agents, shares one `LLMClient`, does NOT inherit `BaseAgent`. All other agents inherit `BaseAgent` (provides `think()`, `think_json()`, `think_with_tools()` + message history).
- **Tools** inherit `BaseTool` with `name`, `description`, `parameters_schema`, `execute()`, `to_openai_tool()`.
- **Async throughout** — all LLM calls and tool executions are `async def`.
- **Pydantic models** for data structures; LLM messages use raw `list[dict[str, Any]]` (OpenAI API compat).
- **Feature flags**: newer features default off (`false`); core features default on (`true`). `ENABLE_REACT_ENGINE_V2` is deprecated (always-on in v12).
- **Token tracking centralized** in `LLMClient` only; individual agents have no token tracking code.
- **System prompts built per-instance** at agent `__init__` via `build_system_prompt()`, NOT at module import time. Each agent stores result on `self.system_prompt`.
- **Error transparency**: tools return `Error:` prefixed strings for LLM consumption; ReActEngine detects these as failures.
- **Fire-and-forget asyncio tasks** need module-level set + `add_done_callback(discard)` for strong refs.
- **Bilingual comments** (Chinese + English).

## Critical Implementation Notes

1. **ReActEngine `_current_log`**: rebound (fresh list per `execute()`), never `clear()` — avoids concurrency bug under parallel DAG nodes sharing one engine.
2. **Lazy import in ReActEngine**: `build_convergence_hint` imported inside `execute()`, not at module top — avoids circular import `react.engine ↔ agents.prompt_utils`. Don't move to top-level.
3. **SubAgentTool local capture**: `self._parent_name` copied to local var before any await — prevents concurrent `set_caller` overwriting attribution.
4. **ToolRouter three-state**: `classify_result()` runs AFTER tool call; precedence: `rate_limited > error > success`.
5. **Context compression**: `_find_safe_split()` never breaks `tool_calls` groups (assistant + tool_responses stay together).
6. **OTel detach**: all `otel_context.detach()` calls unprotected by try/except; logging suppression via `OtelDetachFilter` in `main.py`.
7. **LLM span lifecycle**: `_record_call()` must run before `_end_llm_span()` (reads `_call_records[-1]`). Safe in single-threaded asyncio (no await between).
8. **HITL double-gating**: `OrchestratorAgent(interactive=False)` suppresses both tool registration AND prompt guidance, regardless of `HITL_ENABLED`.
9. **SubAgent depth=1**: structural — tool whitelist filters out `subagent` and `ask_user`.
10. **caller_tag**: named kwarg on `chat`/`chat_with_tools`/`chat_json` — never put in `**kwargs` (would leak to OpenAI API).
11. **DAG dataflow**: `_parse_dag()` infers subgoal-level deps from cross-subgoal action deps; orphan edges stored in `_filtered_edges`.
