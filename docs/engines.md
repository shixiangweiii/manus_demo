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

## Action executors

The executor values name the runtime mechanism directly:

- `tool_calling` delegates to `ToolCallingLoop`. The model emits structured `tool_calls`; the runtime executes them and returns results as `role="tool"` messages.
- `reasoning_aware_tool_calling` delegates to `ReasoningAwareToolCallingLoop`, a subclass that additionally handles reasoning-only rounds, a reasoning-token budget, and a reasoning-round limit.

This implementation is not the classic text-form ReAct protocol in which a prompt requires literal labels and a runtime parser extracts them:

```text
Thought: ...
Action: ...
Observation: ...
```

The semantic correspondence is straightforward—a structured `tool_call` is an action and the matching tool-result message is its observation—but the transport and parser are entirely different. Reasoning may be returned in a provider-specific field, kept internal, or omitted; displaying chain-of-thought is not required by either executor.

In `auto` mode, the executor reads only `llm.supports_reasoning`; model names are not inspected. Explicit `--effort` and `--executor` values always win.

`effort` is the resolved runtime resource level. It can tune planning and
action-loop iterations, temperature, truncation, and reasoning budgets; it is
not a request to display private reasoning.

Examples:

```bash
python main.py run "比较三个方案并汇总" --engine dag
python main.py run "探索这个目录的问题" --engine todo --executor reasoning_aware_tool_calling
python main.py workflow workflow_spec.json
```

Workflow parameter references use only `${steps.<step_id>}` and the referenced step must be a declared dependency. Write `$${steps.<step_id>}` to produce the literal `${steps.<step_id>}` text. Other strings such as `${HOME}` are left unchanged and are never interpreted as workflow references.

DAG success allows action nodes skipped because a condition did not select their branch. An execution failure, rollback, failure cascade, unfinished graph, absence of any completed action, or failed final reflection still makes the DAG unsuccessful. Result metadata separates `failed_action_ids` from `condition_skipped_ids`.
