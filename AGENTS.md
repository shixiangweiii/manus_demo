# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the thin CLI entry point and `cli.py` contains command handling. Stable contracts and configuration live in `core/`; runtime composition lives in `runtime/`. Task orchestration implementations are under `engines/`, while per-action ReAct implementations are under `execution/` and `react/`. Base and optional tools are registered through `tools/registry.py`. User-facing adapters are `webui/`, `tracing/`, and the unified `evaluation/` package. Retained peripheral capabilities live in `a2a/`, `memory/`, `skills/`, `evolution/`, `guardrails/`, and `checkpoint/`. Treat `sxw_aicoding/`, `agentbay_research/`, generated traces, and local evaluation output as historical or generated material.

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

Target Python 3.11+, use four-space indentation, straightforward control flow, and type hints on public interfaces. Prefer `snake_case` for modules/functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Name engines by behavior (`TodoEngine`), never by version. New orchestration code must depend on `core` contracts and receive `AppSettings`, `EventBus`, tools, or runtime context explicitly. Keep compatibility access through `config.py` limited to retained peripheral modules.

## Configuration and Verification

Put normal settings and feature switches in `settings.toml`; keep only secrets in `.env`, using `.env.example` as the key list. CLI flags are single-run overrides. Before handoff, run the documented import, `--help`, `compileall`, static-reference, and `git diff --check` checks. State clearly that these checks do not prove real agent quality.

Local execution is capability-gated. Shell defaults to `restricted`, which permits one allowlisted argv command inside the sandbox; `trusted` uses full bash with the local user's permissions. Python execution is disabled unless `python_mode = "trusted"`. Runtime-owning hosts must call `await runtime.aclose()`, while the host process alone shuts down the shared Tracing provider.

## Commit & Pull Request Guidelines

Use concise action-oriented subjects; recent history uses Chinese summaries such as `修复subagent相关bug to #82161950`. Add `to #<work-item>` only for a real tracker item. PRs should describe behavior and configuration changes, list exact validation commands, and include screenshots for WebUI changes. Keep generated output and secrets out of commits.
