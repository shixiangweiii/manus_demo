"""Read-only compatibility aliases for retained peripheral modules.

New runtime code receives ``AppSettings`` explicitly. Keep this facade limited
to names still used by modules scheduled for later peripheral refactors.
"""

from core.settings import PROJECT_ROOT, get_settings

_settings = get_settings()

# Shared local paths and limits
MEMORY_DIR = _settings.paths.state_dir
SANDBOX_DIR = _settings.paths.sandbox_dir
SHORT_TERM_WINDOW = _settings.capabilities.short_term_window
PYTHON_COMMAND = _settings.tools.python_command
CODE_EXEC_TIMEOUT = _settings.tools.code_timeout_seconds
SHELL_EXEC_TIMEOUT = _settings.tools.shell_timeout_seconds
SUBPROCESS_MAX_OUTPUT_BYTES = _settings.tools.subprocess_max_output_bytes
SHELL_MAX_CONCURRENT = _settings.tools.shell_max_concurrent
CODE_MAX_CONCURRENT = _settings.tools.code_max_concurrent
LOCATION_SSL_VERIFY = _settings.tools.location_ssl_verify
SEARCH_CONVERGENCE_THRESHOLD = _settings.tools.search_convergence_threshold
TOOL_FAILURE_THRESHOLD = _settings.tools.failure_threshold

# Knowledge and memory
KNOWLEDGE_DOCS_DIR = _settings.paths.knowledge_docs_dir
KNOWLEDGE_CHUNK_SIZE = _settings.capabilities.knowledge_chunk_size
KNOWLEDGE_TOP_K = _settings.capabilities.knowledge_top_k
MEMORY_MIN_CONFIDENCE = _settings.capabilities.memory_min_confidence
MEMORY_SEARCH_TOP_K = _settings.capabilities.memory_search_top_k
MEMORY_LLM_CONSOLIDATION_ENABLED = _settings.capabilities.memory_llm_consolidation

# Interactive and delegated execution
HITL_ENABLED = _settings.capabilities.hitl
HITL_MAX_PROMPTS_PER_TASK = _settings.capabilities.hitl_max_prompts
HITL_USER_INPUT_TIMEOUT = _settings.capabilities.hitl_timeout_seconds
SUBAGENT_ENABLED = _settings.capabilities.subagent
SUBAGENT_MAX_ITERATIONS = _settings.capabilities.subagent_max_iterations
SUBAGENT_TIMEOUT = _settings.capabilities.subagent_timeout_seconds
SUBAGENT_MAX_CONCURRENT = _settings.capabilities.subagent_max_concurrent
SUBAGENT_SUMMARY_MAX_LENGTH = _settings.capabilities.subagent_summary_max_length
SUBAGENT_MAX_CALLS_PER_TASK = _settings.capabilities.subagent_max_calls
SUBAGENT_MAX_TOKENS_PER_CALL = _settings.capabilities.subagent_max_tokens
SUBAGENT_DEFAULT_TOOL_WHITELIST = _settings.capabilities.subagent_tool_whitelist
SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH = _settings.capabilities.subagent_task_max_length
EMERGENT_PARALLEL_TODOS = _settings.capabilities.parallel_todos
HANDOFF_MAX_CALLS_PER_TASK = _settings.capabilities.handoff_max_calls
HANDOFF_TIMEOUT = _settings.capabilities.handoff_timeout_seconds
HANDOFF_MAX_ITERATIONS = _settings.capabilities.handoff_max_iterations
REMOTE_SUBAGENT_MAX_CALLS_PER_TASK = _settings.capabilities.remote_subagent_max_calls
REMOTE_SUBAGENT_TIMEOUT = _settings.capabilities.remote_subagent_timeout_seconds
REMOTE_AGENT_FETCH_CARD = _settings.capabilities.remote_agent_fetch_card

# Skills and self-evolution
SKILLS_ENABLED = _settings.capabilities.skills
SKILLS_PROJECT_DIR = str(PROJECT_ROOT / ".agents" / "skills")
SKILLS_USER_DIR = _settings.capabilities.skills_user_dir
SKILLS_DIRS = _settings.capabilities.skills_dirs
SKILLS_MAX_ACTIVATIONS_PER_TASK = _settings.capabilities.skills_max_activations
SKILLS_MAX_CONTENT_TOKENS = _settings.capabilities.skills_max_content_tokens
SKILL_AUTO_DISTILL_MIN_SUCCESSES = _settings.capabilities.skill_auto_distill_min_successes
SKILL_AUTO_DISTILL_CONFIDENCE_CAP = _settings.capabilities.skill_auto_distill_confidence_cap
SKILL_OPTIMIZE_LLM_ENABLED = _settings.capabilities.skill_optimize_with_llm
SKILL_OPTIMIZE_VALIDATION_RATIO = _settings.capabilities.skill_optimize_validation_ratio
SKILL_OPTIMIZE_MAX_TOKENS = _settings.capabilities.skill_optimize_max_tokens
SELF_EVOLUTION_LLM_EXTRACTION = _settings.capabilities.self_evolution_llm_extraction
SELF_EVOLUTION_MAX_HINTS = _settings.capabilities.self_evolution_max_hints
SELF_EVOLUTION_CONFIDENCE_CAP = _settings.capabilities.self_evolution_confidence_cap

# AgentBay compatibility defaults
AGENTBAY_API_KEY = _settings.capabilities.agentbay_api_key
AGENTBAY_LOG_LEVEL = _settings.capabilities.agentbay_log_level
AGENTBAY_MAX_CONCURRENT_SESSIONS = _settings.capabilities.agentbay_max_concurrent
AGENTBAY_CODE_IMAGE = _settings.capabilities.agentbay_code_image
AGENTBAY_BROWSER_IMAGE = _settings.capabilities.agentbay_browser_image
AGENTBAY_SESSION_IDLE_RELEASE_MINUTES = _settings.capabilities.agentbay_idle_release_minutes
AGENTBAY_SESSION_MAX_RUNTIME_MINUTES = _settings.capabilities.agentbay_max_runtime_minutes
AGENTBAY_CODE_TIMEOUT_SECONDS = _settings.capabilities.agentbay_code_timeout_seconds
AGENTBAY_BROWSER_TIMEOUT_MS = _settings.capabilities.agentbay_browser_timeout_ms
