# Shell Tool 核心缺陷修复与安全加固 — 代码评审报告

> **评审时间**: 2026-05-09  
> **评审对象**: 实施计划 `ultrathink-bugfix-shell-virtual-ritchie.md` 及对应代码实现  
> **变更文件**: `config.py`、`tools/subprocess_utils.py`（新建）、`tools/shell_tool.py`、`tools/code_executor.py`、`tests/test_shell_tool.py`

---

## 一、变更背景

`tools/shell_tool.py` 存在 3 个 P0 级正确性 Bug 和多个安全问题：

| 级别 | 问题 | 描述 |
|------|------|------|
| **P0** | 双重超时不一致 | `subprocess.run(timeout=T)` + `asyncio.wait_for(timeout=T)` 两层超时竞争，行为不可预测 |
| **P0** | 子进程泄漏 | `run_in_executor` 中 `subprocess.run` 超时后，线程池中的进程无法被 kill |
| **P0** | `timeout=0` 被吞掉 | `kwargs.get("timeout") or config.SHELL_EXEC_TIMEOUT` 中 `0` 被视为 falsy |
| **P1** | 环境变量泄露 | 子进程继承完整 `os.environ`，包含 API Key 等敏感信息 |
| **P1** | 无输出限制 | 恶意命令可产生无限输出导致内存耗尽 |
| **P2** | 黑名单薄弱 | 缺少提权、网络攻击、系统修改等命令拦截；`format` 规则误报 |

修复方案：将核心的 `subprocess.run + run_in_executor` 替换为 `asyncio.create_subprocess_exec`，一举解决所有 P0 问题，并叠加安全加固。

---

## 二、逐模块评审

### Phase 1: `config.py` — ✅ 完全符合计划

新增 3 个配置项：

```python
SHELL_MAX_OUTPUT_BYTES = int(os.getenv("SHELL_MAX_OUTPUT_BYTES", str(512 * 1024)))  # 512KB
SHELL_MAX_CONCURRENT = int(os.getenv("SHELL_MAX_CONCURRENT", "3"))
CODE_MAX_CONCURRENT = int(os.getenv("CODE_MAX_CONCURRENT", "3"))
```

**评审结论**: 位置正确（`# --- Tools ---` 区域）、命名规范、注释风格与现有配置一致。**无问题。**

---

### Phase 2: `tools/subprocess_utils.py` — ⚠️ 有 2 个中等问题

#### 2.1 `build_safe_env()` — ✅

deny-list 过滤 5 类敏感环境变量（`API_KEY`、`SECRET`、`TOKEN`、`PASSWORD`、`CREDENTIAL`），预编译正则，性能良好。

#### 2.2 `SubprocessResult` dataclass — ✅

字段完备（`stdout`、`stderr`、`returncode`），设计简洁。

#### 2.3 ⚠️ 问题 #1：`run_with_limits` 异常捕获遗漏 `CancelledError`

**位置**: `subprocess_utils.py` 第 89 行

```python
except (asyncio.TimeoutError, Exception):
    proc.kill()
    await proc.wait()
    raise
```

**问题分析**:
- Python 3.9+ 中 `asyncio.CancelledError` 继承自 `BaseException` 而非 `Exception`
- 当外层 `asyncio.Task` 被取消时，此 `except` 无法捕获 `CancelledError`
- 子进程 `proc` 不会被 kill，**可能导致孤儿进程泄漏**
- 另外 `Exception` 已经包含 `TimeoutError`（Python 3.11+），写法冗余

**建议修复**:

```python
except BaseException:
    proc.kill()
    await proc.wait()
    raise
```

**严重度**: 🟡 中 — 涉及进程泄漏，生产环境可能造成实际影响

#### 2.4 ⚠️ 问题 #2：正常路径缺少 `await proc.wait()`

**位置**: `subprocess_utils.py` 第 91-97 行

```python
stdout_bytes, stderr_bytes = await asyncio.wait_for(
    _read_with_limit(proc, max_output_bytes),
    timeout=timeout,
)
# ← 此处缺少 await proc.wait()
return SubprocessResult(
    stdout=..., stderr=...,
    returncode=proc.returncode if proc.returncode is not None else -1,
)
```

