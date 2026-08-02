# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. `AGENTS.md` in the repo root is a sibling guidance file whose key engineering-posture and conventions are folded into this file below; keep the two consistent when either is edited.

## What this is

A local, non-production Python project for studying agent development and comparing the behavior and execution traces of different reasoning engines. The core design is a **unified runtime with three explicit engines across two orchestration paradigms**. Most optional capabilities (subagent, memory, skills, guardrails, MCP, AgentBay, self-evolution) are off by default and attach as tools or lifecycle hooks — they never select an engine.

**Engineering posture** — favor clear experiments, observability, repeatability, and architectural learning over production compatibility or conservative change scope. Substantial refactors are welcome when they make engine comparisons or agent behavior easier to understand and validate; backward compatibility and production hardening are not default constraints unless the user explicitly adds them. This posture does **not** authorize unrequested real LLM, network, or formal evaluation runs, and it does **not** relax secret handling or protection of unrelated user data — run those jobs only when explicitly requested, preserve unrelated workspace changes, and report the validation boundary accurately.

## Commands

Use the repository virtual environment (`.venv/`). There is **no build step and no unit-test suite** — do not invent `pytest` invocations. Do not run real LLM, network, or formal evaluation jobs unless explicitly requested.

```bash
.venv/bin/pip install -r requirements.txt

# CLI host (main.py is a thin shim into cli.py)
.venv/bin/python main.py --help
.venv/bin/python main.py chat                      # interactive session
.venv/bin/python main.py run "整理当前目录结构" --engine agent_loop --effort high
.venv/bin/python main.py mcp-server                # expose local tools over MCP
.venv/bin/python main.py tasks                     # list checkpoint records
.venv/bin/python main.py resume <task_id>          # resume a semantic checkpoint

# Local services (each is a package with __main__.py)
.venv/bin/python -m webui                          # debug console on :8700
.venv/bin/python -m tracing --help
.venv/bin/python -m evaluation run --dry-run      # eval matrix dry run
.venv/bin/python -m evaluation upload notes.md    # store a document
.venv/bin/python -m evaluation generate <doc-id>  # generate tasks from a stored doc
.venv/bin/python -m evaluation report <run-id>    # render a stored run report
.venv/bin/python -m evaluation analyze <run-id>   # aggregate/analyze completed runs
.venv/bin/python -m evaluation list runs          # list stored records
.venv/bin/python -m evaluation serve               # eval UI on :8720
```

CLI exposes only `--engine` and `--effort` as single-run overrides; everything else lives in `settings.toml`.

### Verification before handoff

There is no test suite, so "done" is verified structurally:

```bash
.venv/bin/python -m compileall .                   # byte-compile everything
.venv/bin/python -c "import <module>"              # import smoke check
.venv/bin/python main.py --help                    # CLI parses
.venv/bin/python -m webui --help
.venv/bin/python -m evaluation run --dry-run
git diff --check                                  # whitespace/conflict markers
```

State plainly that these checks do not prove real agent quality — only that the code loads and parses.

## Architecture

Three layers, plus host adapters. Read `docs/architecture.md` and `docs/engines.md` for the full picture.

1. **`core/`** — stable contracts. `core/models.py` defines `TaskRequest`, `EngineResult`, `EngineStats`, `Action`, `ActionResult`, and the `EngineKind`/`EngineStopReason`/`Effort` enums. `core/events.py` is the `EventBus`. `core/settings.py` is `AppSettings`/`RunSettings` plus the loader and validator. `core/redaction.py` is the recursive secret redactor. New orchestration code must depend on these contracts and receive `AppSettings`, `EventBus`, tools, or runtime context explicitly — never reach into module-level singletons.

2. **`runtime/`** — composition root. `runtime/factory.py::build_runtime()` is the only place that wires tools, tracing, guardrails, checkpoints, and the optional capability adapters together; it returns an `AgentRuntime` (`runtime/app.py`). `AgentRuntime.run()` applies the explicit engine and effort, runs lifecycle hooks, persists checkpoints, and publishes events. It owns its LLM HTTP client and exposes idempotent `aclose()`.

3. **`engines/` + loop implementations** — `agent_loop` owns one persistent task transcript and lets the model drive native tool use until a final assistant answer. `sequential` and `dag` are the two adaptive Plan-and-Execute engines; only they use `PlannerAgent`, `ReflectorAgent`, and the internal `ToolCallingActionExecutor`. `ActionToolLoop` completes one planned action, while `AgentLoop` completes the whole task. Both share low-level tool dispatch but neither parses literal `Thought:` / `Action:` / `Observation:` text. There is no engine selector or public executor dimension; provider reasoning metadata is handled dynamically in the same loops. Implementation detail lives in `agent_loop/`, `tool_calling/`, `agents/` (planner/reflector), `dag/`, `context/`, and `llm/`.

