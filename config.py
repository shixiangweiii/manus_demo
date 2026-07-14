"""
Configuration module for the Manus Demo.
Loads settings from environment variables or .env file.
Manus Demo 配置模块。
从环境变量或 .env 文件加载所有配置项。
"""

import os
from dotenv import load_dotenv

load_dotenv()  # 自动读取项目根目录的 .env 文件（若存在），优先级低于系统环境变量

VERSION = "v20.0-dev"  # 当前版本号，main.py 和 tracing 引用此值

# --- LLM API Configuration ---
# --- LLM API 配置 ---
# Load from environment; prefer .env or env vars for API key in production.
# 从环境变量加载；生产环境建议通过 .env 或环境变量设置 API Key。
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")   # OpenAI-compatible API base URL / OpenAI 兼容接口地址
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # API key / API 密钥（请通过 .env 或环境变量设置）
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")                        # Model name / 模型名称

# --- Agent Limits ---
# --- 智能体执行限制 ---
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "16000"))   # 上下文 Token 上限，超出后触发摘要压缩
MAX_REACT_ITERATIONS = int(os.getenv("MAX_REACT_ITERATIONS", "10"))  # 每个 Action 节点 ReAct 循环最大迭代次数
MAX_REPLAN_ATTEMPTS = int(os.getenv("MAX_REPLAN_ATTEMPTS", "3"))     # 反思失败后最大重规划次数

# --- Memory ---
# --- 记忆系统 ---
MEMORY_DIR = os.path.expanduser(os.getenv("MEMORY_DIR", "~/.manus_demo"))  # 长期记忆存储目录（JSON 文件）
SHORT_TERM_WINDOW = int(os.getenv("SHORT_TERM_WINDOW", "20"))              # 短期记忆滑动窗口大小（条数）

# --- Knowledge ---
# --- 知识库 ---
KNOWLEDGE_DOCS_DIR = os.path.join(os.path.dirname(__file__), "knowledge", "docs")  # 知识文档目录（相对于项目根）
KNOWLEDGE_CHUNK_SIZE = int(os.getenv("KNOWLEDGE_CHUNK_SIZE", "500"))               # 文档切片大小（字符数）
KNOWLEDGE_TOP_K = int(os.getenv("KNOWLEDGE_TOP_K", "3"))                           # 知识检索返回的最大条数

# --- Plan Routing ---
# --- 规划路由（v4 新增：混合分类器）---
PLAN_MODE = os.getenv("PLAN_MODE", "auto")  # "auto"=两阶段混合分类 | "simple"=强制v1 | "complex"=强制v2 | "emergent"=强制v5 DAG

# --- DAG Execution ---
# --- DAG 执行参数 ---
MAX_PARALLEL_NODES = int(os.getenv("MAX_PARALLEL_NODES", "3"))  # 每个 Super-step 最多并行执行的节点数
DAG_SERIAL_EXECUTION = os.getenv("DAG_SERIAL_EXECUTION", "true").lower() == "true"  # 串行执行 DAG 节点（默认开启，修复并发串话 bug；设 false 恢复并行）

# --- Adaptive Planning (v3) ---
# --- 自适应规划（v3 新增）---
ADAPTIVE_PLANNING_ENABLED = os.getenv("ADAPTIVE_PLANNING_ENABLED", "true").lower() == "true"  # 是否启用超步间自适应规划
ADAPT_PLAN_INTERVAL = int(os.getenv("ADAPT_PLAN_INTERVAL", "1"))        # 每隔几个超步执行一次自适应检查（1=每步都检查）
ADAPT_PLAN_MIN_COMPLETED = int(os.getenv("ADAPT_PLAN_MIN_COMPLETED", "1"))  # 至少完成多少个 ACTION 节点后才启动自适应

# --- Tool Router (v3) ---
# --- 工具路由（v3 新增）---
TOOL_FAILURE_THRESHOLD = int(os.getenv("TOOL_FAILURE_THRESHOLD", "2"))  # 连续失败多少次后建议切换工具

# --- DAG Execution Robustness ---
# --- DAG 执行健壮性 ---
NODE_EXECUTION_TIMEOUT = int(os.getenv("NODE_EXECUTION_TIMEOUT", "300"))  # 单个节点执行超时时间（秒），默认 5 分钟
MAX_CHECKPOINTS = int(os.getenv("MAX_CHECKPOINTS", "10"))                 # 内存中保留的最大 Checkpoint 数量

