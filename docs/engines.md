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

Workflow parameter references use only `${steps.<step_id>}` and the referenced step must be a declared dependency. Write `$${steps.<step_id>}` to produce the literal `${steps.<step_id>}` text. Other strings such as `${HOME}` are left unchanged and are never interpreted as workflow references.

DAG success allows action nodes skipped because a condition did not select their branch. An execution failure, rollback, failure cascade, unfinished graph, absence of any completed action, or failed final reflection still makes the DAG unsuccessful. Result metadata separates `failed_action_ids` from `condition_skipped_ids`.
