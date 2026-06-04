# 本地 WebParser 评测报告

日期：2026-06-04

## 结论

本地 WebParser 已能替代百炼 WebParser 完成 `fetch_url` 的核心 JSON 页面抓取场景。聚焦评测 `local-webparser-json-diagnostics` 中，`react_auto_baseline` 与 `reasoning_auto` 在 `easy_006` 上均为 100% success / 100% verifier pass，且失败报告为空。

本轮没有继续跑完整 `smoke_reasoning`，因为诊断任务 `resume_001` 在反思后反复重规划为联网查官方人口与面积数据，触发大量 `web_search` / `fetch_url`、上下文压缩和 max-iteration 风险。该问题属于评测任务/反思策略发散，不是本地 WebParser 的 429 限流问题。

## 执行环境

- `LOCAL_WEBPARSER_ENABLED=true`
- `LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN=false`
- `LOCAL_WEBPARSER_BROWSER_FALLBACK=false`
- `TRACING_ENABLED=false`
- 临时 API key 仅在当前 shell 环境中导出，未写入文档或配置文件。

## 预验证

```bash
python3 -m py_compile tools/local_web_parser.py tools/fetch_url.py config.py
python3 -m pytest tests/test_local_web_parser.py tests/test_fetch_url.py -q -o asyncio_mode=auto
```

结果：

- 编译通过。
- 本地 WebParser / fetch_url 单测：`33 passed`。

真实非 LLM 冒烟：

```bash
python3 - <<'PY'
import asyncio
from tools.fetch_url import FetchUrlTool

async def main():
    result = await FetchUrlTool().execute(url='https://httpbin.org/json', format='markdown')
    print(result[:1000])
    print(result.startswith('Error:'))

asyncio.run(main())
PY
```

结果要点：

- `parser_backend: local:raw-json`
- `starts_error=False`
- 成功返回 `slideshow.title = "Sample Slide Show"` 所在 JSON。

## 正式完成的聚焦评测

命令：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \
  --tasks easy_006 \
  --variants react_auto_baseline reasoning_auto \
  --modes simple \
  --repeat 1 \
  --output-dir evaluation/results \
  --run-id local-webparser-json-diagnostics
```

输出目录：

`evaluation/results/local-webparser-json-diagnostics`

结果文件：

- `summary.json`
- `summary.csv`
- `variant_comparison.md`
- `failures.md`
- `raw_results.json`
- `cost_latency.csv`
- `config_snapshot.json`

## 结果汇总

| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | Token Ratio | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.915 | 14367 | 801 | 8.0 | 19932ms | 1.00x | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.921 | 14690 | 882 | 8.0 | 19378ms | 1.02x | neutral |

失败报告：

- `failures.md` 显示：No failed tasks recorded.

日志观察：

- `fetch_url({'url': 'https://httpbin.org/json'})` 命中本地解析。
- 日志显示：`Locally fetched 'https://httpbin.org/json': 547 chars (format=markdown, backend=local:raw-json)`。
- 未出现百炼 WebParser 429。

## 中止的诊断跑

命令原计划：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \
  --tasks easy_006 resume_001 \
  --variants react_auto_baseline reasoning_auto \
  --modes simple \
  --repeat 1 \
  --output-dir evaluation/results \
  --run-id local-webparser-diagnostics
```

中止原因：

- `easy_006` 初始暴露本地 WebParser 不支持 `application/json`，已修复为 `local:raw-json`。
- `resume_001` 在反思后多次重规划为“联网搜索 10 个城市官方人口和面积数据”，连续触发大量 `web_search` 与 `fetch_url`。
- 日志中本地 WebParser 正常抓取大量页面，显示 `backend=local:trafilatura`，没有 WebParser MCP 429。
- 该任务出现上下文压缩与 max-iteration 风险，继续执行会消耗大量 token，且无法形成干净的 WebParser 稳定性结论，因此中止。

中止后目录：

- `evaluation/results/local-webparser-diagnostics/config_snapshot.json`
- 没有完整 summary / comparison 文件，不作为正式评测结论。

## 判读

本地 WebParser 对评测中的 `fetch_url` 核心链路已经明显改善：

- 不再依赖百炼 WebParser MCP。
- JSON 页面抓取已从失败变为成功。
- 聚焦评测无失败任务。
- `reasoning_auto` 在该单任务上仅略增 token，成功率无差异，因此仍是 neutral，不能据此默认启用。

仍需继续修复的问题：

- `resume_001` 的反思/重规划会把本地文件任务扩展为大型联网查证任务，污染 smoke 评测成本与结论。
- 后续完整 smoke 前，建议先约束该 benchmark 的预期数据来源，或修复 Reflector 对“用户要求创建样例数据”与“必须查官方数据”的误判。

## 下一步建议

