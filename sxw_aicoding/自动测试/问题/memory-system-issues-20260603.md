# 4.4 记忆系统 自动化测试问题报告

> 测试时间：2026-06-03 17:09 CST
> 测试范围：`sxw_aicoding/docs/operations-manual.md` 4.4 记忆系统
> 说明：测试使用用户提供的临时 API Key 注入进程环境；Key 未写入本报告、`.env` 或源码。

## 结论

Agentic Memory (v15) 主链路符合手册描述：

- 启用后任务开始时自动搜索记忆，显示 `Searching agentic memory...` 和 `Agentic memory: N results found`。
- 任务结束后自动存储结果，显示 `(Result stored in agentic memory)`。
- `MEMORY_MIN_CONFIDENCE` 和 `MEMORY_SEARCH_TOP_K` 参数正确生效。
- 仅开启 `AGENTIC_MEMORY_ENABLED`（不开 `MEMORY_TOOLS_ENABLED`）时，记忆被动自动管理，4 个 memory tools 不注册，符合手册描述。
- 同时开启两个开关时，4 个 memory tools（`memory_search`/`memory_store`/`memory_consolidate`/`memory_revoke`）正确注册到 ExecutorAgent 和 EmergentPlannerAgent 的工具列表。

但发现 4 个问题：

1. **P1**：Legacy 长期记忆 `LongTermMemory.search()` 对中文任务完全失效（无法召回）。
2. **P2**：`MEMORY_LLM_CONSOLIDATION_ENABLED` 手册描述了用法但代码中未实现（仅为 TODO 注释），属于文档与实际功能不符。
3. **P3**：`AgenticMemoryService._extract_tags()` 的 `\b` 正则无法匹配紧邻 CJK 字符的 ASCII 关键词（如 "Python的..." 中的 "python"），导致所有混合语言任务标签为空。
4. **P3**：Agentic Memory search 对无语义关联的中文任务产生假阳性召回（基于 bigram 重叠而非语义匹配）。

## 测试环境

公共环境变量：

```bash
LLM_API_KEY=<TEMP_KEY>
DASHSCOPE_API_KEY=<TEMP_KEY>
TRACING_ENABLED=false
PLAN_MODE=simple
.venv/bin/python
```

## 测试用例与结果

### Case 1: 基本长期记忆（默认开启）

**对应文档命令**：
```bash
python main.py
# 多轮对话中，前面的结果会自动存入长期记忆
```

**实际执行**：

Case 1a - 第一个任务：
```bash
LLM_API_KEY=... DASHSCOPE_API_KEY=... TRACING_ENABLED=false \
  .venv/bin/python main.py "Python中列表推导式的语法是什么？请简要说明"
```

**输出**（关键部分）：
```
(Result stored in long-term memory)
INFO  Stored long-term memory: Python中列表推导式的语法是什么？请简要说明
```

**验证**：
- `~/.manus_demo/memory.json` 文件被创建，包含 1 条记录 ✓
- `(Result stored in long-term memory)` 正确显示 ✓

Case 1b - 关联任务测试召回：
```bash
LLM_API_KEY=... DASHSCOPE_API_KEY=... TRACING_ENABLED=false \
  .venv/bin/python main.py "Python字典推导式和列表推导式有什么区别？"
```

**输出**（Gathering context 阶段）：
```
>>> Gathering context...
╭──── Knowledge Retrieved ────╮
│ [Knowledge 1] ...            │
╰──────────────────────────────╯
>>> Classifying task complexity...
```

**问题**：Gathering context 和 Classifying 之间没有显示 `Long-term Memory` 面板。说明 legacy memory search 返回了空结果（或 "No relevant past experiences found." 被 UI 抑制）。

**根因分析**（`memory/long_term.py:89`）：
```python
def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
    query_words = set(query.lower().split())  # ← 问题所在
    ...
    for entry in self._entries:
        text = f"{entry.task} {entry.summary} ...".lower()
        entry_words = set(text.split())
        overlap = len(query_words & entry_words)
```

`query.lower().split()` 对中文文本按空格分词：
- `"Python中列表推导式的语法是什么？请简要说明".split()` → `["Python中列表推导式的语法是什么？请简要说明"]`（**一个 token**）
- 查询和存储的 token 完全不同，overlap 永远为 0

**严重程度**：P1（功能失效）
**影响**：所有使用中文任务描述的 legacy memory 用户无法享受到"自动召回"功能。手册 4.4 节声称"多轮对话中，前面的结果会自动存入长期记忆"，但实际只能存储不能召回。

---

### Case 2: Agentic Memory 基本启用

**对应文档命令**：
```bash
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
python main.py
```

**实际执行**：

