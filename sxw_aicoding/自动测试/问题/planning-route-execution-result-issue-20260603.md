# 4.1 规划路由端到端执行结果问题报告

> 测试时间：2026-06-03 15:59 CST  
> 测试范围：`sxw_aicoding/docs/operations-manual.md` 4.1 规划路由  
> 说明：测试使用用户提供的临时 API Key 注入进程环境；Key 未写入本报告、`.env` 或源码。

## 结论

`simple`、`emergent(v5)`、`emergent + goal-driven(v8)` 的端到端执行结果均正确。

`complex(v2 DAG)` 路径存在一个输出契约问题：内部计算结果正确，但最终返回值是 DAG action 执行详情拼接，而不是按用户原始任务约束合成后的最终答案。

## 问题 1：complex/DAG 路径返回内部 action 汇总，未遵守最终输出格式

### 严重程度

P1 - 用户可见结果不符合任务要求。数值计算本身正确，但最终答案没有遵守“只输出最终数字”的明确约束。

### 复现任务

```bash
PLAN_MODE=complex python main.py \
  "先分别计算 6*7 和 8+9，再计算两个结果之和。最终回答必须只包含数字 59，不要解释。"
```

### 期望结果

```text
59
```

### 实际结果摘要

实际结果包含 `42`、`17`、`59` 等 action 明细，并以如下形式返回：

```text
[act_1_1] 执行Python计算6*7:
...
[act_2_1] 执行Python计算8+9:
...
[act_3_1] 执行Python计算42+17并输出59:
...
```

最终数值 `59` 是正确的，但输出不是用户要求的“只包含数字 59”。

### 自动测试证据

复测使用默认 `MAX_REPLAN_ATTEMPTS` 配置，仍然复现。

```json
{
  "name": "complex exact-output retry-default",
  "ok_exact": false,
  "expected_exact": "59",
  "elapsed_seconds": 27.06,
  "complexity_events": [
    {
      "complexity": "complex",
      "effort": "medium"
    }
  ],
  "phases": [
    "Gathering context...",
    "Classifying task complexity...",
    "Planning (v2 hierarchical DAG)...",
    "Executing DAG (attempt 1)...",
    "Reflecting on DAG results..."
  ]
}
```

### 初步原因定位

`agents/orchestrator.py` 中 simple 路径会调用 `_compile_answer()`，再通过 `_synthesize_final_answer()` 将步骤结果合成为面向用户的最终答案。

但 complex/DAG 路径在反思通过后直接返回 `dag_executor.execute(dag)` 的 `final_output`：

```python
if reflection.passed:
    self._record_outcome(True, reflection, results)
    return final_output
```

该 `final_output` 是 DAG action 结果汇总，包含内部节点 ID 和每个 action 的执行详情，因此没有经过与 simple 路径同等级别的最终答案合成，也就无法稳定遵守用户的最终输出格式要求。

### 影响范围

- 强制 `PLAN_MODE=complex` 的任务。
- 自动分类进入 `complex` 的多阶段任务。
- 尤其影响用户对最终答案有明确格式约束的任务，例如“只输出数字”“只返回 JSON”“不要解释”“保存后只回复路径”等。

### 建议修复方向

为 DAG 路径增加最终答案合成步骤，参考 simple 路径的 `_synthesize_final_answer(task, raw_results)`：

1. DAG 执行完成后保留 action 级结果用于 reflection。
2. reflection 通过时，不直接返回 `final_output`。
3. 将 `final_output` 或 ACTION 节点结果汇总传入最终答案合成器。
4. 合成 prompt 必须显式包含用户原始任务，确保格式约束可被遵守。
5. 增加回归测试：`PLAN_MODE=complex` 下要求最终答案严格等于 `59`。

## 通过项

### simple(v1)

任务：

```text
请计算 13 + 29，只输出最终数字。
```

实际结果：

```text
42
```

结论：通过。

### emergent(v5)

任务：

```text
探索并完成这个小任务：找出 20 以内所有质数并计算它们的和，只输出最终数字。
```

实际结果：

```text
77
```

结论：通过。

### emergent + goal-driven(v8)

任务：

```text
多步骤完成这个小任务：找出所有大于 0 且小于 10 的偶数，并计算它们的和。最终回答必须只包含数字 20，不要解释。
```

实际结果：

```text
20
```

结论：通过。

## 备注

初始目标驱动用例使用了“10 以内所有偶数”，模型返回 `30`。该表达在中文中可能包含 10，因此不作为问题记录；后续已用“大于 0 且小于 10”复测并通过。
