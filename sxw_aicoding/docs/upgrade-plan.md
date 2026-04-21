# Manus Demo 升级计划

> **当前版本**: v6.0
> **更新日期**: 2026-04-20
> **目的**: 从当前 v6 到未来版本的升级路线图

---

## 当前状态回顾

### 已完成的版本演进

```
v1 → 线性规划 + 顺序执行 + 完整重规划
v2 → DAG 分层规划 + 并行 Super-step + 局部重规划 + 节点状态机 + 逐节点验证
v3 → 自适应规划（运行时 DAG 变更）+ 工具路由（基于失败的切换）+ 动态 DAG 增删改
v4 → 两阶段混合分类器（规则 + LLM）+ 自动 v1/v2 路径选择
v5 → Claude Code 风格隐式规划 + TODO 列表管理 + while(tool_use) 主循环
v6 → LLM 重试机制（指数退避）+ ReActEngine 统一引擎 Feature Flag
```

### 当前架构优势

- **三路由混合规划**：simple/complex/emergent 三种规划模式自动选择
- **两阶段分类器**：规则快筛 + LLM 兜底，高效判断任务复杂度
- **Super-step 并行执行**：基于 DAG 层级的并行执行机制
- **自适应规划**：运行时动态调整 DAG（增删改节点）
- **工具路由**：基于失败统计的智能工具切换建议
- **隐式规划**：Claude Code 风格的 while(tool_use) 主循环
- **LLM 重试机制**：指数退避策略提升稳定性

### 当前架构短板

- **工具生态薄弱**：仅 3 个工具（CodeExecutor、FileOps、WebSearch），搜索是 mock 实现
- **无真实环境交互能力**：缺少 Shell 命令执行、真实 Web 抓取等能力
- **上下文管理粗糙**：粗略 token 估算，无精确计数和分级压缩
- **无持久化外部记忆**：仅短期/长期内存，无文件持久化
- **无验证-修复闭环**：执行后无自动验证和自愈机制
- **无 CodeAct 模式**：仅支持 function calling，未实现 LLM 直接生成代码执行

---

## v7.0 升级计划：工具生态增强

### 目标

从 mock 工具升级为真实可用的工具集，对标 Claude Code / Manus 的核心能力。

### 具体任务

#### 1. 真实 Web 搜索 — 接入 SerpAPI/Tavily

**当前状态**：`tools/web_search.py` 为 mock 实现，返回硬编码结果

**升级方案**：
- 集成 Tavily API（推荐）或 SerpAPI
- 支持搜索结果解析（标题、摘要、URL、发布日期）
- 保留 mock 模式作为 fallback（无 API key 时自动降级）
- 添加 API key 配置到 `.env`

**预估工作量**：2-3 天

**技术要点**：
```python
# tools/web_search.py 升级示例
class WebSearchTool(BaseTool):
    def __init__(self, use_real_api: bool = True):
        self.use_real_api = use_real_api
        if use_real_api:
            self.api_key = os.getenv("TAVILY_API_KEY")
    
    async def execute(self, query: str, max_results: int = 10):
        if self.use_real_api and self.api_key:
            return await self._tavily_search(query, max_results)
        else:
            return await self._mock_search(query, max_results)
```

#### 2. Shell 命令执行 — 新增 ShellTool

**当前状态**：无 Shell 执行能力

**升级方案**：
- 新增 `tools/shell.py`，支持执行任意 shell 命令（bash/zsh）
- 基于 `asyncio.create_subprocess_exec` 实现，支持超时、流式输出捕获
- 在 sandbox 目录下执行，通过白名单/黑名单限制危险命令
- 支持工作目录切换、环境变量传递

**预估工作量**：1-2 天

**安全策略**：
```python
# 白名单命令示例
ALLOWED_COMMANDS = [
    "ls", "cat", "grep", "find", "head", "tail", "wc",
    "git", "python", "pip", "npm", "node"
]

# 黑名单危险命令
BLOCKED_COMMANDS = [
    "rm", "rmdir", "mkfs", "dd", "chmod 777", "sudo"
]
```