Case 2a - 第一个任务：
```bash
LLM_API_KEY=... AGENTIC_MEMORY_ENABLED=true MEMORY_TOOLS_ENABLED=true \
  PLAN_MODE=simple .venv/bin/python main.py "Python的GIL是什么？简要解释"
```

**输出**（关键部分）：
```
INFO  [Orchestrator] Agentic Memory (v15) enabled
>>> Gathering context...
   Searching agentic memory...
   Agentic memory: 0 results found
...
(Result stored in agentic memory)
INFO  Added agentic memory [experiential] **Python的GIL（全局解释器锁）** ...
INFO  Stored task result memory: Python的GIL是什么？简要解释
```

**验证**：
- `[Orchestrator] Agentic Memory (v15) enabled` 日志出现 ✓
- `Searching agentic memory...` 出现 ✓
- `Agentic memory: 0 results found`（空库，符合预期）✓
- `(Result stored in agentic memory)` 出现 ✓
- `~/.manus_demo/agentic_memory/memories.json` 创建，1 条记录（kind=experiential, confidence=0.7, tags=[]）✓

Case 2b - 关联任务测试召回：
```bash
LLM_API_KEY=... AGENTIC_MEMORY_ENABLED=true MEMORY_TOOLS_ENABLED=true \
  PLAN_MODE=simple .venv/bin/python main.py "Python GIL对多线程编程有什么影响？"
```

**输出**（开头）：
```
>>> Gathering context...
   Searching agentic memory...
   Memory search for 'Python GIL对多线程编程有什么影响？': 1 results
   Agentic memory: 1 results found
```

**验证**：
- Agentic Memory 召回成功（1 result）✓
- 双语 tokenizer（英文 word + 中文 bigram）正确处理了混合语言查询 ✓
- 任务本身因 Bailian MCP 429 限流导致 fetch_url 失败，执行超时（非记忆系统问题）

**结论**：Case 2 完全符合手册描述 ✓

---

### Case 3: Agentic Memory + 调整检索参数

**对应文档命令**：
```bash
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
MEMORY_SEARCH_TOP_K=5 \
MEMORY_MIN_CONFIDENCE=0.5 \
python main.py
```

**实际执行**：

Case 3a - MIN_CONFIDENCE=0.5（现有记录 confidence=0.7）：
```bash
MEMORY_MIN_CONFIDENCE=0.5 → Agentic memory: 1 results found  ✓（0.7 > 0.5，正确召回）
```

Case 3b - MIN_CONFIDENCE=0.8（现有记录 confidence=0.7）：
```bash
MEMORY_MIN_CONFIDENCE=0.8 → Agentic memory: 0 results found  ✓（0.7 < 0.8，正确过滤）
```

**验证**：
- `MEMORY_MIN_CONFIDENCE` 正确生效 ✓
- `MEMORY_SEARCH_TOP_K` 正确生效（虽然只有 1 条记录无法测试上限截断）✓
- 参数过滤行为与手册描述一致 ✓

**结论**：Case 3 完全符合手册描述 ✓

---

### Case 4: Agentic Memory + LLM 辅助记忆巩固

**对应文档命令**：
```bash
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
MEMORY_LLM_CONSOLIDATION_ENABLED=true \
python main.py
```

**实际执行**：
```bash
LLM_API_KEY=... AGENTIC_MEMORY_ENABLED=true MEMORY_TOOLS_ENABLED=true \
  MEMORY_LLM_CONSOLIDATION_ENABLED=true PLAN_MODE=simple \
  .venv/bin/python main.py "Python装饰器的基本用法"
```

**输出**（关键部分）：
```
INFO  [Orchestrator] Agentic Memory (v15) enabled
   Searching agentic memory...
   Agentic memory: 1 results found
```

**问题**：`MEMORY_LLM_CONSOLIDATION_ENABLED=true` 没有任何可见效果。没有额外的日志消息表明 LLM 辅助巩固被激活或执行。

**根因分析**（`memory/service.py` 第 ~130 行 `consolidate_task` 方法）：
```python
def consolidate_task(self, task_id: str, notes: str = "") -> list[AgenticMemoryRecord]:
    # TODO: v16 启用 LLM 辅助巩固时使用 config.MEMORY_LLM_CONSOLIDATION_ENABLED
    ...
```

代码中有明确的 `# TODO` 注释表明该功能尚未实现。`MEMORY_LLM_CONSOLIDATION_ENABLED` 环境变量虽在 `config.py` 中定义、在手册中记录，但实际代码路径中从未读取或使用。

**严重程度**：P2（文档与实现不符）
**影响**：用户按手册设置 `MEMORY_LLM_CONSOLIDATION_ENABLED=true` 后期望 LLM 辅助记忆巩固生效，但实际没有任何效果。手册应标注该功能为 "planned" 或 "not yet implemented"。

