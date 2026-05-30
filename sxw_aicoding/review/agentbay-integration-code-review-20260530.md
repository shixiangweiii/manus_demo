# AgentBay 集成代码评审

- 日期：2026-05-30
- 评审对象：`agentbay_research/AgentBay集成变更记录.md` 所述变更
- 参考资料：`agentbay_research/AgentBay调研总结报告.md`、`agentbay_research/通过sdk接入.md`、已验证 demo（`00/01/02_*.py`）
- 评审范围（实际改动文件）：
  - 新增：`tools/agentbay/{__init__,runtime,code_tool,browser_tool,cleanup_sessions}.py`、`tests/test_agentbay_tools.py`
  - 修改：`config.py`、`main.py`（`_build_tools`）、`guardrails/tool_guardrail.py`、`guardrails/input_guardrail.py`、`requirements.txt`、`.env.example`

## 评审方法

不仅做静态阅读，还对照本机已安装的 `wuying-agentbay-sdk==0.21.0` 实际 API 做了校验，并实跑了测试与若干安全用例：

- 校验 SDK 真实签名/字段（防止文档与 SDK 漂移导致运行期失败）：
  - `agentbay` 顶层确实导出 `AgentBay / CreateSessionParams / LifecyclePolicy / BrowserOption`（与变更记录一致）。
  - `CreateSessionParams(labels=, image_id=, lifecycle_policy=)`、`LifecyclePolicy(idle_release_timeout=, max_runtime=)`（单位：分钟）签名匹配。
  - `Code.run_code(code, language, timeout_s=60, ...)` 支持 `timeout_s`，且 SDK 文档注明"网关限制单次 ≤ 60s"，与代码里硬 clamp `1..60` 一致。
  - `EnhancedCodeExecutionResult` 字段（`logs.stdout/stderr`、`result` property、`error.name/value/traceback`、`request_id`）与 `_format_code_*` 读取一致。
  - `AgentBay.list(labels=, limit=, status=)` / `get(session_id)` / `delete(session)`、`SessionListResult.session_ids` 与 `cleanup_sessions.py` 一致。
  - 顶层 `AgentBay` 为 **同步** 客户端（`agentbay._sync.agentbay`），故 `asyncio.to_thread(agent_bay.create, ...)` 包装正确，无"包了协程"的错误。
- `python -m pytest tests/test_agentbay_tools.py -q` → **7 passed**。
- `py_compile` 全部改动文件 → OK。
- 实跑 URL 校验/ipaddress 分类，复现 SSRF 边界（见 F1）。
- 扫描了仓库根目录的 `agentbay.log`（见 F2）。

**总体结论**：核心封装方向正确、与 SDK 实际 API 吻合、Session 清理兜底到位、特性开关与降级路径合理、测试有效。可以合入，但存在 **2 个 P1（安全/泄密）** 必须先处理，外加若干 P2/P3 工程项。

---

## 结论速览

| 级别 | 编号 | 问题 | 文件 |
|------|------|------|------|
| P1 | F1 | URL 防护未覆盖阿里云元数据 IP `100.100.100.100`，且整数/十六进制 IP、DNS 内网名可绕过——与变更记录"阻止内网/保留地址"的承诺不符 | `browser_tool.py` / `tool_guardrail.py` |
| P1 | F2 | SDK 会向 **仓库根目录** 写 `agentbay.log`（含 `resource_url?authcode=…`、`Aliuid`、`ApikeyId`、`AppUserId`），且未加入 `.gitignore`，`git add -A` 会暂存密钥 | `runtime.py` / `.gitignore` 缺失 |
| P2 | F3 | 日志降噪依赖 `setdefault` 在 import 前生效，易被 `.env` 自动加载或提前 import 破坏；且文件日志始终生成 | `runtime.py` |
| P2 | F4 | `create_session/delete_session/browser.initialize` 无墙钟超时，仅依赖 SDK 内部超时——与调研报告自己的 P1 建议部分未落实 | `runtime.py` / `browser_tool.py` |
| P2 | F5 | `requirements.txt` 把可选重依赖（agentbay-sdk、playwright）硬 `==` 钉进基础安装，默认关闭却人人必装 | `requirements.txt` |
| P3 | F6 | `concurrency_sem()` 用私有 `_value` 判定重建，语义错误且有重建竞态 | `runtime.py` |
| P3 | F7 | URL 校验逻辑在 tool 与 guardrail 两处重复实现，易漂移 | `browser_tool.py` / `tool_guardrail.py` |
| P3 | F8 | `delete` 成功默认 `True`（乐观默认）可能掩盖未删除的云端 Session（计费风险） | `runtime.py` / `cleanup_sessions.py` |
| P3 | F9 | 本次还夹带了与 AgentBay 无关的 `_discover_mcp_bridge_tools` 重构，变更记录未提及（评审/回滚卫生） | `main.py` |
| Note | — | 截图仅返回本地路径、`agentbay_code` 未列为不可信工具——均为合理设计，记录在案 | — |