#### 3. 网页内容抓取 — 新增 WebScraperTool

**当前状态**：无网页抓取能力

**升级方案**：
- 新增 `tools/web_scraper.py`
- 基于 `httpx` + `BeautifulSoup` / `markdownify` 抓取网页内容并转为 Markdown
- 支持 JavaScript 渲染（可选集成 Playwright）
- 自动截断过长内容，提取关键段落

**预估工作量**：2 天

**技术要点**：
```python
class WebScraperTool(BaseTool):
    async def execute(self, url: str, extract_markdown: bool = True):
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            soup = BeautifulSoup(response.text, "html.parser")
            
        if extract_markdown:
            return markdownify(str(soup))
        return response.text
```

#### 4. diff-based 文件编辑 — 升级 FileOpsTool

**当前状态**：`tools/file_ops.py` 仅支持全量覆写

**升级方案**：
- 支持 search-and-replace 模式（类似 Claude Code 的 str_replace）
- 支持行号定位编辑
- 增加文件操作：`mkdir`、`delete`、`move`、`copy`、`find`（glob）、`grep`

**预估工作量**：1 天

**API 设计**：
```python
# 新增编辑模式
async def edit_file(
    self,
    file_path: str,
    mode: str = "replace",  # "replace" | "search_replace" | "line_number"
    old_content: str = None,
    new_content: str = None,
    start_line: int = None,
    end_line: int = None
):
    # 实现三种编辑模式
```

### 技术方案

#### 工具注册机制改造

**当前问题**：工具硬编码在 `tools/__init__.py`

**改进方案**：
```python
# config.py 新增工具配置
AVAILABLE_TOOLS = {
    "web_search": {"enabled": True, "api_required": True},
    "shell": {"enabled": True, "sandbox_required": True},
    "web_scraper": {"enabled": True, "api_required": False},
    "file_ops": {"enabled": True, "api_required": False},
    "code_executor": {"enabled": True, "api_required": False}
}

# tools/__init__.py 动态加载
def load_tools():
    tools = []
    for tool_name, config in AVAILABLE_TOOLS.items():
        if config["enabled"] and self._check_dependencies(config):
            tools.append(self._instantiate_tool(tool_name))
    return tools
```

#### 工具配置外部化

**当前问题**：API key 等配置散落在代码中

**改进方案**：
- 所有工具配置集中到 `.env`
- 新增 `tools/config.py` 统一管理
- 支持工具级别的 Feature Flag

```bash
# .env 新增配置
# Web Search
TAVILY_API_KEY=your_api_key_here
WEB_SEARCH_ENABLED=true

# Shell
SHELL_ENABLED=true
SHELL_SANDBOX_DIR=./sandbox

# Web Scraper
WEB_SCRAPER_ENABLED=true
WEB_SCRAPER_TIMEOUT=10
```

#### 安全策略设计

**沙箱隔离**：
- 所有 Shell 命令在指定目录执行
- 禁止访问系统关键路径（/etc, /usr/bin 等）
- 文件操作限制在项目目录内

**命令过滤**：
- 白名单机制（默认安全）
- 黑名单兜底（危险命令拦截）
- 支持用户自定义规则

**API 安全**：
- API key 从环境变量读取，不硬编码
- 支持 API 调用限流
- 错误信息脱敏

---

## v8.0 升级计划：上下文管理增强

### 目标

解决信息孤岛和上下文窗口限制，实现智能上下文管理。

### 具体任务

#### 1. Working Memory File — 执行期工作日志

**当前状态**：上下文仅在内存中传递，无持久化

**升级方案**：
- 每个执行步骤的中间结果写入工作日志文件
- 后续步骤可按需读取历史结果
- 参考 Claude Code 的 `CLAUDE.md` 设计

**预估工作量**：2-3 天