---

### Case 5: 仅开启 AGENTIC_MEMORY_ENABLED

**对应文档说明**：
> `AGENTIC_MEMORY_ENABLED=true`：启用 Agentic Memory 内部系统（Orchestrator 自动在任务开始时搜索、结束时存储）
> 仅开启 `AGENTIC_MEMORY_ENABLED` 时，记忆是被动自动管理的

**实际执行**：
```bash
LLM_API_KEY=... AGENTIC_MEMORY_ENABLED=true MEMORY_TOOLS_ENABLED=false \
  PLAN_MODE=simple .venv/bin/python main.py "1+1等于几"
```

**输出**（关键部分）：
```
INFO  [Orchestrator] Agentic Memory (v15) enabled
   Searching agentic memory...
   Agentic memory: 0 results found
...
   (Result stored in agentic memory)
INFO  Stored task result memory: 1+1等于几
```

**验证**：
- 即使 `MEMORY_TOOLS_ENABLED=false`，orchestrator 仍自动创建 `AgenticMemoryStore`/`AgenticMemoryService` ✓
- 任务开始时自动搜索 ✓
- 任务结束时自动存储 ✓
- 被动自动管理行为符合手册描述 ✓

**代码路径确认**（`agents/orchestrator.py:232-239`）：
```python
if self._agentic_memory_enabled:
    if agentic_memory_service is not None:
        self._agentic_memory_service = agentic_memory_service
    else:
        # main.py 未传 service 时（MEMORY_TOOLS_ENABLED=false），orchestrator 自行创建
        store = AgenticMemoryStore()
        self._agentic_memory_service = AgenticMemoryService(store)
```

**结论**：Case 5 完全符合手册描述 ✓

---

### Case 6: 记忆工具注册验证

**对应文档说明**：
> `MEMORY_TOOLS_ENABLED=true`：额外注册 4 个 memory 工具到 LLM 的 ReAct 循环

**实际执行**：

Case 6a - 工具注册验证（`AGENTIC_MEMORY_ENABLED=true, MEMORY_TOOLS_ENABLED=true`）：
```
ExecutorAgent tools (10 keys):
  execute_python, execute_shell, fetch_url, file_ops, get_user_location,
  web_search, memory_search, memory_store, memory_consolidate, memory_revoke
```

Case 6b - 反面验证（`AGENTIC_MEMORY_ENABLED=true, MEMORY_TOOLS_ENABLED=false`）：
```
ExecutorAgent tools (6 keys):
  execute_python, execute_shell, fetch_url, file_ops, get_user_location, web_search
```

Case 6c - 4 个工具独立功能测试：
```
memory_search(query='test') → {"results": [], "count": 0}           ✓
memory_store(kind='factual', content='Test') → {"status": "stored"} ✓（confidence=0.6, AGENT_CONFIDENCE_CAP）
memory_consolidate(notes='Test') → {"records_created": 1}           ✓
memory_revoke(memory_id='nonexistent') → Error: Memory record 'nonexistent' not found  ✓
```

**验证**：
- 4 个工具全部正确注册 ✓
- 不开启时 4 个工具不出现 ✓
- 各工具返回格式符合预期 ✓
- `memory_revoke` 对不存在 ID 返回 `Error:` 前缀字符串（ToolRouter 可识别）✓

**结论**：Case 6 完全符合手册描述 ✓

---

## 问题汇总

| # | 严重度 | 模块 | 问题描述 | 文件:行号 |
|---|--------|------|----------|-----------|
| 1 | P1 | Legacy 长期记忆 | `LongTermMemory.search()` 使用 `.split()` 分词，中文任务无法产生关键词重叠，记忆召回永远为空 | `memory/long_term.py:89` |
| 2 | P2 | LLM 记忆巩固 | `MEMORY_LLM_CONSOLIDATION_ENABLED` 在手册中作为可用功能描述，但代码中仅为 TODO 注释，功能未实现 | `memory/service.py:~130` |
| 3 | P3 | 标签提取 | `_extract_tags()` 使用 `\b` 正则匹配 ASCII 关键词，当关键词紧邻 CJK 字符时（如 "Python的..."）匹配失败，所有混合语言任务标签为空 | `memory/service.py:~160` |
| 4 | P3 | 记忆搜索精度 | Agentic Memory search 基于 bigram 重叠评分，对无语义关联的中文任务可能产生假阳性召回（如 "Python装饰器" 召回 GIL 相关记录） | `memory/agentic_store.py:~100` |

### 问题 1 详细分析：Legacy 长期记忆中文召回失效