# --- Emergent Planning (v5) ---
# --- 隐式规划（v5 新增）---
EMERGENT_PLANNING_ENABLED = os.getenv("EMERGENT_PLANNING_ENABLED", "true").lower() == "true"  # 是否启用隐式规划模式
MAX_TODO_ITEMS = int(os.getenv("MAX_TODO_ITEMS", "20"))  # TODO 列表最大项数
MAX_TODO_RETRIES = int(os.getenv("MAX_TODO_RETRIES", "3"))  # 单个 TODO 最大重试次数
TODO_COMPRESSION_THRESHOLD = float(os.getenv("TODO_COMPRESSION_THRESHOLD", "0.8"))  # 上下文窗口使用率达到 80% 时压缩 TODO
MAX_EMERGENT_OUTER_ITERATIONS = int(os.getenv("MAX_EMERGENT_OUTER_ITERATIONS", str(MAX_TODO_ITEMS * MAX_TODO_RETRIES)))  # Emergent 主循环最大迭代数（TODO 调度层）

# --- Tools ---
# --- 工具参数 ---
SANDBOX_DIR = os.path.expanduser(os.getenv("SANDBOX_DIR", "~/.manus_demo/sandbox"))  # 沙箱目录（文件操作和 Shell 命令的工作目录，防止越权访问）
CODE_EXEC_TIMEOUT = int(os.getenv("CODE_EXEC_TIMEOUT", "30"))                        # Python 代码执行超时时间（秒）
SHELL_EXEC_TIMEOUT = int(os.getenv("SHELL_EXEC_TIMEOUT", "30"))                      # Shell 命令执行超时时间（秒）
PYTHON_COMMAND = os.getenv("PYTHON_COMMAND", "python3")                              # Shell/pytest 命令中推荐使用的 Python 可执行命令
SUBPROCESS_MAX_OUTPUT_BYTES = int(os.getenv("SUBPROCESS_MAX_OUTPUT_BYTES", str(512 * 1024)))  # 单次子进程（Shell/Python）最大输出字节数，默认 512KB
SHELL_MAX_CONCURRENT = int(os.getenv("SHELL_MAX_CONCURRENT", "3"))                    # 最大并发 Shell 子进程数
CODE_MAX_CONCURRENT = int(os.getenv("CODE_MAX_CONCURRENT", "3"))                      # 最大并发代码执行子进程数

# --- AgentBay Cloud Runtime Tools ---
# --- AgentBay 云端运行时工具（默认关闭，按需注册）---
AGENTBAY_ENABLED = os.getenv("AGENTBAY_ENABLED", "false").lower() == "true"           # AgentBay 原生工具总开关
AGENTBAY_API_KEY = os.getenv("AGENTBAY_API_KEY", "")                                  # AgentBay API Key
AGENTBAY_CODE_TOOL_ENABLED = os.getenv("AGENTBAY_CODE_TOOL_ENABLED", "true").lower() == "true"  # 注册 agentbay_code
AGENTBAY_BROWSER_TOOL_ENABLED = os.getenv("AGENTBAY_BROWSER_TOOL_ENABLED", "true").lower() == "true"  # 注册 agentbay_browser
AGENTBAY_LOG_LEVEL = os.getenv("AGENTBAY_LOG_LEVEL", "WARNING")                       # SDK 日志级别；WARNING 避免输出 resource_url/authcode
AGENTBAY_MAX_CONCURRENT_SESSIONS = int(os.getenv("AGENTBAY_MAX_CONCURRENT_SESSIONS", "1"))  # AgentBay Session 并发上限
AGENTBAY_CODE_IMAGE = os.getenv("AGENTBAY_CODE_IMAGE", "code_latest")                 # CodeSpace 镜像 alias
AGENTBAY_BROWSER_IMAGE = os.getenv("AGENTBAY_BROWSER_IMAGE", "browser_latest")        # BrowserUse 镜像 alias
AGENTBAY_SESSION_IDLE_RELEASE_MINUTES = int(os.getenv("AGENTBAY_SESSION_IDLE_RELEASE_MINUTES", "5"))  # 空闲自动释放兜底
AGENTBAY_SESSION_MAX_RUNTIME_MINUTES = int(os.getenv("AGENTBAY_SESSION_MAX_RUNTIME_MINUTES", "10"))   # 单 Session 最大运行时间兜底
AGENTBAY_CODE_TIMEOUT_SECONDS = int(os.getenv("AGENTBAY_CODE_TIMEOUT_SECONDS", "60"))  # CodeSpace 单次执行超时（官方建议<=60s）
AGENTBAY_BROWSER_TIMEOUT_MS = int(os.getenv("AGENTBAY_BROWSER_TIMEOUT_MS", "30000"))  # BrowserUse 页面操作超时

# --- User Location Resolution ---
# --- 用户位置解析（fallback 链：env > memory > IP；不再使用系统时区，因 IANA zone 不是地理位置）---
USER_LOCATION = (os.getenv("USER_LOCATION", "") or "").strip()                       # 用户显式指定的城市（最高优先级，工具内部仍以 os.getenv 直读以兼容运行时切换）
LOCATION_IP_LOOKUP_ENABLED = os.getenv("LOCATION_IP_LOOKUP_ENABLED", "true").lower() == "true"   # 是否允许调用公网 IP 接口（ip-api.com / ipapi.co / ip.sb fallback）推断位置；默认开启，隐私敏感用户可显式设为 false 关闭
LOCATION_SSL_VERIFY = os.getenv("LOCATION_SSL_VERIFY", "true").lower() == "true"     # IP 定位 HTTPS 请求是否校验 SSL 证书；设为 false 可跳过证书验证（解决 macOS CERTIFICATE_VERIFY_FAILED）

