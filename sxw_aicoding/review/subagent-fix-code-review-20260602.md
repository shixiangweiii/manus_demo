# SubAgent Fix Code Review - 2026-06-02

## Scope

Reviewed recent SubAgent-related commits:

- `d48e431` - `subagent代码评审修复 to #000000`
- `3622898` - `优化subagent执行和网页解析mcp服务协议修改 to #82161950`

Per request, this report excludes the existing test/protocol mismatch around
`SubAgentTool` exception return format.

## Finding 1 - P0: Parallel SubAgents share token-record slices and can trip each other budget

### Evidence

- `agents/emergent_planner.py:832` dispatches multiple ready TODOs concurrently via `asyncio.gather(...)`.
- `agents/emergent_planner.py:880` delegates each TODO to the shared `subagent` tool.
- `agents/subagent.py:254` records `self._records_before = len(self.llm_client.get_call_records())`.
- `agents/subagent.py:231-235`, `agents/subagent.py:287-290`, and failure branches calculate tokens with:
  `sum(r.total_tokens for r in records[self._records_before:])`.
- `LLMClient` is shared at orchestrator level and appends all successful calls to one global list in `llm/client.py:406-415`.

### Why this is a bug

The index-range method was safe when one SubAgent ran at a time. The latest
parallel emergent path can run multiple SubAgents concurrently, all sharing the
same `LLMClient._call_records` list. If SubAgent A and SubAgent B overlap, each
one's `records[self._records_before:]` can include the other's LLM calls.

This is not just an observability error. `_on_react_iteration()` uses the same
calculation for budget enforcement, so one SubAgent can be failed with
`SubAgentTokenExhausted` because another SubAgent consumed tokens in the same
window.

### Upstream / downstream impact

- Upstream trigger: `EMERGENT_PARALLEL_TODOS=true` and `SUBAGENT_ENABLED=true`
  with two or more ready TODOs.
- Direct impact: `tokens_used` is inflated and non-deterministic under overlap.
- Behavioral impact: SubAgent budget checks can fail unrelated work.
- Reporting impact: tracing/evaluation/token summary can over-attribute usage to
  each concurrent SubAgent.

### Suggested fix

Keep the start-index optimization, but filter records by `caller_tag`:

```python
records = self.llm_client.get_call_records()
tokens_used = sum(
    r.total_tokens
    for r in records[self._records_before:]
    if r.caller_tag == self.name
)
```

Apply the same helper everywhere `SubAgent` currently slices call records:

- `_on_react_iteration()`
- successful completion token calculation
- token-exhausted branch
- timeout branch
- generic exception branch

Add a regression test with two concurrent SubAgents sharing one mock
`LLMClient`, interleaving records tagged `SubAgent-1` and `SubAgent-2`, and
asserting each SubAgent only counts its own tag.

## Finding 2 - P1: Emergent parallel dispatch bypasses caller attribution

### Evidence

- Standard ReAct tool execution goes through `react/engine_helpers.py:138-140`,
  which calls `attribute_caller(t, agent_name)` immediately before
  `t.traced_execute(...)`.
- `attribute_caller()` calls `tool.set_caller(agent_name)` in
  `react/tool_call_helpers.py:35-50`.
- `SubAgentTool.execute()` captures `_parent_name` at the start in
  `tools/subagent_tool.py:116-127`, then passes it into `SubAgent(...)` as
  `parent_agent_name` at `tools/subagent_tool.py:235-246`.
- Emergent parallel dispatch directly calls
  `self.tools["subagent"].traced_execute(...)` in
  `agents/emergent_planner.py:880-882`, bypassing `execute_tool_calls()` and
  therefore bypassing `attribute_caller()`.

### Why this is a bug

The latest parallel emergent path introduces a new direct tool-call path. Because
it does not set the caller immediately before invoking `SubAgentTool`, the
SubAgent's `parent_agent` event field can fall back to the constructor default
`OrchestratorAgent`, or to a stale value written by a previous caller.

This weakens tracing and evaluation accuracy precisely in the new parallel path
where observability matters most.

### Upstream / downstream impact

- Upstream trigger: emergent planner chooses the parallel wave path.
- Direct impact: `subagent_start` event has incorrect `parent_agent`.
- Downstream impact: `TracingBridge`, evaluation probes, and logs cannot
  distinguish whether the SubAgent was spawned by emergent planning, executor
  ReAct, or goal-driven planning.

### Suggested fix

Call `attribute_caller()` in `_dispatch_one_subagent()` immediately before
`traced_execute`, with no `await` between the attribution and the call:

```python
from react.tool_call_helpers import attribute_caller

subagent_tool = self.tools["subagent"]
attribute_caller(subagent_tool, "EmergentPlannerAgent")
summary = await asyncio.wait_for(
    subagent_tool.traced_execute(task_description=task_description),
    timeout=config_module.NODE_EXECUTION_TIMEOUT,
)
```

Add a focused test for `_dispatch_one_subagent()` using a mock `subagent` tool
with `set_caller = MagicMock()` and `traced_execute = AsyncMock(...)`, then
assert `set_caller("EmergentPlannerAgent")` was called before dispatch.

## Notes

- `EMERGENT_PARALLEL_TODOS` is default-off, so the main default runtime path is
  not affected until the feature flag is enabled.
- The underlying SubAgentTool concurrency changes, such as reserve-before-await,
  semaphore limiting, local counter capture, and non-completed `Error:` prefix,
  are directionally sound for the parallel wave design. The two issues above are
  integration gaps introduced by allowing multiple SubAgents to overlap and by
  adding a direct dispatch path outside the shared tool execution helper.