**设计要点**：
```python
# memory/working_memory.py
class WorkingMemory:
    def __init__(self, session_id: str, log_dir: str = "./logs"):
        self.session_id = session_id
        self.log_file = f"{log_dir}/{session_id}_working_memory.md"
    
    async def append_step(self, step_name: str, result: Any):
        """记录步骤结果到工作日志"""
        entry = f"## {step_name}\n\n{self._format_result(result)}\n\n"
        await self._write_to_file(entry)
    
    async def get_relevant_history(self, query: str, top_k: int = 5):
        """基于查询检索相关历史记录"""
        # 使用 TF-IDF 或语义搜索
        return self._search_logs(query, top_k)
```

**日志格式**：
```markdown
# Working Memory - Session: abc123

## Step 1: Initial Planning
**Status**: Completed
**Result**: Generated DAG with 5 nodes...

## Step 2: Execute Node A
**Status**: Completed
**Output**: Tool returned: {...}

## Step 3: Execute Node B
**Status**: Failed
**Error**: Tool timeout after 30s
**Retry**: Attempting retry 1/3...
```

#### 2. 精确 Token 计数 — 替换粗略估算

**当前状态**：`memory/short_term.py` 使用粗略估算（`len(text) * 0.3`）

**升级方案**：
- 集成 `tiktoken` 库进行精确计数
- 按模型选择对应编码器（gpt-4, claude-3 等）
- 实时监控上下文窗口使用率

**预估工作量**：1 天

**技术要点**：
```python
# memory/token_counter.py
import tiktoken

class TokenCounter:
    def __init__(self, model: str = "gpt-4"):
        self.encoding = tiktoken.encoding_for_model(model)
    
    def count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))
    
    def count_messages(self, messages: List[Dict]) -> int:
        """计算消息列表的总 token 数"""
        total = 0
        for msg in messages:
            total += self.count_tokens(msg.get("content", ""))
            # 加上每条消息的固定开销
            total += 4  # OpenAI 格式每条消息约 4 tokens
        return total

# memory/short_term.py 集成
class ShortTermMemory:
    def __init__(self, max_tokens: int = 8000, model: str = "gpt-4"):
        self.token_counter = TokenCounter(model)
        self.max_tokens = max_tokens
        self.current_tokens = 0
    
    def add_message(self, role: str, content: str):
        tokens = self.token_counter.count_tokens(content)
        if self.current_tokens + tokens > self.max_tokens:
            self._compress_context()
        # ... 添加消息逻辑
```

#### 3. DAG 路径上下文传递 — 解决信息孤岛

**当前状态**：DAG 节点间上下文传递依赖手动管理

**升级方案**：
- 父节点结果自动注入子节点上下文
- 基于 `DAGState.get_node_context()` 增强
- 支持选择性上下文传递（避免污染）

**预估工作量**：2 天

**设计要点**：
```python
# dag/state_machine.py 增强
class DAGState:
    def __init__(self):
        self.node_outputs = {}  # 节点输出缓存
        self.context_rules = {}  # 上下文传递规则
    
    def get_node_context(self, node_id: str) -> Dict:
        """获取节点的完整上下文（父节点输出 + 全局上下文）"""
        context = {"global": self.global_context}
        
        # 收集父节点的输出
        parents = self.dag.get_parents(node_id)
        for parent_id in parents:
            if parent_id in self.node_outputs:
                context[f"parent_{parent_id}"] = self.node_outputs[parent_id]
        
        return context
    
    def set_context_rule(self, node_id: str, inherit_from: List[str]):
        """设置上下文继承规则"""
        self.context_rules[node_id] = inherit_from

# dag/executor.py 集成
async def execute_node(self, node: DAGNode):
    context = self.state.get_node_context(node.id)
    result = await self.executor.execute(node.action, context)
    self.state.node_outputs[node.id] = result
    return result
```

#### 4. 上下文压缩优化 — 分级压缩策略

**当前状态**：简单的消息截断，无智能压缩

**升级方案**：
- 近期消息保留完整（最近 N 条）
- 远期消息摘要压缩（使用 LLM 生成摘要）
- 关键信息标记保护（用户指定的重要信息不压缩）