# --- Web Search (v10) ---
# --- 网络搜索（v10：基于 DDGS/DuckDuckGo 的真实搜索）---
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))  # 单次搜索返回最大结果数
WEB_SEARCH_TIMEOUT = int(os.getenv("WEB_SEARCH_TIMEOUT", "15"))         # 单次搜索超时（秒）

# --- Bailian MCP (Aliyun Search & WebParser, v11) ---
# --- 百炼 MCP（阿里云搜索 & 网页解析，v11 新增）---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")  # 阿里云 DashScope API Key（为空时回退到 DDGS）
BAILIAN_WEBSEARCH_MCP_URL = os.getenv("BAILIAN_WEBSEARCH_MCP_URL", "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp")  # 搜索 MCP 服务端点
# 注意：WebParser MCP 仅支持 SSE 传输（streamable HTTP 会返回 405 "current mcp not support streamableHttp"），
# 因此默认端点用 /sse；若用 env 覆盖此 URL，必须同样指向 /sse 端点。传输选择见 tools/mcp_client.py 的 _SERVER_TRANSPORT。
BAILIAN_WEBPARSER_MCP_URL = os.getenv("BAILIAN_WEBPARSER_MCP_URL", "https://dashscope.aliyuncs.com/api/v1/mcps/WebParser/sse")  # 网页解析 MCP 服务端点（SSE 传输）
# 429 限流 / 瞬时传输错误（SSE BrokenResourceError）的指数退避重试。退避总时长受调用方
# asyncio.wait_for 限制（web_search=WEB_SEARCH_TIMEOUT，fetch_url=2×），超出部分会被外层超时切断
# （WebSearch 仍有 DDGS 兜底；WebParser 无兜底，故重试对 fetch_url 价值最大）。
BAILIAN_MCP_MAX_RETRIES = int(os.getenv("BAILIAN_MCP_MAX_RETRIES", "3"))         # 429/瞬时错误最大重试次数（0=不重试）
BAILIAN_MCP_RETRY_BASE_DELAY = float(os.getenv("BAILIAN_MCP_RETRY_BASE_DELAY", "2.0"))  # 指数退避基础延迟（秒）：delay = base × 2**attempt
BAILIAN_WEBPARSER_MAX_CONCURRENT = int(os.getenv("BAILIAN_WEBPARSER_MAX_CONCURRENT", "1"))  # WebParser MCP 最大并发；默认 1 避免 SSE/429 噪声
BAILIAN_WEBPARSER_MIN_INTERVAL_SECONDS = float(os.getenv("BAILIAN_WEBPARSER_MIN_INTERVAL_SECONDS", "1.0"))  # WebParser 调用最小间隔秒数

# --- Convergence Guidance ---
# --- 收敛指引（防止搜索循环）---
SEARCH_CONVERGENCE_THRESHOLD = int(os.getenv("SEARCH_CONVERGENCE_THRESHOLD", "3"))  # 同工具调用 N 次后注入收敛提示
FETCH_URL_MAX_CONTENT_LENGTH = int(os.getenv("FETCH_URL_MAX_CONTENT_LENGTH", "10000"))  # fetch_url 返回内容最大字符数
FETCH_URL_SHORT_CONTENT_WARNING_LENGTH = int(os.getenv("FETCH_URL_SHORT_CONTENT_WARNING_LENGTH", "80"))  # fetch_url 极短内容告警阈值
TOOL_RESULT_TRUNCATION_LIMIT = int(os.getenv("TOOL_RESULT_TRUNCATION_LIMIT", "2000"))  # ToolCallRecord 成功结果截断长度

# --- Local WebParser (fetch_url primary path) ---
# --- 本地网页解析（fetch_url 主路径）---
LOCAL_WEBPARSER_ENABLED = os.getenv("LOCAL_WEBPARSER_ENABLED", "true").lower() == "true"  # 默认使用本地解析，避免 WebParser MCP 限流
LOCAL_WEBPARSER_TIMEOUT = float(os.getenv("LOCAL_WEBPARSER_TIMEOUT", "20"))  # 本地抓取超时（秒）
LOCAL_WEBPARSER_MAX_BYTES = int(os.getenv("LOCAL_WEBPARSER_MAX_BYTES", "2097152"))  # 本地抓取最大响应字节数（默认 2 MiB）
LOCAL_WEBPARSER_USER_AGENT = os.getenv("LOCAL_WEBPARSER_USER_AGENT", "ManusDemoBot/1.0")  # fetch_url 本地抓取 UA
LOCAL_WEBPARSER_RESPECT_ROBOTS = os.getenv("LOCAL_WEBPARSER_RESPECT_ROBOTS", "false").lower() == "true"  # 是否遵循 robots.txt
LOCAL_WEBPARSER_BROWSER_FALLBACK = os.getenv("LOCAL_WEBPARSER_BROWSER_FALLBACK", "false").lower() == "true"  # 是否启用 Playwright 渲染兜底
LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN = os.getenv("LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN", "false").lower() == "true"  # 本地失败时是否回退百炼 WebParser
LOCAL_WEBPARSER_MIN_CONTENT_LENGTH = int(os.getenv("LOCAL_WEBPARSER_MIN_CONTENT_LENGTH", "120"))  # 本地解析结果低于该长度则尝试 fallback
LOCAL_WEBPARSER_CACHE_SIZE = int(os.getenv("LOCAL_WEBPARSER_CACHE_SIZE", "64"))  # 进程内 fetch_html LRU 缓存条目数，0=关闭

