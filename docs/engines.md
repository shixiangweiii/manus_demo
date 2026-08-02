# Engines

All engines implement the same task/result contract and are selected explicitly. There is no `auto` value.

| Engine | Execution model | Useful for |
|---|---|---|
| `sequential` | Plan ordered steps, execute and reflect, then replan when needed | Clear linear work and trace comparison |
| `dag` | Build dependencies and schedule ready nodes; concurrency is configurable | Independent branches followed by joins |
| `agent_loop` | Let the model repeatedly choose native tool calls and update a full todo snapshot | Open-ended exploration and adaptive work |

`effort` is a resource policy, not another engine. In the current implementation it adjusts model temperature, loop or Action turn limits, tool-result truncation, and the Plan-and-Execute ActionLoop reasoning cap while preserving the selected engine's semantics. It does not currently change Planner depth.

For `agent_loop`, `max_agent_turns` counts only task-level model decisions. `low` effort uses half of that configured limit; `medium` and `high` use the full limit. Context-compaction model calls remain real work in `EngineStats.llm_calls` and `context_compaction_calls`, but do not consume agent turns. A compressed canonical-history view is reused until appended messages make it exceed `max_context_tokens`. `max_agent_total_tokens` limits the root loop's total recorded or conservatively estimated model usage, including compaction calls.

The engines use one native tool-calling protocol through two scope-specific loops: `ActionToolLoop` completes one planned Action, while `AgentLoop` owns the whole task history. They are implementation helpers, not user-selectable executor identities. Both consume structured `tool_calls` and matching tool-result messages without requiring visible chain-of-thought text.

When one assistant response contains multiple tool calls, both loops execute them serially in provider order. This preserves deterministic side effects and exact tool-result pairing, but it also means latency comparisons include the cost of serial dispatch; DAG scheduling remains the explicit path for concurrent independent work.

Examples:

```bash
python main.py run "检查配置并总结" --engine sequential --effort low
python main.py run "并行调查三个模块后汇总" --engine dag --effort medium
python main.py run "探索未知问题并持续修正计划" --engine agent_loop --effort high
```

Agent Loop emits `todo_updated` with the complete current list. Consumers replace the previous snapshot rather than replaying incremental start/complete/fail mutations. Sequential and DAG keep their plan/step and graph/node event families.

`settings.toml` currently sets `dag_serial_execution = true` for reproducible local comparisons. Set it to `false` to let DAG execute independent ready nodes concurrently, up to `max_parallel_nodes`. `dag_checkpoint_history_limit` controls only the in-memory DAG super-step snapshot history; the runtime checkpoint store separately keeps the latest semantic checkpoint per task.