**预估工作量**：2-3 天

**压缩策略**：
```python
# memory/compressor.py
class ContextCompressor:
    def __init__(self, llm_client):
        self.llm_client = llm_client
    
    async def compress(self, messages: List[Dict], 
                     keep_recent: int = 10,
                     protected_keys: List[str] = None) -> List[Dict]:
        """分级压缩上下文"""
        if len(messages) <= keep_recent:
            return messages
        
        # 分离近期和远期消息
        recent = messages[-keep_recent:]
        old = messages[:-keep_recent]
        
        # 提取保护信息
        protected = self._extract_protected(old, protected_keys)
        
        # 压缩远期消息
        summary = await self._summarize(old)
        
        # 组装压缩后的上下文
        compressed = [
            {"role": "system", "content": f"Previous conversation summary:\n{summary}"},
            *protected,
            *recent
        ]
        
        return compressed
    
    async def _summarize(self, messages: List[Dict]) -> str:
        prompt = "Summarize the following conversation concisely:\n" + \
                 "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        return await self.llm_client.generate(prompt)
```

---

## v9.0 升级计划：验证-修复闭环

### 目标

实现 Manus 风格的自愈循环，从"执行即结束"升级为"执行→验证→修复"闭环。

### 具体任务

#### 1. 验证器框架 — 可插拔验证策略

**当前状态**：无验证机制

**升级方案**：
- 设计可插拔验证器接口 `BaseValidator`
- 实现多种验证策略：语法验证、语义验证、测试验证、人工验证
- 支持验证结果评分（0-1 分数）

**预估工作量**：3-4 天

**设计要点**：
```python
# validators/base.py
class BaseValidator(ABC):
    @abstractmethod
    async def validate(self, context: Dict, result: Any) -> ValidationResult:
        """验证执行结果"""
        pass

# validators/syntax_validator.py
class SyntaxValidator(BaseValidator):
    async def validate(self, context: Dict, result: Any) -> ValidationResult:
        if result.get("type") == "code":
            code = result.get("content")
            syntax_errors = self._check_syntax(code)
            if syntax_errors:
                return ValidationResult(
                    passed=False,
                    score=0.0,
                    errors=syntax_errors
                )
        return ValidationResult(passed=True, score=1.0)

# validators/test_validator.py
class TestValidator(BaseValidator):
    async def validate(self, context: Dict, result: Any) -> ValidationResult:
        """运行测试验证"""
        test_results = await self._run_tests(result.get("test_file"))
        passed = all(r["passed"] for r in test_results)
        score = sum(r["passed"] for r in test_results) / len(test_results)
        return ValidationResult(
            passed=passed,
            score=score,
            details=test_results
        )
```

#### 2. 自动修复 — 基于错误分析的修复建议

**当前状态**：失败后仅重试，无修复逻辑

**升级方案**：
- 分析验证失败原因，生成修复建议
- 使用 LLM 生成修复代码
- 支持多轮修复迭代

**预估工作量**：3-4 天

**设计要点**：
```python
# agents/fixer.py
class FixerAgent:
    def __init__(self, llm_client):
        self.llm_client = llm_client
    
    async def fix(self, context: Dict, 
                 result: Any, 
                 validation_result: ValidationResult) -> FixResult:
        """基于验证结果生成修复方案"""
        error_analysis = self._analyze_errors(validation_result)
        
        prompt = f"""
        The following code/plan failed validation:
        
        {result.get('content')}
        
        Errors found:
        {error_analysis}
        
        Please provide a fixed version that addresses all errors.
        """
        
        fixed_content = await self.llm_client.generate(prompt)
        
        return FixResult(
            original=result,
            fixed=fixed_content,
            applied=True
        )
    
    def _analyze_errors(self, validation_result: ValidationResult) -> str:
        """分析错误原因"""
        if validation_result.errors:
            return "\n".join(validation_result.errors)
        if validation_result.details:
            failed_tests = [d for d in validation_result.details if not d["passed"]]
            return "\n".join([f"Test {d['name']}: {d['error']}" for d in failed_tests])
        return "Unknown error"
```

