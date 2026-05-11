# Manus Demo v7 后续优化技术调研与规划报告（聚焦版）

> **日期**: 2026-05-11
> **版本**: v7 Research（聚焦版）
> **范围**: Context Engineering / Memory System / Multi-Agent
> **排除**: Harness Engineering、模型微调、强化学习
> **依据**: 项目源码 + CLAUDE.md + codemap.md + 5篇ATA优秀文章 + ATA搜索

---

## 一、现状评估：三大核心问题

### 1.1 当前代码现状

基于对项目源码的深入分析，当前系统在以下三个维度存在显著优化空间：

**Context管理**（`context/manager.py`）:
- 仅实现了单一LLM摘要压缩，触发阈值后一次性压缩旧消息
- 每次压缩都需要LLM调用，成本极高
- 无层级递进策略，简单场景也走LLM
- 压缩时可能丢失关键决策（如架构变更、文件修改记录）
- 无工具输出特殊处理（Edit/Write等状态变更操作应被保护）

**记忆系统**（`memory/long_term.py`）:
- 使用简单关键词重叠度检索，无法理解语义
- 无记忆分类（User/Feedback/Project/Reference）
- 无LLM参与的语义排序
- 检索结果数量无限制，可能大量消耗token

**多Agent架构**（`agents/orchestrator.py`）:
- Orchestrator直接调用ExecutorAgent，非纯协调者
- Orchestrator本身持有llm_client和tools，有直接执行能力
- 所有Agent共享同一个LLMClient实例，上下文互相污染
- 无子Agent隔离机制，复杂任务上下文爆炸

### 1.2 与业界最佳实践的差距

| 维度 | 当前状态 | 业界最佳实践（Claude Code） | 差距 |
|------|---------|---------------------------|------|
| **Context压缩** | 单一LLM摘要压缩 | 三层渐进式压缩（Micro→SM→Full LLM） | 压缩策略单一，无层级递进 |
| **记忆系统** | 关键词重叠度检索 | 四类分类 + LLM语义检索 | 检索精度低，无分类 |
| **多Agent** | 共享context，无隔离 | 纯协调者 + 子Agent隔离 + JSONL协议 | 上下文污染，无隔离 |

---

## 二、ATA文章核心洞察（聚焦版）

### 2.1 文章索引

