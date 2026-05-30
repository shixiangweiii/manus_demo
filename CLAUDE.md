# CLAUDE.md

## Project Overview

Multi-agent AI system with hybrid plan routing. Tasks classified by complexity → one of four engines: simple flat planning (v1), DAG-based parallel (v2), emergent TODO-list (v5), or goal-driven (v8). Supports SubAgent spawning (v9), OTel tracing (v7), HITL ask_user (v13), Task Resume/checkpoint persistence (v14.5), Agentic Memory (v15), MCP Bridge (v16), Self-Evolution (v17), explicit dual-engine + Handoff + Remote/A2A (v18), and security Guardrails (v19).

- **Language**: Python 3.11+ (async/await throughout)
- **LLM**: OpenAI-compatible API (DeepSeek default)
- **UI**: Rich console, event-driven
- **Version**: v19 Guardrails (tool / input-injection / output-redaction)

## Architecture

```
User Task → Orchestrator → [classify_task] → simple / complex / emergent
  simple:    Planner.create_plan()     → Executor (ReActEngine)      → Reflector
  complex:   Planner.create_dag()      → DAGExecutor (super-steps)   → Reflector
  emergent:  ENABLE_GOAL_DRIVEN_PLANNER=false → EmergentPlanner (TODO + per-TODO ReAct)
             ENABLE_GOAL_DRIVEN_PLANNER=true  → GoalDrivenPlanner (goal anchoring + dynamic TODO)
All paths → Token usage → Long-term memory → TracingBridge (OTel)
All paths → Checkpoint persistence (v14.5, per-step/per-TODO/per-super-step save, resume from checkpoint)
All paths → Agentic Memory (v15, structured records, bilingual retrieval, Memory as Tool, opt-in via AGENTIC_MEMORY_ENABLED)
All paths → MCP Bridge (v16, generic MCP client via stdio/HTTP, schema adapter, MCP server exposing tools/memory, opt-in via MCP_BRIDGE_ENABLED)
All paths → Self-Evolution (v17, post-task experience/failure-lesson learning into Agentic Memory, auto-inject avoidance hints into similar future tasks, opt-in via SELF_EVOLUTION_ENABLED, requires AGENTIC_MEMORY_ENABLED)
Explicit dual-engine (v18.1): OrchestratorAgent.run(task) = autonomous agentic loop; OrchestratorAgent.run_workflow(spec) = deterministic tool-DAG (WorkflowEngine, no per-step LLM), triggered by `--workflow <spec.json>`
ReAct loops → subagent tool (depth=1, isolated, summary-only)
ReAct loops → handoff tool (v18.2, context-passing + control transfer to a specialist; on success ReActEngine ends the loop with the specialist's full output; opt-in via HANDOFF_ENABLED)
ReAct loops → remote_subagent tool (v18.3, delegate to a remote MCP-hosted agent over A2A; result returned to parent loop; opt-in via REMOTE_SUBAGENT_ENABLED). MCP server can expose this project as a remote agent (get_agent_card + a2a_run_task) via MCP_SERVER_EXPOSE_AGENT (v18.4 A2A: AgentCard + task request/response envelope, local-trusted)
All tool calls → Guardrails (v19, opt-in via GUARDRAILS_ENABLED): 19.1 ToolGuardrail (dangerous params / path traversal / write-op gating) + 19.2 InputGuardrail (neutralize indirect injection in untrusted output/memory) hook in execute_tool_calls; 19.3 OutputGuardrail (redact PII/credentials) hooks at orchestrator final answer
ReAct loops → ask_user tool (HITL, asyncio.Future bridge, interactive-only)
```

## Entry Point