---

## P1 详述

### F1 — URL/SSRF 防护存在缺口，且与变更记录承诺不符

变更记录与 `tool_guardrail.py` 均声称阻止"private / loopback / link-local / reserved / multicast"。但浏览器跑在 **AgentBay 阿里云沙箱** 里，`localhost`/`100.100.100.100` 指向的是 **云沙箱自身**，最值得防的目标是云厂商元数据服务（可能泄露沙箱实例的 RAM/STS 凭证）。实测：

```
http://169.254.169.254/...   -> BLOCKED   (link-local，已挡)
http://100.100.100.100/...   -> ALLOWED   ← 阿里云 ECS 元数据端点，未挡！
http://2130706433/           -> ALLOWED   ← 127.0.0.1 的十进制整数形式
http://0x7f000001/           -> ALLOWED   ← 127.0.0.1 的十六进制形式
http://internal.corp/admin   -> ALLOWED   ← DNS 名解析到内网（不做解析）
```

根因：
- `ipaddress.ip_address("100.100.100.100").is_private == False`（RFC 不归类为私有），而它恰是阿里云元数据 IP——讽刺的是 `169.254.169.254` 挡住了、阿里云自家的反而没挡。
- `ipaddress.ip_address("2130706433")` 对**字符串**整数会抛 `ValueError`，于是 `validate_public_url` 走 `except → return None`（放行）；十六进制 `0x7f000001` 同理。
- 只对 IP 字面量判定，DNS 名一律放行（不做解析），凡解析到内网的域名都能过。

威胁可达性：攻击面在被提示注入污染的 URL（任务文本或被检索到的网页内容诱导 LLM 调用 `agentbay_browser`）。爆炸半径限于临时沙箱实例的角色凭证，但 **这正是变更记录声称已防住的一类**，属于"声明的安全保证未兑现"，定级 P1。

建议（按性价比）：
1. 显式拉黑云元数据网段：`169.254.0.0/16`（已含）+ `100.100.100.100`（阿里云）/`100.64.0.0/10`（CGNAT/可选）；如要更稳，把 `0.0.0.0/8`、`is_unspecified` 也纳入。
2. 解析失败时，先尝试把 host 规整为 IP（`socket.inet_aton`/按整数解析）再判定；至少对纯数字/`0x` 前缀 host 直接拒绝。
3. 对 DNS 名做一次解析后再校验各解析地址（注意 DNS rebinding 仍有残留风险，可在文档标注为已知限制）。
4. 若短期不实现 2/3，**至少把变更记录与 guardrail 注释里的"阻止内网/保留地址"措辞改为"阻止常见本地/链路本地/部分保留地址"**，避免过度承诺。

### F2 — SDK 在仓库根目录落盘 `agentbay.log`，含敏感信息且未 gitignore

SDK 的 `agentbay/_common/logger.py` 在 import 时即 `AgentBayLogger.setup()`，`enable_file` 默认 True，默认把日志写到 **`Path.cwd()/agentbay.log`**。本仓库根目录现存的 `agentbay.log`（调研期 INFO 运行产物）实测包含：

```
resource_url : 3   (URL 内含 authcode=...)
authcode     : 2
ApikeyId     : 6
Aliuid       : 6
AppUserId    : 6
```

而 `git check-ignore agentbay.log` → 未被忽略，当前为 `??` 未跟踪状态，**`git add -A` 会把含 authcode 的文件一起暂存**。变更记录把"日志降噪"当作安全措施，但只压低了**级别**，没处理**落盘路径**与 **gitignore**。定级 P1（密钥泄露/误提交）。

