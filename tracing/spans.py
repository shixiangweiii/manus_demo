"""
Span Names and Attribute Keys - Semantic constants for tracing.
Span 名称和属性键名 —— 追踪语义常量定义。

Follows OpenTelemetry GenAI Semantic Conventions where applicable.
尽可能遵循 OpenTelemetry GenAI 语义规范。

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

from __future__ import annotations


# ======================================================================
# Span Names（Span 名称常量）
# ======================================================================

class SpanName:
    """Standard span names used throughout the tracing system.
    追踪系统中使用的标准 Span 名称。"""

    # --- Top Level ---
    TASK_EXECUTION = "task_execution"

    # --- Context Gathering ---
    GATHER_CONTEXT = "orchestrator.gather_context"
    MEMORY_SEARCH = "memory.search"
    KNOWLEDGE_RETRIEVE = "knowledge.retrieve"

    # --- Planning ---
    CLASSIFY_TASK = "planner.classify_task"
    CREATE_PLAN = "planner.create_plan"
    CREATE_DAG = "planner.create_dag"
    CREATE_TODO_LIST = "planner.create_todo_list"
    REPLAN = "planner.replan"
    REPLAN_SUBTREE = "planner.replan_subtree"
    ADAPTIVE_PLANNING = "planner.adaptive_planning"

    # --- Execution ---
    EXECUTION_SIMPLE = "execution.simple"
    EXECUTION_DAG = "execution.dag"
    EXECUTION_EMERGENT = "execution.emergent"

    # --- DAG Specific ---
    DAG_SUPER_STEP = "dag.super_step"
    NODE_EXECUTE = "node.execute"
    NODE_VALIDATE = "node.validate_exit_criteria"
    CONDITION_EVAL = "dag.condition_eval"

    # --- Simple Specific ---
    STEP_EXECUTE = "step.execute"

    # --- Emergent Specific ---
    TODO_EXECUTE = "todo.execute"
    TODO_UPDATE_LIST = "todo.update_list"

    # --- ReAct Loop ---
    REACT_ITERATION = "react.iteration"

    # --- LLM Calls ---
    LLM_CHAT = "llm.chat"
    LLM_CHAT_WITH_TOOLS = "llm.chat_with_tools"
    LLM_CHAT_JSON = "llm.chat_json"

    # --- Tool Calls ---
    TOOL_EXECUTE = "tool.execute"

    # --- Reflection ---
    REFLECT = "reflector.reflect"
    REFLECT_DAG = "reflector.reflect_dag"

    # --- Memory ---
    MEMORY_STORE = "memory.store"


# ======================================================================
# Attribute Keys（属性键名常量）
# ======================================================================

class AttrKey:
    """Standard attribute keys following OTel GenAI Semantic Conventions.
    遵循 OTel GenAI 语义规范的标准属性键名。"""

    # --- Task Level ---
    TASK_ID = "task.id"
    TASK_INPUT = "task.input"
    TASK_COMPLEXITY = "task.complexity"
    TASK_CLASSIFICATION_METHOD = "task.classification_method"
    TASK_SUCCESS = "task.success"
    TASK_OUTPUT = "task.output"

    # --- GenAI / LLM (OTel GenAI SemConv) ---
    GEN_AI_SYSTEM = "gen_ai.system"
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
    GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
    GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
    GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
    GEN_AI_RESPONSE_CONTENT = "gen_ai.response.content"
    GEN_AI_PROMPT_CONTENT = "gen_ai.prompt.content"
    GEN_AI_CALL_TYPE = "gen_ai.call_type"
    GEN_AI_RETRY_COUNT = "gen_ai.retry_count"

    # --- Tool ---
    TOOL_NAME = "tool.name"
    TOOL_PARAMETERS = "tool.parameters"
    TOOL_RESULT = "tool.result"
    TOOL_RESULT_SIZE = "tool.result_size"
    TOOL_SUCCESS = "tool.success"
    TOOL_LATENCY_MS = "tool.latency_ms"
    TOOL_ERROR = "tool.error"

    # --- DAG ---
    DAG_TOTAL_NODES = "dag.total_nodes"
    DAG_TOTAL_EDGES = "dag.total_edges"
    DAG_SUPERSTEP_INDEX = "dag.superstep_index"
    DAG_PARALLEL_COUNT = "dag.parallel_count"

    # --- Node ---
    NODE_ID = "node.id"
    NODE_TYPE = "node.type"
    NODE_DESCRIPTION = "node.description"
    NODE_STATUS = "node.status"
    NODE_EXIT_CRITERIA = "node.exit_criteria"

    # --- Step (Simple path) ---
    STEP_ID = "step.id"
    STEP_DESCRIPTION = "step.description"
    STEP_STATUS = "step.status"

    # --- TODO (Emergent path) ---
    TODO_ID = "todo.id"
    TODO_DESCRIPTION = "todo.description"
    TODO_STATUS = "todo.status"
    TODO_RETRY_COUNT = "todo.retry_count"
    TODO_LIST_SIZE = "todo.list_size"

    # --- ReAct ---
    REACT_ITERATION_INDEX = "react.iteration"
    REACT_MAX_ITERATIONS = "react.max_iterations"
    REACT_HAS_TOOL_CALL = "react.has_tool_call"

    # --- Reflection ---
    REFLECTION_PASSED = "reflection.passed"
    REFLECTION_SCORE = "reflection.score"
    REFLECTION_FEEDBACK = "reflection.feedback"

    # --- Plan ---
    PLAN_TYPE = "plan.type"
    PLAN_STEPS_COUNT = "plan.steps_count"
    PLAN_NODES_COUNT = "plan.nodes_count"

    # --- Memory ---
    MEMORY_QUERY = "memory.query"
    MEMORY_RESULTS_COUNT = "memory.results_count"

    # --- Knowledge ---
    KNOWLEDGE_QUERY = "knowledge.query"
    KNOWLEDGE_RESULTS_COUNT = "knowledge.results_count"

    # --- Adaptation ---
    ADAPTATION_ACTION_COUNT = "adaptation.action_count"
    ADAPTATION_SHOULD_ADAPT = "adaptation.should_adapt"

    # --- Performance ---
    LATENCY_MS = "latency_ms"
    ERROR_TYPE = "error.type"
    ERROR_MESSAGE = "error.message"


# ======================================================================
# Event Names（事件名称常量）
# ======================================================================

class EventName:
    """Standard event names for span events.
    Span 事件的标准名称。"""

    # --- LLM ---
    LLM_REQUEST_START = "llm.request.start"
    LLM_REQUEST_END = "llm.request.end"
    LLM_RETRY = "llm.retry"
    LLM_RATE_LIMITED = "llm.rate_limited"

    # --- Tool ---
    TOOL_CALL_START = "tool.call.start"
    TOOL_CALL_END = "tool.call.end"
    TOOL_CALL_ERROR = "tool.call.error"

    # --- DAG ---
    NODE_STATE_TRANSITION = "node.state_transition"
    SUPERSTEP_START = "superstep.start"
    SUPERSTEP_END = "superstep.end"

    # --- Planning ---
    PLAN_GENERATED = "plan.generated"
    PLAN_ADAPTATION_TRIGGERED = "plan.adaptation.triggered"
    REPLAN_TRIGGERED = "replan.triggered"

    # --- Execution ---
    STEP_SKIPPED = "step.skipped"
    EARLY_BREAK = "execution.early_break"

    # --- Reflection ---
    REFLECTION_COMPLETE = "reflection.complete"

    # --- Context ---
    CONTEXT_COMPRESSED = "context.compressed"