- **`main.py`** parses `sys.argv` directly. `"--verbose"` / `"-v"` for debug. Positional args joined as task.
- **Interactive** (`run_interactive()`): one `OrchestratorAgent(interactive=True)` for session, memory accumulates.
- **Single-task** (`run_single()`): `OrchestratorAgent(interactive=False)` — HITL double-gated off.
- **Base tools** in `main.py`: `WebSearchTool`, `FetchUrlTool`, `UserLocationTool`, `CodeExecutorTool`, `FileOpsTool`, `ShellTool`. `SubAgentTool` injected when `SUBAGENT_ENABLED=true`. `AskUserTool` when `HITL_ENABLED=true AND interactive=True`. MCP bridge tools discovered via `_discover_mcp_bridge_tools()` when `MCP_BRIDGE_ENABLED=true`.
- **`on_event` callback**: 30+ event types with Rich rendering. `OtelDetachFilter` suppresses OTel detach errors.

## Key Enums (schema.py)

- **NodeStatus**: `PENDING → READY → RUNNING → COMPLETED | FAILED | SKIPPED | ROLLED_BACK`
- **NodeType**: `GOAL / SUBGOAL / ACTION`
- **EdgeType**: `DEPENDENCY / CONDITIONAL / ROLLBACK`
- **StepStatus**: `PENDING / RUNNING / COMPLETED / FAILED / SKIPPED`
- **TodoStatus**: `PENDING / IN_PROGRESS / COMPLETED / BLOCKED`
- **SubAgentStatus**: `PENDING / RUNNING / COMPLETED / FAILED / TIMED_OUT`
- **GoalAction**: `EXECUTE_TODO / REPLAN / COMPLETE`
- **TaskRunState**: `RUNNING / PAUSED_WAITING_USER / COMPLETED / FAILED`
- **MemoryKind**: `FACTUAL / EXPERIENTIAL / WORKING / PROCEDURAL` (v15, in `memory/models.py`)
- **MemoryStatus**: `ACTIVE / REVOKED` (v15, in `memory/models.py`)

## Module Roles

