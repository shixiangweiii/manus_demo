"""Typed application settings.

Normal configuration is read from ``settings.toml``. Only explicitly
whitelisted secrets are read from ``.env`` or the process environment.
"""

from __future__ import annotations

import copy
import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from core.models import Effort, EngineKind, ExecutorKind

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _expand_path(value: str, base_dir: Path) -> str:
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


@dataclass
class LLMSettings:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    supports_reasoning: bool = False
    timeout_seconds: float = 120.0
    retry_enabled: bool = False
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 2.0
    token_tracking: bool = True
    reasoning_token_tracking: bool = True


@dataclass
class EngineSettings:
    default: EngineKind = EngineKind.AUTO
    executor: ExecutorKind = ExecutorKind.AUTO
    effort: Effort = Effort.AUTO
    max_context_tokens: int = 16000
    max_action_iterations: int = 10
    max_replan_attempts: int = 3
    max_parallel_nodes: int = 3
    dag_serial_execution: bool = True
    node_timeout_seconds: int = 300
    adaptive_planning: bool = True
    adaptive_interval: int = 1
    adaptive_min_completed: int = 1
    max_todo_items: int = 20
    max_todo_retries: int = 3
    max_todo_iterations: int = 60
    goal_reanchor_interval: int = 5
    goal_reflection_interval: int = 1
    goal_stagnation_window: int = 3
    react_temperature: float = 0.5
    thinking_temperature: float = 0.5
    planner_temperature: float = 0.3
    reflector_temperature: float = 0.1
    max_thinking_tokens: int = 10000
    max_thinking_rounds: int = 5


@dataclass
class PathSettings:
    state_dir: str = "~/.manus_demo"
    sandbox_dir: str = "~/.manus_demo/sandbox"
    checkpoint_dir: str = "~/.manus_demo/checkpoints"
    knowledge_docs_dir: str = str(PROJECT_ROOT / "knowledge" / "docs")

    def expand(self, base_dir: Path) -> None:
        self.state_dir = _expand_path(self.state_dir, base_dir)
        self.sandbox_dir = _expand_path(self.sandbox_dir, base_dir)
        self.checkpoint_dir = _expand_path(self.checkpoint_dir, base_dir)
        self.knowledge_docs_dir = _expand_path(self.knowledge_docs_dir, base_dir)


@dataclass
class ToolSettings:
    python_command: str = "python3"
    code_timeout_seconds: int = 30
    shell_timeout_seconds: int = 30
    subprocess_max_output_bytes: int = 524288
    shell_max_concurrent: int = 3
    code_max_concurrent: int = 3
    failure_threshold: int = 2
    result_truncation_limit: int = 2000
    web_search_max_results: int = 5
    web_search_timeout_seconds: int = 15
    user_location: str = ""
    location_ip_lookup: bool = True
    location_ssl_verify: bool = True
    dashscope_api_key: str = ""
    bailian_websearch_url: str = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    bailian_webparser_url: str = "https://dashscope.aliyuncs.com/api/v1/mcps/WebParser/sse"
    bailian_max_retries: int = 3
    bailian_retry_base_delay: float = 2.0
    bailian_webparser_max_concurrent: int = 1
    bailian_webparser_min_interval: float = 1.0
    search_convergence_threshold: int = 3
    fetch_url_max_content_length: int = 10000
    fetch_url_short_warning_length: int = 80
    local_webparser_enabled: bool = True
    local_webparser_timeout: float = 20.0
    local_webparser_max_bytes: int = 2097152
    local_webparser_user_agent: str = "ManusDemoBot/1.0"
    local_webparser_respect_robots: bool = False
    local_webparser_browser_fallback: bool = False
    local_webparser_fallback_to_bailian: bool = False
    local_webparser_min_content_length: int = 120
    local_webparser_cache_size: int = 64