1. 针对 `resume_001` 修复 Reflector / verifier 预期，避免把“创建 10 个城市数据”误判为必须联网查官方数据。
2. 再跑完整 `smoke_reasoning simple complex`。
3. 如果 smoke 稳定，再进入 `core_reasoning`，不要直接把 `reasoning_auto` 设为默认。

## 二次迭代记录

根据本轮评测发现的问题，继续优化本地 WebParser：

- 将 JSON/API 响应升级为一等输入：支持 `application/json`、`application/*+json`，返回格式化 JSON，并标记 `parser_backend: local:raw-json`。
- 支持纯文本类页面：任意 `text/*`、CSV、XML / `*+xml` 等可读内容不再强行进入 HTML 主体抽取，无法识别为 markup 时返回 `parser_backend: local:raw-text`。
- 新增进程内 LRU 缓存：`LOCAL_WEBPARSER_CACHE_SIZE=64`，重复抓取同一 URL 时复用已下载内容，减少评测中重复 `fetch_url` 的网络耗时。
- `Accept` 请求头补充 `application/json`、`text/plain`、`application/xml`，更适合 API / 文档混合场景。

新增验证：

```bash
python3 -m pytest tests/test_local_web_parser.py tests/test_fetch_url.py -q -o asyncio_mode=auto
```

结果：`38 passed`。

相关回归：

```bash
python3 -m pytest tests/test_local_web_parser.py tests/test_fetch_url.py tests/test_mcp_client.py tests/test_engine_helpers.py tests/test_evaluation.py -q -o asyncio_mode=auto
```

结果：`131 passed`。

二次聚焦评测：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \
  --tasks easy_006 \
  --variants react_auto_baseline reasoning_auto \
  --modes simple \
  --repeat 1 \
  --output-dir evaluation/results \
  --run-id local-webparser-iteration-2
```

结果：

| Variant | Mode | Success | Verifier | Score | Tokens | Calls | Wall Time | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.922 | 12849 | 7.0 | 20054ms | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.924 | 15183 | 8.0 | 14536ms | neutral |

失败报告：No failed tasks recorded.

## resume_001 发散修复记录

根据 `resume_001` 在诊断中出现的反思/重规划发散，进行了以下修复：

- 明确 `resume_001` 任务契约：使用自造示例数据，固定输出 `cities.json` 与 `city_density.csv`，明确“不需要联网查询官方人口或面积数据”。
- 增强 `resume_001` 确定性 verifier：
  - 检查 `cities.json` 存在。
  - 检查 `city_density.csv` 存在。
  - 检查 CSV 中包含 `density` / `人口密度` / `density_per_km2` / `密度` 字段语义。
  - 检查 CSV 至少包含 10 行数据。
- 收紧 Reflector 规则：
  - 明确“生成/创建示例数据”不属于未授权默认值。
  - 明确文件产物任务中，如果结果显示指定文件已创建且无工具错误，不应仅为重复读取/验证文件而判失败。
  - 增加窄范围启发式：对“自造/示例数据 + 明确文件名 + 全步骤成功 + 输出提到产物”的文件任务直接通过，避免 LLM 反思随机触发重复重规划。

修复前后观察：

- 修复前：`resume_001` 会被 Reflector 重规划为联网查询 10 个城市官方人口/面积数据，触发大量 `web_search` / `fetch_url`。
- 第一次修复后：联网发散消失，`resume001-fix-diagnostics` 中两个 variant 均通过：

| Variant | Mode | Success | Verifier | Score | Tokens | Calls | Wall Time | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| react_auto_baseline | simple | 100.0% | 100.0% | 0.883 | 10237 | 5.0 | 17031ms | baseline |
| reasoning_auto | simple | 100.0% | 100.0% | 0.882 | 11239 | 5.0 | 18800ms | neutral |

- 组合评测 `webparser-resume-smoke-simple` 暴露了第二个问题：`reasoning_auto` 仍会进行重复文件验证，且 verifier 未接受中文 `密度` 表头，导致旧结果中 `reasoning_auto/resume_001` 被判失败。
- 已进一步修复 verifier 与 Reflector 文件产物启发式。

当前验证：

```bash
python3 -m py_compile agents/reflector.py evaluation/benchmark.py
python3 -m pytest tests/test_evaluation.py tests/test_prompt_freshness.py tests/test_local_web_parser.py tests/test_fetch_url.py tests/test_mcp_client.py tests/test_engine_helpers.py -q -o asyncio_mode=auto
```

结果：`147 passed`。

未完成项：

- 修复后的 LLM 评测重跑被临时 key 失效阻断：DeepSeek 返回 `401 Authentication Fails, key invalid`。
- 因此 `webparser-resume-smoke-simple` 旧结果不能代表当前代码状态；需要换有效 `LLM_API_KEY` 后重跑：

```bash
python3 -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \
  --tasks easy_006 resume_001 \
  --variants react_auto_baseline reasoning_auto \
  --modes simple \
  --repeat 1 \
  --output-dir evaluation/results \
  --run-id webparser-resume-smoke-simple-rerun
```
