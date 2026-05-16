"""
Configuration module for the Manus Demo.
Loads settings from environment variables or .env file.
Manus Demo 配置模块。
从环境变量或 .env 文件加载所有配置项。
"""

import os
from dotenv import load_dotenv

load_dotenv()  # 自动读取项目根目录的 .env 文件（若存在），优先级低于系统环境变量

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
SUBPROCESS_MAX_OUTPUT_BYTES = int(os.getenv("SUBPROCESS_MAX_OUTPUT_BYTES", str(512 * 1024)))  # 单次子进程（Shell/Python）最大输出字节数，默认 512KB
SHELL_MAX_CONCURRENT = int(os.getenv("SHELL_MAX_CONCURRENT", "3"))                    # 最大并发 Shell 子进程数
CODE_MAX_CONCURRENT = int(os.getenv("CODE_MAX_CONCURRENT", "3"))                      # 最大并发代码执行子进程数

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
BAILIAN_WEBPARSER_MCP_URL = os.getenv("BAILIAN_WEBPARSER_MCP_URL", "https://dashscope.aliyuncs.com/api/v1/mcps/WebParser/mcp")  # 网页解析 MCP 服务端点

# --- Convergence Guidance ---
# --- 收敛指引（防止搜索循环）---
SEARCH_CONVERGENCE_THRESHOLD = int(os.getenv("SEARCH_CONVERGENCE_THRESHOLD", "3"))  # 同工具调用 N 次后注入收敛提示
FETCH_URL_MAX_CONTENT_LENGTH = int(os.getenv("FETCH_URL_MAX_CONTENT_LENGTH", "10000"))  # fetch_url 返回内容最大字符数
TOOL_RESULT_TRUNCATION_LIMIT = int(os.getenv("TOOL_RESULT_TRUNCATION_LIMIT", "2000"))  # ToolCallRecord 成功结果截断长度

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
SUBAGENT_MAX_CONCURRENT = int(os.getenv("SUBAGENT_MAX_CONCURRENT", "3"))  # 最大并发 SubAgent 数量
SUBAGENT_SUMMARY_MAX_LENGTH = int(os.getenv("SUBAGENT_SUMMARY_MAX_LENGTH", "2000"))  # SubAgent 返回摘要最大字符数
SUBAGENT_MAX_CALLS_PER_TASK = int(os.getenv("SUBAGENT_MAX_CALLS_PER_TASK", "3"))  # 反模式 #3/8：单任务 SubAgent 调用次数上限
SUBAGENT_MAX_TOKENS_PER_CALL = int(os.getenv("SUBAGENT_MAX_TOKENS_PER_CALL", "50000"))  # 反模式 #8：单次 SubAgent 调用 Token 预算上限
SUBAGENT_DEFAULT_TOOL_WHITELIST = os.getenv("SUBAGENT_DEFAULT_TOOL_WHITELIST", "")  # 默认工具白名单（逗号分隔，空=全量授权）

# --- v13.0 Human-in-the-Loop Feature Flags ---
# --- 人机交互（v13 新增）---
HITL_ENABLED = os.getenv("HITL_ENABLED", "false").lower() == "true"  # 是否启用 HITL 人机交互（默认关闭，向后兼容）
HITL_MAX_PROMPTS_PER_TASK = int(os.getenv("HITL_MAX_PROMPTS_PER_TASK", "5"))  # 单任务最大 ask_user 调用次数（防止无限提问循环）
HITL_USER_INPUT_TIMEOUT = int(os.getenv("HITL_USER_INPUT_TIMEOUT", "120"))  # 等待用户输入超时（秒），超时后工具返回 Error 由 LLM 自主继续

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
