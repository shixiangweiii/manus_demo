# Engines and Executors

All general task engines implement `TaskEngine.run(TaskRequest) -> EngineResult` and delegate individual actions to `ActionExecutor`.

| Engine | Use case | Automatic effort |
|---|---|---|
| `sequential` | Bounded work with a flat ordered plan | `low` |
| `dag` | Parallel steps or explicit dependencies | `medium` |
| `todo` | Exploration with a changing work list | `high` |
| `goal` | Long tasks with goals, constraints, and completion criteria | `high` |
| `workflow` | Explicit deterministic tool graph | `low` |

Auto-routing checks explicit settings first, then goal markers, exploratory markers, dependency/parallel markers, and finally chooses Sequential. Workflow never participates in auto-routing.

`react` performs a normal tool-use loop. `thinking` uses the reasoning-aware loop. In `auto` mode, the executor reads only `llm.supports_reasoning`; model names are not inspected. Explicit `--effort` and `--executor` values always win.

Examples:

```bash
python main.py run "比较三个方案并汇总" --engine dag
python main.py run "探索这个目录的问题" --engine todo --executor thinking
python main.py workflow workflow_spec.json
```