建议：
1. 立即 `git rm`/删除现有 `agentbay.log`，并在 `.gitignore` 增加 `agentbay.log`（必要时 `agentbay*.log`）。
2. 在 `runtime.get_agentbay_sdk()` 内、import SDK 前设置 `AGENTBAY_LOG_FILE` 到仓库外（如 `os.path.join(config.SANDBOX_DIR, "agentbay.log")` 或临时目录），让密钥级日志不落进仓库；SDK 支持 `AGENTBAY_LOG_FILE` 环境变量覆盖路径。
3. 评估直接禁用文件日志（SDK 仅 `AgentBayLogger.setup(enable_file=False)` 支持，无 env 开关；若不想 import 后再调 setup，至少用方案 2 改路径）。

---

## P2 详述

### F3 — 日志降噪的生效条件较脆

`runtime.get_agentbay_sdk()` 用 `os.environ.setdefault("AGENTBAY_LOG_LEVEL", "WARNING")` 后再 `import agentbay`，思路正确（SDK 在 import 时读 env、且只 setup 一次）。但：
- `setdefault` 不覆盖已有值。SDK 自身会自动加载项目根 `.env`（调研报告已注明），若 `.env`/shell 里已是 `INFO`，降噪失效、F2 的 INFO 密钥行照样落盘。
- 若任何路径在首次 `get_agentbay_sdk()` 之前先 import 了 `agentbay`（其它模块、demo、交互式），文件 logger 已按 INFO 初始化，后续改不动。

建议：若项目意图是"始终降噪"，对该项用强制赋值而非 setdefault（或与 F2 方案 2 合并：改 `AGENTBAY_LOG_FILE` 比改级别更彻底）。当前 lazy-import 设计已尽量保证 runtime 是唯一首个 importer，可接受，但建议在代码注释里点明这一前提。

### F4 — 缺墙钟超时

`react/engine_helpers.execute_tool_calls` 不对单个工具调用包 `asyncio.wait_for`（已确认）。`run_code(timeout_s)` 与 playwright 导航 `timeout=timeout_ms` 有上限，但 **`agent_bay.create` / `agent_bay.delete` / `session.browser.initialize` / `get_endpoint_url`** 没有任何墙钟超时，一旦网关/网络卡住，`asyncio.to_thread` 虽不阻塞事件循环，但该工具调用会一直挂起、占用并发名额，云端 Session 也可能长期运行计费。调研报告自己的 P1 建议（"长任务增加超时，避免本地卡住导致云端 Session 长跑")在 create/delete/initialize 上未落实。

建议：对 `create_session`/`delete_session` 以及浏览器 initialize 用 `asyncio.wait_for` 包一层（超时后记录 Session ID 走 cleanup 脚本回收）。注意 `to_thread` 超时不会真正中断底层线程，但能交还控制权并触发兜底删除/告警。

### F5 — requirements 把可选重依赖钉进基础安装

```
wuying-agentbay-sdk==0.21.0
playwright==1.60.0
```
AgentBay 默认关闭、且代码已 lazy-import（实测 `import tools.agentbay` 不会拉起 SDK），但 `pip install -r requirements.txt` 仍会强制所有人（含 CI、从不用 AgentBay 的用户）安装这两个重依赖。硬 `==` 还可能与其它依赖产生约束冲突。

建议：移到可选 extras（如 `requirements-agentbay.txt` 或 `pyproject` 的 `[agentbay]` extra），README/变更记录注明"启用前先装"。补充一点：`connect_over_cdp` 连接的是远端浏览器，**无需 `playwright install` 本地浏览器二进制**，仅需 playwright Python 包——这点最好在文档写清，省得使用者误装数百 MB 浏览器。

---

## P3 / 工程项

### F6 — `concurrency_sem()` 重建逻辑语义错误

```python
if _sem is None or getattr(_sem, "_value", limit) > limit:
    _sem = asyncio.Semaphore(limit)
```
`_value` 是 Semaphore 的**当前可用许可数**（被 acquire 时会减小），拿它和"配置上限"比较语义不对：上限调小但有许可在用时未必触发重建；真触发重建时，旧 Semaphore 可能仍被在途持有 → 两个信号量并存，并发限制在过渡期失效。且依赖 CPython 私有属性 `_value`，脆。实际中 `AGENTBAY_MAX_CONCURRENT_SESSIONS` 来自 import 期 env、几乎不变，所以走的是 `_sem is None` 分支、稳定可用；但重建分支基本是"会引入竞态的死代码"。

建议：要么只 `if _sem is None`，要么单独缓存一个 `_sem_limit`，仅当 `limit != _sem_limit` 时重建，别读 `_value`。

