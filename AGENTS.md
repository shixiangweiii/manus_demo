# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the CLI entry point; `config.py` loads environment-driven settings and `schema.py` defines shared Pydantic models. Agent orchestration lives in `agents/`, ReAct execution in `react/`, and graph/workflow engines in `dag/` and `workflow/`. Integrations and supporting services are grouped under `tools/`, `memory/`, `knowledge/`, `checkpoint/`, `guardrails/`, and `a2a/`. User-facing services live in `webui/`, `tracing/`, `evaluation/`, and `evalplatform/`. Tests mirror behavior in `tests/test_*.py`. Treat `backups/`, generated `traces/`, and research material under `sxw_aicoding/` as reference or artifacts, not primary runtime code.

## Build, Test, and Development Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py                         # interactive CLI
python main.py "summarize this topic"  # one task
python -m webui                        # local UI on port 8700
python -m evaluation.eval_cli --dry-run
python -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

Use `python -m pytest tests/test_subagent.py -o asyncio_mode=auto` for a focused run. The project has no separate build step.

## Coding Style & Naming Conventions

Target Python 3.11+, four-space indentation, and PEP 8-oriented formatting. No formatter or linter is pinned, so match nearby code. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes and Pydantic models; and `UPPER_SNAKE_CASE` for constants and environment keys. Keep LLM/tool I/O asynchronous, add type hints to public interfaces, and preserve bilingual docstrings where the surrounding module uses them. New tools should inherit `BaseTool`; shared structured data should use Pydantic models.

## Testing Guidelines

Pytest and `pytest-asyncio` are the test stack. Name files `test_<feature>.py` and tests `test_<behavior>`. Add regression coverage for every behavior change and mock network or LLM calls by default. Mark tests needing external services with `@pytest.mark.integration`; never rely on live credentials in the normal suite. There is no enforced coverage percentage, but affected modules should have focused success, failure, and async/concurrency cases.

## Commit & Pull Request Guidelines

Use a concise, action-oriented subject naming the changed behavior. Recent history commonly uses Chinese summaries such as `修复subagent相关bug to #82161950`; include the `to #<work-item>` suffix only when a real tracker item exists. PRs should summarize scope and behavior, link the work item, list exact test commands/results, and call out configuration or API changes. Include screenshots for `webui/` changes and keep unrelated refactors separate.

## Security & Configuration

Copy `.env.example` to `.env` for local configuration. Never commit API keys, tokens, local memory, generated traces, or evaluation outputs containing prompts. Document new settings in `.env.example` with safe defaults.