#### 3. 测试驱动执行 — 生成测试 → 执行 → 分析失败 → 修复

**当前状态**：无测试驱动机制

**升级方案**：
- 执行前自动生成测试用例
- 执行后运行测试验证
- 失败时触发修复循环

**预估工作量**：4-5 天

**执行流程**：
```python
# agents/test_driven_executor.py
class TestDrivenExecutor:
    async def execute_with_validation(self, task: str) -> ExecutionResult:
        # 1. 生成测试
        tests = await self._generate_tests(task)
        
        # 2. 执行任务
        result = await self.executor.execute(task)
        
        # 3. 运行测试验证
        validation = await self.validator.validate(
            context={"task": task, "tests": tests},
            result=result
        )
        
        # 4. 如果未通过，进入修复循环
        max_retries = 3
        for attempt in range(max_retries):
            if validation.passed:
                break
            
            # 生成修复方案
            fix_result = await self.fixer.fix(
                context={"task": task},
                result=result,
                validation_result=validation
            )
            
            # 应用修复
            result = fix_result.fixed
            
            # 重新验证
            validation = await self.validator.validate(
                context={"task": task, "tests": tests},
                result=result
            )
        
        return ExecutionResult(
            result=result,
            validation=validation,
            success=validation.passed
        )
```

---

## v10.0 升级计划：CodeAct + MCP

### 目标

从 function calling 升级为 CodeAct 模式，支持 LLM 直接生成可执行代码。

### 具体任务

#### 1. CodeAct 执行器 — LLM 直接生成可执行代码

**当前状态**：仅支持 function calling，LLM 不能直接写代码执行

**升级方案**：
- 实现 CodeAct 模式：LLM 生成代码 → 解释执行 → 返回结果
- 支持多轮代码生成和执行
- 保持与 function calling 的兼容性

**预估工作量**：5-6 天

**设计要点**：
```python
# agents/codeact_executor.py
class CodeActExecutor:
    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.code_executor = CodeExecutor()
    
    async def execute(self, task: str, context: Dict = None) -> ExecutionResult:
        conversation = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": task}
        ]
        
        if context:
            conversation.insert(1, {"role": "system", "content": f"Context: {context}"})
        
        max_iterations = 10
        for iteration in range(max_iterations):
            # LLM 生成代码
            response = await self.llm_client.generate(conversation)
            code = self._extract_code(response)
            
            if not code:
                # 如果没有代码，可能是最终答案
                return ExecutionResult(
                    result=response,
                    success=True,
                    iterations=iteration
                )
            
            # 执行代码
            execution_result = await self.code_executor.execute(code)
            
            # 将执行结果反馈给 LLM
            conversation.append({
                "role": "assistant",
                "content": response
            })
            conversation.append({
                "role": "user",
                "content": f"Execution result:\n{execution_result.output}\n\n" +
                          f"Errors:\n{execution_result.error}\n\n" +
                          "Continue if needed, or provide final answer."
            })
            
            # 如果执行成功且没有错误，可能已经完成任务
            if execution_result.success and not execution_result.error:
                # 让 LLM 判断是否完成
                decision = await self._check_completion(conversation)
                if decision == "DONE":
                    return ExecutionResult(
                        result=response,
                        success=True,
                        iterations=iteration
                    )
        
        return ExecutionResult(
            result=None,
            success=False,
            error="Max iterations reached"
        )
    
    def _get_system_prompt(self) -> str:
        return """
        You are a coding agent. Write Python code to solve the user's task.
        Wrap your code in ```python``` blocks.
        The code will be executed and results will be fed back to you.
        Continue generating code until the task is complete.
        """
```

#### 2. MCP 协议支持 — 标准化工具接口

**当前状态**：工具接口自定义，不符合 MCP 标准

**升级方案**：
- 实现 MCP 客户端，支持连接 MCP 服务器
- 将现有工具适配为 MCP 兼容接口
- 支持动态发现和调用外部 MCP 工具

