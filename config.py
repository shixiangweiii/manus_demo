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
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "8000"))    # 上下文 Token 上限，超出后触发摘要压缩
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

# --- v6.0 Feature Flags (向后兼容，默认关闭) ---
# --- ReAct Engine ---
ENABLE_REACT_ENGINE_V2 = os.getenv("ENABLE_REACT_ENGINE_V2", "false").lower() == "true"  # 使用抽取后的统一 ReActEngine

# --- LLM Client Retry ---
LLM_RETRY_ENABLED = os.getenv("LLM_RETRY_ENABLED", "false").lower() == "true"  # LLM 调用重试机制
LLM_RETRY_MAX_ATTEMPTS = int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "3"))  # 最大重试次数
LLM_RETRY_BACKOFF_FACTOR = float(os.getenv("LLM_RETRY_BACKOFF_FACTOR", "2.0"))  # 退避因子

# --- Token Usage Tracking ---
TOKEN_TRACKING_ENABLED = os.getenv("TOKEN_TRACKING_ENABLED", "true").lower() == "true"  # 是否启用 Token 消耗追踪