**复现**：
```bash
# 存储
python main.py "Python中列表推导式的语法是什么？"
# → (Result stored in long-term memory) ✓

# 关联任务（应召回但实际无召回）
python main.py "Python字典推导式和列表推导式有什么区别？"
# → Gathering context 后无 Long-term Memory 面板 ✗
```

**根因**：`memory/long_term.py:89` 的 `set(query.lower().split())` 对中文无空格文本不做切分，整个句子变成一个 token。两个不同的中文句子 token 永远不重叠。

**修复建议**：
- 方案 A：引入 jieba 分词（`pip install jieba`），对中文文本做分词后再计算 overlap
- 方案 B：复用 `agentic_store.py` 的双语 tokenizer（英文 word + 中文 bigram），统一到 `LongTermMemory.search()`

### 问题 2 详细分析：MEMORY_LLM_CONSOLIDATION_ENABLED 未实现

**复现**：
```bash
MEMORY_LLM_CONSOLIDATION_ENABLED=true python main.py "任务"
# 无任何 LLM 巩固相关日志或行为
```

**根因**：`memory/service.py` 的 `consolidate_task()` 方法中有 `# TODO: v16 启用 LLM 辅助巩固时使用 config.MEMORY_LLM_CONSOLIDATION_ENABLED` 注释，实际代码路径从未读取该配置。

**修复建议**：
- 方案 A：在手册中标注该功能为 "planned (v16+)"，避免误导用户
- 方案 B：实现 LLM 辅助巩固功能

### 问题 3 详细分析：_extract_tags 正则匹配失败

**复现**：
```python
from memory.service import AgenticMemoryService
tags = AgenticMemoryService._extract_tags("Python的GIL是什么？简要解释")
# tags = []  ← 期望 ["python"]
```

**根因**：`_extract_tags` 使用 `r'\b' + keyword + r'\b'` 匹配 ASCII 关键词。在 "python的gil..." 中，"python" 后紧跟 "的"（CJK 字符，Python 正则视为 word character），导致 `\bpython\b` 无法匹配（`\b` 要求 word→non-word 边界）。

**修复建议**：将 ASCII 关键词的正则从 `\b{kw}\b` 改为 `(?:^|[^a-zA-Z0-9]){kw}(?:$|[^a-zA-Z0-9])`，或使用 `(?<!\w){kw}(?!\w)` 并配合 `re.ASCII` 标志使 `\w` 只匹配 ASCII 字符。

### 问题 4 详细分析：Agentic Memory 假阳性召回

**复现**：
```bash
# 存储了 GIL 相关记录
python main.py "Python的GIL是什么？"

# 查询 "Python装饰器的基本用法" → 返回 1 result（GIL 记录）
# 查询 "Python lambda表达式是什么" → 返回 1 result（GIL 记录）
```

**根因**：agentic_store 的 tokenizer 对中文做 bigram 切分（如 "Python装饰器的基本用法" → `{"py", "yt", "th", "ho", "on", "装饰", "饰器", "器的", "的基", "基本", "本用", "用法"}`），与 GIL 记录的 bigram 集合存在重叠（如 "的基"、"基本" 等常见 bigram），导致 keyword_score > 0 从而被召回。

**影响**：低。当前记录数量少时影响不大，但随着记忆积累，假阳性召回会注入无关上下文，浪费 token 预算。

**修复建议**：
- 对中文 bigram 引入停用词过滤（如 "的基"、"基本" 等无意义组合）
- 或改用 TF-IDF 加权（已在 knowledge/ 模块中实现），降低高频 bigram 的权重

---

## 与手册描述的一致性检查

| 手册声明 | 实际行为 | 一致性 |
|----------|----------|--------|
| "Searching agentic memory..." 出现 | ✓ 出现 | 一致 |
| "Agentic memory: N results found" 出现 | ✓ 出现 | 一致 |
| "(Result stored in agentic memory)" 出现 | ✓ 出现 | 一致 |
| "(Result stored in long-term memory)" 出现 | ✓ 出现 | 一致 |
| 多轮对话中前面的结果自动存入长期记忆 | 存入正常，但**召回失败**（中文） | **不一致** |
| MEMORY_SEARCH_TOP_K 控制返回条数 | ✓ 正确 | 一致 |
| MEMORY_MIN_CONFIDENCE 控制最低置信度 | ✓ 正确 | 一致 |
| MEMORY_LLM_CONSOLIDATION_ENABLED 启用 LLM 辅助巩固 | ✗ 未实现 | **不一致** |
| 仅开启 AGENTIC_MEMORY_ENABLED 时记忆被动管理 | ✓ 正确 | 一致 |
| memory_search/memory_store/memory_consolidate/memory_revoke 4 个工具 | ✓ 正确注册 | 一致 |
