# Architecture

The repository has three core layers:

1. `core/` defines task, action, result, statistics, event, and configuration contracts.
2. `runtime/` composes tools and optional capabilities, instantiates one explicitly requested engine, and owns lifecycle work.
3. `engines/` adapts `sequential`, `dag`, and `agent_loop` to the common result contract. `agent_loop/` owns the task-level autonomous loop; `tool_calling/` owns the bounded per-Action loop and shared tool dispatch.

There is no engine selector, executor dimension, or deterministic Workflow layer. A run names its engine and effort directly. Each engine owns its orchestration semantics while depending on shared contracts and tools.

Hosts (`cli.py`, `webui/`, and `evaluation/`) call `AgentRuntime` and subscribe to the same `EventBus`. They consume structured events rather than log text or concrete engine classes. Sequential plan/step events and DAG node events retain dedicated views; Agent Loop publishes `todo_updated` as a complete current snapshot.

`EventBus.emit()` supports synchronous callers and tracks async subscribers until `drain()`. `emit_async()` provides an awaited delivery boundary. Each subscriber receives its own recursive copy of mutable event containers, while opaque runtime objects such as HITL futures retain identity.

`runtime/factory.py` is the composition root. It registers base tools, attaches tracing, and optionally adds MCP, AgentBay, Subagent, memory, skill, guardrail, and self-evolution adapters. Capabilities may add tools, context, or lifecycle work but do not change the requested engine.

Native tool calls and matching `role="tool"` results form the action/observation protocol. Provider reasoning metadata is normalized dynamically by the same response path, with accounting and convergence limits but no second executor identity and no parsing of literal chain-of-thought labels.

Tracing follows the execution hierarchy: task → engine → AgentLoop turn → LLM/tool call. Plan-and-Execute adds planner, action, DAG-execution, and reflector spans; every ActionToolLoop model round is represented as action → action-loop turn → LLM/tool call, including failed and cancelled turns.

Checkpoints store the engine, effort, task, and latest semantic state. Runtime results expose `output`, `stop_reason`, and `stats`; the shared statistics contract records whole-call-tree physical LLM calls, AgentLoop task turns, context-compaction calls, prompt/completion/total tokens, tool calls, reasoning tokens, and SubAgent calls by combining loop-local observations with isolated child-loop aggregates and global usage records.

The runtime owns its LLM HTTP client and exposes idempotent `aclose()`. CLI commands, WebUI sessions, and each evaluation unit close the runtimes they create. The process host owns the shared tracing provider and flushes it only during overall shutdown.
