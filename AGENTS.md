# Repository Guidelines

## Project Purpose & Engineering Posture

This is a local learning project for studying agent development and comparing the strengths, weaknesses, behavior, and execution traces of different reasoning engines. It is not deployed or intended for production use. When the user asks for implementation or improvement work, favor clear experiments, observability, repeatability, and architectural learning over production compatibility or conservative change scope. Substantial refactors are welcome when they make engine comparisons or agent behavior easier to understand and validate; backward compatibility and production hardening are not default constraints unless the user explicitly adds them.

The local-learning posture does not authorize unrequested real LLM, network, or formal evaluation runs, and it does not relax secret handling or protection of unrelated user data. Continue to run those jobs only when explicitly requested, preserve unrelated workspace changes, and report the validation boundary accurately.

## Project Structure & Module Organization

`main.py` is the thin CLI entry point and `cli.py` contains command handling. Stable contracts and configuration live in `core/`; runtime composition lives in `runtime/`. The only task engines are `sequential`, `dag`, and `agent_loop` under `engines/`. The task-level autonomous loop lives in `agent_loop/`; the bounded per-Action loop lives in `tool_calling/`. There is no automatic engine selector, executor dimension, or declarative Workflow layer. Base and optional tools are registered through `tools/registry.py`. User-facing adapters are `webui/`, `tracing/`, and the unified `evaluation/` package. Retained peripheral capabilities live in `a2a/`, `memory/`, `skills/`, `evolution/`, `guardrails/`, and `checkpoint/`. Treat `sxw_aicoding/`, `agentbay_research/`, generated traces, and local evaluation output as historical or generated material.

## Build and Development Commands

Use the repository virtual environment:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py --help
.venv/bin/python main.py chat
.venv/bin/python -m webui --help
.venv/bin/python -m evaluation run --dry-run
.venv/bin/python -m compileall .
```

There is no build step and this learning repository does not maintain a unit-test suite. Do not run real LLM, network, or formal evaluation jobs unless explicitly requested.

## Coding Style & Naming Conventions

Target Python 3.11+, use four-space indentation, straightforward control flow, and type hints on public interfaces. Prefer `snake_case` for modules/functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Name engines by behavior (`AgentLoopEngine`), never by version. New orchestration code must depend on `core` contracts and receive `AppSettings`, `EventBus`, tools, or runtime context explicitly. Keep compatibility access through `config.py` limited to retained peripheral modules.

## Configuration and Verification

Put normal settings and feature switches in `settings.toml`; keep only secrets in `.env`, using `.env.example` as the key list. CLI flags are single-run overrides. Before handoff, run the documented import, `--help`, `compileall`, static-reference, and `git diff --check` checks. State clearly that these checks do not prove real agent quality.

Local execution is capability-gated. Base defaults are Shell `restricted` and Python `disabled`, while this checkout explicitly sets both modes to `trusted` for local experiments. Trusted execution uses the local user's permissions and is not a security sandbox. Runtime-owning hosts must call `await runtime.aclose()`, while the host process alone shuts down the shared Tracing provider.

## Commit & Pull Request Guidelines

Use concise action-oriented subjects; recent history uses Chinese summaries such as `修复subagent相关bug to #82161950`. Add `to #<work-item>` only for a real tracker item. PRs should describe behavior and configuration changes, list exact validation commands, and include screenshots for WebUI changes. Keep generated output and secrets out of commits.
