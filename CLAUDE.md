# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Manus Demo is a multi-agent AI system demonstrating autonomous task execution through hybrid plan routing. The system classifies tasks by complexity and routes them to one of four execution engines: simple flat planning (v1), DAG-based parallel execution (v2), emergent TODO-list planning (v5), or goal-driven planning (v8). A v9 SubAgent mechanism allows any ReAct-loop agent to spawn isolated sub-agents for complex subtasks. A v7 tracing module provides OpenTelemetry-based full-lifecycle observability. A v13 HITL (Human-in-the-Loop) mechanism lets the LLM proactively ask the user for clarification via an `ask_user` tool when information is ambiguous.

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default, supports Ollama/Qwen/etc.)
- **UI**: Rich console with event-driven rendering
- **Current version**: v13.0

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (sequential ReAct via ReActEngine)  → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (parallel super-steps) → Reflector
  emergent:  ENABLE_GOAL_DRIVEN_PLANNER=false → EmergentPlanner (TODO scheduling + per-TODO ReAct)
             ENABLE_GOAL_DRIVEN_PLANNER=true  → GoalDrivenPlanner (goal anchoring + dynamic TODO + goal reflection)
All paths → Token usage summary → Long-term memory store
All paths → TracingBridge (event-to-span, v7 OpenTelemetry)
Any ReAct path (when SUBAGENT_ENABLED=true) → can call "subagent" tool → SubAgent (depth=1, isolated context, summary-only return)
Any ReAct path (when HITL_ENABLED=true AND interactive=true) → can call "ask_user" tool → pauses ReAct via asyncio.Future, UI collects input, returns "User response: <text>" or Error:
```

### Entry Point & Interactive Mode

- **`main.py`** parses `sys.argv` directly (not `argparse`). Verbose flag: `"--verbose" in sys.argv or "-v" in sys.argv`. Positional args joined as task string after filtering flags.
- **Interactive mode** (`run_interactive()`) creates one `LLMClient` + `OrchestratorAgent(interactive=True)` instance for the entire session — long-term memory accumulates across multiple tasks within a session. Quit commands: "quit", "exit", "q".
- **Single-task mode** (`run_single()`) creates `OrchestratorAgent(interactive=False)` so v13 HITL is **double-gated off** (tool not registered + guidance not injected) regardless of `HITL_ENABLED`.
- **Base tools** registered in `main.py` (both single-task and interactive paths): `WebSearchTool`, `FetchUrlTool`, `UserLocationTool`, `CodeExecutorTool`, `FileOpsTool`, `ShellTool`. `SubAgentTool` is injected by `OrchestratorAgent.__init__()` when `SUBAGENT_ENABLED=true`. `AskUserTool` (v13) is injected when `HITL_ENABLED=true AND interactive=True`.
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

- **`agents/`** — OrchestratorAgent (compose + route, no BaseAgent inheritance; v13: `interactive` param double-gates HITL), PlannerAgent (two-stage classifier + plan/DAG), ExecutorAgent (delegates to ReActEngine, no own ReAct loop), ReflectorAgent (exit criteria + quality; v13: now sees `tool_calls_log` summary in reflect/reflect_dag prompts), EmergentPlannerAgent (v5 TODO-driven), GoalDrivenPlannerAgent (v8 goal anchoring; v13: ToolRouter accounting fixed to match ReActEngine v12), SubAgent (v9 depth=1 isolated sub-agent), prompt_utils (system prompt composition + context injection + SubAgent / HITL tool-selection guidance + convergence hint; v13 adds `_HITL_RUNTIME_OVERRIDE` + `set_hitl_runtime_enabled()`)
- **`dag/`** — TaskDAG (graph + topological sort + ready-node detection + dynamic mutation), DAGExecutor (super-step parallel loop), NodeStateMachine (enforces legal status transitions)
- **`react/`** — ReActEngine (sole ReAct loop implementation; v12 removed legacy `_react_loop`; runs independent tool_calls concurrently via `asyncio.gather`)
- **`llm/`** — LLMClient (OpenAI-compatible async wrapper with retry + centralized per-call token tracking)
- **`tools/`** — BaseTool ABC, WebSearchTool (v11: Bailian MCP primary + DDGS fallback; v12: parses `{pages, tools}` Bailian object structure), FetchUrlTool (v11: Bailian WebParser MCP), UserLocationTool (env > memory file > IP geolocation fallback chain; deliberately does NOT use system timezone, since IANA zones are not geographic identifiers), CodeExecutorTool, FileOpsTool, ShellTool, SubAgentTool (v9 meta-tool), **AskUserTool (v13 HITL — `asyncio.Future` bridge, Semaphore(1) serialization, max_prompts/timeout/cancel guards)**, ToolRouter (per-node failure tracking; v12: success/failure decided after Error: detection), BailianMCPClient (v11: MCP Streamable HTTP client; v12: extracts ExceptionGroup sub-exceptions, single source of timeout truth), subprocess_utils (shared sandbox runner)
- **`tracing/`** — OpenTelemetry observability: TracingBridge (event→span), provider (TracerProvider setup), spans (span creation helpers), multi-backend exporters, FastAPI web viewer with Jinja2 templates (left-right split detail page), @traced decorator
- **`memory/`** — ShortTermMemory (sliding-window), LongTermMemory (JSON-file persistence + keyword search)
- **`context/`** — ContextManager (token estimation including assistant.tool_calls + LLM-based compression with safe split boundary)
- **`knowledge/`** — KnowledgeRetriever (TF-IDF + cosine similarity)
- **`evaluation/`** — Benchmark 3 paradigms with 4-dimension weighted scoring (Planning 30% / Execution 40% / Efficiency 20% / Reflection 10%)

### Event Multicast Pattern (Central to the System)

OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which fans out to multiple subscribers via `on_event` callback:

1. **UI renderer** (`main.py`) — Rich console output (tables, panels, trees)
2. **TracingBridge** (`tracing/bridge.py`) — Translates events into OTel Spans with parent-child hierarchy
3. **EvaluationProbe** (`evaluation/runner.py`) — Collects metrics per-phase for benchmark scoring

ExecutorAgent and ReflectorAgent do **not** emit events directly — they return results to their caller which then emits.

**v13 HITL events**: `ask_user_prompt` (carries `response_future: asyncio.Future` for UI to resolve — **NOT serializable**; only UI consumer extracts the Future, TracingBridge / EvaluationProbe use dict-lookup dispatch and silently ignore unknown events), `ask_user_response`, `ask_user_timeout`, `ask_user_cancelled`.

## Key Dependencies

`openai` (LLM API), `pydantic` (data models), `rich` (console UI), `python-dotenv` (env loading), `ddgs>=8.0.0` (web search DDGS fallback, v10), `mcp>=1.0.0` + `httpx>=0.24.0` (Bailian MCP client, v11), `opentelemetry-api/sdk>=1.27.0` + `opentelemetry-exporter-otlp>=1.27.0` (tracing), `fastapi>=0.100.0` + `uvicorn>=0.20.0` + `jinja2>=3.1.0` (tracing web viewer). Test deps: `pytest`, `pytest-asyncio`.

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

# HITL (v13) — only effective in interactive mode (run_single auto-disables)
HITL_ENABLED=true python main.py
HITL_ENABLED=true HITL_MAX_PROMPTS_PER_TASK=3 HITL_USER_INPUT_TIMEOUT=60 python main.py

# Tracing viewer (v7)
python -m tracing                    # Start web viewer on http://localhost:8000

# All tests (no API key needed, mock-based) — REQUIRES asyncio_mode flag
# (no conftest.py / pytest.ini exists; pytest-asyncio defaults to strict mode)
python -m pytest tests/ -v -o asyncio_mode=auto

# Run a single test
python -m pytest tests/test_dag_capabilities.py::test_topological_sort -v -o asyncio_mode=auto

# Skip integration tests that need real LLM API (when running offline / under sandbox)
python -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py

# Evaluation CLI (requires LLM_API_KEY)
python -m evaluation.eval_cli --dry-run                    # Show benchmark tasks
python -m evaluation.eval_cli --difficulty easy --modes simple  # Quick smoke test
python -m evaluation.eval_cli --output results.json        # Full eval with JSON export

# Syntax check modified files
python3 -m py_compile schema.py llm/client.py agents/orchestrator.py react/engine.py
```