@dataclass
class CapabilitySettings:
    workflow: bool = True
    checkpoint: bool = True
    checkpoint_max_per_task: int = 5
    subagent: bool = False
    subagent_max_iterations: int = 10
    subagent_timeout_seconds: int = 300
    subagent_max_concurrent: int = 2
    subagent_summary_max_length: int = 2000
    subagent_max_calls: int = 3
    subagent_max_tokens: int = 120000
    subagent_tool_whitelist: str = ""
    subagent_task_max_length: int = 2000
    parallel_todos: bool = False
    hitl: bool = False
    hitl_max_prompts: int = 5
    hitl_timeout_seconds: int = 120
    agentic_memory: bool = False
    memory_tools: bool = False
    memory_min_confidence: float = 0.35
    memory_search_top_k: int = 3
    memory_llm_consolidation: bool = False
    short_term_window: int = 20
    knowledge: bool = True
    knowledge_chunk_size: int = 500
    knowledge_top_k: int = 3
    self_evolution: bool = False
    self_evolution_llm_extraction: bool = False
    self_evolution_max_hints: int = 3
    self_evolution_confidence_cap: float = 0.6
    skills: bool = False
    skills_user_dir: str = "~/.manus_demo/skills"
    skills_dirs: str = ""
    skills_max_activations: int = 3
    skills_max_content_tokens: int = 5000
    skill_auto_distill: bool = False
    skill_auto_distill_min_successes: int = 3
    skill_auto_distill_confidence_cap: float = 0.55
    skill_optimize_with_llm: bool = False
    skill_optimize_validation_ratio: float = 0.2
    skill_optimize_max_tokens: int = 1200
    handoff: bool = False
    handoff_allow_ask_user: bool = False
    handoff_max_calls: int = 2
    handoff_timeout_seconds: int = 300
    handoff_max_iterations: int = 10
    remote_subagent: bool = False
    remote_agent_server_json: str = ""
    remote_subagent_max_calls: int = 2
    remote_subagent_timeout_seconds: int = 300
    remote_agent_fetch_card: bool = True
    guardrails: bool = False
    guardrail_tool_enabled: bool = True
    guardrail_input_enabled: bool = True
    guardrail_output_enabled: bool = True
    guardrail_tool_mode: str = "block"
    guardrail_input_mode: str = "neutralize"
    guardrail_output_mode: str = "redact"
    guardrail_write_confirm: str = "block"
    mcp_bridge: bool = False
    mcp_bridge_config_path: str = ""
    mcp_bridge_servers_json: str = ""
    mcp_bridge_tool_prefix: str = "mcp"
    mcp_bridge_schema_mode: str = "loose"
    mcp_bridge_discovery_ttl: int = 300
    mcp_bridge_call_timeout: int = 30
    mcp_server_transport: str = "streamable_http"
    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = 8080
    mcp_server_expose_agent: bool = False
    agentbay: bool = False
    agentbay_api_key: str = ""
    agentbay_code_tool: bool = True
    agentbay_browser_tool: bool = True
    agentbay_log_level: str = "WARNING"
    agentbay_max_concurrent: int = 1
    agentbay_code_image: str = "code_latest"
    agentbay_browser_image: str = "browser_latest"
    agentbay_idle_release_minutes: int = 5
    agentbay_max_runtime_minutes: int = 10
    agentbay_code_timeout_seconds: int = 60
    agentbay_browser_timeout_ms: int = 30000

    def expand(self, base_dir: Path) -> None:
        self.skills_user_dir = _expand_path(self.skills_user_dir, base_dir)
        if self.mcp_bridge_config_path:
            self.mcp_bridge_config_path = _expand_path(
                self.mcp_bridge_config_path,
                base_dir,
            )


@dataclass
class TracingSettings:
    enabled: bool = False
    backend: str = "console"
    endpoint: str = "http://localhost:4318"
    service_name: str = "manus-demo"
    sample_rate: float = 1.0
    log_prompts: bool = False
    max_attribute_length: int = 1000
    output_dir: str = "traces"

    def expand(self, base_dir: Path) -> None:
        self.output_dir = _expand_path(self.output_dir, base_dir)


@dataclass
class WebUISettings:
    host: str = "127.0.0.1"
    port: int = 8700


@dataclass
class EvaluationSettings:
    output_dir: str = "~/.manus_demo/evaluation"
    port: int = 8720
    max_document_chars: int = 24000
    default_num_tasks: int = 6
    generation_max_tokens: int = 4096

    def expand(self, base_dir: Path) -> None:
        self.output_dir = _expand_path(self.output_dir, base_dir)