**预估工作量**：4-5 天

**设计要点**：
```python
# tools/mcp_client.py
class MCPClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.available_tools = {}
    
    async def connect(self):
        """连接到 MCP 服务器并获取工具列表"""
        response = await self._call_mcp("tools/list")
        self.available_tools = {t["name"]: t for t in response["tools"]}
    
    async def call_tool(self, tool_name: str, arguments: Dict) -> Any:
        """调用 MCP 工具"""
        if tool_name not in self.available_tools:
            raise ValueError(f"Tool {tool_name} not found")
        
        response = await self._call_mcp(
            "tools/call",
            {"name": tool_name, "arguments": arguments}
        )
        return response["result"]
    
    async def _call_mcp(self, method: str, params: Dict = None) -> Dict:
        """底层 MCP 通信"""
        # 实现 JSON-RPC 通信
        pass

# tools/mcp_adapter.py
class MCPAdapter(BaseTool):
    """将 MCP 工具适配为 BaseTool 接口"""
    def __init__(self, mcp_client: MCPClient, tool_name: str):
        self.mcp_client = mcp_client
        self.tool_name = tool_name
        self.tool_def = mcp_client.available_tools[tool_name]
    
    async def execute(self, **kwargs) -> Any:
        return await self.mcp_client.call_tool(self.tool_name, kwargs)
    
    @property
    def description(self) -> str:
        return self.tool_def.get("description", "")
```

#### 3. 动态工具发现 — 运行时注册新工具

**当前状态**：工具列表静态配置

**升级方案**：
- 支持运行时发现新的 MCP 工具
- 自动注册工具到工具路由器
- 支持工具的热插拔

**预估工作量**：2-3 天

**设计要点**：
```python
# tools/registry.py
class ToolRegistry:
    def __init__(self):
        self.tools = {}
        self.mcp_clients = {}
    
    async def register_mcp_server(self, server_url: str):
        """注册 MCP 服务器"""
        client = MCPClient(server_url)
        await client.connect()
        self.mcp_clients[server_url] = client
        
        # 自动注册该服务器的所有工具
        for tool_name in client.available_tools:
            adapter = MCPAdapter(client, tool_name)
            self.tools[tool_name] = adapter
    
    async def discover_tools(self, query: str = None) -> List[Dict]:
        """发现工具（支持搜索）"""
        tools = []
        for name, tool in self.tools.items():
            if query is None or query.lower() in name.lower() or \
               query.lower() in tool.description.lower():
                tools.append({
                    "name": name,
                    "description": tool.description,
                    "type": type(tool).__name__
                })
        return tools
    
    async def call_tool(self, tool_name: str, **kwargs) -> Any:
        """调用工具"""
        if tool_name not in self.tools:
            raise ValueError(f"Tool {tool_name} not found")
        return await self.tools[tool_name].execute(**kwargs)
```

---

## 升级优先级矩阵

| 版本 | 核心目标 | 优先级 | 预估工期 | 依赖 | 收益 |
|------|---------|--------|---------|------|------|
| v7 | 工具生态增强 | P0 | 1-2 周 | 无 | 高：解锁真实能力 |
| v8 | 上下文管理增强 | P1 | 2-3 周 | v7 | 中：提升稳定性 |
| v9 | 验证-修复闭环 | P1 | 2-3 周 | v7 | 高：实现自愈 |
| v10 | CodeAct+MCP | P2 | 3-4 周 | v7+v8 | 中：标准化扩展 |

**优先级说明**：
- **P0（必须）**：核心能力缺失，严重影响实用性
- **P1（重要）**：显著提升系统质量和可靠性
- **P2（可选）**：长期架构优化，锦上添花

**推荐执行顺序**：
1. v7（工具生态）→ 解锁真实能力，奠定基础
2. v8（上下文管理）→ 提升稳定性和可扩展性
3. v9（验证-修复）→ 实现自愈闭环，提升可靠性
4. v10（CodeAct+MCP）→ 标准化扩展，长期演进

