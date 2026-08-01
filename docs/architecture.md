# Architecture

The repository has three core layers:

1. `core/` defines stable task, action, result, event, and configuration contracts.
2. `runtime/` builds tools and optional capabilities, selects policies, runs lifecycle hooks, and owns checkpoints.
3. `engines/` decides which actions exist; `execution/` decides how one action is carried out.

Hosts (`cli.py`, `webui/`, and `evaluation/`) call `AgentRuntime` and subscribe to the same `EventBus`. They do not inspect implementation-specific log text or branch on engine classes. Tracing, console output, WebUI streaming, and evaluation metrics therefore observe one structured event stream.

`runtime/factory.py` is the composition root. It registers base tools, attaches Tracing when enabled, and optionally adds MCP, AgentBay, Subagent, Handoff, remote Agent, memory, skill, guardrail, and self-evolution adapters. These capabilities may add tools, context, or lifecycle work, but do not choose the orchestration engine.

Retained modules in `agents/`, `dag/`, `react/`, and `workflow/` contain implementation details adapted behind the new contracts. `config.py` is a temporary read-only compatibility facade for peripheral modules; new code should use `AppSettings` directly.

Checkpoints store only the semantic engine, executor, effort, task, and latest outcome. Older path-specific checkpoint JSON is intentionally ignored.