**问题分析**:
- `_read_with_limit` 读完 stdout/stderr 后，`proc` 可能尚未完全退出
- `proc.returncode` 可能为 `None`，fallback `-1` 语义不正确（-1 通常表示被信号终止）
- 依赖 "管道关闭即进程退出" 的隐式行为不够稳健

**建议修复**:

```python
stdout_bytes, stderr_bytes = await asyncio.wait_for(
    _read_with_limit(proc, max_output_bytes),
    timeout=timeout,
)
await proc.wait()  # 确保进程完全退出，returncode 被正确设置
return SubprocessResult(...)
```

**严重度**: 🟡 中 — 影响返回值正确性

#### 2.5 `_read_with_limit` — 已知限制（可接受）

`total` 和 `truncated` 通过 `nonlocal` 在两个并发 coroutine 间共享。虽然 asyncio 是单线程的，但两个流交替执行时 `total` 可能短暂超过 `max_bytes`（最多超一个 chunk = 8KB）。属于边界条件，实际影响极小，建议补充注释说明。

---

### Phase 3: `tools/shell_tool.py` — ✅ 整体优秀

#### 3.1 P0 修复 — 双重超时 ✅

- 完全移除 `subprocess.run` + `run_in_executor` + `get_event_loop()` 反模式
- 超时统一由 `run_with_limits` 内的 `asyncio.wait_for` 管理
- `timeout=0` 修复：`kwargs.get("timeout") if kwargs.get("timeout") is not None else ...`

#### 3.2 P1 修复 — 安全加固 ✅

- `build_safe_env()` 传入子进程
- `SHELL_MAX_OUTPUT_BYTES` 限制输出

#### 3.3 黑名单改进 ✅

- ✅ 移除了误报严重的 `format` 规则
- ✅ 新增 `sudo`/`su`/`pkexec`（提权）
- ✅ 新增 `curl|sh`/`wget|sh`/`nc -e`/`ncat -e`（网络攻击）
- ✅ 新增 `systemctl`/`service`/`crontab`/`launchctl`（系统修改）
- ✅ 新增 `printenv`/`export.*API_KEY`（凭证泄露）

#### 3.4 并发控制 ✅

`asyncio.Semaphore` + `_get_sem()` 延迟初始化，避免模块加载时无 event loop 的问题。

#### ⚠️ 小问题：黑名单可绕过

| 规则 | 绕过方式 | 风险 |
|------|---------|------|
| `\bsu\b\s+` | 无参 `su`（直接切 root） | 低（沙箱环境） |
| 缺少 `\benv\b` | `env` 命令可打印环境变量 | 低（`build_safe_env` 已清理） |

---

### Phase 4: `tools/code_executor.py` — ✅ 完全符合计划

与旧版对比，改动精准：

- ✅ 移除 `subprocess.run` + `run_in_executor` + `get_event_loop()`
- ✅ 改用 `run_with_limits`
- ✅ 添加并发控制 `_concurrency_sem` + `_get_sem()`
- ✅ 传入 `build_safe_env()`

#### 小瑕疵：复用 `SHELL_MAX_OUTPUT_BYTES`

`code_executor.py` 中 `max_output_bytes=config.SHELL_MAX_OUTPUT_BYTES` 语义不够清晰。建议在 `config.py` 增加 `CODE_MAX_OUTPUT_BYTES = SHELL_MAX_OUTPUT_BYTES` 作为语义别名。

---

### Phase 5: `tests/test_shell_tool.py` — ✅ 覆盖全面

#### 计划要求的 7 个新测试全部实现

| 测试用例 | 验证点 | 状态 |
|---------|--------|------|
| `test_env_sanitized` | `echo $LLM_API_KEY` 不泄露密钥 | ✅ |
| `test_output_truncation` | 1KB 限制下 2KB 输出被截断 | ✅ |
| `test_timeout_zero` | `timeout=0` 不崩溃 | ✅ |
| `test_no_orphan_on_timeout` | `sleep 60` 超时后无残留进程 | ✅ |
| `test_curl_pipe_sh_blocked` | `curl ... \| sh` 被拦截 | ✅ |
| `test_format_not_blocked` | `echo "format test"` 不被误拦 | ✅ |
| `test_concurrency_limit` | semaphore=1 时并发命令正常 | ✅ |

#### 额外补充的测试

