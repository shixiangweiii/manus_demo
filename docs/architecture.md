# Architecture

The repository has three core layers:

1. `core/` defines stable task, action, result, event, and configuration contracts.
2. `runtime/` builds tools and optional capabilities, selects policies, runs lifecycle hooks, and owns checkpoints.
3. `engines/` decides which actions exist; `execution/` selects how one action is carried out; `tool_calling/` implements the reusable structured tool loop.

Hosts (`cli.py`, `webui/`, and `evaluation/`) call `AgentRuntime` and subscribe to the same `EventBus`. They do not inspect implementation-specific log text or branch on engine classes. Tracing, console output, WebUI streaming, and evaluation metrics therefore observe one structured event stream.

`EventBus.emit()` supports synchronous callers and tracks async subscribers until `drain()`. `emit_async()` provides an awaited delivery boundary. Each subscriber receives its own recursive copy of mutable event containers, while opaque runtime objects such as HITL futures retain identity.

`runtime/factory.py` is the composition root. It registers base tools, attaches Tracing when enabled, and optionally adds MCP, AgentBay, Subagent, Handoff, remote Agent, memory, skill, guardrail, and self-evolution adapters. These capabilities may add tools, context, or lifecycle work, but do not choose the orchestration engine.

Modules in `agents/`, `dag/`, `tool_calling/`, and `workflow/` contain implementation details behind the stable contracts. `ToolCallingLoop` implements the standard structured tool loop, while `ReasoningAwareToolCallingLoop` adds reasoning-model accounting and convergence limits. Neither parses literal `Thought:` / `Action:` / `Observation:` text. `config.py` is a temporary read-only compatibility facade for peripheral modules; new code should use `AppSettings` directly.

Configuration follows the same separation: `[runtime]` selects engine,
executor, and effort defaults; `[engines]` contains orchestration tuning; and
`[execution]` contains action-loop and context limits.

Checkpoints store only the semantic engine, executor, effort, task, and latest outcome. Older path-specific checkpoint JSON is intentionally ignored.

The runtime owns its LLM HTTP client and exposes idempotent `aclose()`. CLI commands, WebUI sessions, and each evaluation unit close the runtimes they create. The process host owns the shared Tracing provider and flushes it only during overall shutdown. Cancelled tasks publish `task_cancelled` and persist a `cancelled` checkpoint state before re-raising cancellation.
