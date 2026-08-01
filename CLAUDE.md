# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, non-production Python project for learning and comparing agent orchestration patterns. The core design is a **unified runtime, swappable task engines, and swappable action executors**. Most optional capabilities (subagent, memory, skills, guardrails, MCP, AgentBay, self-evolution) are off by default and attach as tools or lifecycle hooks — they never participate in engine routing.

## Commands

Use the repository virtual environment (`.venv/`). There is **no build step and no unit-test suite** — do not invent `pytest` invocations. Do not run real LLM, network, or formal evaluation jobs unless explicitly requested.

```bash
.venv/bin/pip install -r requirements.txt

# CLI host (main.py is a thin shim into cli.py)
.venv/bin/python main.py --help
.venv/bin/python main.py chat                      # interactive session
.venv/bin/python main.py run "整理当前目录结构" --engine sequential --executor react --effort low
.venv/bin/python main.py workflow workflow_spec.json   # deterministic tool graph
.venv/bin/python main.py mcp-server                # expose local tools over MCP
.venv/bin/python main.py tasks                     # list checkpoint records
.venv/bin/python main.py resume <task_id>          # resume a semantic checkpoint

# Local services (each is a package with __main__.py)
.venv/bin/python -m webui                          # debug console on :8700
.venv/bin/python -m tracing --help
.venv/bin/python -m evaluation run --dry-run      # eval matrix dry run
.venv/bin/python -m evaluation serve               # eval UI on :8720
```

CLI exposes only `--engine`, `--executor`, and `--effort` as single-run overrides; everything else lives in `settings.toml`.

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

1. **`core/`** — stable contracts. `core/models.py` defines `TaskRequest`, `EngineResult`, `Action`, `ActionResult`, and the `EngineKind`/`ExecutorKind`/`Effort` enums. `core/events.py` is the `EventBus`. `core/settings.py` is `AppSettings`/`RunSettings` plus the loader and validator. `core/redaction.py` is the recursive secret redactor. New orchestration code must depend on these contracts and receive `AppSettings`, `EventBus`, tools, or runtime context explicitly — never reach into module-level singletons.

2. **`runtime/`** — composition root. `runtime/factory.py::build_runtime()` is the only place that wires tools, tracing, guardrails, checkpoints, and the optional capability adapters together; it returns an `AgentRuntime` (`runtime/app.py`). `AgentRuntime.run()` selects the engine/executor/effort, runs lifecycle hooks, persists checkpoints, and publishes events. It owns its LLM HTTP client and exposes idempotent `aclose()`.

3. **`engines/` + `execution/`** — engines decide *which actions exist*; executors decide *how one action runs*. Every engine implements `TaskEngine.run(TaskRequest) -> EngineResult` (`engines/base.py`) and delegates individual actions to an `ActionExecutor` (`execution/base.py`): `sequential`, `dag`, `todo`, `goal`, `workflow` engines; `react` and `thinking` executors. Auto-routing (`engines/selector.py`) checks explicit settings, then goal/exploratory/dependency markers, then falls back to Sequential; Workflow never auto-routes. Executor auto-selection reads only `llm.supports_reasoning` — it never inspects model names.

Hosts — `cli.py`, `webui/`, `evaluation/` — call `AgentRuntime` and subscribe to the same `EventBus`. **Hosts must not branch on engine classes or parse implementation-specific log text.** Console output, tracing, WebUI streaming, and evaluation metrics all observe one structured event stream, so adding a consumer never requires touching engines.

### The EventBus is the integration seam

`EventBus.emit()` is synchronous and schedules async subscribers as fire-and-forget tasks tracked until `drain()`. `emit_async()` is the awaited delivery boundary. Each subscriber receives its own recursive deep copy of mutable containers, while opaque runtime objects (e.g. HITL response futures) keep identity. When you add an event consumer, subscribe to the bus; do not add a parallel notification path.

### Optional capabilities attach, they don't route