# --- v6.0 Feature Flags (向后兼容，默认关闭) ---
# --- ReAct Engine ---
ENABLE_REACT_ENGINE_V2 = os.getenv("ENABLE_REACT_ENGINE_V2", "false").lower() == "true"  # 使用抽取后的统一 ReActEngine

# --- LLM Client Retry ---
LLM_RETRY_ENABLED = os.getenv("LLM_RETRY_ENABLED", "false").lower() == "true"  # LLM 调用重试机制
LLM_RETRY_MAX_ATTEMPTS = int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "3"))  # 最大重试次数
LLM_RETRY_BACKOFF_FACTOR = float(os.getenv("LLM_RETRY_BACKOFF_FACTOR", "2.0"))  # 退避因子

# --- Token Usage Tracking ---
TOKEN_TRACKING_ENABLED = os.getenv("TOKEN_TRACKING_ENABLED", "true").lower() == "true"  # 是否启用 Token 消耗追踪

# --- v8.0 Goal-Driven Planning Feature Flags ---
# --- 目标驱动规划（v8 新增）---
ENABLE_GOAL_DRIVEN_PLANNER = os.getenv("ENABLE_GOAL_DRIVEN_PLANNER", "false").lower() == "true"  # 是否启用 v8 目标驱动规划引擎（默认关闭，向后兼容）
GOAL_REANCHOR_INTERVAL = int(os.getenv("GOAL_REANCHOR_INTERVAL", "5"))  # 每隔多少次外层迭代重新锚定目标文档
GOAL_REFLECTION_INTERVAL = int(os.getenv("GOAL_REFLECTION_INTERVAL", "1"))  # 每隔多少次外层迭代执行目标反思（1=每次都反思）
MAX_GOAL_DRIVEN_ITERATIONS = int(os.getenv("MAX_GOAL_DRIVEN_ITERATIONS", str(MAX_TODO_ITEMS * MAX_TODO_RETRIES)))  # v8 主循环最大迭代数
GOAL_DRIVEN_STAGNATION_WINDOW = int(os.getenv("GOAL_DRIVEN_STAGNATION_WINDOW", "3"))  # 连续多少轮无进度突破则提前终止

# --- v9.0 SubAgent Feature Flags (Claude Code Subagent pattern, default off) ---
# --- 子智能体（v9 新增）- Claude Code Subagent 模式 ---
SUBAGENT_ENABLED = os.getenv("SUBAGENT_ENABLED", "false").lower() == "true"  # 是否启用 SubAgent 模式
SUBAGENT_MAX_ITERATIONS = int(os.getenv("SUBAGENT_MAX_ITERATIONS", str(MAX_REACT_ITERATIONS)))  # SubAgent 内部 ReAct 最大迭代次数
SUBAGENT_TIMEOUT = int(os.getenv("SUBAGENT_TIMEOUT", str(NODE_EXECUTION_TIMEOUT)))  # SubAgent 执行超时时间（秒）
SUBAGENT_MAX_CONCURRENT = int(os.getenv("SUBAGENT_MAX_CONCURRENT", "2"))  # 最大并发 SubAgent 数量（降到 2 削峰，缓解并行 wave 对外部 API 的瞬时 QPS 限流）
SUBAGENT_SUMMARY_MAX_LENGTH = int(os.getenv("SUBAGENT_SUMMARY_MAX_LENGTH", "2000"))  # SubAgent 返回摘要最大字符数
SUBAGENT_MAX_CALLS_PER_TASK = int(os.getenv("SUBAGENT_MAX_CALLS_PER_TASK", "3"))  # 反模式 #3/8：单任务 SubAgent 调用次数上限
SUBAGENT_MAX_TOKENS_PER_CALL = int(os.getenv("SUBAGENT_MAX_TOKENS_PER_CALL", "120000"))  # 反模式 #8：单次 SubAgent 调用 Token 预算上限（深度联网调研子任务 50000 偏小易触顶失败，上调至 120000；仍为安全上限非目标值）
SUBAGENT_DEFAULT_TOOL_WHITELIST = os.getenv("SUBAGENT_DEFAULT_TOOL_WHITELIST", "")  # 默认工具白名单（逗号分隔，空=全量授权）

