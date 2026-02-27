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
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-55470978f1044b70955df04ab6908c02")  # API key / API 密钥
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
PLAN_MODE = os.getenv("PLAN_MODE", "auto")  # "auto"=两阶段混合分类 | "simple"=强制v1扁平计划 | "complex"=强制v2 DAG

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

# --- Tools ---
# --- 工具参数 ---
SANDBOX_DIR = os.path.expanduser(os.getenv("SANDBOX_DIR", "~/.manus_demo/sandbox"))  # 文件操作沙箱目录（防止越权访问）
CODE_EXEC_TIMEOUT = int(os.getenv("CODE_EXEC_TIMEOUT", "30"))                        # Python 代码执行超时时间（秒）