- **`agents/`** — OrchestratorAgent (compose+route, no BaseAgent), PlannerAgent (classifier+plan/DAG), ExecutorAgent (delegates to ReActEngine), ReflectorAgent (exit criteria), EmergentPlannerAgent (v5 TODO), GoalDrivenPlannerAgent (v8 goal), SubAgent (v9 depth=1), prompt_utils (system prompt composition + context injection + convergence hints)
- **`dag/`** — TaskDAG, DAGExecutor (super-step parallel), NodeStateMachine
- **`react/`** — ReActEngine (canonical loop, concurrent tool_calls), tool_call_helpers (`attribute_caller`/`classify_result`/`truncate_for_llm` — shared by all 3 ReAct loops)
- **`llm/`** — LLMClient (async wrapper, centralized token tracking, `caller_tag` per-call attribution)
- **`tools/`** — BaseTool ABC, WebSearchTool (Bailian MCP + DDGS fallback), FetchUrlTool, UserLocationTool, CodeExecutorTool, FileOpsTool, ShellTool, SubAgentTool, AskUserTool, ToolRouter, BailianMCPClient
- **`tools/mcp/`** — v16 MCP Bridge: MCPClientManager (multi-server discovery+execution), MCPBridgeTool (BaseTool wrapper), MCPServerWrapper (expose tools/memory as MCP server), schema_adapter (MCP→OpenAI conversion), transport (stdio/HTTP factory)
- **`tracing/`** — TracingBridge (event→span), FastAPI web viewer, multi-backend exporters
- **`checkpoint/`** — TaskCheckpoint + path state models (SimplePathState, DAGPathState, EmergentPathState, GoalDrivenPathState), TaskStateStore (atomic JSON file persistence, version check, pruning)
- **`memory/`** — ShortTermMemory (sliding-window), LongTermMemory (JSON-file), AgenticMemoryStore + AgenticMemoryService (v15 structured memory), models (MemoryKind, MemoryStatus, AgenticMemoryRecord)
- **`workflow/`** — v18.1 deterministic engine: WorkflowEngine (topo-ordered tool-DAG execution, `${step_id}` param templating, fail-fast, no per-step LLM), models (WorkflowSpec/WorkflowStep/WorkflowResult), loader (`load_workflow_spec`). Invoked via `OrchestratorAgent.run_workflow()` / `main.py --workflow`
- **`guardrails/`** — v19 security guardrails: `patterns` (injection/PII/dangerous-cmd compiled constants), `models` (GuardrailAction/Decision/Layer), `tool_guardrail` (19.1), `input_guardrail` (19.2 neutralize), `output_guardrail` (19.3 redact), `engine` (GuardrailEngine + module-level `current_guardrail()`/`set_event_sink()`/`set_confirm_callback()`). Hooks in `react/engine_helpers.execute_tool_calls` + orchestrator final answer. Threat matrix: `sxw_aicoding/security/owasp-asi-threat-matrix.md`
- **`a2a/`** — v18.4 A2A prototype (local-trusted over MCP): models (AgentCard, AgentSkill, A2ATaskRequest/Response), A2AClient (fetch_agent_card + run_task, thin layer over MCPClientManager). `tools/remote_subagent_tool.py` RemoteSubAgentTool delegates to a remote MCP agent; `tools/mcp/server.py` exposes `get_agent_card`+`a2a_run_task` (runs a depth=1 SubAgent) when `expose_agent=True`
- **`agents/specialist.py`** — v18.2 Handoff target: SpecialistAgent (context-passing, role-specific prompt, returns full output; depth=1, excludes handoff/subagent) + SPECIALIST_REGISTRY (researcher/coder/writer). `tools/handoff_tool.py` HandoffTool sets `is_handoff=True`
- **`evolution/`** — v17 Self-Evolution: ExperienceLearner (post-task distill success→procedural / failure→experiential lesson + v17.4 HITL→FACTUAL preferences, with dedup; build avoidance/preference hints), models (TaskOutcome + tag/source conventions), ClassifierCalibrator + `calibrate` CLI (v17.3 offline grid-search of complexity thresholds → suggestion JSON, never auto-applies). Persists to AgenticMemory; opt-in via SELF_EVOLUTION_ENABLED
- **`context/`** — ContextManager (token estimation + LLM-based compression with safe split)
- **`knowledge/`** — KnowledgeRetriever (TF-IDF + cosine)
- **`evaluation/`** — benchmark tasks + 4-dimension weighted scoring (Planning 30% / Execution 40% / Efficiency 20% / Reflection 10%). v18.5: delegation-aware execution score (SubAgent+Handoff+Remote), `multi_agent` suite + `handoff_on` variant, handoff/remote metrics in probe/metrics/compare_variants, `expected_handoff_calls` ground truth, runner activates HANDOFF by `handoff` tag (+ HANDOFF_ALLOW_ASK_USER when also `hitl`). v19.4 red-team: `is_attack` ground truth, `red_team` suite + `guardrails_on` variant, security metrics (attack_success_rate / blocked_benign_rate / guardrail_* counts) — guardrails on/off via variant A/B (not tag-activated)

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
python main.py --list-tasks             # List checkpointed tasks
python main.py --resume <task_id>       # Resume a checkpointed task
python -m tracing                       # Web viewer localhost:8000

python -m pytest tests/ -v -o asyncio_mode=auto
python -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py

python -m evaluation.eval_cli --dry-run
python -m evaluation.eval_cli --difficulty easy --modes simple
python -m evaluation.eval_cli --output results.json
python -m evaluation.eval_cli --suite multi_agent   # v18.5 multi-agent collaboration (handoff_on vs baseline)
python -m evaluation.eval_cli --suite red_team       # v19.4 security red-team (set GUARDRAILS_ENABLED=true or use guardrails_on variant)

python3 -m py_compile schema.py llm/client.py agents/orchestrator.py react/engine.py

python -m evolution.calibrate                    # v17.3 offline classifier-threshold calibration (suggestion only)
python -m evolution.calibrate --show-per-task    # include per-task rule-score breakdown