# --- Wave-3/4 SubAgent UX & resource limits ---
# Wave-3/4 子智能体 UX 与资源限制
SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH = int(os.getenv("SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH", "2000"))  # L2：SubAgent task_description 最大字符数,超出则截断 + warning
SUBAGENT_ITERATION_EVENT_VERBOSITY = os.getenv("SUBAGENT_ITERATION_EVENT_VERBOSITY", "summary").lower()  # L5: subagent_iteration 事件 UI 粒度: summary（仅每 N 轮）/ full（全部）/ silent（关闭渲染）
SUBAGENT_ITERATION_EVENT_EVERY_N = int(os.getenv("SUBAGENT_ITERATION_EVENT_EVERY_N", "2"))  # L5 summary 模式下每 N 轮渲染一次

# --- Emergent parallel multi-agent dispatch (default off) ---
# --- 隐式规划并行多智能体派发（默认关闭）---
# emergent 路径：把无依赖的 ready TODO 集一次性并发委派给隔离 SubAgent。
# 仅在 SUBAGENT_ENABLED=true 且 emergent 拿到了 subagent 工具时生效；遵循"新特性默认关"约定。
EMERGENT_PARALLEL_TODOS = os.getenv("EMERGENT_PARALLEL_TODOS", "false").lower() == "true"

# --- v13.0 Human-in-the-Loop Feature Flags ---
# --- 人机交互（v13 新增）---
HITL_ENABLED = os.getenv("HITL_ENABLED", "false").lower() == "true"  # 是否启用 HITL 人机交互（默认关闭，向后兼容）
HITL_MAX_PROMPTS_PER_TASK = int(os.getenv("HITL_MAX_PROMPTS_PER_TASK", "5"))  # 单任务最大 ask_user 调用次数（防止无限提问循环）
HITL_USER_INPUT_TIMEOUT = int(os.getenv("HITL_USER_INPUT_TIMEOUT", "120"))  # 等待用户输入超时（秒），超时后工具返回 Error 由 LLM 自主继续

# --- v13.x Reasoning Model Adaptation (v14 in progress) ---
# --- 推理模型适配（v14 新增）---
REASONING_TOKEN_TRACKING = os.getenv("REASONING_TOKEN_TRACKING", "true").lower() == "true"  # 是否追踪 reasoning tokens（默认开启）

# --- v14 Phase 2: Reasoning Engine ---
# --- 推理引擎（v14 Phase 2 新增）---
ENABLE_REASONING_ENGINE = os.getenv("ENABLE_REASONING_ENGINE", "false").lower() == "true"  # 启用 ReasoningEngine（默认关闭，灰度切换）
MAX_THINKING_TOKENS = int(os.getenv("MAX_THINKING_TOKENS", "10000"))  # 推理模型 thinking token 预算上限
MAX_THINKING_ROUNDS = int(os.getenv("MAX_THINKING_ROUNDS", "5"))  # 连续纯思考轮次硬上限（防无限循环，独立于 token tracking）
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "auto").lower()  # 推理力度：auto / low / medium / high（auto = 由 classifier 动态决定）

# --- v14.5 Task Resume ---
# --- 任务恢复 / Checkpoint（v14.5 新增）---
TASK_RESUME_ENABLED = os.getenv("TASK_RESUME_ENABLED", "true").lower() == "true"  # 是否启用 Task Resume checkpoint
CHECKPOINT_DIR = os.path.expanduser(os.getenv("CHECKPOINT_DIR", os.path.join(MEMORY_DIR, "checkpoints")))  # checkpoint 文件存储目录
CHECKPOINT_MAX_PER_TASK = int(os.getenv("CHECKPOINT_MAX_PER_TASK", "5"))  # 每个任务最多保留的 checkpoint 文件数
CHECKPOINT_RETENTION_DAYS = int(os.getenv("CHECKPOINT_RETENTION_DAYS", "7"))  # 已完成 checkpoint 保留天数

# --- v15 Agentic Memory ---
# --- 结构化记忆（v15 新增）---
AGENTIC_MEMORY_ENABLED = os.getenv("AGENTIC_MEMORY_ENABLED", "false").lower() == "true"  # 启用 Agentic Memory（默认关闭，向后兼容）
MEMORY_TOOLS_ENABLED = os.getenv("MEMORY_TOOLS_ENABLED", "false").lower() == "true"  # 注册 memory tools 到 ReAct（默认关闭）
MEMORY_MIN_CONFIDENCE = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.35"))  # 记忆检索最低置信度阈值
MEMORY_SEARCH_TOP_K = int(os.getenv("MEMORY_SEARCH_TOP_K", "3"))  # 记忆检索返回最大条数
MEMORY_LLM_CONSOLIDATION_ENABLED = os.getenv("MEMORY_LLM_CONSOLIDATION_ENABLED", "false").lower() == "true"  # 启用 LLM 辅助记忆巩固（默认关闭）