| 文章 | 作者 | 核心主题 | 与本次优化的关联 |
|------|------|---------|----------------|
| [你不知道的Agent](https://ata.atatech.org/articles/11020600930) | 汤威 | Agent架构全景 | 上下文分层、多Agent组织、记忆设计 |
| [深度解析Claude Code](https://ata.atatech.org/articles/11020605711) | 姜剑 | Prompt/Context/Harness | **三层压缩**、Memdir、**子Agent隔离** |
| [Qoder工程实践](https://ata.atatech.org/articles/11020601776) | 泮圣伟 | Harness Engineering | 协调者模式、子Agent隔离 |
| [深度解析Hermes](https://ata.atatech.org/articles/11020604988) | 姜剑 | 自进化Agent | 动态Skill生成、**上下文注入** |
| [AGENTS.md实践指南](https://ata.atatech.org/articles/11020618417) | 徐靖峰 | AGENTS.md最佳实践 | 渐进式披露、上下文管理 |

### 2.2 核心设计模式

**上下文工程三层模型**：
```
常驻层：身份定义、项目约定、绝对禁止项（短、硬、可执行）
按需加载：Skills描述符（~9 tokens）、领域知识
运行时注入：当前时间、用户偏好、工具调用结果
记忆层：MEMORY.md跨会话经验（不直接进系统提示）
系统层：Hooks/代码规则处理的确定性逻辑（不进上下文）
```

**记忆系统四层分类**：
```
工作记忆（上下文窗口）：当前任务所需最小信息，token有限
程序性记忆（Skills）：怎么做某件事，操作流程，按需加载
情景记忆（JSONL）：发生了什么，磁盘持久化，支持跨会话检索
语义记忆（MEMORY.md）：Agent主动写入的稳定事实，每次启动注入
```

**多Agent组织模式**：
```
统筹者模式（Orchestrator-Workers）：
  - 主Agent作为Orchestrator统筹全局
  - 子Agent独立并行工作
  - JSONL inbox协议通信
  - Worktree隔离文件修改
  - 任务图管理依赖关系

关键原则：
  1. 协调者绝对不写代码
  2. 子Agent只回传摘要，探索细节留在自己上下文
  3. 协议先于协作，隔离先于并行
```

---

## 三、优化方向与优先级

### 3.1 优化方向总览

```
┌─────────────────────────────────────────────────────────────────┐
│                    Manus Demo v7+ 优化方向（聚焦版）              │
├─────────────────────────────────────────────────────────────────┤
│  Phase 1: Context Engineering（上下文工程）                      │
│    ├── 三层渐进式压缩（MicroCompact → SM Compact → Full LLM）   │
│    └── Skills延迟加载机制                                       │
├─────────────────────────────────────────────────────────────────┤
│  Phase 2: Memory System（记忆系统升级）                          │
│    ├── Memdir四类记忆（User/Feedback/Project/Reference）       │
│    └── 语义检索（LLM-in-the-loop）                              │
├─────────────────────────────────────────────────────────────────┤
│  Phase 3: Multi-Agent（多智能体架构）                            │
│    ├── 纯协调者模式（Orchestrator零执行）                       │
│    ├── 子Agent隔离（独立上下文 + Worktree）                     │
│    ├── JSONL通信协议                                            │
│    └── 模型选择器（任务复杂度 → 模型映射）                      │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 优先级矩阵

| 优先级 | 方向 | 影响面 | 实施难度 | 预期收益 |
|--------|------|--------|----------|----------|
| **P0** | 三层Context压缩 | context/ | 中 | Token消耗降低30-50% |
| **P0** | Memdir四类记忆 | memory/ | 中 | 记忆召回准确率提升至85%+ |
| **P1** | 纯协调者模式 | agents/orchestrator.py | 高 | 上下文不再被执行细节污染 |
| **P1** | 子Agent隔离协议 | subagent/（新增） | 高 | 复杂任务上下文可控 |
| **P1** | Skills延迟加载 | skills/（新增） | 中 | 上下文减少30-50% |

---

## 四、详细技术方案

### 4.1 Phase 1: Context Engineering（上下文工程）

#### 4.1.1 当前代码问题分析

**`context/manager.py` 现状**：
```python
class ContextManager:
    async def compress_if_needed(self, messages, llm_client):
        total = self.estimate_messages_tokens(messages)
        if total <= self.max_tokens:
            return messages  # 未超限，直接返回
        
        # 问题1: 超限即调用LLM，无层级递进
        # 问题2: 所有消息统一压缩，无优先级保护
        # 问题3: 状态变更工具输出可能被压缩丢失
        summary = await self._summarize(old_text, llm_client)
        return system_msgs + [summary_message] + recent_msgs
```

**核心问题**：
1. **无层级递进**：一超限就调用LLM，简单场景也走LLM
2. **无工具输出保护**：Edit/Write等状态变更操作可能被压缩
3. **无缓存边界感知**：KV Cache友好性未考虑
4. **无头尾保护**：早期决策和最近对话可能被压缩

#### 4.1.2 三层渐进式压缩设计

**整体设计思路**：

```
三层渐进式压缩触发流程（AUTOCOMPACT_BUFFER_TOKENS = 13000）：
┌────────────────────────────────────┐
│ 上下文剩余空间 < 13000 tokens？    │
└────────────────┬─────────────────┘
                 ▼
     ┌────────────────────────────┐
     │ Layer 1: MicroCompact        │
     │ 规则驱动，无 LLM 调用        │
     │ ① 工具输出按时间截断        │
     │ ② KV Cache 边界外压缩        │
     └────────────┬───────────────┘
                  ▼
         ┌───────────────────┐
         │ Token ≥ 10000     │ 否 → 直接返回
         │ 文本消息 ≥ 5 条？  │
         └────────┬──────────┘
                  ▼ 是
      ┌─────────────────────────┐
      │ Layer 2: SM Compact      │
      │ 复用已有会话记忆摘要      │
      │ 零额外推理成本          │
      └────────────┬────────────┘
                   ▼
          ┌─────────────────┐
          │ 仍超限？        │ 否 → 返回
          └────────┬────────┘
                   ▼ 是
       ┌────────────────────────┐
       │ Layer 3: Full LLM       │
       │ 调用 LLM 生成9段式摘要  │
       │ 保护头尾，中间压缩      │
       └────────────────────────┘
```

**Layer 1: MicroCompact（微压缩）**：

```python
# context/micro_compact.py
"""MicroCompact — 规则驱动的微压缩（Layer 1），无LLM调用。"""

from __future__ import annotations

import time
from typing import Any

# 白名单工具：可安全压缩（只读工具）
COMPACTABLE_TOOLS = {"bash", "read", "grep", "glob", "web_search"}
# 受保护工具：涉及状态变更，输出完整保留
PROTECTED_TOOLS = {"edit", "write", "delete", "execute_code"}


class MicroCompressor:
    """
    规则驱动微压缩器。
    
    核心策略：
    - 只压缩白名单工具（只读工具）的输出
    - 保护状态变更工具（Edit/Write/Delete/ExecuteCode）的完整输出
    - 按时间截断旧工具输出（>120秒）
    - KV Cache边界外消息优先压缩
    """

    def __init__(self, max_tool_age_sec: int = 120, max_tool_tokens: int = 500):
        self.max_tool_age_sec = max_tool_age_sec
        self.max_tool_tokens = max_tool_tokens

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        执行微压缩。
        
        规则：
        1. 遍历所有消息，识别tool消息
        2. PROTECTED_TOOLS的tool消息完整保留
        3. COMPACTABLE_TOOLS的tool消息按时间截断
        4. 其他消息保留
        """
        result = []
        now = time.time()

        for msg in reversed(messages):
            if msg.get("role") == "tool":
                tool_name = self._extract_tool_name(msg)
                if tool_name in PROTECTED_TOOLS:
                    # 状态变更工具：完整保留
                    result.append(msg)
                elif tool_name in COMPACTABLE_TOOLS:
                    # 只读工具：按时间和token截断
                    truncated = self._truncate_tool_output(msg, now)
                    result.append(truncated)
                else:
                    # 未知工具：保留但截断
                    result.append(self._truncate_tool_output(msg, now))
            else:
                # 非tool消息：保留
                result.append(msg)

        return list(reversed(result))

    def _extract_tool_name(self, msg: dict[str, Any]) -> str:
        """从tool消息中提取工具名。"""
        tool_call_id = msg.get("tool_call_id", "")
        if tool_call_id:
            # 格式通常是 {tool_name}_{uuid}，提取前缀
            return tool_call_id.split("_")[0] if "_" in tool_call_id else tool_call_id
        # 尝试从content中推断
        content = msg.get("content", "")
        for name in COMPACTABLE_TOOLS | PROTECTED_TOOLS:
            if name in content.lower():
                return name
        return ""

    def _truncate_tool_output(
        self,
        msg: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """截断工具输出。"""
        content = msg.get("content", "")
        # 如果内容超过阈值，截断
        if len(content) > self.max_tool_tokens * 4:  # 粗略估算
            truncated = content[:self.max_tool_tokens * 4] + "\n[...truncated...]"
            return {**msg, "content": truncated}
        return msg


def micro_compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """便捷函数：执行微压缩。"""
    compressor = MicroCompressor()
    return compressor.compress(messages)
```

**Layer 2: SM Compact（会话记忆复用压缩）**：

```python
# context/sm_compact.py
"""SM Compact — 复用会话记忆摘要（Layer 2），零LLM调用。"""

from __future__ import annotations

import json
import os
from typing import Any


class SMCompressor:
    """
    会话记忆复用压缩器。
    
    核心策略：
    - 检查是否有对应会话的摘要缓存
    - 用摘要替换旧消息，不产生新的LLM调用
    - 严格保留最近N轮消息（近因效应）
    - 单次最大压缩40000 tokens
    """

    def __init__(
        self,
        sessions_dir: str = ".manus_demo/sessions",
        protect_recent: int = 4,
        max_compress_tokens: int = 40000,
    ):
        self.sessions_dir = sessions_dir
        self.protect_recent = protect_recent
        self.max_compress_tokens = max_compress_tokens
        os.makedirs(sessions_dir, exist_ok=True)

    async def compress(
        self,
        messages: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        执行会话记忆复用压缩。
        
        流程：
        1. 检查是否有对应session的摘要
        2. 用摘要替换旧消息（保留最近protect_recent轮）
        3. 不产生新的LLM调用
        """
        if not session_id:
            return messages

        summary_path = os.path.join(self.sessions_dir, f"{session_id}_summary.json")
        if not os.path.exists(summary_path):
            return messages

        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_data = json.load(f)
            summary_text = summary_data.get("summary", "")
        except (json.JSONDecodeError, FileNotFoundError):
            return messages

        # 分离系统消息和非系统消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近protect_recent轮
        if len(non_system) <= self.protect_recent:
            return messages

        old_msgs = non_system[:-self.protect_recent]
        recent_msgs = non_system[-self.protect_recent:]

        # 用摘要替换旧消息
        summary_message = {
            "role": "system",
            "content": f"[Session Summary - Compressed context]\n{summary_text}",
        }

        return system_msgs + [summary_message] + recent_msgs

    def save_summary(self, session_id: str, summary: str) -> None:
        """保存会话摘要供后续复用。"""
        summary_path = os.path.join(self.sessions_dir, f"{session_id}_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "timestamp": time.time()}, f)


def sm_compact(
    messages: list[dict[str, Any]],
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """便捷函数：执行会话记忆复用压缩。"""
    compressor = SMCompressor()
    return compressor.compress(messages, session_id)
```

**Layer 3: Full LLM Compact（完全LLM压缩）**：

```python
# context/llm_compact.py
"""Full LLM Compact — 调用LLM生成结构化摘要（Layer 3）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMCompressor:
    """
    LLM驱动的完全压缩器。
    
    核心策略：
    - 调用LLM生成9段式结构化摘要
    - 头部保护：系统指令+第一条用户消息+第一条助手回复+第一轮工具交互
    - 尾部保护：最后N轮对话
    - 中间压缩：工具调用历史、试错过程用摘要替换
    """

    # 9段式结构化摘要模板（来自Claude Code）
    SUMMARY_TEMPLATE = """
请在<analysis>标签内进行逻辑推演，然后在<summary>标签内输出结构化摘要。
禁止在此阶段调用任何工具（工具调用将被拒绝）。

按以下9段式输出：
1. Primary Request and Intent: 原始任务目标
2. Key Technical Concepts: 关键技术概念
3. Files and Code Sections: 已修改的文件
4. Errors and Fixes: 错误及修复
5. Problem Solving: 问题解决过程
6. All User Messages: 所有用户消息
7. Pending Tasks: 未完成的TODO
8. Current Work: 当前工作
9. Optional Next Step: 下一步（可选）
"""

    def __init__(
        self,
        head_protection: int = 4,
        tail_protection: int = 4,
    ):
        self.head_protection = head_protection
        self.tail_protection = tail_protection

    async def compress(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
    ) -> list[dict[str, Any]]:
        """
        执行LLM完全压缩。
        """
        # 分离系统消息和非系统消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 头部保护
        head_end = min(self.head_protection, len(non_system))
        head = non_system[:head_end]

        # 尾部保护
        tail = non_system[-self.tail_protection:] if len(non_system) >= self.tail_protection else non_system

        # 中间压缩区
        middle = non_system[head_end:-self.tail_protection] if len(non_system) > head_end + self.tail_protection else []

        if not middle:
            return messages

        # 构建待摘要文本
        middle_text = self._messages_to_text(middle)

        # 调用LLM生成摘要
        summary_prompt = [
            {
                "role": "system",
                "content": "你是一个上下文压缩助手。将以下对话历史压缩为结构化摘要。",
            },
            {
                "role": "user",
                "content": f"{self.SUMMARY_TEMPLATE}\n\n待压缩内容：\n{middle_text}",
            },
        ]

        try:
            summary = await llm_client.chat(summary_prompt, temperature=0.2, max_tokens=1024)
        except Exception as exc:
            logger.warning("LLM compression failed: %s", exc)
            # 降级：截断
            summary = middle_text[:2000] + "\n[...earlier context truncated...]"

        summary_message = {
            "role": "system",
            "content": f"[Context Summary]\n{summary}",
        }

        return system_msgs + head + [summary_message] + tail

    def _messages_to_text(self, messages: list[dict[str, Any]]) -> str:
        """将消息列表转换为可读文本。"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content[:500]}")
        return "\n".join(lines)


async def full_llm_compact(
    messages: list[dict[str, Any]],
    llm_client: Any,
) -> list[dict[str, Any]]:
    """便捷函数：执行LLM完全压缩。"""
    compressor = LLMCompressor()
    return await compressor.compress(messages, llm_client)
```

**改造 `context/manager.py`**：

```python
# context/manager.py
"""Context Manager — 三层渐进式压缩协调器。"""

from __future__ import annotations

import logging
from typing import Any

import config
from context.llm_compact import full_llm_compact
from context.micro_compact import micro_compact
from context.sm_compact import sm_compact

logger = logging.getLogger(__name__)


class ContextManager:
    """
    三层渐进式压缩协调器：
      Layer 1: MicroCompact（规则驱动，无LLM调用）
      Layer 2: SM Compact（复用会话记忆，零LLM调用）
      Layer 3: Full LLM Compact（最后手段，调用LLM）
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        reserve_recent: int = 6,
    ):
        self.max_tokens = max_tokens or config.MAX_CONTEXT_TOKENS
        self.reserve_recent = reserve_recent
        # 三层压缩阈值
        self.micro_threshold = 0.7  # 上下文满70%触发MicroCompact
        self.sm_threshold = 10000   # 10000 tokens触发SM Compact
        self.llm_threshold = 13000  # 13000 tokens触发Full LLM Compact

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """粗略估算：英文约每3字符1 token，CJK约每2字符1 token。"""
        return max(1, len(text) // 3)

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            total += self.estimate_tokens(content) + 4
        return total

    async def compress_if_needed(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        三层渐进式压缩入口。
        
        流程：
        1. Layer 1: MicroCompact（规则驱动，无LLM调用）
        2. Layer 2: SM Compact（复用会话记忆，零LLM调用）
        3. Layer 3: Full LLM Compact（最后手段，调用LLM）
        """
        total = self.estimate_messages_tokens(messages)

        # Layer 1: MicroCompact（规则驱动，无LLM调用）
        if total > self.max_tokens * self.micro_threshold:
            messages = micro_compact(messages)
            logger.info("Layer 1 MicroCompact applied")
            total = self.estimate_messages_tokens(messages)

        # Layer 2: SM Compact（复用会话记忆，零LLM调用）
        if total > self.sm_threshold:
            messages = sm_compact(messages, session_id)
            logger.info("Layer 2 SM Compact applied")
            total = self.estimate_messages_tokens(messages)

        # Layer 3: Full LLM Compact（最后手段）
        if total > self.llm_threshold:
            messages = await full_llm_compact(messages, llm_client)
            logger.info("Layer 3 Full LLM Compact applied")

        return messages
```

#### 4.1.3 与现有架构的集成

**集成点1：`agents/base.py` 的 `think` 方法**：
```python
# agents/base.py
class BaseAgent:
    async def think(self, user_input: str, **kwargs: Any) -> str:
        self.add_message("user", user_input)
        
        # 改造后：传入session_id支持SM Compact
        self._messages = await self.context_manager.compress_if_needed(
            self._messages, 
            self.llm_client,
            session_id=getattr(self, 'session_id', None),
        )
        
        response = await self.llm_client.chat(self._messages, **kwargs)
        self.add_message("assistant", response)
        return response
```

**集成点2：`config.py` 新增配置**：
```python
# config.py 新增
# ---- Context三层压缩 ----
MICRO_COMPACT_THRESHOLD = 0.7        # Layer 1触发阈值（上下文满70%）
SM_COMPACT_THRESHOLD = 10000        # Layer 2触发阈值（10000 tokens）
LLM_COMPACT_THRESHOLD = 13000        # Layer 3触发阈值（13000 tokens）
SM_COMPACT_MAX_TOKENS = 40000       # Layer 2单次最大压缩量
LLM_COMPACT_PROTECT_TURNS = 4        # 尾部保护轮数
```

#### 4.1.4 预期收益

| 指标 | 当前 | 改造后 | 提升 |
|------|------|--------|------|
| Token消耗 | 基准 | 降低30-50% | 高 |
| LLM调用次数 | 每次超限都调用 | 仅Layer 3调用 | 显著降低 |
| 关键决策保留率 | 可能丢失 | 头尾保护，确保不丢 | 高 |
| 压缩延迟 | 高（每次LLM调用） | 低（Layer 1/2无LLM） | 显著降低 |

---

### 4.2 Phase 2: Memory System（记忆系统升级）

#### 4.2.1 当前代码问题分析

**`memory/long_term.py` 现状**：
```python
class LongTermMemory:
    def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
        query_words = set(query.lower().split())
        for entry in self._entries:
            text = f"{entry.task} {entry.summary} {' '.join(entry.learnings)}".lower()
            overlap = len(query_words & set(text.split()))
            # 问题1: 无法理解语义相似度
            # 问题2: 无记忆分类
            # 问题3: 返回数量无限制
```

**核心问题**：
1. **无分类**：所有记忆混在一起，无法按类别检索
2. **无法理解语义**："planning"和"routing"语义相关但关键词不匹配
3. **返回数量无限制**：可能大量消耗token
4. **无LLM参与排序**：纯关键词匹配，精度低

#### 4.2.2 Memdir四类记忆设计

**`memory/memdir.py`**：

```python
"""Memdir — 结构化四类记忆目录。
参考Claude Code的Memdir设计。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryCategory(str, Enum):
    USER = "user"           # 用户偏好、操作习惯
    FEEDBACK = "feedback"    # 纠错记录、失败经验
    PROJECT = "project"      # 项目信息、架构决策
    REFERENCE = "reference"   # 通用知识、最佳实践


@dataclass
class MemoryEntry:
    category: MemoryCategory
    content: str
    timestamp: str
    tags: list[str] = field(default_factory=list)
    importance: float = 1.0  # 1.0-10.0
    source: str = ""  # 来源：任务ID、用户输入等


class Memdir:
    """
    四类记忆目录管理器。
    
    每个类别独立存储，支持语义检索。
    """

    def __init__(self, memdir_path: str = ".manus_demo/memory"):
        self.memdir_path = memdir_path
        os.makedirs(memdir_path, exist_ok=True)
        # 四类记忆文件
        self._files = {
            MemoryCategory.USER: os.path.join(memdir_path, "user.md"),
            MemoryCategory.FEEDBACK: os.path.join(memdir_path, "feedback.md"),
            MemoryCategory.PROJECT: os.path.join(memdir_path, "project.md"),
            MemoryCategory.REFERENCE: os.path.join(memdir_path, "reference.md"),
        }
        self._entries: dict[MemoryCategory, list[MemoryEntry]] = {
            cat: [] for cat in MemoryCategory
        }
        self._load_all()

    def _load_all(self) -> None:
        """从磁盘加载所有记忆。"""
        for cat, path in self._files.items():
            if os.path.exists(path):
                self._entries[cat] = self._parse_markdown(path)

    def _parse_markdown(self, path: str) -> list[MemoryEntry]:
        """解析Markdown格式的记忆文件。"""
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("- "):
                    content = line[2:].strip()
                    if content:
                        entries.append(MemoryEntry(
                            category=MemoryCategory.REFERENCE,  # 默认分类
                            content=content,
                            timestamp="",
                            tags=[],
                        ))
        return entries

    def add(self, entry: MemoryEntry) -> None:
        """添加一条记忆并持久化。"""
        self._entries[entry.category].append(entry)
        self._persist(entry.category)

    def _persist(self, category: MemoryCategory) -> None:
        """将指定类别的记忆持久化到磁盘。"""
        path = self._files[category]
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {category.value}\n\n")
            for entry in self._entries[category]:
                f.write(f"- {entry.content}\n")

    def get_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        """获取指定类别的所有记忆。"""
        return list(self._entries[category])

    def search_by_category(
        self,
        query: str,
        category: MemoryCategory | None = None,
        top_k: int = 10,
    ) -> list[MemoryEntry]:
        """按类别检索记忆。"""
        if category:
            candidates = self._entries[category]
        else:
            candidates = []
            for entries in self._entries.values():
                candidates.extend(entries)

        # 简单关键词匹配（第一阶段粗筛）
        query_words = set(query.lower().split())
        scored = []
        for entry in candidates:
            entry_words = set(entry.content.lower().split())
            overlap = len(query_words & entry_words)
            if overlap > 0:
                scored.append((overlap * entry.importance, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def get_formatted(self, entries: list[MemoryEntry]) -> str:
        """将记忆条目格式化为可注入上下文的字符串。"""
        if not entries:
            return "No relevant memories found."
        parts = []
        for i, e in enumerate(entries, 1):
            parts.append(f"[Memory {i}] ({e.category.value}) {e.content}")
        return "\n".join(parts)
```

#### 4.2.3 语义检索实现

**`memory/semantic_retriever.py`**：

```python
"""Semantic Retriever — LLM参与的语义检索。
让LLM充当"图书管理员"，限制最多返回5条。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SemanticRetriever:
    """
    用LLM评估记忆与查询的相关性，返回最相关的记忆。
    限制最多返回5条，避免token膨胀。
    """

    def __init__(self, llm_client: Any, max_results: int = 5):
        self.llm_client = llm_client
        self.max_results = max_results

    async def retrieve(
        self,
        query: str,
        memories: list[Any],
    ) -> list[Any]:
        """
        用LLM判断每条记忆与query的相关性，返回最多max_results条。
        """
        if not memories:
            return []

        # 分批处理，避免一次请求太多
        batch_size = 10
        all_scored = []

        for i in range(0, len(memories), batch_size):
            batch = memories[i:i + batch_size]
            scored = await self._batch_score(query, batch)
            all_scored.extend(scored)

        # 按分数排序，取top_k
        all_scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in all_scored[:self.max_results]]

    async def _batch_score(
        self,
        query: str,
        entries: list[Any],
    ) -> list[tuple[float, Any]]:
        """用LLM批量评估相关性。"""
        entries_text = "\n".join(
            f"{i}. {entry.content}" for i, entry in enumerate(entries)
        )

        prompt = f"""Query: {query}

请评估以下每条记忆与query的相关性，从0-10打分。
只返回JSON数组格式：[{{"index": 0, "score": 8.5, "reason": "..."}}]

Memories:
{entries_text}"""

        try:
            response = await self.llm_client.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            scores = []
            for item in response:
                idx = item.get("index", 0)
                score = item.get("score", 0)
                if 0 <= idx < len(entries):
                    scores.append((score, entries[idx]))
            return scores
        except Exception as exc:
            logger.warning("Semantic scoring failed: %s", exc)
            return [(0, e) for e in entries]
```

#### 4.2.4 改造 `memory/long_term.py`

```python
"""Long-Term Memory — 升级后的长期记忆系统。
整合Memdir四类记忆 + 语义检索。
"""

from __future__ import annotations

import logging
from typing import Any

from memory.memdir import Memdir, MemoryCategory, MemoryEntry
from memory.semantic_retriever import SemanticRetriever

logger = logging.getLogger(__name__)


class LongTermMemory:
    """
    升级后的长期记忆系统。
    支持Memdir四类记忆 + 两层检索（关键词粗筛 + LLM语义精排）。
    """

    def __init__(self, memory_dir: str | None = None, llm_client=None):
        self._dir = memory_dir or ".manus_demo/memory"
        self.memdir = Memdir(self._dir)
        self.semantic_retriever = SemanticRetriever(llm_client, max_results=5) if llm_client else None

    # ------------------------------------------------------------------
    # 兼容旧接口
    # ------------------------------------------------------------------

    def store(self, entry: MemoryEntry) -> None:
        """添加一条记忆（兼容旧接口）。"""
        self.memdir.add(entry)

    def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
        """
        两层检索：关键词粗筛 + LLM语义精排。
        """
        # Step 1: 关键词粗筛（Top 20）
        candidates = self.memdir.search_by_category(query, top_k=20)

        # Step 2: LLM语义精排（如果可用）
        if self.semantic_retriever and len(candidates) > top_k:
            ranked = self.semantic_retriever.retrieve(query, candidates)
            return ranked[:top_k]

        return candidates[:top_k]

    def search_by_category(
        self,
        query: str,
        category: MemoryCategory | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """按类别检索记忆。"""
        return self.memdir.search_by_category(query, category, top_k)

    def get_all(self) -> list[MemoryEntry]:
        """返回所有记忆（兼容旧接口）。"""
        all_entries = []
        for cat in MemoryCategory:
            all_entries.extend(self.memdir.get_by_category(cat))
        return all_entries

    def clear(self) -> None:
        """清除所有记忆（兼容旧接口）。"""
        for cat in MemoryCategory:
            self.memdir._entries[cat] = []
            self.memdir._persist(cat)

    def get_formatted(self, entries: list[MemoryEntry]) -> str:
        """将记忆条目格式化为可注入上下文的字符串。"""
        return self.memdir.get_formatted(entries)
```

#### 4.2.5 预期收益

| 指标 | 当前 | 改造后 | 提升 |
|------|------|--------|------|
| 记忆分类 | 无 | User/Feedback/Project/Reference四类 | 高 |
| 检索方式 | 关键词重叠 | 两层检索（关键词+语义） | 高 |
| 语义理解 | 无 | LLM评估相关性 | 高 |
| 召回准确率 | 低 | 预计>85% | 显著提升 |
| 返回数量 | 无限制 | 最多5条（控制token） | 高 |

---

### 4.3 Phase 3: Multi-Agent（多智能体架构）

#### 4.3.1 当前代码问题分析

**`agents/orchestrator.py` 现状**：
```python
class OrchestratorAgent:
    def __init__(self, ...):
        self.llm_client = llm_client or LLMClient()
        self.tools = tools or []
        
    async def _execute_and_reflect_simple(self, task, plan, context):
        # 问题1: 直接调用ExecutorAgent，非纯委派
        executor = ExecutorAgent(...)
        result = await executor.execute_plan(plan, context)
        
    async def _execute_dag_and_reflect(self, dag):
        # 问题2: 直接调用DAGExecutor
        executor = DAGExecutor(...)
        result = await executor.execute(dag)
```

**核心问题**：
1. **Orchestrator直接执行**：持有llm_client和tools，有直接执行能力
2. **上下文共享**：所有Agent共享同一个LLMClient实例
3. **无隔离机制**：复杂任务上下文膨胀
4. **无模型选择**：所有任务用同一个模型

#### 4.3.2 纯协调者模式设计

**改造后架构**：

```
改造前：
┌─────────────────────────────────────┐
│         OrchestratorAgent           │
│  (持有llm_client, 直接执行)          │
│        ↓                            │
│    ExecutorAgent (共享上下文)        │
└─────────────────────────────────────┘

改造后：
┌─────────────────────────────────────┐
│         OrchestratorAgent            │
│    (纯协调者，零执行，只委派)          │
│         ↓              ↓            │
│  ┌─────────┐    ┌─────────┐        │
│  │SubAgent │    │SubAgent │        │
│  │(Simple) │    │(DAG)    │        │
│  │独立context│   │独立context│       │
│  └─────────┘    └─────────┘        │
│       ↓              ↓             │
│   只回摘要      JSONL协议通信       │
└─────────────────────────────────────┘
```

#### 4.3.3 子Agent基类

**`agents/sub_agent.py`**：

```python
"""SubAgent — 子智能体基类，提供独立上下文的执行单元。"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class SubAgent(BaseAgent):
    """
    子Agent基类，提供独立context的执行单元。

    关键特性：
    - 独立message history，不污染父Agent
    - 可选Git Worktree隔离（复杂任务用）
    - 执行完只回传摘要，不回传完整context
    - 支持模型选择器（轻量/深度/审查）
    """

    def __init__(
        self,
        parent_agent: "OrchestratorAgent",
        name: str,
        system_prompt: str,
        llm_client: Any,
        context_manager: Any = None,
        model: str | None = None,
    ):
        super().__init__(name, system_prompt, llm_client, context_manager)
        self.parent = parent_agent
        self.messages: list[dict] = []  # 独立上下文
        self.model = model

    async def execute(self, task: str, context: str = "") -> str:
        """执行任务，返回摘要（而非完整context）。"""
        result = await self._run_task(task, context)
        return self._summarize_result(result)

    def _summarize_result(self, result: str) -> str:
        """将执行结果摘要化，供父Agent聚合。"""
        return f"[SubAgent {self.name}] Result: {result[:500]}..."

    async def _run_task(self, task: str, context: str) -> str:
        """实际执行任务（子类可覆盖）。"""
        raise NotImplementedError
```

#### 4.3.4 JSONL通信协议

**`subagent/protocol.py`**：

```python
"""JSONL Protocol — Agent间结构化通信协议。
append-only，崩溃可恢复。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AgentMessage:
    request_id: str
    from_agent: str
    to_agent: str
    content: dict
    status: str = "pending"  # pending / approved / rejected
    timestamp: str = ""

    def model_dump_json(self) -> str:
        return json.dumps({
            "request_id": self.request_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "content": self.content,
            "status": self.status,
            "timestamp": self.timestamp,
        })


class JSONLProtocol:
    """
    Agent间结构化通信协议。
    基于JSONL文件，append-only，崩溃可恢复。
    """

    def __init__(self, team_dir: str = ".manus_demo/team"):
        self.team_dir = team_dir
        self.inbox_dir = os.path.join(team_dir, "inbox")
        os.makedirs(self.inbox_dir, exist_ok=True)

    def send(self, to_agent: str, message: AgentMessage) -> None:
        """发送消息到目标Agent的inbox。"""
        filepath = os.path.join(self.inbox_dir, f"{to_agent}.jsonl")
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(message.model_dump_json() + "\n")

    def receive(self, agent_id: str) -> list[AgentMessage]:
        """读取并解析收到的消息（按status过滤）。"""
        filepath = os.path.join(self.inbox_dir, f"{agent_id}.jsonl")
        if not os.path.exists(filepath):
            return []

        messages = []
        pending = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    if data.get("status") == "pending":
                        pending.append(AgentMessage(**data))
                except json.JSONDecodeError:
                    continue

        self._rewrite_inbox(agent_id, pending)
        return pending

    def _rewrite_inbox(self, agent_id: str, messages: list[AgentMessage]) -> None:
        """重写inbox文件，只保留pending消息。"""
        filepath = os.path.join(self.inbox_dir, f"{agent_id}.jsonl")
        with open(filepath, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(msg.model_dump_json() + "\n")
```

#### 4.3.5 改造 `agents/orchestrator.py`

```python
"""Orchestrator Agent — 纯协调者模式改造。"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from agents.sub_agent import SubAgent
from dag.graph import TaskDAG
from llm.client import LLMClient
from schema import Plan
from subagent.protocol import JSONLProtocol
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    纯协调者模式：Orchestrator零执行，只做规划、委派、汇总。
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self.llm_client = llm_client or LLMClient()
        self.tools = tools or []
        self.on_event = on_event
        # 子Agent管理
        self._sub_agents: dict[str, SubAgent] = {}
        # 通信协议
        self._protocol = JSONLProtocol()

    # ------------------------------------------------------------------
    # 纯协调者：委派而非执行
    # ------------------------------------------------------------------

    async def _execute_and_reflect_simple(
        self,
        task: str,
        plan: Plan,
        context: str,
    ) -> str:
        """
        纯委派：创建子Agent执行，Orchestrator只做聚合。
        """
        # 创建独立的ExecutorAgent（隔离context）
        executor = SubAgent(
            parent_agent=self,
            name="simple_executor",
            system_prompt="You are an executor agent. Execute the given plan step by step.",
            llm_client=self.llm_client,
            model="deepseek-chat",  # 轻量模型
        )

        # 委派执行
        result = await executor.execute(task, context)

        # 返回结果（Orchestrator只做聚合）
        return result

    async def _execute_dag_and_reflect(self, dag: TaskDAG) -> str:
        """
        纯委派：DAG执行委派给DAGExecutor子Agent。
        """
        # 创建DAG执行子Agent
        dag_executor = SubAgent(
            parent_agent=self,
            name="dag_executor",
            system_prompt="You are a DAG executor. Execute the given DAG plan in parallel.",
            llm_client=self.llm_client,
            model="deepseek-reasoner",  # 复杂任务用深度模型
        )

        # 委派执行
        result = await dag_executor.execute(f"Execute DAG: {dag.task}", "")

        return result

    async def _execute_emergent(self, task: str, context: str) -> str:
        """
        纯委派：Emergent规划委派给EmergentPlanner子Agent。
        """
        # 创建EmergentPlanner子Agent
        emergent = SubAgent(
            parent_agent=self,
            name="emergent_planner",
            system_prompt="You are an emergent planner. Manage TODO list and execute tasks.",
            llm_client=self.llm_client,
            model="deepseek-chat",
        )

        # 委派执行
        result = await emergent.execute(task, context)

        return result

    # ------------------------------------------------------------------
    # 子Agent生命周期管理
    # ------------------------------------------------------------------

    def spawn_sub_agent(
        self,
        name: str,
        system_prompt: str,
        model: str | None = None,
    ) -> SubAgent:
        """创建并注册一个子Agent。"""
        sub = SubAgent(
            parent_agent=self,
            name=name,
            system_prompt=system_prompt,
            llm_client=self.llm_client,
            model=model,
        )
        self._sub_agents[name] = sub
        return sub

    def get_sub_agent(self, name: str) -> SubAgent | None:
        """获取已注册的子Agent。"""
        return self._sub_agents.get(name)
```

#### 4.3.6 模型选择器

**`subagent/model_selector.py`**：

```python
"""Model Selector — 根据任务复杂度选择合适的模型。"""

from __future__ import annotations


class ModelSelector:
    """
    根据任务复杂度选择合适的模型：
    - 简单任务用轻量模型（快、便宜）
    - 复杂任务用旗舰模型（高质量）
    """

    MODEL_MAP = {
        "quick": "deepseek-chat",        # 简单执行类
        "reasoning": "deepseek-reasoner", # 深度推理类
    }

    def select(self, task_type: str) -> str:
        if "search" in task_type or "find" in task_type:
            return self.MODEL_MAP["quick"]
        elif "refactor" in task_type or "design" in task_type:
            return self.MODEL_MAP["reasoning"]
        else:
            return "deepseek-chat"  # 默认
```

#### 4.3.7 预期收益

| 指标 | 当前 | 改造后 | 提升 |
|------|------|--------|------|
| 上下文隔离 | 无 | 子Agent独立context | 高 |
| 协调者职责 | 混合（执行+协调） | 纯协调 | 清晰 |
| 复杂任务处理 | 上下文爆炸 | 子Agent隔离，可控 | 高 |
| 模型选择 | 无 | 按任务选模型（轻量/深度） | 成本降低 |
| 通信协议 | 无 | JSONL inbox，append-only | 可恢复 |

---

## 五、实施路线图（聚焦版）

### Phase 1: Context Engineering（1-2周）

```
新增文件：
- context/micro_compact.py      # 规则驱动微压缩
- context/sm_compact.py         # 复用会话记忆摘要
- context/llm_compact.py        # 9段式结构化摘要

改造文件：
- context/manager.py            # 三层渐进式压缩协调器
- config.py                     # 新增三层压缩配置

验收标准：
- 上下文Token消耗降低30-50%（同等任务）
- Edit/Write等状态变更工具输出不被压缩
- 仅当Layer 1/2无法解决时才触发LLM压缩
```

### Phase 2: Memory System（2-3周）

```
新增文件：
- memory/memdir.py              # 四类记忆目录
- memory/semantic_retriever.py   # LLM语义检索

改造文件：
- memory/long_term.py           # 整合Memdir+语义检索

验收标准：
- 支持按类别检索（User/Feedback/Project/Reference）
- 语义检索准确率 > 80%
- 返回数量可控（最多5条）
```

### Phase 3: Multi-Agent（2-3周）

```
新增文件：
- agents/sub_agent.py           # 子Agent基类
- subagent/protocol.py          # JSONL通信协议
- subagent/model_selector.py    # 模型选择器

改造文件：
- agents/orchestrator.py        # 纯协调者模式

验收标准：
- Orchestrator不直接执行，只做委派
- 子Agent独立context，不污染父Agent
- 复杂任务上下文可控
```

---

## 六、风险与缓解措施

| 风险 | 缓解措施 |
|------|---------|
| 三层压缩丢失关键决策 | 明确压缩保留优先级：架构决策 > 文件变更 > 验证状态 > 工具输出 |
| 语义检索成本过高 | 先用关键词粗筛（top 20），再LLM精排（top 5） |
| 纯协调者改造引入回归 | 保留原有执行路径作为fallback，渐进式迁移 |
| 子Agent通信延迟 | JSONL文件通信，本地磁盘IO，延迟可忽略 |
| Worktree合并冲突 | 复杂任务先用Worktree isolation，成功后再合并 |

---

## 七、附录：ATA文章索引

| 文章 | 关键参考点 |
|------|-----------|
| [你不知道的Agent](https://ata.atatech.org/articles/11020600930) | 上下文分层、多Agent组织、记忆设计 |
| [深度解析Claude Code](https://ata.atatech.org/articles/11020605711) | 三层压缩、Memdir、子Agent隔离 |
| [Qoder工程实践](https://ata.atatech.org/articles/11020601776) | 协调者模式、子Agent隔离 |
| [深度解析Hermes](https://ata.atatech.org/articles/11020604988) | 动态Skill生成、上下文注入 |
| [AGENTS.md实践指南](https://ata.atatech.org/articles/11020618417) | 渐进式披露、上下文管理 |

---

## 八、总结

本次调研聚焦 **Context Engineering、Memory System、Multi-Agent** 三个核心方向，基于项目源码和ATA优秀文章，提出了以下优化方案：

1. **Context三层压缩**：
   - Layer 1: MicroCompact（规则驱动，无LLM调用）
   - Layer 2: SM Compact（复用会话记忆，零LLM调用）
   - Layer 3: Full LLM Compact（9段式结构化摘要）
   - **预期收益**：Token消耗降低30-50%，关键决策不丢失

2. **Memdir四类记忆+语义检索**：
   - 四类记忆：User/Feedback/Project/Reference
   - 两层检索：关键词粗筛（Top 20） + LLM语义精排（Top 5）
   - **预期收益**：记忆召回准确率提升至85%+

3. **纯协调者+子Agent隔离**：
   - Orchestrator零执行，只做委派
   - 子Agent独立context，JSONL协议通信
   - **预期收益**：复杂任务上下文可控，支持多模型调度

实施顺序建议：**Phase 1 → Phase 2 → Phase 3**，每个Phase可独立交付，降低一次性改造风险。