- `test_printenv_blocked`、`test_systemctl_blocked`、`test_sudo_blocked` — 新黑名单规则验证
- `test_git_format_not_blocked` — git format 参数不被误拦

#### ⚠️ 注意点

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | `test_env_sanitized` 直接操作 `os.environ`，建议改用 pytest `monkeypatch` fixture | 低 |
| 2 | `test_concurrency_limit` 只验证"都成功"，未验证排队行为；`import unittest.mock` 未使用 | 低 |

---

## 三、评审总结

### 🟢 优点

1. **P0 Bug 全部修复** — 双重超时、子进程泄漏、`timeout=0` 三个核心缺陷均已正确解决
2. **架构设计合理** — 抽取 `subprocess_utils.py` 为共享模块，`ShellTool` 和 `CodeExecutorTool` 复用同一套逻辑，符合 DRY 原则
3. **asyncio 原生实现** — 彻底移除 `get_event_loop()` + `run_in_executor` 反模式，改用 `asyncio.create_subprocess_exec`
4. **代码风格一致** — 中英文双语注释、模块文档、命名规范与项目现有风格完全统一
5. **测试覆盖全面** — 7 个计划内新测试 + 多个额外测试，且废弃 API 已全部清理
6. **计划执行度高** — 5 个 Phase 全部按计划完成，无遗漏项

### 🟡 建议改进项

| # | 文件 | 问题 | 严重度 | 建议 |
|---|------|------|--------|------|
| 1 | `subprocess_utils.py:89` | `except` 未捕获 `CancelledError`，取消场景可能泄漏子进程 | **中** | 改为 `except BaseException` |
| 2 | `subprocess_utils.py:94` | 正常路径缺少 `await proc.wait()`，`returncode` 可能不正确 | **中** | 构建 Result 前加 `await proc.wait()` |
| 3 | `code_executor.py:88` | 复用 `SHELL_MAX_OUTPUT_BYTES` 命名混淆 | **低** | 增加 `CODE_MAX_OUTPUT_BYTES` 别名 |
| 4 | `shell_tool.py:41` | `\bsu\b\s+` 不匹配无参 `su`；缺少 `env` 拦截 | **低** | 调整正则；增加 `\benv\b` |
| 5 | `test_shell_tool.py:152` | 手动操作 `os.environ` 不如 `monkeypatch` 安全 | **低** | 使用 pytest `monkeypatch` |
| 6 | `test_shell_tool.py:197` | 并发测试未验证排队行为；无用 import | **低** | 增强验证或清理 import |

### 🔴 无严重（Blocker）问题

整体实现质量很高，核心修复全部到位。**建议优先处理 #1 和 #2 两个中等严重度的问题**，它们分别涉及进程泄漏和返回值正确性，在高并发或任务取消场景下可能造成实际影响。

---

## 四、新旧代码关键对比

### 旧版核心问题代码（已删除）

```python
# ❌ 旧版 shell_tool.py — 双重超时 + 子进程泄漏
async def execute(self, **kwargs):
    timeout = kwargs.get("timeout") or config.SHELL_EXEC_TIMEOUT  # ← timeout=0 被吞
    result = await asyncio.wait_for(          # ← 外层超时
        self._run_shell(command, timeout),
        timeout=timeout,
    )

async def _run_shell(self, command, timeout):
    loop = asyncio.get_event_loop()           # ← 废弃 API
    result = await loop.run_in_executor(      # ← 线程池中子进程无法 kill
        None,
        lambda: subprocess.run(               # ← 内层超时
            ["bash", "-c", command],
            timeout=timeout,                  # ← 双重超时竞争
        ),
    )
```

### 新版修复代码

```python
# ✅ 新版 shell_tool.py — 单一超时 + asyncio 原生
async def execute(self, **kwargs):
    timeout = (
        kwargs.get("timeout")
        if kwargs.get("timeout") is not None    # ← 正确处理 timeout=0
        else config.SHELL_EXEC_TIMEOUT
    )
    async with self._get_sem():                 # ← 并发控制
        return await self._run_shell(command, timeout)

async def _run_shell(command, timeout):
    result = await run_with_limits(             # ← asyncio 原生，单一超时
        cmd=["bash", "-c", command],
        env=build_safe_env(),                   # ← 环境变量清理
        max_output_bytes=config.SHELL_MAX_OUTPUT_BYTES,  # ← 输出限制
    )
```