# --- v17 Self-Evolution ---
# --- 自演化（v17 新增：经验学习 + 失败反思）---
SELF_EVOLUTION_ENABLED = os.getenv("SELF_EVOLUTION_ENABLED", "false").lower() == "true"  # v17 主开关（需 AGENTIC_MEMORY_ENABLED=true 才生效）
SELF_EVOLUTION_LLM_EXTRACTION = os.getenv("SELF_EVOLUTION_LLM_EXTRACTION", "false").lower() == "true"  # 用 LLM 提炼经验/失败三元组（关则走确定性提炼）
SELF_EVOLUTION_MAX_HINTS = int(os.getenv("SELF_EVOLUTION_MAX_HINTS", "3"))  # 单次注入的失败避坑提示上限
SELF_EVOLUTION_CONFIDENCE_CAP = float(os.getenv("SELF_EVOLUTION_CONFIDENCE_CAP", "0.6"))  # 自动学习记忆 confidence 上限（防 memory poisoning）
SELF_EVOLUTION_PREFERENCE_ENABLED = os.getenv("SELF_EVOLUTION_PREFERENCE_ENABLED", "true").lower() == "true"  # v17.4：从 HITL 交互学习用户偏好（仅在 SELF_EVOLUTION_ENABLED + HITL 激活时生效）

# --- v20.5 Skill Auto-Distillation ---
# --- 技能自动蒸馏（v20.5：从高频成功模式蒸馏 SKILL.md，需 SELF_EVOLUTION + SKILLS 同时启用）
SKILL_AUTO_DISTILL_ENABLED = os.getenv("SKILL_AUTO_DISTILL_ENABLED", "false").lower() == "true"  # v20.5 蒸馏主开关
SKILL_AUTO_DISTILL_MIN_SUCCESSES = int(os.getenv("SKILL_AUTO_DISTILL_MIN_SUCCESSES", "3"))  # 同类任务成功 N 次后触发蒸馏
SKILL_AUTO_DISTILL_CONFIDENCE_CAP = float(os.getenv("SKILL_AUTO_DISTILL_CONFIDENCE_CAP", "0.55"))  # 蒸馏记忆 confidence 上限

# --- v17.3 Classifier Calibration ---
# --- 分类器校准（v17.3：外置决策阈值，离线网格搜索建议，禁止静默自改）---
CLASSIFIER_SIMPLE_THRESHOLD = int(os.getenv("CLASSIFIER_SIMPLE_THRESHOLD", "-1"))   # 规则评分 <= 此值 → simple（默认 -1，等于原硬编码）
CLASSIFIER_COMPLEX_THRESHOLD = int(os.getenv("CLASSIFIER_COMPLEX_THRESHOLD", "2"))  # 规则评分 >= 此值 → complex（默认 2，等于原硬编码）

# --- v18.1 Workflow Engine ---
# --- 确定性工具工作流（v18.1：声明式工具步骤，无每步 LLM）---
WORKFLOW_ENABLED = os.getenv("WORKFLOW_ENABLED", "true").lower() == "true"  # Workflow 引擎开关（仅经 --workflow/run_workflow 显式触发，默认 true 不影响 agentic 路径）

# --- v18.2 Handoff (context-passing + control transfer, default off) ---
# --- 专家 Handoff 委派（v18.2：上下文传递 + 控制权转移，与 SubAgent 隔离式互补）---
HANDOFF_ENABLED = os.getenv("HANDOFF_ENABLED", "false").lower() == "true"  # Handoff 主开关
HANDOFF_ALLOW_ASK_USER = os.getenv("HANDOFF_ALLOW_ASK_USER", "false").lower() == "true"  # 专家 agent 是否可调 ask_user（路线图要求显式配置）
HANDOFF_MAX_CALLS_PER_TASK = int(os.getenv("HANDOFF_MAX_CALLS_PER_TASK", "2"))  # 单任务 handoff 调用上限
HANDOFF_TIMEOUT = int(os.getenv("HANDOFF_TIMEOUT", str(NODE_EXECUTION_TIMEOUT)))  # 专家执行超时（秒）
HANDOFF_MAX_ITERATIONS = int(os.getenv("HANDOFF_MAX_ITERATIONS", str(MAX_REACT_ITERATIONS)))  # 专家 ReAct 迭代上限

