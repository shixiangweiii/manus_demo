# 4.2 子智能体 SubAgent 自动化测试问题报告

> 测试时间：2026-06-03  
> 测试范围：`sxw_aicoding/docs/operations-manual.md` 4.2 子智能体 SubAgent  
> 说明：测试使用用户提供的临时 API Key 注入进程环境；Key 未写入本报告、`.env` 或源码。

## 结论

真实 SubAgent 执行链路通过：

- `SubAgentTool` 可真实 spawn 子智能体并完成任务。
- 事件日志包含 `subagent_start`、`subagent_iteration`、`subagent_complete`。
- 最终返回结果包含正确计算结果 `42`。
- `tool_whitelist` 中请求的 `subagent` 会被结构性过滤，符合 depth=1 约束。
- token caller 维度中出现独立 `SubAgent-1` bucket，符合手册验证点。

但现有 `tests/test_subagent.py` 自动化测试套件当前不是全绿：

```text
102 passed, 3 failed
```

这说明 SubAgent 相关测试/文档与当前实现存在不一致，需要修复或更新测试断言。

## 验证通过项

### 1. 语法检查通过

命令：

```bash
python3 -m py_compile \
  config.py \
  agents/subagent.py \
  tools/subagent_tool.py \
  agents/orchestrator.py \
  react/engine.py \
  agents/emergent_planner.py
```

结果：通过。

### 2. 直接 SubAgentTool 真实执行通过

测试方式：

- 构造 `SubAgentTool`
- 仅授权 `execute_python`
- 请求子智能体计算 `13 + 29`
- 同时传入 `tool_whitelist=["execute_python", "subagent"]` 验证 depth=1 过滤

关键结果：

```json
{
  "event_names": [
    "subagent_start",
    "subagent_iteration",
    "subagent_iteration",
    "subagent_complete"
  ],
  "tool_whitelist": ["execute_python"],
  "findings": "13 + 29 = 42",
  "caller_totals": {
    "SubAgent-1": 3485
  },
  "summary_mentions_42": true
}
```

结论：通过。

### 3. Orchestrator + emergent + SubAgent 真实执行通过

测试配置：

```bash
SUBAGENT_ENABLED=true
PLAN_MODE=emergent
SUBAGENT_DEFAULT_TOOL_WHITELIST=execute_python
```

测试任务：

```text
请在本任务中必须调用 subagent 工具委派一个子任务：
让子智能体使用 execute_python 计算 13 + 29。
最终回答只需要说明子智能体返回的计算结果数字。
```

关键结果：

```json
{
  "answer": "子智能体返回的计算结果是：42。",
  "has_start": true,
  "has_complete": true,
  "has_subagent_caller_bucket": true,
  "by_caller": {
    "EmergentPlanner": 9964,
    "SubAgent-1": 3429
  },
  "subagent_complete": {
    "iterations_used": 2,
    "tool_calls_count": 1,
    "tokens_used": 2382
  }
}
```

结论：通过。

## 问题 1：`tests/test_subagent.py` 当前有 3 个失败用例

### 严重程度

P2 - 自动化测试红，但真实 SubAgent 正常执行。主要风险是 CI/回归信号不可信，以及测试语义与当前实现脱节。

### 复现命令

```bash
python3 -m pytest tests/test_subagent.py -q -o asyncio_mode=auto
```

### 实际结果

```text
3 failed, 102 passed
```

失败用例：

```text
tests/test_subagent.py::TestSubAgentTool::test_execute_handles_subagent_exception
tests/test_subagent.py::TestP0TokenBudgetCircuitBreaker::test_token_budget_exceeded_returns_failed
tests/test_subagent.py::TestP2TokenIndexRange::test_tokens_used_from_record_index_range
```

### 失败 1：异常返回协议与测试预期不一致

失败用例：

```text
TestSubAgentTool.test_execute_handles_subagent_exception
```

测试期望：

```python
data = json.loads(result)
```

实际返回：

```text
Error: {"accomplished": "", "findings": "", "issues": "SubAgent error: crash", ...}
```

当前实现会给非正常结果添加 `Error:` 前缀，让 ReAct 路径和 emergent 并行派发路径可通过 `classify_result()` 识别失败。这与旧测试“异常结果仍是裸 JSON”的预期不一致。

建议处理：

- 如果当前 `Error:` 协议是正式设计：更新测试，先剥离 `Error:` 前缀后再解析 JSON，并断言错误可被 classify 为失败。
- 如果希望保持旧 JSON 协议：需要调整 `SubAgentTool.execute()`，但这会影响工具失败识别，不建议直接回退。

### 失败 2：token budget 测试仍使用无 caller_tag 的旧记录

失败用例：

```text
TestP0TokenBudgetCircuitBreaker.test_token_budget_exceeded_returns_failed
```

当前实现：

```python
return sum(
    r.total_tokens
    for r in records[self._records_before:]
    if r.caller_tag == self.name
)
```

测试构造的 `LLMCallRecord` 没有设置 `caller_tag="SA-1"`，所以 `_own_tokens_used()` 返回 `0`，不会触发 `SubAgentTokenExhausted`。

建议处理：

- 更新测试数据，为属于该 SubAgent 的 token 记录显式设置 `caller_tag="SA-1"`。
- 增加一个 sibling caller 的记录，验证不会错误计入本 SubAgent token budget。

### 失败 3：tokens_used 测试仍断言“record index range”旧语义

失败用例：

```text
TestP2TokenIndexRange.test_tokens_used_from_record_index_range
```

测试期望：

```text
tokens_used == 80
```

实际结果：

```text
tokens_used == 0
```

原因同上：测试追加的新 token 记录没有 `caller_tag="SA-1"`。当前代码已从单纯 index range 迁移到 `records[_records_before:] + caller_tag == self.name` 的组合过滤，以避免并发 SubAgent 互相污染 token 统计。

建议处理：

- 将该测试改名为 caller-tag scoped token accounting。
- 构造三类记录：
  - 运行前旧记录：不计入。
  - 当前 SubAgent 记录：计入。
  - sibling SubAgent 记录：不计入。
- 断言 tokens_used 只等于当前 SubAgent 的记录总和。

## 影响范围

- 真实 `SUBAGENT_ENABLED=true` 执行链路未在本轮 smoke 中失败。
- 主要影响自动化测试可信度，以及文档/测试对 token accounting 的描述一致性。
- 如果 CI 运行 `tests/test_subagent.py`，当前会失败。

## 建议后续修复

1. 更新 `tests/test_subagent.py` 中异常返回协议测试，适配 `Error:` 前缀。
2. 更新 token budget / tokens_used 测试，使用 `caller_tag` 构造记录。
3. 同步检查项目文档中是否仍描述为“仅 record index range 统计 token”，避免与当前源码的 caller-tag scoped 实现冲突。
4. 修复后重新运行：

```bash
python3 -m pytest tests/test_subagent.py -q -o asyncio_mode=auto
```

## 备注

本轮曾有一次 Orchestrator smoke 脚本将 logging format 写成 `%(INFO)s`，导致 Python logging 自身报错；该错误来自测试脚本格式串，不属于项目源码问题，因此未列为产品问题。