Everything in `runtime/factory.py::_register_capability_tools()` — AgentBay, MCP bridge, agentic memory + memory tools, skills, HITL, subagent, handoff, remote subagent — plus guardrails, checkpoints, and self-evolution, may add tools, context, or lifecycle hooks. They do not choose the orchestration engine. Feature switches live in `[capabilities]` in `settings.toml` and most default to `false`.

## Configuration

Fixed precedence (do not work around it): dataclass defaults in `core/settings.py` → `settings.toml` → whitelisted secrets in `.env`/environment → CLI overrides for one run.

- **Unknown TOML sections/fields fail at startup.** Extra non-secret `.env` keys also fail with a migration hint. Ordinary process environment variables are intentionally ignored.
- `.env` accepts only `LLM_API_KEY`, `DASHSCOPE_API_KEY`, `AGENTBAY_API_KEY` (see `.env.example`). Never put credentials in `remote_agent_server_json` or other ordinary settings.
- `config.py` is a **read-only compatibility facade** for retained peripheral modules — it mirrors `AppSettings` fields as module constants. It is not a second config source and must not receive new fields. New code uses `AppSettings` directly.

### Local execution is capability-gated

`[tools]` shell/python modes are ordinary TOML settings, not a security boundary:

- `shell_mode = "restricted"` (default): `shlex`-parses one command, executes its argv directly, rejects shell operators/expansion/globs and sandbox escapes, and allows only the documented read-oriented allowlist in `tools/shell_safety.py`. It does **not** expand globs.
- `shell_mode = "trusted"`: full bash with the local user's permissions. Must be explicitly enabled.
- `python_mode = "disabled"` (default); `"trusted"` enables the Python executor. Trusted Python is also not a sandbox — subprocess cwd/timeouts do not limit filesystem or network authority.

## Checkpoints

Checkpoints store only the **semantic** tuple: engine, executor, effort, task, and latest outcome (`checkpoint/`). Legacy path-specific checkpoint JSON is intentionally ignored, so resume refuses to load it. `CancelledError` publishes `task_cancelled` and persists a `cancelled` checkpoint state before re-raising — when fixing cancellation paths, preserve this contract.

## Lifecycle ownership

Runtime-owning hosts (CLI commands, WebUI sessions, each evaluation matrix unit) call `await runtime.aclose()` on the runtimes they create. The **process host alone** owns the shared Tracing provider and flushes it during overall shutdown via `tracing.shutdown_tracing()` (see the `finally` in `cli.main`). Do not close Tracing from inside a runtime.

## Evaluation

`evaluation/` is document-driven: upload a doc → generate a task set → run an isolated matrix → report → aggregate. Each matrix cell clones `AppSettings`, applies its capability set, creates a fresh runtime, and uses an isolated sandbox/checkpoint dir — it never mutates module-level config, and it closes its runtime before deleting the temp sandbox. Results report **separate dimensions** (success, verifier, tokens, latency, tool calls, iterations, replans, repeated-run stability, selector accuracy), not one composite score. `actual_engine`/`actual_executor`/`actual_effort` are nullable and populated only when the event stream proves a selection occurred — never from the requested experiment value.

## Conventions

- Python 3.11+. `snake_case` for modules/functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants. Name engines by behavior (`TodoEngine`), never by version. Type-hint public interfaces.
- Workflow parameter references use only `${steps.<step_id>}` against a declared dependency; `$${...}` produces the literal text. Other `${...}` strings are left untouched.
- DAG success tolerates actions skipped because a condition didn't select their branch; an execution failure, cascade, unfinished graph, or failed final reflection still fails. Result metadata keeps `failed_action_ids` separate from `condition_skipped_ids`.
- Commit subjects are concise and action-oriented; recent history uses Chinese summaries with `to #<work-item>` for a real tracker item (e.g. `修复subagent相关bug to #82161950`). Keep generated traces, local evaluation output (`~/.manus_demo/evaluation`), and secrets out of commits.
- Treat `sxw_aicoding/`, `agentbay_research/`, generated traces, and `.agents/` as historical or generated material, not active source.