# --- v16 MCP Bridge ---
# --- MCP 桥接（v16 新增）---
MCP_BRIDGE_ENABLED = os.getenv("MCP_BRIDGE_ENABLED", "false").lower() == "true"  # MCP Bridge 客户端总开关（默认关闭）
MCP_BRIDGE_CONFIG_PATH = os.getenv("MCP_BRIDGE_CONFIG_PATH", "")                # JSON 配置文件路径
MCP_BRIDGE_SERVERS_JSON = os.getenv("MCP_BRIDGE_SERVERS_JSON", "")              # 内联 JSON 服务器配置（快速测试）
MCP_BRIDGE_TOOL_PREFIX = os.getenv("MCP_BRIDGE_TOOL_PREFIX", "mcp")             # 工具名前缀：{prefix}_{server}_{tool}
MCP_BRIDGE_SCHEMA_MODE = os.getenv("MCP_BRIDGE_SCHEMA_MODE", "loose").lower()   # Schema 转换模式：loose | strict
MCP_BRIDGE_DISCOVERY_TTL = int(os.getenv("MCP_BRIDGE_DISCOVERY_TTL", "300"))    # 工具重新发现间隔（秒）
MCP_BRIDGE_CALL_TIMEOUT = int(os.getenv("MCP_BRIDGE_CALL_TIMEOUT", "30"))       # 单次 MCP 工具调用超时（秒）

# --- v16 MCP Server ---
# --- MCP 服务端（v16 新增）---
MCP_SERVER_ENABLED = os.getenv("MCP_SERVER_ENABLED", "false").lower() == "true"       # MCP Server 开关（默认关闭）
MCP_SERVER_TRANSPORT = os.getenv("MCP_SERVER_TRANSPORT", "streamable_http").lower()   # 传输模式：streamable_http | stdio
MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")                           # HTTP 监听地址
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8080"))                            # HTTP 监听端口

# --- v18.3/18.4 Remote SubAgent + A2A ---
# --- 远端 Agent（v18.3 通过 MCP 调远端 agent）+ A2A 原型（v18.4 AgentCard + 任务信封）---
MCP_SERVER_EXPOSE_AGENT = os.getenv("MCP_SERVER_EXPOSE_AGENT", "false").lower() == "true"  # 服务端暴露 get_agent_card + a2a_run_task（需 MCP_SERVER_ENABLED）
REMOTE_SUBAGENT_ENABLED = os.getenv("REMOTE_SUBAGENT_ENABLED", "false").lower() == "true"  # 客户端 remote_subagent 工具开关
REMOTE_AGENT_SERVER_JSON = os.getenv("REMOTE_AGENT_SERVER_JSON", "")                       # 远端 agent server 的 MCPServerConfig 内联 JSON
REMOTE_SUBAGENT_MAX_CALLS_PER_TASK = int(os.getenv("REMOTE_SUBAGENT_MAX_CALLS_PER_TASK", "2"))  # 单任务远端调用上限
REMOTE_SUBAGENT_TIMEOUT = int(os.getenv("REMOTE_SUBAGENT_TIMEOUT", str(NODE_EXECUTION_TIMEOUT)))  # 远端任务超时（秒）
REMOTE_AGENT_FETCH_CARD = os.getenv("REMOTE_AGENT_FETCH_CARD", "true").lower() == "true"   # 调用前是否先拉取 AgentCard

# --- v19 Guardrails (security; OWASP Agentic Top 10 taxonomy) ---
# --- 安全护栏（v19；以 OWASP ASI 为分类，默认关，向后兼容）---
GUARDRAILS_ENABLED = os.getenv("GUARDRAILS_ENABLED", "false").lower() == "true"           # v19 主开关（关 → current_guardrail() 返回 None，零开销）
GUARDRAIL_TOOL_ENABLED = os.getenv("GUARDRAIL_TOOL_ENABLED", "true").lower() == "true"     # 19.1 工具输入层
GUARDRAIL_INPUT_ENABLED = os.getenv("GUARDRAIL_INPUT_ENABLED", "true").lower() == "true"   # 19.2 工具输出/上下文层
GUARDRAIL_OUTPUT_ENABLED = os.getenv("GUARDRAIL_OUTPUT_ENABLED", "true").lower() == "true" # 19.3 输出层
GUARDRAIL_TOOL_MODE = os.getenv("GUARDRAIL_TOOL_MODE", "block").lower()                    # block | observe
GUARDRAIL_INPUT_MODE = os.getenv("GUARDRAIL_INPUT_MODE", "neutralize").lower()             # neutralize | annotate | observe
GUARDRAIL_OUTPUT_MODE = os.getenv("GUARDRAIL_OUTPUT_MODE", "redact").lower()               # redact | observe
GUARDRAIL_WRITE_CONFIRM = os.getenv("GUARDRAIL_WRITE_CONFIRM", "block").lower()            # block | confirm | allow