---

## 技术债务清单

### 当前代码中需要清理的技术债务

#### 1. 工具层

**问题**：
- `tools/web_search.py` 硬编码 mock 数据
- `tools/file_ops.py` 功能不完整（缺少 grep、find 等）
- 工具注册机制硬编码在 `__init__.py`

**影响**：中等
**清理优先级**：P1（在 v7 中一并解决）

#### 2. 上下文管理

**问题**：
- `memory/short_term.py` 使用粗略 token 估算
- 上下文压缩策略简单粗暴（直接截断）
- 无持久化工作日志

**影响**：高（影响长对话稳定性）
**清理优先级**：P1（在 v8 中解决）

#### 3. 测试覆盖

**问题**：
- 部分模块缺少单元测试
- 集成测试覆盖不完整
- 无端到端测试

**影响**：中等
**清理优先级**：P2（持续改进）

#### 4. 配置管理

**问题**：
- 配置分散在多个文件（`config.py`、各模块内部）
- 缺少配置验证机制
- 环境变量使用不一致

**影响**：低
**清理优先级**：P2（在 v7 中统一管理）

#### 5. 错误处理

**问题**：
- 部分异常捕获过于宽泛（`except Exception`）
- 错误信息不够详细
- 缺少错误分类和恢复策略

**影响**：中等
**清理优先级**：P1（在 v9 中完善）

#### 6. 文档

**问题**：
- API 文档不完整
- 部分复杂逻辑缺少注释
- 架构文档需要更新

**影响**：低（不影响功能）
**清理优先级**：P2（持续改进）

---

## 风险评估

### 各升级阶段的风险和缓解措施

#### v7.0 工具生态增强

**风险**：
1. **API 依赖风险**：真实搜索 API 可能不稳定或收费
   - **缓解**：保留 mock 模式作为 fallback，支持多 API 备选

2. **安全风险**：Shell 执行可能带来安全隐患
   - **缓解**：严格的沙箱隔离、白名单机制、命令审计

3. **兼容性风险**：新工具可能与现有系统集成困难
   - **缓解**：保持工具接口兼容，渐进式替换

**风险等级**：中

#### v8.0 上下文管理增强

**风险**：
1. **性能风险**：精确 token 计数和上下文压缩可能增加延迟
   - **缓解**：异步处理、缓存机制、分级压缩

2. **准确性风险**：上下文压缩可能丢失关键信息
   - **缓解**：关键信息标记保护、用户可配置压缩策略

3. **存储风险**：工作日志文件可能占用大量磁盘空间
   - **缓解**：日志轮转、定期清理、压缩存储

**风险等级**：低

#### v9.0 验证-修复闭环

**风险**：
1. **成本风险**：验证和修复需要额外的 LLM 调用
   - **缓解**：智能跳过不必要的验证、缓存验证结果

2. **循环风险**：修复可能陷入死循环
   - **缓解**：最大重试次数限制、修复效果评估

3. **准确性风险**：自动修复可能引入新问题
   - **缓解**：修复后重新验证、人工审核机制

**风险等级**：中

#### v10.0 CodeAct + MCP

**风险**：
1. **安全风险**：代码执行可能带来安全隐患
   - **缓解**：沙箱隔离、代码审计、权限限制

2. **兼容性风险**：MCP 协议可能变化或不稳定
   - **缓解**：版本锁定、兼容性测试、降级方案

3. **复杂度风险**：CodeAct 模式可能增加系统复杂度
   - **缓解**：保持 function calling 作为默认模式，CodeAct 作为可选增强

**风险等级**：中高

---

## 附录：关键参考文档

- [混合规划路由 v4](./hybrid-plan-routing-v4.md)
- [动态规划 v3](./emergent-planning-v5.md)
- [LLM 集成 v6](./llm-integration-v6.md)
- [动态功能对比](./dynamic-features-v1-vs-v2.md)

---

**文档维护**：本文档应随着版本演进持续更新，每个版本完成后更新"当前状态回顾"部分。