@dataclass
class AppSettings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    engines: EngineSettings = field(default_factory=EngineSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    capabilities: CapabilitySettings = field(default_factory=CapabilitySettings)
    tracing: TracingSettings = field(default_factory=TracingSettings)
    webui: WebUISettings = field(default_factory=WebUISettings)
    evaluation: EvaluationSettings = field(default_factory=EvaluationSettings)

    def clone(self) -> "AppSettings":
        return copy.deepcopy(self)


@dataclass(frozen=True)
class RunSettings:
    engine: EngineKind = EngineKind.AUTO
    executor: ExecutorKind = ExecutorKind.AUTO
    effort: Effort = Effort.AUTO
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_app(cls, settings: AppSettings) -> "RunSettings":
        return cls(
            engine=settings.engines.default,
            executor=settings.engines.executor,
            effort=settings.engines.effort,
        )

    def with_overrides(self, overrides: dict[str, Any] | None) -> "RunSettings":
        if not overrides:
            return self
        allowed = {"engine", "executor", "effort", "capabilities"}
        unknown = set(overrides) - allowed
        if unknown:
            raise ValueError(f"Unknown run setting(s): {', '.join(sorted(unknown))}")
        return RunSettings(
            engine=EngineKind(overrides.get("engine", self.engine)),
            executor=ExecutorKind(overrides.get("executor", self.executor)),
            effort=Effort(overrides.get("effort", self.effort)),
            capabilities=tuple(overrides.get("capabilities", self.capabilities)),
        )


_SECTION_TYPES = {
    "llm": LLMSettings,
    "engines": EngineSettings,
    "paths": PathSettings,
    "tools": ToolSettings,
    "capabilities": CapabilitySettings,
    "tracing": TracingSettings,
    "webui": WebUISettings,
    "evaluation": EvaluationSettings,
}

_SECRET_PATHS = {
    "LLM_API_KEY": ("llm", "api_key"),
    "DASHSCOPE_API_KEY": ("tools", "dashscope_api_key"),
    "AGENTBAY_API_KEY": ("capabilities", "agentbay_api_key"),
}

_TOML_SECRET_FIELDS = {
    "llm": {"api_key"},
    "tools": {"dashscope_api_key"},
    "capabilities": {"agentbay_api_key"},
}


def _coerce_value(current: Any, value: Any, dotted_name: str) -> Any:
    try:
        if isinstance(current, EngineKind):
            return EngineKind(value)
        if isinstance(current, ExecutorKind):
            return ExecutorKind(value)
        if isinstance(current, Effort):
            return Effort(value)
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise TypeError("must be a boolean")
            return value
        if isinstance(current, int) and not isinstance(current, bool):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError("must be an integer")
            return value
        if isinstance(current, float):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError("must be a number")
            return float(value)
        if isinstance(current, str):
            if not isinstance(value, str):
                raise TypeError("must be a string")
            return value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid setting {dotted_name}: {exc}") from exc
    return value


def _apply_section(section_name: str, target: Any, values: dict[str, Any]) -> None:
    forbidden = set(values) & _TOML_SECRET_FIELDS.get(section_name, set())
    if forbidden:
        names = ", ".join(f"{section_name}.{name}" for name in sorted(forbidden))
        raise ValueError(f"Secret setting(s) must be stored in .env: {names}")
    known = {item.name for item in fields(target)}
    unknown = set(values) - known
    if unknown:
        names = ", ".join(f"{section_name}.{name}" for name in sorted(unknown))
        raise ValueError(f"Unknown setting(s): {names}")
    for name, value in values.items():
        current = getattr(target, name)
        setattr(target, name, _coerce_value(current, value, f"{section_name}.{name}"))


def load_settings(path: str | Path | None = None) -> AppSettings:
    settings = AppSettings()
    config_path = Path(path) if path is not None else PROJECT_ROOT / "settings.toml"
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        unknown_sections = set(data) - set(_SECTION_TYPES)
        if unknown_sections:
            raise ValueError(
                f"Unknown settings section(s): {', '.join(sorted(unknown_sections))}"
            )
        for section_name, values in data.items():
            if not isinstance(values, dict):
                raise ValueError(f"Settings section {section_name} must be a table")
            _apply_section(section_name, getattr(settings, section_name), values)

    dotenv_path = PROJECT_ROOT / ".env"
    dotenv_data = dotenv_values(dotenv_path) if dotenv_path.exists() else {}
    unknown_dotenv = set(dotenv_data) - set(_SECRET_PATHS)
    if unknown_dotenv:
        raise ValueError(
            ".env may contain secrets only; move these settings to settings.toml: "
            + ", ".join(sorted(unknown_dotenv))
        )
    for env_name, (section_name, field_name) in _SECRET_PATHS.items():
        value = os.environ.get(env_name, dotenv_data.get(env_name))
        if value is not None:
            setattr(getattr(settings, section_name), field_name, str(value).strip())

    _validate_required_text(settings)
    base_dir = config_path.resolve().parent
    settings.paths.expand(base_dir)
    settings.capabilities.expand(base_dir)
    settings.tracing.expand(base_dir)
    settings.evaluation.expand(base_dir)
    validate_settings(settings)
    return settings


def _validate_required_text(settings: AppSettings) -> None:
    """Reject empty identifiers and paths before relative paths are expanded."""
    required = {
        "llm.base_url": settings.llm.base_url,
        "llm.model": settings.llm.model,
        "paths.state_dir": settings.paths.state_dir,
        "paths.sandbox_dir": settings.paths.sandbox_dir,
        "paths.checkpoint_dir": settings.paths.checkpoint_dir,
        "paths.knowledge_docs_dir": settings.paths.knowledge_docs_dir,
        "tools.python_command": settings.tools.python_command,
        "tracing.service_name": settings.tracing.service_name,
        "tracing.output_dir": settings.tracing.output_dir,
        "webui.host": settings.webui.host,
        "evaluation.output_dir": settings.evaluation.output_dir,
        "capabilities.mcp_server_host": settings.capabilities.mcp_server_host,
    }
    for name, value in required.items():
        if not value.strip():
            raise ValueError(f"Invalid setting {name}: must not be empty")


def validate_settings(settings: AppSettings) -> None:
    _validate_required_text(settings)
    capabilities = settings.capabilities
    positive_fields = {
        "engines.max_context_tokens": settings.engines.max_context_tokens,
        "engines.max_action_iterations": settings.engines.max_action_iterations,
        "engines.max_parallel_nodes": settings.engines.max_parallel_nodes,
        "engines.adaptive_interval": settings.engines.adaptive_interval,
        "engines.max_todo_iterations": settings.engines.max_todo_iterations,
        "engines.node_timeout_seconds": settings.engines.node_timeout_seconds,
        "engines.max_todo_items": settings.engines.max_todo_items,
        "engines.max_thinking_tokens": settings.engines.max_thinking_tokens,
        "engines.max_thinking_rounds": settings.engines.max_thinking_rounds,
        "engines.goal_reflection_interval": settings.engines.goal_reflection_interval,
        "engines.goal_stagnation_window": settings.engines.goal_stagnation_window,
        "tools.code_timeout_seconds": settings.tools.code_timeout_seconds,
        "tools.shell_timeout_seconds": settings.tools.shell_timeout_seconds,
        "tools.web_search_max_results": settings.tools.web_search_max_results,
        "tools.web_search_timeout_seconds": settings.tools.web_search_timeout_seconds,
        "tools.result_truncation_limit": settings.tools.result_truncation_limit,
        "tools.subprocess_max_output_bytes": settings.tools.subprocess_max_output_bytes,
        "tools.shell_max_concurrent": settings.tools.shell_max_concurrent,
        "tools.code_max_concurrent": settings.tools.code_max_concurrent,
        "tools.failure_threshold": settings.tools.failure_threshold,
        "tools.bailian_retry_base_delay": settings.tools.bailian_retry_base_delay,
        "tools.bailian_webparser_max_concurrent": settings.tools.bailian_webparser_max_concurrent,
        "tools.search_convergence_threshold": settings.tools.search_convergence_threshold,
        "tools.local_webparser_timeout": settings.tools.local_webparser_timeout,
        "tools.fetch_url_max_content_length": settings.tools.fetch_url_max_content_length,
        "tools.local_webparser_max_bytes": settings.tools.local_webparser_max_bytes,
        "tools.local_webparser_min_content_length": settings.tools.local_webparser_min_content_length,
        "llm.timeout_seconds": settings.llm.timeout_seconds,
        "llm.retry_backoff_factor": settings.llm.retry_backoff_factor,
        "llm.retry_max_attempts": settings.llm.retry_max_attempts,
        "tracing.max_attribute_length": settings.tracing.max_attribute_length,
        "evaluation.max_document_chars": settings.evaluation.max_document_chars,
        "evaluation.default_num_tasks": settings.evaluation.default_num_tasks,
        "evaluation.generation_max_tokens": settings.evaluation.generation_max_tokens,
        "capabilities.checkpoint_max_per_task": settings.capabilities.checkpoint_max_per_task,
        "capabilities.subagent_max_iterations": settings.capabilities.subagent_max_iterations,
        "capabilities.subagent_timeout_seconds": settings.capabilities.subagent_timeout_seconds,
        "capabilities.subagent_max_concurrent": settings.capabilities.subagent_max_concurrent,
        "capabilities.subagent_summary_max_length": settings.capabilities.subagent_summary_max_length,
        "capabilities.subagent_max_calls": settings.capabilities.subagent_max_calls,
        "capabilities.subagent_max_tokens": settings.capabilities.subagent_max_tokens,
        "capabilities.subagent_task_max_length": settings.capabilities.subagent_task_max_length,
        "capabilities.hitl_max_prompts": settings.capabilities.hitl_max_prompts,
        "capabilities.hitl_timeout_seconds": settings.capabilities.hitl_timeout_seconds,
        "capabilities.short_term_window": settings.capabilities.short_term_window,
        "capabilities.memory_search_top_k": settings.capabilities.memory_search_top_k,
        "capabilities.knowledge_chunk_size": settings.capabilities.knowledge_chunk_size,
        "capabilities.knowledge_top_k": settings.capabilities.knowledge_top_k,
        "capabilities.self_evolution_max_hints": settings.capabilities.self_evolution_max_hints,
        "capabilities.skills_max_activations": settings.capabilities.skills_max_activations,
        "capabilities.skills_max_content_tokens": settings.capabilities.skills_max_content_tokens,
        "capabilities.skill_auto_distill_min_successes": settings.capabilities.skill_auto_distill_min_successes,
        "capabilities.skill_optimize_max_tokens": settings.capabilities.skill_optimize_max_tokens,
        "capabilities.handoff_max_calls": settings.capabilities.handoff_max_calls,
        "capabilities.handoff_timeout_seconds": settings.capabilities.handoff_timeout_seconds,
        "capabilities.handoff_max_iterations": settings.capabilities.handoff_max_iterations,
        "capabilities.remote_subagent_max_calls": settings.capabilities.remote_subagent_max_calls,
        "capabilities.remote_subagent_timeout_seconds": settings.capabilities.remote_subagent_timeout_seconds,
        "capabilities.mcp_bridge_discovery_ttl": settings.capabilities.mcp_bridge_discovery_ttl,
        "capabilities.mcp_bridge_call_timeout": settings.capabilities.mcp_bridge_call_timeout,
        "capabilities.agentbay_max_concurrent": settings.capabilities.agentbay_max_concurrent,
        "capabilities.agentbay_idle_release_minutes": settings.capabilities.agentbay_idle_release_minutes,
        "capabilities.agentbay_max_runtime_minutes": settings.capabilities.agentbay_max_runtime_minutes,
        "capabilities.agentbay_code_timeout_seconds": settings.capabilities.agentbay_code_timeout_seconds,
        "capabilities.agentbay_browser_timeout_ms": settings.capabilities.agentbay_browser_timeout_ms,
    }
    for name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"Invalid setting {name}: must be greater than zero")
    nonnegative_fields = {
        "engines.max_replan_attempts": settings.engines.max_replan_attempts,
        "engines.max_todo_retries": settings.engines.max_todo_retries,
        "engines.adaptive_min_completed": settings.engines.adaptive_min_completed,
        "engines.goal_reanchor_interval": settings.engines.goal_reanchor_interval,
        "tools.bailian_max_retries": settings.tools.bailian_max_retries,
        "tools.local_webparser_cache_size": settings.tools.local_webparser_cache_size,
        "tools.bailian_webparser_min_interval": settings.tools.bailian_webparser_min_interval,
        "tools.fetch_url_short_warning_length": settings.tools.fetch_url_short_warning_length,
    }
    for name, value in nonnegative_fields.items():
        if value < 0:
            raise ValueError(f"Invalid setting {name}: must be zero or greater")
    for name, port in (
        ("webui.port", settings.webui.port),
        ("evaluation.port", settings.evaluation.port),
        ("capabilities.mcp_server_port", settings.capabilities.mcp_server_port),
    ):
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid setting {name}: must be between 1 and 65535")
    if not 0.0 <= settings.tracing.sample_rate <= 1.0:
        raise ValueError("Invalid setting tracing.sample_rate: must be between 0 and 1")
    bounded_fields = {
        "capabilities.memory_min_confidence": capabilities.memory_min_confidence,
        "capabilities.self_evolution_confidence_cap": capabilities.self_evolution_confidence_cap,
        "capabilities.skill_auto_distill_confidence_cap": capabilities.skill_auto_distill_confidence_cap,
        "capabilities.skill_optimize_validation_ratio": capabilities.skill_optimize_validation_ratio,
    }
    for name, value in bounded_fields.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"Invalid setting {name}: must be between 0 and 1")
    temperature_fields = {
        "engines.react_temperature": settings.engines.react_temperature,
        "engines.thinking_temperature": settings.engines.thinking_temperature,
        "engines.planner_temperature": settings.engines.planner_temperature,
        "engines.reflector_temperature": settings.engines.reflector_temperature,
    }
    for name, value in temperature_fields.items():
        if not 0.0 <= value <= 2.0:
            raise ValueError(f"Invalid setting {name}: must be between 0 and 2")
    if settings.engines.default == EngineKind.WORKFLOW:
        raise ValueError(
            "Invalid setting engines.default: workflow requires an explicit workflow file"
        )
    if settings.tracing.backend in {"otlp", "phoenix"} and not settings.tracing.endpoint.strip():
        raise ValueError(
            "Invalid setting tracing.endpoint: required for the selected tracing backend"
        )
    if capabilities.memory_tools and not capabilities.agentic_memory:
        raise ValueError(
            "Invalid settings: capabilities.memory_tools requires capabilities.agentic_memory"
        )
    if capabilities.parallel_todos and not capabilities.subagent:
        raise ValueError(
            "Invalid settings: capabilities.parallel_todos requires capabilities.subagent"
        )
    if capabilities.self_evolution and not capabilities.agentic_memory:
        raise ValueError(
            "Invalid settings: capabilities.self_evolution requires capabilities.agentic_memory"
        )
    if capabilities.skill_auto_distill and not (
        capabilities.skills and capabilities.self_evolution
    ):
        raise ValueError(
            "Invalid settings: capabilities.skill_auto_distill requires skills and self_evolution"
        )
    if capabilities.agentbay and not capabilities.agentbay_api_key:
        raise ValueError("AGENTBAY_API_KEY is required when capabilities.agentbay is enabled")
    if capabilities.agentbay and not (
        capabilities.agentbay_code_tool or capabilities.agentbay_browser_tool
    ):
        raise ValueError(
            "Invalid settings: capabilities.agentbay requires at least one AgentBay tool"
        )
    if capabilities.remote_subagent and not capabilities.remote_agent_server_json.strip():
        raise ValueError(
            "Invalid setting capabilities.remote_agent_server_json: required for remote_subagent"
        )
    if capabilities.remote_agent_server_json.strip():
        try:
            remote_server = json.loads(capabilities.remote_agent_server_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid setting capabilities.remote_agent_server_json: {exc}"
            ) from exc
        if not isinstance(remote_server, dict):
            raise ValueError(
                "Invalid setting capabilities.remote_agent_server_json: must be a JSON object"
            )
        validate_server_entry(
            remote_server,
            "capabilities.remote_agent_server_json",
        )
    if capabilities.mcp_bridge:
        if not capabilities.mcp_bridge_tool_prefix.strip():
            raise ValueError(
                "Invalid setting capabilities.mcp_bridge_tool_prefix: must not be empty"
            )
        has_inline = bool(capabilities.mcp_bridge_servers_json.strip())
        has_file = bool(capabilities.mcp_bridge_config_path.strip())
        if not has_inline and not has_file:
            raise ValueError(
                "Invalid settings: capabilities.mcp_bridge requires servers_json or config_path"
            )
        if has_inline:
            try:
                bridge_data = json.loads(capabilities.mcp_bridge_servers_json)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid setting capabilities.mcp_bridge_servers_json: {exc}"
                ) from exc
            if not isinstance(bridge_data, dict):
                raise ValueError(
                    "Invalid setting capabilities.mcp_bridge_servers_json: must be a JSON object"
                )
            _validate_bridge_data(
                bridge_data,
                "capabilities.mcp_bridge_servers_json",
            )
        else:
            bridge_path = Path(capabilities.mcp_bridge_config_path)
            if not bridge_path.is_file():
                raise ValueError(
                    "Invalid setting capabilities.mcp_bridge_config_path: file does not exist"
                )
            try:
                bridge_data = json.loads(bridge_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Invalid setting capabilities.mcp_bridge_config_path: {exc}"
                ) from exc
            _validate_bridge_data(
                bridge_data,
                "capabilities.mcp_bridge_config_path",
            )
    allowed_values = {
        "tracing.backend": (
            settings.tracing.backend,
            {"console", "file", "rich", "otlp", "phoenix"},
        ),
        "capabilities.guardrail_tool_mode": (
            capabilities.guardrail_tool_mode,
            {"block", "observe"},
        ),
        "capabilities.guardrail_input_mode": (
            capabilities.guardrail_input_mode,
            {"neutralize", "observe"},
        ),
        "capabilities.guardrail_output_mode": (
            capabilities.guardrail_output_mode,
            {"redact", "observe"},
        ),
        "capabilities.guardrail_write_confirm": (
            capabilities.guardrail_write_confirm,
            {"block", "allow", "confirm"},
        ),
        "capabilities.mcp_server_transport": (
            capabilities.mcp_server_transport,
            {"stdio", "streamable_http"},
        ),
        "capabilities.mcp_bridge_schema_mode": (
            capabilities.mcp_bridge_schema_mode,
            {"loose", "strict"},
        ),
        "capabilities.agentbay_log_level": (
            capabilities.agentbay_log_level,
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        ),
    }
    for name, (value, allowed) in allowed_values.items():
        if value not in allowed:
            raise ValueError(
                f"Invalid setting {name}: expected one of {', '.join(sorted(allowed))}"
            )


