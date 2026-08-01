# Unified Evaluation

`evaluation/` contains static cases, document-driven generation, isolated execution, storage, reporting, analysis, API routes, and the local UI. Use one entry point:

```bash
python -m evaluation run --dry-run
python -m evaluation run --engines sequential dag --executors react --efforts low medium
python -m evaluation upload notes.md
python -m evaluation generate <document-id>
python -m evaluation report <run-id>
python -m evaluation analyze <run-id> [<run-id> ...]
python -m evaluation list runs
python -m evaluation serve
```

Built-in JSON cases are under `evaluation/cases/`. Each matrix cell clones `AppSettings`, applies its capability set, creates a fresh runtime, and uses an isolated sandbox/checkpoint directory. It never mutates module-level configuration.

Each matrix cell closes its runtime before deleting the temporary sandbox. CLI and server runs persist progress after every unit. The server owns background task references and marks interrupted generation or evaluation records failed during shutdown. Corrupted local JSON records are logged and skipped rather than crashing list/get routes.

Results report success, verifier status, tokens, latency, tool calls, iterations, replans, repeated-run stability, and automatic selector accuracy as separate dimensions. Generated artifacts default to `~/.manus_demo/evaluation` and are ignored by Git. Existing historical result formats and version-named baselines are not migrated.

For failed cases, `actual_engine`, `actual_executor`, and `actual_effort` are nullable. They are populated only when the structured event stream proves a selection occurred; the requested experiment value is not presented as an observed choice.
