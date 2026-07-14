"""
Declarative config schema for the WebUI config panel.
WebUI 配置面板的声明式配置 schema。

Maps config.py module attributes to grouped, typed UI form items.
Does NOT modify config.py. Apply/restore follows the proven
getattr/setattr shape of evaluation/variants.apply_variant.
将 config.py 的模块属性映射为分组、带类型的 UI 表单项。
不修改 config.py。apply/restore 沿用 evaluation/variants.apply_variant
的 getattr/setattr 形态。

Conventions / 约定:
- `name` 是 config.py 的属性名（多数同 env 变量名；个别不同，如
  TRACING_MAX_ATTRIBUTE_LENGTH 的 env 名是 TRACING_MAX_ATTR_LENGTH——
  本模块只操作 config 属性，与 env 名无关）。
- sensitive: 值永不出进程（schema 只带 configured 布尔），且拒绝经 UI 修改。
- restart_required: import 时被其他模块捕获（如 tracing/config.py），
  运行时改无效 → UI 只读展示，validate 拒绝。
- env_passthrough: 消费方绕过 config 直读 os.environ（如 USER_LOCATION，
  tools/user_location.py 实时 os.getenv）→ apply/restore 需同步写回 env。
- derived_note: 默认值在 import 时由其他配置派生（运行时直接改本项仍生效）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import config


class ConfigValidationError(ValueError):
    """Per-field validation errors. 按字段的校验错误集合。"""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__(f"config validation failed: {errors}")


@dataclass(frozen=True)
class ConfigItem:
    name: str                          # config.py attribute name / config.py 属性名
    type: str                          # bool | int | float | str | enum
    label: str                         # 中文标签（界面展示）
    description: str = ""              # 一句话说明
    options: tuple[str, ...] = ()      # enum 可选值
    core: bool = False                 # True=默认可见；False=收进"高级"折叠区
    sensitive: bool = False            # 敏感项：只显示是否已配置，不可编辑
    restart_required: bool = False     # import 时捕获，改动需重启进程
    env_passthrough: bool = False      # 同步写 os.environ（消费方直读 env）
    derived_note: str = ""             # 默认值派生说明


@dataclass(frozen=True)
class ConfigGroup:
    id: str
    title: str
    items: tuple[ConfigItem, ...] = field(default_factory=tuple)


def _i(name: str, type_: str, label: str, description: str = "", **kw) -> ConfigItem:
    """Shorthand item constructor. 条目构造简写。"""
    return ConfigItem(name=name, type=type_, label=label, description=description, **kw)


# =====================================================================
# 分组定义（顺序即界面顺序）/ group definitions (UI order)
# =====================================================================

GROUPS: tuple[ConfigGroup, ...] = (
    ConfigGroup("llm", "LLM 接口", (
        _i("LLM_BASE_URL", "str", "API 地址", "OpenAI 兼容接口地址", core=True),
        _i("LLM_API_KEY", "str", "API Key", "LLM API 密钥（请通过 .env 设置）", core=True, sensitive=True),
        _i("LLM_MODEL", "str", "模型名称", "如 deepseek-chat", core=True),
        _i("LLM_RETRY_ENABLED", "bool", "调用重试", "LLM 调用失败自动重试"),
        _i("LLM_RETRY_MAX_ATTEMPTS", "int", "最大重试次数"),
        _i("LLM_RETRY_BACKOFF_FACTOR", "float", "重试退避因子"),
        _i("TOKEN_TRACKING_ENABLED", "bool", "Token 追踪", "记录每次调用的 token 消耗"),
        _i("REASONING_TOKEN_TRACKING", "bool", "推理 Token 追踪"),
    )),
    ConfigGroup("engine", "引擎与规划", (
        _i("PLAN_MODE", "enum", "规划模式", "auto=自动分类；其余强制指定引擎",
           options=("auto", "simple", "complex", "emergent"), core=True),
        _i("ENABLE_GOAL_DRIVEN_PLANNER", "bool", "目标驱动规划 (v8)",
           "emergent 路径内改用目标锚定 + 动态 TODO", core=True),
        _i("EMERGENT_PARALLEL_TODOS", "bool", "并行 TODO 派发",
           "就绪的独立 TODO 并发派给隔离 SubAgent（需开启 SubAgent）", core=True),
        _i("MAX_TODO_ITEMS", "int", "TODO 上限", "TODO 列表最大项数", core=True),
        _i("EMERGENT_PLANNING_ENABLED", "bool", "隐式规划路由", "关闭后不再路由到 emergent"),
        _i("MAX_TODO_RETRIES", "int", "TODO 重试上限"),
        _i("TODO_COMPRESSION_THRESHOLD", "float", "TODO 压缩阈值", "上下文使用率达到该值时压缩"),
        _i("MAX_EMERGENT_OUTER_ITERATIONS", "int", "emergent 外层迭代上限",
           derived_note="默认 = MAX_TODO_ITEMS × MAX_TODO_RETRIES（import 时计算；直接改本项生效）"),
        _i("ADAPTIVE_PLANNING_ENABLED", "bool", "自适应规划 (v3)", "超步间自适应调整计划"),
        _i("ADAPT_PLAN_INTERVAL", "int", "自适应检查间隔（超步）"),
        _i("ADAPT_PLAN_MIN_COMPLETED", "int", "自适应启动最少完成节点"),
        _i("GOAL_REANCHOR_INTERVAL", "int", "目标重锚定间隔"),
        _i("GOAL_REFLECTION_INTERVAL", "int", "目标反思间隔"),
        _i("MAX_GOAL_DRIVEN_ITERATIONS", "int", "goal-driven 迭代上限",
           derived_note="默认 = MAX_TODO_ITEMS × MAX_TODO_RETRIES（import 时计算）"),
        _i("GOAL_DRIVEN_STAGNATION_WINDOW", "int", "停滞检测窗口", "连续 N 轮无进展提前终止"),
        _i("CLASSIFIER_SIMPLE_THRESHOLD", "int", "分类器 simple 阈值 (v17.3)"),
        _i("CLASSIFIER_COMPLEX_THRESHOLD", "int", "分类器 complex 阈值 (v17.3)"),
        _i("WORKFLOW_ENABLED", "bool", "Workflow 引擎 (v18.1)", "仅影响 --workflow 显式触发路径"),
    )),
    ConfigGroup("limits", "执行限额", (
        _i("MAX_CONTEXT_TOKENS", "int", "上下文 Token 上限", "超出后触发摘要压缩", core=True),
        _i("MAX_REACT_ITERATIONS", "int", "ReAct 迭代上限", "单个执行单元的 ReAct 循环次数", core=True),
        _i("MAX_REPLAN_ATTEMPTS", "int", "重规划上限", "反思失败后的最大重规划次数", core=True),
        _i("MAX_PARALLEL_NODES", "int", "DAG 并行节点数", "每个 Super-step 最多并行节点", core=True),
        _i("DAG_SERIAL_EXECUTION", "bool", "DAG 串行执行", "false 恢复并行执行", core=True),
        _i("NODE_EXECUTION_TIMEOUT", "int", "节点超时（秒）", core=True),
        _i("MAX_CHECKPOINTS", "int", "内存 Checkpoint 上限"),
        _i("TOOL_FAILURE_THRESHOLD", "int", "工具失败切换阈值"),
    )),
    ConfigGroup("subagent", "子智能体 SubAgent", (
        _i("SUBAGENT_ENABLED", "bool", "启用 SubAgent (v9)", "注册 subagent 工具（深度=1 隔离执行）", core=True),
        _i("SUBAGENT_MAX_CONCURRENT", "int", "最大并发数", core=True),
        _i("SUBAGENT_MAX_CALLS_PER_TASK", "int", "单任务调用上限", core=True),
        _i("SUBAGENT_TIMEOUT", "int", "执行超时（秒）",
           derived_note="默认 = NODE_EXECUTION_TIMEOUT（import 时取值）"),
        _i("SUBAGENT_MAX_ITERATIONS", "int", "内部迭代上限",
           derived_note="默认 = MAX_REACT_ITERATIONS（import 时取值）"),
        _i("SUBAGENT_SUMMARY_MAX_LENGTH", "int", "返回摘要最大字符"),
        _i("SUBAGENT_MAX_TOKENS_PER_CALL", "int", "单次 Token 预算"),
        _i("SUBAGENT_DEFAULT_TOOL_WHITELIST", "str", "默认工具白名单", "逗号分隔，空=全量授权"),
        _i("SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH", "int", "任务描述截断长度"),
        _i("SUBAGENT_ITERATION_EVENT_VERBOSITY", "enum", "迭代事件粒度",
           options=("summary", "full", "silent")),
        _i("SUBAGENT_ITERATION_EVENT_EVERY_N", "int", "summary 模式采样间隔"),
    )),
    ConfigGroup("hitl", "人机交互 HITL", (
        _i("HITL_ENABLED", "bool", "启用 HITL (v13)", "注册 ask_user 工具（Web 端内联问答）", core=True),
        _i("HITL_MAX_PROMPTS_PER_TASK", "int", "单任务提问上限", core=True),
        _i("HITL_USER_INPUT_TIMEOUT", "int", "等待输入超时（秒）", "超时后 LLM 自主继续", core=True),
    )),
    ConfigGroup("memory", "记忆", (
        _i("AGENTIC_MEMORY_ENABLED", "bool", "结构化记忆 (v15)", core=True),
        _i("MEMORY_TOOLS_ENABLED", "bool", "记忆工具", "注册 memory_search/store/consolidate/revoke", core=True),
        _i("MEMORY_SEARCH_TOP_K", "int", "检索返回条数", core=True),
        _i("MEMORY_MIN_CONFIDENCE", "float", "检索置信度阈值"),
        _i("MEMORY_LLM_CONSOLIDATION_ENABLED", "bool", "LLM 辅助巩固"),
        _i("SHORT_TERM_WINDOW", "int", "短期记忆窗口（条）"),
        _i("MEMORY_DIR", "str", "记忆存储目录",
           derived_note="CHECKPOINT_DIR 默认由本项派生（import 时计算，改本项不影响已派生值）"),
        _i("KNOWLEDGE_CHUNK_SIZE", "int", "知识切片大小（字符）"),
        _i("KNOWLEDGE_TOP_K", "int", "知识检索条数"),
    )),
    ConfigGroup("evolution", "自演化", (
        _i("SELF_EVOLUTION_ENABLED", "bool", "自演化 (v17)",
           "任务后学习经验/失败教训（需开启结构化记忆）", core=True),
        _i("SELF_EVOLUTION_PREFERENCE_ENABLED", "bool", "偏好学习 (v17.4)",
           "从 HITL 问答学习用户偏好", core=True),
        _i("SELF_EVOLUTION_LLM_EXTRACTION", "bool", "LLM 提炼经验"),
        _i("SELF_EVOLUTION_MAX_HINTS", "int", "避坑提示注入上限"),
        _i("SELF_EVOLUTION_CONFIDENCE_CAP", "float", "自学记忆置信度上限", "防 memory poisoning"),
    )),
    ConfigGroup("guardrails", "安全护栏", (
        _i("GUARDRAILS_ENABLED", "bool", "启用护栏 (v19)", "工具输入/注入中和/输出脱敏三层", core=True),
        _i("GUARDRAIL_TOOL_MODE", "enum", "工具层模式", options=("block", "observe"), core=True),
        _i("GUARDRAIL_INPUT_MODE", "enum", "注入防护模式",
           options=("neutralize", "annotate", "observe"), core=True),
        _i("GUARDRAIL_OUTPUT_MODE", "enum", "输出脱敏模式", options=("redact", "observe"), core=True),
        _i("GUARDRAIL_WRITE_CONFIRM", "enum", "写操作确认",
           "confirm 在交互模式下经 ask_user 确认", options=("block", "confirm", "allow"), core=True),
        _i("GUARDRAIL_TOOL_ENABLED", "bool", "19.1 工具输入层"),
        _i("GUARDRAIL_INPUT_ENABLED", "bool", "19.2 上下文注入层"),
        _i("GUARDRAIL_OUTPUT_ENABLED", "bool", "19.3 输出层"),
    )),
    ConfigGroup("delegation", "委派协作", (
        _i("HANDOFF_ENABLED", "bool", "专家 Handoff (v18.2)", "控制权转移给专家 agent", core=True),
        _i("HANDOFF_ALLOW_ASK_USER", "bool", "专家可 ask_user", core=True),
        _i("REMOTE_SUBAGENT_ENABLED", "bool", "远端 SubAgent (v18.3)", "经 MCP/A2A 委派远端 agent", core=True),
        _i("HANDOFF_MAX_CALLS_PER_TASK", "int", "Handoff 调用上限"),
        _i("HANDOFF_TIMEOUT", "int", "专家超时（秒）",
           derived_note="默认 = NODE_EXECUTION_TIMEOUT（import 时取值）"),
        _i("HANDOFF_MAX_ITERATIONS", "int", "专家迭代上限",
           derived_note="默认 = MAX_REACT_ITERATIONS（import 时取值）"),
        _i("REMOTE_AGENT_SERVER_JSON", "str", "远端 server 配置 JSON"),
        _i("REMOTE_SUBAGENT_MAX_CALLS_PER_TASK", "int", "远端调用上限"),
        _i("REMOTE_SUBAGENT_TIMEOUT", "int", "远端超时（秒）",
           derived_note="默认 = NODE_EXECUTION_TIMEOUT（import 时取值）"),
        _i("REMOTE_AGENT_FETCH_CARD", "bool", "调用前拉取 AgentCard"),
    )),
    ConfigGroup("skills", "技能 Skills", (
        _i("SKILLS_ENABLED", "bool", "启用技能 (v20)", "技能发现/激活/过滤", core=True),
        _i("SKILLS_MAX_ACTIVATIONS_PER_TASK", "int", "单任务激活上限"),
        _i("SKILLS_MAX_CONTENT_TOKENS", "int", "单技能内容 Token 上限"),
        _i("SKILLS_USER_DIR", "str", "用户技能目录"),
        _i("SKILLS_DIRS", "str", "额外技能目录", "逗号分隔"),
        _i("SKILL_AUTO_DISTILL_ENABLED", "bool", "自动蒸馏 (v20.5)"),
        _i("SKILL_AUTO_DISTILL_MIN_SUCCESSES", "int", "蒸馏触发成功次数"),
        _i("SKILL_AUTO_DISTILL_CONFIDENCE_CAP", "float", "蒸馏置信度上限"),
        _i("SKILL_OPTIMIZE_LLM_ENABLED", "bool", "LLM 辅助优化 (v20.6)"),
        _i("SKILL_OPTIMIZE_VALIDATION_RATIO", "float", "优化验证集比例"),
        _i("SKILL_OPTIMIZE_MAX_TOKENS", "int", "优化输出 Token 上限"),
    )),
    ConfigGroup("reasoning", "推理引擎", (
        _i("ENABLE_REASONING_ENGINE", "bool", "ReasoningEngine (v14)", core=True),
        _i("REASONING_EFFORT", "enum", "推理力度",
           "auto 由分类器动态决定", options=("auto", "low", "medium", "high"), core=True),
        _i("MAX_THINKING_TOKENS", "int", "thinking Token 预算"),
        _i("MAX_THINKING_ROUNDS", "int", "纯思考轮次上限"),
        _i("REACT_TEMPERATURE", "float", "ReAct 温度"),
        _i("REASONING_TEMPERATURE", "float", "Reasoning 温度"),
        _i("PLANNER_TEMPERATURE", "float", "Planner 温度"),
        _i("REFLECTOR_TEMPERATURE", "float", "Reflector 温度"),
        _i("THINKING_AWARE_CONTEXT", "bool", "上下文感知 thinking"),
        _i("CONVERGENCE_ESCALATION_MULTIPLIER", "int", "收敛提示升级倍数"),
    )),
    ConfigGroup("resume", "任务恢复 Checkpoint", (
        _i("TASK_RESUME_ENABLED", "bool", "启用 checkpoint (v14.5)", "按步/TODO/超步保存，可恢复", core=True),
        _i("CHECKPOINT_DIR", "str", "存储目录", restart_required=True),
        _i("CHECKPOINT_MAX_PER_TASK", "int", "单任务文件上限"),
        _i("CHECKPOINT_RETENTION_DAYS", "int", "保留天数"),
    )),
    ConfigGroup("tools", "工具与沙箱", (
        _i("USER_LOCATION", "str", "用户位置", "显式指定城市（最高优先级）",
           core=True, env_passthrough=True),
        _i("SANDBOX_DIR", "str", "沙箱目录", "文件操作/Shell 的工作目录"),
        _i("CODE_EXEC_TIMEOUT", "int", "代码执行超时（秒）"),
        _i("SHELL_EXEC_TIMEOUT", "int", "Shell 超时（秒）"),
        _i("PYTHON_COMMAND", "str", "Python 命令"),
        _i("SUBPROCESS_MAX_OUTPUT_BYTES", "int", "子进程输出上限（字节）"),
        _i("SHELL_MAX_CONCURRENT", "int", "Shell 并发上限"),
        _i("CODE_MAX_CONCURRENT", "int", "代码执行并发上限"),
        _i("LOCATION_IP_LOOKUP_ENABLED", "bool", "IP 定位", "允许调用公网 IP 接口推断位置"),
        _i("LOCATION_SSL_VERIFY", "bool", "IP 定位 SSL 校验"),
        _i("TOOL_RESULT_TRUNCATION_LIMIT", "int", "工具结果截断长度"),
        _i("SEARCH_CONVERGENCE_THRESHOLD", "int", "搜索收敛阈值", "同工具 N 次后注入收敛提示"),
    )),
    ConfigGroup("web", "搜索与网页解析", (
        _i("WEB_SEARCH_MAX_RESULTS", "int", "搜索结果数"),
        _i("WEB_SEARCH_TIMEOUT", "int", "搜索超时（秒）"),
        _i("DASHSCOPE_API_KEY", "str", "DashScope Key", "百炼 MCP（为空回退 DDGS）", sensitive=True),
        _i("BAILIAN_WEBSEARCH_MCP_URL", "str", "百炼搜索端点"),
        _i("BAILIAN_WEBPARSER_MCP_URL", "str", "百炼解析端点", "必须指向 /sse"),
        _i("BAILIAN_MCP_MAX_RETRIES", "int", "百炼重试次数"),
        _i("BAILIAN_MCP_RETRY_BASE_DELAY", "float", "百炼退避基础延迟（秒）"),
        _i("BAILIAN_WEBPARSER_MAX_CONCURRENT", "int", "解析并发上限"),
        _i("BAILIAN_WEBPARSER_MIN_INTERVAL_SECONDS", "float", "解析最小间隔（秒）"),
        _i("LOCAL_WEBPARSER_ENABLED", "bool", "本地网页解析", "fetch_url 主路径"),
        _i("LOCAL_WEBPARSER_TIMEOUT", "float", "本地抓取超时（秒）"),
        _i("LOCAL_WEBPARSER_MAX_BYTES", "int", "本地抓取字节上限"),
        _i("LOCAL_WEBPARSER_USER_AGENT", "str", "本地抓取 UA"),
        _i("LOCAL_WEBPARSER_RESPECT_ROBOTS", "bool", "遵循 robots.txt"),
        _i("LOCAL_WEBPARSER_BROWSER_FALLBACK", "bool", "Playwright 渲染兜底"),
        _i("LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN", "bool", "失败回退百炼"),
        _i("LOCAL_WEBPARSER_MIN_CONTENT_LENGTH", "int", "fallback 触发长度"),
        _i("LOCAL_WEBPARSER_CACHE_SIZE", "int", "HTML 缓存条目数"),
        _i("FETCH_URL_MAX_CONTENT_LENGTH", "int", "fetch_url 内容上限"),
        _i("FETCH_URL_SHORT_CONTENT_WARNING_LENGTH", "int", "极短内容告警阈值"),
    )),
    ConfigGroup("mcp", "MCP 桥接/服务端", (
        _i("MCP_BRIDGE_ENABLED", "bool", "MCP Bridge (v16)", "发现并注册外部 MCP 工具", core=True),
        _i("MCP_BRIDGE_CONFIG_PATH", "str", "服务器配置文件"),
        _i("MCP_BRIDGE_SERVERS_JSON", "str", "内联服务器 JSON"),
        _i("MCP_BRIDGE_TOOL_PREFIX", "str", "工具名前缀"),
        _i("MCP_BRIDGE_SCHEMA_MODE", "enum", "Schema 转换模式", options=("loose", "strict")),
        _i("MCP_BRIDGE_DISCOVERY_TTL", "int", "重发现间隔（秒）"),
        _i("MCP_BRIDGE_CALL_TIMEOUT", "int", "调用超时（秒）"),
        _i("MCP_SERVER_ENABLED", "bool", "MCP Server"),
        _i("MCP_SERVER_TRANSPORT", "enum", "Server 传输", options=("streamable_http", "stdio")),
        _i("MCP_SERVER_HOST", "str", "Server 地址"),
        _i("MCP_SERVER_PORT", "int", "Server 端口"),
        _i("MCP_SERVER_EXPOSE_AGENT", "bool", "暴露为远端 Agent (v18.4)"),
    )),
    ConfigGroup("agentbay", "AgentBay 云端运行时", (
        _i("AGENTBAY_ENABLED", "bool", "启用 AgentBay", "云端代码/浏览器工具", core=True),
        _i("AGENTBAY_API_KEY", "str", "AgentBay Key", sensitive=True, env_passthrough=True),
        _i("AGENTBAY_CODE_TOOL_ENABLED", "bool", "代码工具"),
        _i("AGENTBAY_BROWSER_TOOL_ENABLED", "bool", "浏览器工具"),
        _i("AGENTBAY_LOG_LEVEL", "str", "SDK 日志级别"),
        _i("AGENTBAY_MAX_CONCURRENT_SESSIONS", "int", "Session 并发上限"),
        _i("AGENTBAY_CODE_IMAGE", "str", "CodeSpace 镜像"),
        _i("AGENTBAY_BROWSER_IMAGE", "str", "Browser 镜像"),
        _i("AGENTBAY_SESSION_IDLE_RELEASE_MINUTES", "int", "空闲释放（分钟）"),
        _i("AGENTBAY_SESSION_MAX_RUNTIME_MINUTES", "int", "最大运行（分钟）"),
        _i("AGENTBAY_CODE_TIMEOUT_SECONDS", "int", "代码执行超时（秒）"),
        _i("AGENTBAY_BROWSER_TIMEOUT_MS", "int", "浏览器操作超时（毫秒）"),
    )),
    ConfigGroup("tracing", "追踪 Tracing", (
        _i("TRACING_ENABLED", "bool", "启用追踪 (v7)",
           "webui 启动时默认强制开启", core=True, restart_required=True),
        _i("TRACING_BACKEND", "enum", "导出后端", "webui 默认 file（供 trace 查看）",
           options=("console", "file", "rich", "otlp", "phoenix"), core=True, restart_required=True),
        _i("TRACING_ENDPOINT", "str", "OTLP 端点", restart_required=True),
        _i("TRACING_SERVICE_NAME", "str", "服务标识", restart_required=True),
        _i("TRACING_SAMPLE_RATE", "float", "采样率", restart_required=True),
        _i("TRACING_LOG_PROMPTS", "bool", "记录完整 prompt", restart_required=True),
        _i("TRACING_MAX_ATTRIBUTE_LENGTH", "int", "属性最大字符", restart_required=True),
    )),
)

# name → item 索引 / name → item index
_ITEM_INDEX: dict[str, ConfigItem] = {
    item.name: item for group in GROUPS for item in group.items
}


# =====================================================================
# 读取 / read
# =====================================================================

def get_schema() -> dict:
    """Schema for the frontend. 供前端渲染的 schema（敏感项只带 configured）。"""
    return {
        "groups": [
            {
                "id": g.id,
                "title": g.title,
                "items": [
                    {
                        "name": it.name,
                        "type": it.type,
                        "label": it.label,
                        "description": it.description,
                        "options": list(it.options),
                        "core": it.core,
                        "sensitive": it.sensitive,
                        "restart_required": it.restart_required,
                        "derived_note": it.derived_note,
                        **(
                            {"configured": bool(getattr(config, it.name, ""))}
                            if it.sensitive else {}
                        ),
                    }
                    for it in g.items
                ],
            }
            for g in GROUPS
        ]
    }


def get_values() -> dict[str, object]:
    """Current live values (non-sensitive only). 当前生效值（不含敏感项）。"""
    return {
        name: getattr(config, name)
        for name, item in _ITEM_INDEX.items()
        if not item.sensitive
    }


# =====================================================================
# 校验 / validate
# =====================================================================

def _coerce(item: ConfigItem, value: object) -> object:
    """Coerce a single value to the item type; raise ValueError on mismatch.
    按条目类型强转单个值；类型不符抛 ValueError。"""
    if item.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError(f"需要布尔值，收到 {value!r}")
    if item.type == "int":
        if isinstance(value, bool):  # bool 是 int 子类，显式拒绝 / bool is int subclass
            raise ValueError(f"需要整数，收到布尔 {value!r}")
        return int(value)  # type: ignore[arg-type]
    if item.type == "float":
        if isinstance(value, bool):
            raise ValueError(f"需要数值，收到布尔 {value!r}")
        return float(value)  # type: ignore[arg-type]
    if item.type == "enum":
        candidate = str(value).lower()
        if candidate not in item.options:
            raise ValueError(f"取值必须是 {list(item.options)} 之一，收到 {value!r}")
        return candidate
    # str
    return str(value)


def validate(overrides: dict[str, object]) -> dict[str, object]:
    """Validate + coerce overrides; raise ConfigValidationError on any failure.
    校验并强转 overrides；任一失败抛 ConfigValidationError（含逐字段错误）。"""
    errors: dict[str, str] = {}
    coerced: dict[str, object] = {}
    for name, value in overrides.items():
        item = _ITEM_INDEX.get(name)
        if item is None:
            errors[name] = "未知配置项"
            continue
        if item.sensitive:
            errors[name] = "敏感项不允许通过界面修改（请编辑 .env 并重启）"
            continue
        if item.restart_required:
            errors[name] = "该项在 import 时被捕获，需设置环境变量并重启进程"
            continue
        try:
            coerced[name] = _coerce(item, value)
        except (ValueError, TypeError) as exc:
            errors[name] = str(exc)
    if errors:
        raise ConfigValidationError(errors)
    return coerced


# =====================================================================
# 应用 / 恢复 —— evaluation/variants.apply_variant 的形态
# apply / restore — the shape of evaluation/variants.apply_variant
# =====================================================================

def apply(overrides: dict[str, object]) -> dict[str, object]:
    """Apply validated overrides onto the config module; return originals.
    将（已校验的）overrides 写入 config 模块，返回原值快照。"""
    originals: dict[str, object] = {}
    for name, value in overrides.items():
        originals[name] = getattr(config, name)
        setattr(config, name, value)
        item = _ITEM_INDEX[name]
        if item.env_passthrough:
            # 消费方直读 os.environ（如 tools/user_location.py）
            # consumers read os.environ live
            if str(value):
                os.environ[name] = str(value)
            else:
                os.environ.pop(name, None)
    return originals


def restore(originals: dict[str, object]) -> None:
    """Restore config module attributes from an originals snapshot.
    从原值快照逆序恢复 config 模块属性。"""
    for name, value in reversed(list(originals.items())):
        setattr(config, name, value)
        item = _ITEM_INDEX.get(name)
        if item is not None and item.env_passthrough:
            if str(value):
                os.environ[name] = str(value)
            else:
                os.environ.pop(name, None)