### F7 — URL 校验重复实现

`browser_tool.validate_public_url` 与 `tool_guardrail.py` 里的 agentbay_browser 分支几乎是同一套 http(s)/localhost/.local/私有 IP 判断，两份独立维护，修了一处忘另一处就会漂移（F1 的修复尤其要两处同步）。建议 guardrail 直接复用 `validate_public_url`（或抽到 `tools/agentbay/url_guard.py` 共享），保留工具内自校验作为"guardrail 关闭时"的纵深防御即可。

### F8 — 删除成功的乐观默认

`delete_session`：`success = bool(getattr(result, "success", True))`；`cleanup_sessions`：`ok = getattr(delete_result, "success", True)`。已确认 `DeleteResult` 有 `success` 字段，所以当前正确；但在以"避免误扣费"为核心诉求的场景，缺字段时默认 `True`（汇报"已删")比默认 `False` 更危险——会把"可能残留的 Session"误报成已删。建议改为悲观默认 `False`，把不确定性暴露出来、促使跑 cleanup。`cleanup_sessions` 里 "Delete requested/failed" 文案也偏含糊，可拆成明确的成功/失败两态。

### F9 — 夹带无关重构

`main.py` 本次 diff 除 AgentBay 工具注册外，还把 `_discover_mcp_bridge_tools` 改为委托 `tools/mcp/discovery.py`（已确认该文件存在、功能等价）。这与 AgentBay 无关，变更记录也未提，混在一起不利于评审与回滚。建议拆分提交或在记录里说明。

### 记录在案（非问题）

- **截图仅返回本地路径**：`agentbay_browser` 截图存到 `SANDBOX_DIR/agentbay_screenshots/`（默认 `~/.manus_demo/sandbox/...`，在仓库外✓），但只回传路径、LLM 无法"看图"。这与本地 CodeExecutor/FileOps 一致，属 v1 合理限制。若后续 SANDBOX_DIR 被指到仓库内，记得一并 gitignore。
- **`agentbay_code` 未列入 `_UNTRUSTED_TOOLS`**：与本地 `execute_python` 保持一致（两者都是 LLM 自写代码的输出），符合既有约定。但注意云端代码若发起网络请求，stdout 可能含外部不可信内容——这点与本地一致、可接受，记录即可。
- **lazy-import / 降级路径**：确认 `import tools.agentbay` 不触发 SDK import；启用但缺 Key 时 `_build_tools` 跳过并告警（避免给 LLM 一个必失败的工具）；SDK 未装时 execute 抛错被 `except` 兜成 `Error:` 字符串。设计良好。
- **Session 清理兜底**：code/browser 两个工具都在 `finally` 删 Session 并把 `session_deleted` 回传，符合记录与调研报告要求。✓

---

## 正确性确认（与 SDK 实测一致）

- SDK 真实 API 全部对得上（见"评审方法"），不存在文档漂移导致运行期 `ImportError/TypeError` 的风险——特别是变更记录重点关注的 `LifecyclePolicy` 顶层导入与 `BrowserOption` 顶层导入均成立。
- 同步 SDK + `asyncio.to_thread` 包装正确；sync playwright 在 `to_thread` 线程内运行（避开事件循环线程）正确。
- guardrail 集成：`agentbay_code` 复用 `DANGEROUS_PYTHON_PATTERNS`、`agentbay_browser` 走 URL 校验、`agentbay_browser` 进入 `_UNTRUSTED_TOOLS` 注入中和链路——三处接线均正确，测试覆盖到 BLOCK/ALLOW。
- 不向 LLM 回传 `resource_url`/CDP endpoint/authcode（代码确实不返回 `endpoint_url`），符合安全要求。

## 处置建议（优先级顺序）

1. **F2**：删 `agentbay.log` + 加 `.gitignore` + 用 `AGENTBAY_LOG_FILE` 把日志移出仓库（先做，防止已存在的 authcode 被提交）。
2. **F1**：补全元数据/编码 IP 防护，并同步修正 F7（两处合一），同时校准对外措辞。
3. **F4 / F5 / F3**：补 create/delete 超时；可选依赖拆分；降噪改强制或改落盘路径。
4. **F6 / F8 / F9**：随手清理，非阻塞。

整体实现质量良好，方向与调研结论一致；处理完两个 P1 后建议合入。