python main.py --workflow path/to/spec.json      # v18.1 run a deterministic tool workflow (no per-step LLM)
HANDOFF_ENABLED=true python main.py "task"       # v18.2 enable specialist handoff (control transfer)
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
| `TASK_RESUME_ENABLED` | `true` | v14.5 checkpoint master switch |
| `CHECKPOINT_DIR` | `~/.manus_demo/checkpoints` | Checkpoint storage directory |
| `CHECKPOINT_MAX_PER_TASK` | `5` | Max checkpoint files per task |
| `CHECKPOINT_RETENTION_DAYS` | `7` | Auto-delete checkpoints older than N days |
| `AGENTIC_MEMORY_ENABLED` | `false` | v15 Agentic Memory master switch |
| `MEMORY_TOOLS_ENABLED` | `false` | Register memory_search/store/consolidate/revoke tools |
| `MEMORY_MIN_CONFIDENCE` | `0.35` | Min confidence for memory retrieval |
| `MEMORY_SEARCH_TOP_K` | `3` | Max memory results per search |
| `MEMORY_LLM_CONSOLIDATION_ENABLED` | `false` | Enable LLM-assisted consolidation |
| `SELF_EVOLUTION_ENABLED` | `false` | v17 Self-Evolution master switch (requires `AGENTIC_MEMORY_ENABLED`) |
| `SELF_EVOLUTION_LLM_EXTRACTION` | `false` | Use LLM to distill experience/failure (off → deterministic) |
| `SELF_EVOLUTION_MAX_HINTS` | `3` | Max past-failure avoidance hints injected per task |
| `SELF_EVOLUTION_CONFIDENCE_CAP` | `0.6` | Confidence cap for auto-learned memories (anti-poisoning) |
| `SELF_EVOLUTION_PREFERENCE_ENABLED` | `true` | v17.4 learn user preferences from HITL (effective only with self-evolution + HITL) |
| `CLASSIFIER_SIMPLE_THRESHOLD` | `-1` | v17.3 rule score ≤ this → simple (externalized for calibration) |
| `CLASSIFIER_COMPLEX_THRESHOLD` | `2` | v17.3 rule score ≥ this → complex (externalized for calibration) |
| `WORKFLOW_ENABLED` | `true` | v18.1 deterministic Workflow engine (only triggered by `--workflow`/`run_workflow`) |
| `HANDOFF_ENABLED` | `false` | v18.2 Handoff master switch |
| `HANDOFF_ALLOW_ASK_USER` | `false` | Let handoff specialists call ask_user (must be explicit) |
| `HANDOFF_MAX_CALLS_PER_TASK` | `2` | Per-task handoff cap |
| `HANDOFF_TIMEOUT` | `=NODE_EXECUTION_TIMEOUT` | Specialist execution timeout |
| `HANDOFF_MAX_ITERATIONS` | `=MAX_REACT_ITERATIONS` | Specialist ReAct iteration cap |
| `MCP_SERVER_EXPOSE_AGENT` | `false` | v18.3/18.4 expose this project as a remote agent (get_agent_card + a2a_run_task) |
| `REMOTE_SUBAGENT_ENABLED` | `false` | v18.3 client `remote_subagent` tool master switch |
| `REMOTE_AGENT_SERVER_JSON` | `""` | Remote agent server MCPServerConfig inline JSON |
| `REMOTE_SUBAGENT_MAX_CALLS_PER_TASK` | `2` | Per-task remote-delegation cap |
| `REMOTE_SUBAGENT_TIMEOUT` | `=NODE_EXECUTION_TIMEOUT` | Remote task timeout |
| `REMOTE_AGENT_FETCH_CARD` | `true` | Fetch AgentCard before delegating |
| `GUARDRAILS_ENABLED` | `false` | v19 guardrails master switch (off → `current_guardrail()` is None, zero overhead) |
| `GUARDRAIL_TOOL_ENABLED` / `_INPUT_ENABLED` / `_OUTPUT_ENABLED` | `true` | Per-layer toggles (effective only with master on) |
| `GUARDRAIL_TOOL_MODE` | `block` | `block` / `observe` |
| `GUARDRAIL_INPUT_MODE` | `neutralize` | `neutralize` / `annotate` / `observe` |
| `GUARDRAIL_OUTPUT_MODE` | `redact` | `redact` / `observe` |
| `GUARDRAIL_WRITE_CONFIRM` | `block` | `block` / `confirm` (ask_user in interactive) / `allow` |
| `MCP_BRIDGE_ENABLED` | `false` | v16 MCP Bridge client master switch |
| `MCP_BRIDGE_CONFIG_PATH` | `""` | JSON config file for MCP servers |
| `MCP_BRIDGE_SERVERS_JSON` | `""` | Inline JSON server config |
| `MCP_BRIDGE_TOOL_PREFIX` | `mcp` | Prefix for discovered MCP tools |
| `MCP_BRIDGE_SCHEMA_MODE` | `loose` | `loose` / `strict` schema conversion |
| `MCP_SERVER_ENABLED` | `false` | v16 MCP Server master switch |
| `MCP_SERVER_TRANSPORT` | `streamable_http` | `streamable_http` / `stdio` |

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
12. **Checkpoint resume boundary**: resumes to next execution boundary (step/TODO/super-step), does NOT restore LLM call mid-stream or ShortTermMemory. Each ReAct call starts fresh.
13. **Checkpoint atomic write**: `TaskStateStore.save()` writes `.tmp` then `os.rename` — never partial files on crash. Version check on load rejects future/old formats.
14. **Emergent/Goal-Driven loop extraction**: `_run_emergent_loop()` / `_run_goal_driven_loop()` shared between `execute()` and `resume_execute()` — no logic duplication.
15. **MCP Bridge lazy import**: `tools/mcp/` uses lazy import for `mcp` SDK (`from mcp.client.session import ClientSession` inside methods, not module top) — avoids import failures when `mcp` not installed or during test collection. `BailianMCPClient` in `tools/mcp_client.py` remains unchanged for backward compat.
16. **MCPBridgeTool eager schema conversion**: schema is converted at `__init__` time, not at `execute()` time — avoids repeated conversion overhead. `traced_execute()` is inherited from `BaseTool` with zero overrides.
17. **v17 outcome snapshot**: `OrchestratorAgent._record_outcome()` writes `_last_success/_last_reflection/_last_trajectory` at every execution-path return site (simple/DAG/emergent/goal-driven + their resume paths). `_learn_from_task()` reads this snapshot AFTER `_store_memory()`. The snapshot is reset at `run()` start. Note `_store_memory()` now passes `success=self._last_success` (was hardcoded `True`). Self-evolution is hard-gated on `AGENTIC_MEMORY_ENABLED` — `_experience_learner` stays `None` otherwise. Learning failures are swallowed (debug-logged) and never break the task.
18. **v17.3 threshold externalization**: the rule classifier's decision thresholds live in `config.CLASSIFIER_SIMPLE_THRESHOLD/_COMPLEX_THRESHOLD` (defaults -1/2 = historical hardcoded values). Scoring weights stay hardcoded. `PlannerAgent._rule_score`/`_is_emergent_by_rule`/`classify_by_rule` are classmethods so `ClassifierCalibrator` can grid-search offline without an LLMClient. Calibration is suggestion-only (`python -m evolution.calibrate` writes a `.suggested.json` and prints env-vars to apply manually) — it NEVER edits config or source (roadmap: 禁止静默自改).
19. **v17.4 HITL preference capture**: `_handle_user_prompt()` registers a `response_future.add_done_callback` that records the (question, answer) pair into `_hitl_pairs` once the UI resolves it — zero changes to `ask_user.py`/`main.py`. Cancelled/timed-out futures and the `(user cancelled)` sentinel are skipped. Preferences are stored as FACTUAL memories (tag `user_preference`) and injected by `build_preference_hints()` via `list_records` (tag-listed, NOT keyword-gated, since preferences are usually global like default city).
20. **v18.2 Handoff control transfer**: `BaseTool.is_handoff` (default False; True on HandoffTool). `ReActEngine.__init__` computes `_handoff_tool_names`; the transfer check is `ReActEngine._check_handoff_transfer()` — a SHARED method called by BOTH `ReActEngine.execute` AND `ReasoningEngine.execute` after `execute_tool_calls` (review F1.1 fix: ReasoningEngine previously bypassed it). If a handoff tool's `_last_ok` is True the loop ENDS returning `_last_output` (full, untruncated — read from the tool instance, NOT the truncated tool message). Failed handoff leaves `_last_ok=False` → loop continues. Empty `_handoff_tool_names` = zero behavior change. Handoff/remote_subagent are blocked from SubAgent whitelists AND from specialists (depth=1).
21. **v18.1 Workflow vs agent**: `WorkflowEngine.execute(spec)` is deterministic — topological order, `${step_id}` string templating of prior outputs, `tool.traced_execute()` per step, fail-fast on `Error:`-prefixed results or raises, NO LLM. `OrchestratorAgent.run_workflow()` deliberately bypasses classify/reflection/self-evolution (the explicit "workflow" half of the dual engine). Note: `main.py` was missing a top-level `import config` (pre-existing latent bug surfaced by the `--workflow` path) — now fixed.
22. **v18.3 Remote SubAgent anti-recursion**: the server-side agent (`MCPServerWrapper._register_agent_endpoints`, gated by `expose_agent` + `llm_client`) runs an isolated depth=1 `SubAgent` over a tool set filtered by `_REMOTE_BLOCKED = {remote_subagent, handoff, subagent, ask_user}` — so a remote agent can't recurse into further remote/handoff/subagent delegation. The client `RemoteSubAgentTool` returns the result to the parent loop (NOT control transfer; it is not `is_handoff`). Reuses `MCPClientManager` for transport (no new transport code).
23. **v18.4 A2A envelope**: `a2a/models.py` defines AgentCard/AgentSkill/A2ATaskRequest/A2ATaskResponse (local-trusted, `auth="local"`, protocol `a2a-prototype/0.1`). `A2AClient` (thin layer over MCPClientManager) does `fetch_agent_card` then `run_task`; prefixed tool names follow `make_prefixed_name(server, tool)` (e.g. `mcp_remote_agent_a2a_run_task`). Server `a2a_run_task`/`get_agent_card` are registered with TYPED signatures (not `**kwargs`) so FastMCP generates proper input schemas.
24. **v19 guardrail chokepoint**: `react/engine_helpers.execute_tool_calls` resolves `current_guardrail()` ONCE (None when `GUARDRAILS_ENABLED` off → zero overhead; reads live config so eval variants are honored). Per tool call: `await check_tool_input` before exec (BLOCK → `Error: [GUARDRAIL BLOCKED] ...`, tool NOT run; CONFIRM resolved inside engine per `GUARDRAIL_WRITE_CONFIRM`), `scan_tool_output` after success (NEUTRALIZE injection in untrusted output). Output redaction is at orchestrator `run()`/`run_workflow()`/`resume()` via `_apply_output_guardrail` before store/emit/return (review F2.1: resume() parity added). Retrieved memory is injection-scanned via `_apply_memory_guardrail` in `_gather_context` (review F5.1: `scan_memory` wiring added). Note: deterministic `workflow/` tool steps call `tool.traced_execute` directly and are NOT gated by tool-level guardrails (author-controlled engine; output still redacted). Events flow through a module-level sink (`set_event_sink(self._emit)` wired in run start, reset in finally) — same runtime-override convention as `set_hitl_runtime_enabled`.
25. **v19 untrusted boundary + write confirm**: InputGuardrail wraps untrusted tool output (`web_search`/`fetch_url`/`mcp_*`/`remote_subagent`) in `[UNTRUSTED TOOL OUTPUT …]` boundary and strips injected directive lines (neutralize mode). Write-op confirm (`file_ops write`) bridges to HITL `ask_user` via `OrchestratorAgent._guardrail_confirm` only when interactive + `GUARDRAIL_WRITE_CONFIRM=confirm`; non-interactive/no-callback degrades to fail-safe BLOCK. v19.4 red-team eval suite is designed but NOT yet implemented.