def validate_server_entry(raw: Any, field_name: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid setting {field_name}: server entry must be an object")
    allowed = {
        "name", "transport", "command", "args", "env", "cwd", "url",
        "headers", "timeout", "enabled",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"Invalid setting {field_name}: unknown field(s): {', '.join(sorted(unknown))}"
        )
    transport = raw.get("transport", "streamable_http")
    if transport not in {"stdio", "streamable_http"}:
        raise ValueError(f"Invalid setting {field_name}: unknown transport {transport!r}")
    command = raw.get("command", "")
    url = raw.get("url", "")
    if not isinstance(command, str) or not isinstance(url, str):
        raise ValueError(f"Invalid setting {field_name}: command and url must be strings")
    if transport == "stdio" and not command.strip():
        raise ValueError(f"Invalid setting {field_name}: stdio transport requires command")
    if transport == "streamable_http" and not url.strip():
        raise ValueError(f"Invalid setting {field_name}: streamable_http requires url")
    args = raw.get("args", [])
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise ValueError(f"Invalid setting {field_name}: args must be a list of strings")
    for key in ("env", "headers"):
        values = raw.get(key, {})
        if not isinstance(values, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in values.items()
        ):
            raise ValueError(
                f"Invalid setting {field_name}: {key} must map strings to strings"
            )
    if raw.get("cwd") is not None and not isinstance(raw.get("cwd"), str):
        raise ValueError(f"Invalid setting {field_name}: cwd must be a string or null")
    if not isinstance(raw.get("enabled", True), bool):
        raise ValueError(f"Invalid setting {field_name}: enabled must be a boolean")
    timeout = raw.get("timeout", 30)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"Invalid setting {field_name}: timeout must be greater than zero")


def _validate_bridge_data(data: Any, field_name: str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"Invalid setting {field_name}: must be a JSON object")
    servers = data.get("servers")
    unknown = set(data) - {
        "servers", "schema_mode", "tool_prefix", "discovery_ttl_seconds",
        "call_timeout_seconds",
    }
    if unknown:
        raise ValueError(
            f"Invalid setting {field_name}: unknown field(s): {', '.join(sorted(unknown))}"
        )
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"Invalid setting {field_name}: servers must be a non-empty object")
    for name, raw in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid setting {field_name}: server names must not be empty")
        validate_server_entry(raw, f"{field_name}.servers.{name}")


_SETTINGS: AppSettings | None = None


def get_settings(*, reload: bool = False) -> AppSettings:
    global _SETTINGS
    if _SETTINGS is None or reload:
        _SETTINGS = load_settings()
    return _SETTINGS
