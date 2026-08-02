# Unified Evaluation

`evaluation/` contains static cases, document-driven generation, isolated execution, storage, reporting, analysis, API routes, and a local UI. Use one entry point:

```bash
python -m evaluation run --dry-run
python -m evaluation run --engines sequential dag agent_loop --efforts low medium
python -m evaluation run --engines agent_loop --capability-set subagent,skills
python -m evaluation upload notes.md
python -m evaluation generate <document-id>
python -m evaluation report <run-id>
python -m evaluation analyze <run-id> [<run-id> ...]
python -m evaluation list runs
python -m evaluation serve
```

The experiment matrix is `engine × effort × capabilities`. There is no automatic selector or executor dimension. A case selects no preferred engine: every matrix cell runs the same task, and success criteria are expressed only through its explicit deterministic `verifiers`. Built-in JSON cases are under `evaluation/cases/`.

Each matrix cell clones `AppSettings`, applies its capability set, creates a fresh runtime, and uses isolated state, sandbox, checkpoint, and user-skill directories. It never mutates module-level configuration and closes the runtime before deleting the temporary directory.

Results preserve four independent outcome dimensions: engine success, typed engine stop reason, deterministic verifier status, and overall pass (`engine_success AND verifier_passed` when a verifier exists). Summaries report engine-success rate, verifier rate, overall success rate, and stop-reason counts separately.

Cost and behavior dimensions come from `EngineResult.stats`: all physical LLM calls, task-level AgentLoop turns, context-compaction calls, prompt/completion/total tokens, tool calls, reasoning tokens, SubAgent calls, latency, and repeated-run stability. `llm_calls` intentionally includes context compaction because it is real model work; `agent_turns` excludes compaction so convergence and cost remain independently observable. No weighted composite score is calculated. Failed cases keep `actual_engine` and `actual_effort` nullable when the event stream does not prove that execution began.

`success` always requires the engine to report completion. When a case defines deterministic `verifiers`, they must pass as well; when it defines none, the result measures engine-reported completion only and must not be interpreted as semantic or document-grounding quality. Built-in cases and both document-generation paths produce non-empty verifier lists; an empty list remains possible only for manually authored cases. LLM generation also requires a verbatim source excerpt and embeds it into the executable task, because the runner receives no separate hidden document. A generated case that omits either its evidence excerpt or deterministic verifier is rejected.

CLI and server runs persist progress after every unit. The server owns background task references and marks interrupted work failed during shutdown. Corrupted local JSON records are logged and skipped. Generated artifacts default to `~/.manus_demo/evaluation` and are ignored by Git.
