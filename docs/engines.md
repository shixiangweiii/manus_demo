# Engines

All engines implement the same task/result contract and are selected explicitly. There is no `auto` value.

| Engine | Execution model | Useful for |
|---|---|---|
| `sequential` | Plan ordered steps, execute and reflect, then replan when needed | Clear linear work and trace comparison |
| `dag` | Build dependencies and schedule ready nodes; concurrency is configurable | Independent branches followed by joins |
| `agent_loop` | Let the model repeatedly choose native tool calls and update a full todo snapshot | Open-ended exploration and adaptive work |

`effort` is a resource policy, not another engine. In the current implementation it adjusts model temperature, loop or Action turn limits, tool-result truncation, and the Plan-and-Execute ActionLoop reasoning cap while preserving the selected engine's semantics. It does not currently change Planner depth.

The engines use one native tool-calling protocol through two scope-specific loops: `ActionToolLoop` completes one planned Action, while `AgentLoop` owns the whole task history. They are implementation helpers, not user-selectable executor identities. Both consume structured `tool_calls` and matching tool-result messages without requiring visible chain-of-thought text.

Examples:

```bash
python main.py run "检查配置并总结" --engine sequential --effort low
python main.py run "并行调查三个模块后汇总" --engine dag --effort medium
python main.py run "探索未知问题并持续修正计划" --engine agent_loop --effort high
```

Agent Loop emits `todo_updated` with the complete current list. Consumers replace the previous snapshot rather than replaying incremental start/complete/fail mutations. Sequential and DAG keep their plan/step and graph/node event families.

`settings.toml` currently sets `dag_serial_execution = true` for reproducible local comparisons. Set it to `false` to let DAG execute independent ready nodes concurrently, up to `max_parallel_nodes`.