Hosts — `cli.py`, `webui/`, `evaluation/` — call `AgentRuntime` and subscribe to the same `EventBus`. **Hosts must not branch on engine classes or parse implementation-specific log text.** Console output, tracing, WebUI streaming, and evaluation metrics all observe one structured event stream, so adding a consumer never requires touching engines.

### The EventBus is the integration seam

`EventBus.emit()` is synchronous and schedules async subscribers as fire-and-forget tasks tracked until `drain()`. `emit_async()` is the awaited delivery boundary. Each subscriber receives its own recursive deep copy of mutable containers, while opaque runtime objects (e.g. HITL response futures) keep identity. When you add an event consumer, subscribe to the bus; do not add a parallel notification path.

### Optional capabilities attach, they don't route

Everything in `runtime/factory.py::_register_capability_tools()` — AgentBay, MCP bridge, agentic memory + memory tools, skills, HITL, and the local subagent — plus guardrails, checkpoints, and self-evolution, may add tools, context, or lifecycle hooks. They do not choose the orchestration engine. Feature switches live in `[capabilities]` in `settings.toml` and most default to `false`.

## Configuration

Fixed precedence (do not work around it): dataclass defaults in `core/settings.py` → `settings.toml` → whitelisted secrets in `.env`/environment → CLI overrides for one run.

- `[runtime]` stores engine/effort defaults, `[engines]` stores orchestration tuning, and `[execution]` stores action-loop and context limits.
- **Unknown TOML sections/fields fail at startup.** Extra non-secret `.env` keys also fail with a migration hint. Ordinary process environment variables are intentionally ignored.
- `.env` accepts only `LLM_API_KEY`, `DASHSCOPE_API_KEY`, and `AGENTBAY_API_KEY` (see `.env.example`). Never put credentials in ordinary TOML settings.
- `config.py` is a **read-only compatibility facade** for retained peripheral modules — it mirrors `AppSettings` fields as module constants. It is not a second config source and must not receive new fields. New code uses `AppSettings` directly.

### Local execution is capability-gated

`[tools]` shell/python modes are ordinary TOML settings, not a security boundary:

- `shell_mode = "restricted"` (default): `shlex`-parses one command, executes its argv directly, rejects shell operators/expansion/globs and sandbox escapes, and allows only the documented read-oriented allowlist in `tools/shell_safety.py`. It does **not** expand globs.
- `shell_mode = "trusted"`: full bash with the local user's permissions. Must be explicitly enabled.
- This checkout sets `python_mode = "trusted"` and `shell_mode = "trusted"` for local experiments. Trusted execution is not a sandbox — subprocess cwd/timeouts do not limit filesystem or network authority.

## Checkpoints

Checkpoint schema v2 stores only the **semantic** tuple: engine, effort, task, and latest outcome (`checkpoint/`). Resume restarts the whole task; it does not restore an AgentLoop transcript or tool state. Version 1 is intentionally incompatible. `CancelledError` publishes `task_cancelled` and persists a `cancelled` checkpoint state before re-raising — when fixing cancellation paths, preserve this contract.

## Lifecycle ownership

Runtime-owning hosts (CLI commands, WebUI sessions, each evaluation matrix unit) call `await runtime.aclose()` on the runtimes they create. The **process host alone** owns the shared Tracing provider and flushes it during overall shutdown via `tracing.shutdown_tracing()` (see the `finally` in `cli.main`). Do not close Tracing from inside a runtime.

## Evaluation

`evaluation/` supports static cases and document-derived task generation, followed by an isolated matrix, report, and aggregation. Each matrix cell clones `AppSettings`, applies its capability set, creates a fresh runtime, and uses isolated state, sandbox, checkpoint, and user-skill directories — it never mutates module-level config, and it closes its runtime before deleting the temporary directory. Results report **separate dimensions** (engine success, verifier status when a case defines verifiers, reasoning tokens, latency, tool calls, model calls, subagent calls, and repeated-run stability), not one composite score. Comparisons explicitly enumerate `agent_loop`, `sequential`, and `dag`; there is no selector-accuracy or executor dimension. A case without verifiers measures engine-reported completion only, not semantic answer quality.

## Conventions

- Python 3.11+. `snake_case` for modules/functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Name engines by behavior (`AgentLoopEngine`), never by version. Type-hint public interfaces.
- DAG success tolerates actions skipped because a condition didn't select their branch; an execution failure, cascade, unfinished graph, or failed final reflection still fails. Result metadata keeps `failed_action_ids` separate from `condition_skipped_ids`.
- Commit subjects are concise and action-oriented; recent history uses Chinese summaries with `to #<work-item>` for a real tracker item (e.g. `修复subagent相关bug to #82161950`). Keep generated traces, local evaluation output (`~/.manus_demo/evaluation`), and secrets out of commits.
- PRs describe behavior and configuration changes, list exact validation commands, and include screenshots for WebUI changes.
- Treat `sxw_aicoding/`, `agentbay_research/`, generated traces, and `.agents/` as historical or generated material, not active source.