# --- v20 Agent Skills ---
# --- 智能体技能（v20 新增）---
SKILLS_ENABLED = os.getenv("SKILLS_ENABLED", "false").lower() == "true"                     # v20 主开关（默认关闭，向后兼容）
SKILLS_PROJECT_DIR = os.path.join(os.path.dirname(__file__), ".agents", "skills")            # 项目级技能目录（可信，随代码版本管理）
SKILLS_USER_DIR = os.path.expanduser(os.getenv("SKILLS_USER_DIR", "~/.manus_demo/skills"))   # 用户级技能目录（半可信）
SKILLS_DIRS = os.getenv("SKILLS_DIRS", "")                                                  # 额外技能目录（逗号分隔，优先级最低）
SKILLS_MAX_ACTIVATIONS_PER_TASK = int(os.getenv("SKILLS_MAX_ACTIVATIONS_PER_TASK", "3"))     # 单任务最大技能激活次数
SKILLS_MAX_CONTENT_TOKENS = int(os.getenv("SKILLS_MAX_CONTENT_TOKENS", "5000"))              # 单技能内容最大 token 数（4 chars/token 估算）

# --- v20.6 Skill Optimization Loop ---
# --- 技能优化闭环（v20.6：评估→诊断→修订→验证→部署；默认只生成 diff，不自动写入）---
SKILL_OPTIMIZE_LLM_ENABLED = os.getenv("SKILL_OPTIMIZE_LLM_ENABLED", "false").lower() == "true"  # 使用 LLM 辅助修订 SKILL.md
SKILL_OPTIMIZE_VALIDATION_RATIO = float(os.getenv("SKILL_OPTIMIZE_VALIDATION_RATIO", "0.2"))     # train/validation split 验证集比例
SKILL_OPTIMIZE_MAX_TOKENS = int(os.getenv("SKILL_OPTIMIZE_MAX_TOKENS", "1200"))                  # LLM 修订最大输出 token

# --- v21 Evaluation Platform ---
# --- 评测平台（v21：文档 → 评测集 → 执行 → 报告 → 聚合分析）---
EVAL_PLATFORM_DIR = os.path.expanduser(os.getenv("EVAL_PLATFORM_DIR", "~/.manus_demo/evalplatform"))  # 平台数据目录（文档/评测集/运行/报告/分析）
EVAL_PLATFORM_PORT = int(os.getenv("EVAL_PLATFORM_PORT", "8720"))                                     # Web 服务端口
EVAL_PLATFORM_MAX_DOC_CHARS = int(os.getenv("EVAL_PLATFORM_MAX_DOC_CHARS", "24000"))                  # 送入 LLM 的文档字符上限（超出截断）
EVAL_PLATFORM_DEFAULT_NUM_TASKS = int(os.getenv("EVAL_PLATFORM_DEFAULT_NUM_TASKS", "6"))              # 默认生成任务数
EVAL_PLATFORM_GEN_MAX_TOKENS = int(os.getenv("EVAL_PLATFORM_GEN_MAX_TOKENS", "4096"))                 # 评测集生成 LLM 输出 token 上限

# --- v14 Phase 3: Harness Configuration ---
# --- Harness 配置层（v14 Phase 3 新增）---
REACT_TEMPERATURE = float(os.getenv("REACT_TEMPERATURE", "0.5"))          # ReActEngine chat_with_tools 温度
REASONING_TEMPERATURE = float(os.getenv("REASONING_TEMPERATURE", "0.5"))  # ReasoningEngine chat_with_tools 温度
PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.3"))      # PlannerAgent 温度
REFLECTOR_TEMPERATURE = float(os.getenv("REFLECTOR_TEMPERATURE", "0.1"))  # ReflectorAgent 温度
CONVERGENCE_ESCALATION_MULTIPLIER = int(os.getenv("CONVERGENCE_ESCALATION_MULTIPLIER", "2"))  # 收敛提示升级倍数（threshold * N 触发 CRITICAL）
THINKING_AWARE_CONTEXT = os.getenv("THINKING_AWARE_CONTEXT", "true").lower() == "true"  # ContextManager 是否感知 thinking_content

# ======================================================================
# Tracing Configuration (v7)
# 全链路追踪配置（v7 新增）
# ======================================================================
TRACING_ENABLED: bool = os.getenv("TRACING_ENABLED", "false").lower() == "true"       # 总开关（默认关闭，向后兼容）
TRACING_BACKEND: str = os.getenv("TRACING_BACKEND", "console")                        # 导出后端：console / file / rich / otlp / phoenix
TRACING_ENDPOINT: str = os.getenv("TRACING_ENDPOINT", "http://localhost:4318")         # OTLP HTTP 端点地址
TRACING_SERVICE_NAME: str = os.getenv("TRACING_SERVICE_NAME", "manus-demo")            # 服务标识
TRACING_SAMPLE_RATE: float = max(0.0, min(1.0, float(os.getenv("TRACING_SAMPLE_RATE", "1.0"))))  # 采样率 (clamped to 0.0-1.0)
TRACING_LOG_PROMPTS: bool = os.getenv("TRACING_LOG_PROMPTS", "false").lower() == "true"  # 是否记录完整 prompt（默认关闭，隐私保护）
TRACING_MAX_ATTRIBUTE_LENGTH: int = int(os.getenv("TRACING_MAX_ATTR_LENGTH", "1000"))  # 属性值最大字符数