## Key Configuration

All config via env vars / `.env` file (see `config.py` for full list). Most commonly needed:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_API_KEY` | — | API key (required) |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API endpoint (supports Ollama/Qwen/etc.) |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `DASHSCOPE_API_KEY` | — | Bailian MCP auth key (v11; absent → web_search uses DDGS fallback, fetch_url returns Error) |
| `USER_LOCATION` | — | Explicit city for `user_location` tool (highest priority in fallback chain) |
| `LOCATION_IP_LOOKUP_ENABLED` | `true` | Whether `user_location` may call public IP APIs (ipapi.co + ip.sb) when env/memory miss |
| `PLAN_MODE` | `auto` | `auto` / `simple` / `complex` / `emergent` |
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | v8 goal-driven engine within emergent path |
| `SUBAGENT_ENABLED` | `false` | v9 SubAgent master switch |
| `HITL_ENABLED` | `false` | v13 HITL master switch — auto-suppressed by `OrchestratorAgent(interactive=False)` regardless of this value |
| `HITL_MAX_PROMPTS_PER_TASK` | `5` | v13: per-task `ask_user` call cap; value is interpolated into the system-prompt guidance at runtime |
| `HITL_USER_INPUT_TIMEOUT` | `120` | v13: seconds to wait for user input before returning `Error:` |
| `TRACING_ENABLED` | `false` | Master switch for v7 tracing |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `MAX_REACT_ITERATIONS` | `10` | ReAct loop cap per node |
| `MAX_CONTEXT_TOKENS` | `16000` | v12: context compression threshold (raised from 8000; user .env may override) |
| `SEARCH_CONVERGENCE_THRESHOLD` | `3` | v11: web_search/fetch_url call count threshold for convergence hints |
| `FETCH_URL_MAX_CONTENT_LENGTH` | `10000` | v11: max chars returned by fetch_url before truncation |
| `TOOL_RESULT_TRUNCATION_LIMIT` | `2000` | v11/v12: max chars in ToolCallRecord AND tool messages sent to LLM (v12: previously only ToolCallRecord was truncated) |
| `DAG_SERIAL_EXECUTION` | `true` | Serial DAG execution (default; set `false` for parallel) |
| `EMERGENT_PLANNING_ENABLED` | `true` | Enable v5/v8 emergent planning route |
| `ENABLE_REACT_ENGINE_V2` | `false` | **DEPRECATED in v12**: ReActEngine is now the only implementation; flag retained for backward compat but no longer affects behavior |

## Code Conventions

- **All agents except OrchestratorAgent inherit `BaseAgent`** which provides `think()`, `think_json()`, `think_with_tools()` and manages message history; OrchestratorAgent does not inherit BaseAgent (no own LLM message history) — it composes sub-agents and shares a single `LLMClient` instance
- **All tools inherit `BaseTool`** with `name`, `description`, `parameters_schema`, `execute()`, and `to_openai_tool()`
- **Async throughout** — all LLM calls and tool executions are `async def`
- **Event-driven UI** — OrchestratorAgent, EmergentPlannerAgent, and DAGExecutor call `self._emit(event, data)` which forwards to the `on_event` callback in `main.py`; ExecutorAgent and ReflectorAgent do not emit events directly
- **Pydantic models** for data structures, but LLM message passing uses raw `list[dict[str, Any]]` (OpenAI API compatibility)
- **Chinese + English bilingual comments** — most modules have dual-language docstrings
- **Feature flags** — v6 capabilities (LLM retry) default to disabled (`false`); v3/v5 features (adaptive planning, emergent planning) default to enabled (`true`); v7 tracing defaults to disabled (`false`); v9 SubAgent defaults to disabled (`false`); v12 removed `ENABLE_REACT_ENGINE_V2` as a behavior flag (always-on); v13 HITL defaults to disabled (`false`) and additionally gated by `interactive` parameter
- **Token tracking centralized** — only `LLMClient` and `OrchestratorAgent` manage token usage; individual execution agents (Executor, EmergentPlanner, Reflector, Planner) have no token tracking code
- **OTel detach convention** — all `otel_context.detach()` calls in `tracing/bridge.py` are unprotected by try/except (OTel library catches ValueError internally); logging suppression is handled centrally by `OtelDetachFilter` in `main.py`, not at each call site
- **Fire-and-forget asyncio tasks need strong refs** — the event loop only holds weak refs to tasks. Modules that spawn background tasks must keep a module-level set + `add_done_callback(discard)` (e.g. `main._pending_input_tasks` for HITL input collection).

## System Prompt Composition (v12/v13)

`agents/prompt_utils.build_system_prompt(base, inject_context=True, inject_subagent_guidance=True, inject_location_guidance=True, inject_search_guidance=True, inject_hitl_guidance=True)` is the single composition entry point used by Executor and Planner. It appends:

1. **Context injection** (v12, default ON) — current date / weekday / time, so the LLM does not guess the year in search queries and the Planner does not over-split tasks for date discovery. Implemented in `build_context_injection()`.
2. **Location guidance** (always on) — `get_user_location` usage rules.
3. **Search guidance** (always on) — prefer `web_search` / `fetch_url` over `execute_python` for info retrieval.
4. **SubAgent tool guidance** (v9, only when `SUBAGENT_ENABLED=true`) — when-to-use / when-NOT-to-use heuristics. Set `inject_subagent_guidance=False` for agents that do not call tools (Planner, Reflector).
5. **HITL `ask_user` guidance** (v13, only when HITL is active) — when-to-use / when-NOT-to-use heuristics. Activation checks `_HITL_RUNTIME_OVERRIDE` first (set by `OrchestratorAgent` per-instance) and falls back to `config.HITL_ENABLED`. The `max_prompts` limit displayed in the guidance is interpolated from `config.HITL_MAX_PROMPTS_PER_TASK` at call time — never hard-coded.

`build_convergence_hint(tool_call_counts)` is the single source of dynamic NOTE/CRITICAL nudges based on `web_search`/`fetch_url` call frequency; called by ReActEngine after each iteration.

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
- **`tool.success` accuracy** (v12): `BaseTool.traced_execute` detects `Error:`-prefixed string returns and sets `tool.success=False` + `StatusCode.ERROR` on the span. Previously only exceptions were marked as failure; tools that swallow exceptions and return error strings (web_search/fetch_url) now correctly show as failed in traces.
- **Env var naming discrepancy**: The env var `TRACING_MAX_ATTR_LENGTH` maps to Python attribute `TRACING_MAX_ATTRIBUTE_LENGTH` — `.env.example` uses the shorter form, `config.py` reads it with the shorter key but exposes the longer attribute name.

## Bailian MCP Web Search + fetch_url (v11/v12)

Replaces DDGS-only search with Bailian MCP (Aliyun) primary + DDGS fallback, and adds a `fetch_url` tool for URL page content fetching.

**Key design decisions**:
- **Bailian primary + DDGS fallback**: When `DASHSCOPE_API_KEY` is set, WebSearchTool tries Bailian MCP first; on failure, falls back to DDGS. When key is absent, uses DDGS directly.
- **v12 result formatting**: `_format_bailian_results` parses Bailian's `{pages, tools, request_id, status}` object structure. The `tools[]` array (structured data like real-time stock prices, weather) is rendered FIRST under "## Structured data ({type})" headers; `pages[]` then follow as compact numbered list (title + snippet + URL only). `hostlogo` / `request_id` / `status` are dropped (no information value). Falls back gracefully to legacy list-of-results shape and raw text passthrough for unexpected JSON.
- **Per-call MCP connection**: `BailianMCPClient` uses per-call `streamablehttp_client` + `ClientSession` (no session caching, no singleton).
- **v12 timeout strategy**: Inner streamablehttp timeout is set to `outer_timeout * 4 + 30` so the OUTER `asyncio.wait_for` is the single source of truth. Previously double-layered timeouts caused inner `streamablehttp` to fire first, raising `ExceptionGroup` before the outer wait_for could yield a clean TimeoutError.
- **v12 ExceptionGroup unwrapping**: `BailianMCPClient.call_tool()` catches `BaseException`, inspects `exc.exceptions` for sub-exceptions, and re-raises as `RuntimeError(f"MCP {server}.{tool} failed: {details}")`. This converts opaque "unhandled errors in a TaskGroup (1 sub-exception)" into actionable diagnostics for the LLM.
- **Error transparency**: `WebSearchTool.execute()` / `FetchUrlTool.execute()` translate all exceptions into `Error:` prefixed strings for LLM consumption.
- **Convergence guidance**: `build_convergence_hint()` in `agents/prompt_utils.py` generates dynamic NOTE/CRITICAL messages based on tool call frequency.
- **Content truncation**: fetch_url truncates at `FETCH_URL_MAX_CONTENT_LENGTH` (10,000 chars default).

## SubAgent (v9)

Multi-agent mechanism following the Claude Code Subagent pattern. When `SUBAGENT_ENABLED=true`, the `subagent` tool is injected into the ExecutorAgent's tool list; the LLM can then decide to call it during ReAct loops.

**Key design principles**:
- **depth=1**: SubAgent's tool whitelist structurally excludes the `subagent` tool — it cannot spawn further SubAgents
- **Independent context**: SubAgent has its own `ReActEngine` instance, own messages list, own system prompt
- **Summary-only return**: Parent receives `SubAgentSummary` JSON (accomplished/findings/issues/artifacts/tool_calls_summary), never the full conversation history
- **Token budget circuit breaker**: `SubAgentTokenExhausted` exception raised via `on_iteration` callback when cumulative tokens exceed `SUBAGENT_MAX_TOKENS_PER_CALL`
- **Sandbox isolation**: Optional per-SubAgent sandbox subdirectory to prevent dual-write conflicts
- **Artifacts extraction**: `_extract_artifacts_from_log` uses `file_ops` tool's actual parameter name `filename` (not `path`/`file_path`) and filters only `action="write"` operations; shell-created files cannot be statically detected (best-effort)
- **HITL isolation (v13)**: SubAgent's tool whitelist also structurally excludes `ask_user` (3 filter sites in `tools/subagent_tool.py`) — SubAgents should report ambiguity via their structured summary, not by prompting the user directly.

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

## HITL — Human-in-the-Loop (v13)

When the LLM hits ambiguous information (e.g. IP-based location returned APPROXIMATE city) or needs a user preference, it can call the `ask_user` tool to pause the ReAct loop and collect input. Follows the Human-as-Tool pattern; bridges async execution and sync user input via `asyncio.Future`.

**Activation gating (double-gated)**:
- `config.HITL_ENABLED=true` is necessary but not sufficient
- `OrchestratorAgent(interactive=True)` is also required
- Effective flag is `self._hitl_active = config.HITL_ENABLED and interactive`
- When inactive, both the tool registration AND the system-prompt guidance injection are suppressed (no wasted LLM calls on a tool that would only return Error:)
- `OrchestratorAgent.__init__` calls `prompt_utils.set_hitl_runtime_enabled(self._hitl_active)` to push the runtime decision into `get_hitl_guidance()`

**Key design principles**:
- **Async bridge**: `AskUserTool.execute()` creates `asyncio.Future`, emits `ask_user_prompt` event carrying it, awaits with timeout. UI handler in `main.py` schedules `asyncio.to_thread(console.input, ...)` so the event loop keeps running.
- **Strong refs for fire-and-forget**: `main._pending_input_tasks: set[asyncio.Task]` holds strong refs to spawned input-collection tasks (event loop only keeps weak refs).
- **Semaphore(1) serialization**: prevents multiple concurrent prompts from overlapping in the console.
- **Error: prefix for all failure modes**: timeout, max_prompts exceeded, non-interactive, user cancel (Ctrl+C/EOF → UI sets future to `"(user cancelled)"` sentinel → `AskUserTool` detects it and returns `Error: User cancelled the prompt...`). All paths go through ReActEngine's `Error:` detection → ToolRouter records failure → evaluation can distinguish outcomes.
- **HITL_MAX_PROMPTS_PER_TASK**: per-task cap (reset at task boundary by `AskUserTool.reset_task_state()` called from `OrchestratorAgent.run()`).
- **Reflector visibility (v13 fix)**: `ReflectorAgent.reflect()` and `reflect_dag()` inject a `TOOL CALLS PER STEP/NODE` summary derived from `StepResult.tool_calls_log` into the LLM prompt. Without this, the soft-guidance rule ("flag missed ask_user opportunities") in `REFLECTOR_SYSTEM_PROMPT` had no data to act on.

**Event keys**: `ask_user_prompt` (carries non-serializable `response_future`), `ask_user_response`, `ask_user_timeout`, `ask_user_cancelled`.

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
13. **SubAgent depth=1 enforcement**: Structural, not just prompt-based — `SubAgentTool` always filters "subagent" (and "ask_user", v13) from the tool whitelist before passing to `SubAgent`, so the LLM literally cannot call them
14. **ReActEngine on_iteration callback**: `execute()` accepts `on_iteration: Callable[[int, list[ToolCallRecord]], None]` invoked after each iteration; used by `SubAgent._on_react_iteration()` for token budget checking (can raise to abort); `StepResult.iterations_completed` populated from the iteration counter
15. **SubAgent token accounting**: Uses record index range (`records[_records_before:]`) throughout both `_on_react_iteration` and `run()` — no delta method, no `_get_total_tokens()` helper; `_records_before` is initialized to 0 in `__init__` and set to `len(llm_client.get_call_records())` at the start of each `run()`
16. **SubAgent token budget scope**: The per-call token budget covers only the ReAct main loop; the summarize step (one additional LLM call, `max_tokens=1500`) is excluded from the budget check by design, as it occurs after the loop completes and before returning
17. **Bailian MCP primary + DDGS fallback (v11)**: WebSearchTool tries Bailian MCP first when `DASHSCOPE_API_KEY` is set; on failure falls back to DDGS; when key is absent uses DDGS directly. FetchUrlTool requires `DASHSCOPE_API_KEY` (no DDGS equivalent for page fetching). Both tools use `Error:` prefixed strings for error transparency, shared with ReActEngine's `[TOOL ERROR]` detection.
18. **Convergence hint single source (v11)**: `build_convergence_hint()` in `agents/prompt_utils.py` is the sole implementation; ReActEngine and `EmergentPlannerAgent` import and call it — no duplicated convergence logic.
19. **Per-call MCP connection (v11)**: `BailianMCPClient` has no singleton or session caching; each `call_tool()` creates a fresh `streamablehttp_client` + `ClientSession`, calls the tool, then closes.
20. **ReActEngine is the only ReAct implementation (v12)**: Legacy `_react_loop` removed from `ExecutorAgent`. `ENABLE_REACT_ENGINE_V2` flag retained in config for backward compat but no longer affects behavior. `EmergentPlannerAgent` still keeps a legacy non-ReAct fallback path; the `ENABLE_REACT_ENGINE_V2=false` branch there is preserved for compatibility but should be considered for removal in a future cleanup. **`GoalDrivenPlannerAgent` has its OWN ReAct loop independent of `ReActEngine`** — v13 brought its ToolRouter accounting in line with ReActEngine (see #21).
21. **ToolRouter accounting (v12 ReActEngine + v13 GoalDrivenPlanner)**: `record_failure()` vs `record_success()` is decided AFTER `Error:`-prefix detection on the result. Previously `record_success()` was called immediately after a non-throwing `traced_execute()`, masking tools that swallow exceptions and return error strings. v12 fixed this in `react/engine.py:251-261`; v13 propagated the same fix to `agents/goal_driven_planner.py:707-723` (which has its own independent tool-execution loop) — important because HITL's `ask_user` returns `Error:` on timeout/limit/cancel/non-interactive and these would otherwise be miscounted as success in the v8 goal-driven path.
22. **Tool result truncation reaches the LLM (v12)**: `react/engine.py` now truncates oversized successful tool results at `TOOL_RESULT_TRUNCATION_LIMIT` BEFORE inserting into the messages list (previously only `ToolCallRecord` was truncated, while the LLM saw the full content). The truncation appends a clearly visible `[Tool output truncated at X chars; original length=Y]` marker so the LLM knows there is more available.
23. **Concurrent tool_calls (v12)**: When the LLM returns multiple `tool_calls` in a single response, ReActEngine executes them concurrently via `asyncio.gather`. Results are then iterated in original order to write `tool_messages` in the order required by the OpenAI protocol (tool message order must match assistant.tool_calls order). ToolRouter writes are concurrency-safe in single-threaded asyncio.
24. **Context injection into system prompts (v12)**: `prompt_utils.build_context_injection()` adds today's date / weekday / local time to Executor and Planner system prompts. This eliminates two recurring failures: LLM guessing wrong year in search queries, and Planner over-splitting "today/tomorrow" tasks for date-discovery steps. Planner's IMPLICIT DATA rule was rewritten to (a) USE injected date directly, (b) still create discovery steps for location/preferences.
25. **ContextManager token estimation (v12)**: `estimate_messages_tokens` now counts `assistant.tool_calls[].function.{name, arguments}` in addition to `content`. Previously the tool_calls field was ignored, causing 12-30% underestimation and `compress_if_needed` failing to trigger even when prompts had ballooned past 18k tokens. Default `MAX_CONTEXT_TOKENS` raised from 8000 → 16000 to match real usage patterns; `.env.example` updated, but a user's existing `.env` file may still contain the old value.
26. **HITL double-gating (v13)**: `OrchestratorAgent.__init__(interactive: bool = True)` combined with `config.HITL_ENABLED` decides the runtime `self._hitl_active` flag. Both tool registration AND `prompt_utils._HITL_RUNTIME_OVERRIDE` are flipped together so the LLM never sees `ask_user` in tools nor in guidance when it cannot actually be used (single-task mode). This avoids the "register a tool that always returns Error:" anti-pattern.
27. **HITL guidance is dynamic, not static (v13)**: `_HITL_GUIDANCE_TEMPLATE` is a format string; `get_hitl_guidance()` interpolates `config.HITL_MAX_PROMPTS_PER_TASK` at call time so the limit the LLM is told about always matches the actual configured cap. Don't hard-code numbers into the template.
28. **HITL cancellation goes through Error: (v13)**: UI's `_collect_and_resolve` resolves the Future with the literal sentinel string `"(user cancelled)"` on KeyboardInterrupt/EOF. `AskUserTool` detects this sentinel before returning the normal `"User response: ..."` path and instead returns an `Error:` string + emits `ask_user_cancelled`. This unifies cancel with timeout/limit/non-interactive at the ToolRouter level and makes the outcome distinguishable to evaluation/tracing.
29. **Reflector tool-call visibility (v13)**: Both `ReflectorAgent.reflect()` and `reflect_dag()` prepend a `TOOL CALLS PER STEP/NODE` summary built from `r.tool_calls_log` to the LLM prompt. The soft rule "flag missed `ask_user` opportunities" in `REFLECTOR_SYSTEM_PROMPT` requires this data to be effective; without it the rule is dead text.

## Documentation

Detailed design docs live in `sxw_aicoding/docs/` (codemap, CHANGELOG, per-feature design docs, evaluation guide, tracing design/guide, env var configuration guide, related papers, planning gap analysis). The v13 HITL design is documented in `sxw_aicoding/Human-in-the-Loop专项/` (v13 设计方案 / 核心流程 / 调研资料).
