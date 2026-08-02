"""Planning implementation shared by the sequential and DAG engines."""

from __future__ import annotations

import logging
from typing import Any

from agents.base import BaseAgent
from agents.prompt_utils import PromptCapabilities, build_system_prompt
from context.manager import ContextManager
from core.models import EngineStopReason
from dag.graph import TaskDAG
from llm.client import LLMClient
from dag.models import (
    AdaptAction,
    AdaptationResult,
    EdgeType,
    ExitCriteria,
    NodeStatus,
    NodeType,
    PlanAdaptation,
    RiskAssessment,
    TaskEdge,
    TaskNode,
)
from engines.sequential_models import Plan, Step, StepStatus
from execution.models import StepResult

logger = logging.getLogger(__name__)

# ======================================================================
# System prompts
# 系统提示词
# ======================================================================

_SIMPLE_PLANNER_BASE_PROMPT = """\
You are a task planning agent. Your job is to decompose a complex user task
into a clear, ordered sequence of executable steps.

Rules:
1. Break the task into 2-6 concrete, actionable steps.
2. Each step should be independently executable by an executor agent using
   the runtime tools listed at the end of this prompt.
3. Order steps logically; specify dependencies if a step requires output
   from a prior step.
4. Keep step descriptions clear and specific.
5. IMPLICIT DATA: If the task requires information not directly provided
   by the user, distinguish two cases:
   (a) Date/time/weekday — already provided in the auto-injected
       "Current Context" section above. Use it directly. Do NOT create a
       step to obtain the date.
   (b) Other contextual data (current location, user preferences, etc.)
       — create a dedicated step to OBTAIN it via tools. Do NOT make
       assumptions about such data — discover it explicitly.
6. NO UNAUTHORIZED ASSUMPTIONS: Never invent default values for
   unspecified data (e.g., do not assume "Beijing" if location is
   unspecified). If real data cannot be obtained after reasonable
   attempts, the step should return a clear "data unavailable" message
   — never fabricate.
7. LANGUAGE: Write step descriptions in the SAME language as the user's
   task. If the user's task is in Chinese, write step descriptions in
   Chinese. This ensures downstream agents respond consistently.

You MUST respond with a valid JSON object in this exact format:
{
  "steps": [
    {
      "id": 1,
      "description": "What this step should accomplish",
      "dependencies": []
    },
    {
      "id": 2,
      "description": "Next step description",
      "dependencies": [1]
    }
  ]
}
"""

# Builder functions keep the date and runtime guidance current. The planner's
# create_plan() / create_dag() / replan() each rebuild the system prompt before
# use, so the date / HITL guidance reflect runtime state. Module-level
# evaluation would freeze them at import time.
def _build_simple_planner_prompt(
    capabilities: PromptCapabilities | None = None,
) -> str:
    prompt = build_system_prompt(
        _SIMPLE_PLANNER_BASE_PROMPT,
        inject_context=True,
        inject_subagent_guidance=False,
        inject_location_guidance=False,
        inject_search_guidance=False,
        inject_hitl_guidance=False,
        inject_hitl_unavailable_guidance=True,
        inject_skill_guidance=False,
        capabilities=capabilities,
    )
    if capabilities is not None:
        prompt += "\nAvailable action tools: " + ", ".join(
            sorted(capabilities.tool_names)
        )
    return prompt

_PLANNER_BASE_PROMPT = """\
You are a hierarchical task planning agent. Your job is to decompose a
complex user task into a structured, three-level plan:

  Goal (1) -> SubGoals (2-5) -> Actions (1-3 per subgoal)

Rules:
1. Create exactly ONE goal that captures the overall objective.
2. Break the goal into 2-5 subgoals (logical groupings of work).
3. Each subgoal contains 1-3 concrete, executable actions.
4. Actions should be independently executable by a tool-using agent using
   the runtime tools listed at the end of this prompt.
5. For each node, specify:
   - exit_criteria: what defines "done" for this node
   - confidence: 0.0-1.0 how likely this will succeed
   - risk_level: "low" / "medium" / "high"
6. DEPENDENCIES express DATAFLOW — an action's "dependencies" must list ALL
   prior actions whose OUTPUT this action needs, even if they are in a
   DIFFERENT subgoal. Cross-subgoal action dependencies are expected and correct.
   Example: If act_2_1 needs the city name from act_1_1, then
   act_2_1.dependencies MUST include "act_1_1".
   Also set subgoal-level dependencies to reflect ordering (sub_2 depends on sub_1).
7. IMPORTANT: When an action has a "condition" (it's a fallback path), downstream
   actions should list the PRIMARY action (not the fallback) in their "dependencies".
   For example, if act_1_2 is a fallback that runs only when act_1_1 fails, then
   act_2_1 should depend on act_1_1, NOT on act_1_2.
8. If a step might fail, you can add:
   - condition: a keyword that must appear in a prior result for this action to run
   - rollback: description of how to undo this action
   - fallback_strategy: alternative approach if this fails
9. DATAFLOW-FIRST: Before assigning actions to subgoals, trace what data
   flows between steps:
   - What information does each step NEED as input?
   - What information does each step PRODUCE as output?
   - Which prior step produces the input this step needs?
   Then organize into subgoals for clarity, but keep cross-subgoal
   dependencies on the actions that actually exchange data.
10. IMPLICIT DATA: If a task requires information not directly provided in
    the user's message, distinguish two cases:
    (a) Date/time/weekday — already provided in the auto-injected
        "Current Context" section above. Use it directly. Do NOT create an
        action to obtain the date.
    (b) Other contextual data (current location, user preferences, etc.)
        — create a dedicated action to obtain it. Do NOT assume the
        executor already has this data.

You MUST respond with a valid JSON object in this exact format:
{
  "goal": "查询今天用户所在城市的天气并给出建议",
  "goal_exit_criteria": "输出包含天气数据和穿衣建议的自然语言回复",
  "subgoals": [
    {
      "id": "sub_1",
      "description": "获取基础信息",
      "exit_criteria": "获得日期和城市信息",
      "confidence": 0.9,
      "risk_level": "low",
      "fallback_strategy": "",
      "dependencies": [],
      "actions": [
        {
          "id": "act_1_1",
          "description": "获取今天的日期",
          "exit_criteria": "成功获取当前日期",
          "confidence": 0.95,
          "risk_level": "low",
          "fallback_strategy": "",
          "dependencies": [],
          "condition": null,
          "rollback": null
        },
        {
          "id": "act_1_2",
          "description": "确认用户所在城市",
          "exit_criteria": "成功获取城市名称",
          "confidence": 0.8,
          "risk_level": "medium",
          "fallback_strategy": "调用位置查询工具或显式询问用户，禁止假设默认城市",
          "dependencies": [],
          "condition": null,
          "rollback": null
        }
      ]
    },
    {
      "id": "sub_2",
      "description": "查询天气数据",
      "exit_criteria": "获取到有效的天气数据",
      "confidence": 0.85,
      "risk_level": "low",
      "fallback_strategy": "",
      "dependencies": ["sub_1"],
      "actions": [
        {
          "id": "act_2_1",
          "description": "根据城市和日期调用天气API获取数据",
          "exit_criteria": "API返回有效的天气JSON数据",
          "confidence": 0.85,
          "risk_level": "low",
          "fallback_strategy": "使用网络搜索作为备选",
          "dependencies": ["act_1_1", "act_1_2"],
          "condition": null,
          "rollback": null
        }
      ]
    },
    {
      "id": "sub_3",
      "description": "生成并呈现结果",
      "exit_criteria": "输出用户可读的天气报告",
      "confidence": 0.9,
      "risk_level": "low",
      "fallback_strategy": "",
      "dependencies": ["sub_2"],
      "actions": [
        {
          "id": "act_3_1",
          "description": "基于天气数据生成自然语言描述和建议",
          "exit_criteria": "生成完整的天气描述和穿衣建议",
          "confidence": 0.9,
          "risk_level": "low",
          "fallback_strategy": "",
          "dependencies": ["act_2_1"],
          "condition": null,
          "rollback": null
        }
      ]
    }
  ]
}
"""

def _build_planner_prompt(
    capabilities: PromptCapabilities | None = None,
) -> str:
    prompt = build_system_prompt(
        _PLANNER_BASE_PROMPT,
        inject_context=True,
        inject_subagent_guidance=False,
        inject_location_guidance=False,
        inject_search_guidance=False,
        inject_hitl_guidance=False,
        inject_hitl_unavailable_guidance=True,
        inject_skill_guidance=False,
        capabilities=capabilities,
    )
    if capabilities is not None:
        prompt += "\nAvailable action tools: " + ", ".join(
            sorted(capabilities.tool_names)
        )
    return prompt


class PlannerAgent(BaseAgent):
    """Create flat plans, dependency graphs, and adaptive revisions."""

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager | None = None,
        temperature: float = 0.3,
        prompt_capabilities: PromptCapabilities | None = None,
    ):
        # Build the prompt per instance (default to DAG form;
        # create_plan/create_dag below switch as needed).
        super().__init__(
            name="Planner",
            system_prompt=_build_planner_prompt(prompt_capabilities),
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self.temperature = temperature
        self.prompt_capabilities = prompt_capabilities
        self.last_failure_reason: EngineStopReason | None = None

    @staticmethod
    def _classify_model_failure(exc: Exception) -> EngineStopReason:
        if isinstance(
            exc,
            (ValueError, TypeError, KeyError, AttributeError, IndexError),
        ):
            return EngineStopReason.INVALID_RESPONSE
        return EngineStopReason.MODEL_ERROR

    # ==================================================================
    # Sequential planning (flat step list)
    # ==================================================================

    async def create_plan(self, task: str, context: str = "") -> Plan:
        """
        Create a flat step-based plan for the sequential engine.

        Uses SIMPLE_PLANNER_SYSTEM_PROMPT for a lightweight 2-6 step plan.
        使用 SIMPLE_PLANNER_SYSTEM_PROMPT 生成 2-6 步的轻量级计划。
        """
        self.system_prompt = _build_simple_planner_prompt(self.prompt_capabilities)
        self.reset()

        prompt = f"Create an execution plan for this task:\n\nTask: {task}"
        if context:
            prompt += f"\n\nRelevant context:\n{context}"

        logger.info("[Planner] Creating simple plan for: %s", task[:80])
        result = await self.think_json(prompt, temperature=self.temperature)
        plan = self._parse_plan(task, result)

        self.system_prompt = _build_planner_prompt(self.prompt_capabilities)
        return plan

    async def replan(
        self,
        task: str,
        completed_results: list[StepResult],
        failed_steps: list[Step] | None = None,
        feedback: str = "",
    ) -> Plan:
        """
        Revise the sequential plan based on execution progress and feedback.
        基于执行进度和反馈修订顺序计划。
        """
        self.system_prompt = _build_simple_planner_prompt(self.prompt_capabilities)
        self.reset()

        completed_summary = "\n".join(
            f"- Step {r.step_id}: {'SUCCESS' if r.success else 'FAILED'} - {r.output[:200]}"
            for r in completed_results
        )

        prompt = (
            f"The original task needs re-planning.\n\n"
            f"Task: {task}\n\n"
            f"Completed steps so far:\n{completed_summary}\n"
        )
        if failed_steps:
            failed_summary = "\n".join(f"- Step {s.id}: {s.description}" for s in failed_steps)
            prompt += f"\nFailed steps:\n{failed_summary}\n"
        if feedback:
            prompt += f"\nFeedback: {feedback}\n"
        prompt += (
            "\nCreate a NEW plan for the REMAINING work. "
            "Do not repeat already-completed steps. "
            "Account for the feedback and any failures.\n\n"
            "IMPORTANT: In your new plan's \"dependencies\" fields, only "
            "reference step IDs that you DEFINE in THIS new plan. Do NOT "
            "reference step IDs from the 'Completed steps so far' list "
            "above — those steps are already done and their IDs do not "
            "exist in the new plan. The new plan is self-contained.\n"
            "Also: do NOT reuse IDs from completed steps for new steps — "
            "pick fresh IDs (e.g., if completed had 1, 2, start new at 3)."
        )

        logger.info("[Planner] Re-planning sequential task: %s", task[:80])
        result = await self.think_json(prompt, temperature=self.temperature)
        plan = self._parse_plan(task, result)

        self.system_prompt = _build_planner_prompt(self.prompt_capabilities)
        return plan

    @staticmethod
    def _parse_plan(task: str, data: Any) -> Plan:
        """
        Parse LLM JSON output into a sequential Plan model.
        将 LLM 的 JSON 输出解析为顺序 Plan 模型。
        """
        steps = []
        if not isinstance(data, dict):
            logger.warning("[Planner] LLM returned non-dict: %s", type(data).__name__)
            data = {}

        raw_steps = data.get("steps", [])
        # Fallback: try alternative keys if "steps" is missing or empty
        if not raw_steps:
            for alt_key in ("plan", "tasks", "actions"):
                alt = data.get(alt_key, [])
                if isinstance(alt, list) and alt:
                    logger.warning(
                        "[Planner] 'steps' missing/empty, using '%s' (%d items) as fallback. "
                        "Data keys: %s",
                        alt_key, len(alt), list(data.keys()),
                    )
                    raw_steps = alt
                    break

        if not raw_steps:
            logger.warning(
                "[Planner] Plan has 0 steps. LLM response keys: %s, data preview: %.300s",
                list(data.keys()) if isinstance(data, dict) else "N/A",
                str(data),
            )

        for s in raw_steps:
            steps.append(Step(
                id=s.get("id", len(steps) + 1),
                description=s.get("description", ""),
                dependencies=s.get("dependencies", []),
                status=StepStatus.PENDING,
            ))
        plan = Plan(task=task, steps=steps, current_step_index=0)
        logger.info("[Planner] Simple plan created with %d steps", len(steps))
        return plan

    # ==================================================================
    # Hierarchical DAG planning
    # 分层 DAG 规划
    # ==================================================================

    async def create_dag(self, task: str, context: str = "") -> TaskDAG:
        """
        Create a hierarchical TaskDAG for the given task.
        为给定任务创建分层 TaskDAG。

        The LLM outputs a nested JSON (goal > subgoals > actions) in one call.
        We then flatten it into nodes + edges to build the DAG.

        LLM 通过单次调用输出嵌套 JSON（goal > subgoals > actions），
        然后我们将其展开为节点 + 边来构建 DAG。
        """
        prompt = f"Create a hierarchical execution plan for this task:\n\nTask: {task}"
        if context:
            prompt += f"\n\nRelevant context:\n{context}"

        logger.info("[Planner] Creating DAG for: %s", task[:80])

        # 使用 JSON 模式调用 LLM（低温度保证输出结构稳定）
        result = await self.think_json(prompt, temperature=self.temperature)
        dag = self._parse_dag(task, result, context)

        logger.info(
            "[Planner] DAG created: %d nodes, %d edges",
            len(dag.nodes), len(dag.edges),
        )
        return dag

    # ------------------------------------------------------------------
    # Partial replan
    # 局部重规划
    # ------------------------------------------------------------------

    async def replan_subtree(
        self,
        dag: TaskDAG,
        failed_node_id: str,
        feedback: str = "",
    ) -> TaskDAG:
        """
        Replan only the subtree rooted at the failed node's parent.
        Completed nodes are preserved; only the failed branch is regenerated.

        仅重新规划失败节点父节点下的子树。
        已完成的节点完全保留，只重新生成失败的分支。

        Unlike full replanning, this preserves successful work.
        与整体重规划不同，此方法保留所有已成功的工作成果。
        """
        failed_node = dag.nodes.get(failed_node_id)
        if not failed_node:
            logger.warning("[Planner] Cannot replan: node %s not found", failed_node_id)
            return dag

        parent_id = failed_node.parent_id or failed_node_id

        # 汇总已完成节点的结果，供新计划参考（避免重复已做的工作）
        completed_summary = "\n".join(
            f"- {nid}: {dag.state.node_results.get(nid, '(no result)')[:200]}"
            for nid, n in dag.nodes.items()
            if n.status == NodeStatus.COMPLETED
        )

        prompt = (
            f"A subtask has failed and needs replanning.\n\n"
            f"Original task: {dag.state.task}\n\n"
            f"Failed node: {failed_node.description}\n"
            f"Failed node ID: {failed_node_id}\n"
            f"Parent: {parent_id}\n\n"
            f"Completed work so far:\n{completed_summary}\n\n"
        )
        if feedback:
            prompt += f"Feedback: {feedback}\n\n"

        prompt += (
            "Create a NEW plan for ONLY the remaining work under this subtask. "
            "Do not repeat already-completed work. "
            "IMPORTANT: In your plan's \"dependencies\" fields, only reference node IDs "
            "that you define in THIS new plan. Do not reference IDs from the completed work list. "
            "Respond in the same JSON format as before."
        )

        logger.info("[Planner] Replanning subtree from %s", parent_id)
        self.reset()  # 清空历史，以全新视角重规划
        result = await self.think_json(prompt, temperature=self.temperature)
        new_dag = self._parse_dag(dag.state.task, result, dag.state.context)

        # 将新子树合并回原 DAG（保留已完成节点）
        return self._merge_dags(dag, new_dag, parent_id)

    # ------------------------------------------------------------------
    # Adaptive planning — mid-execution plan adjustment
    # 自适应规划——执行中动态调整计划
    # ------------------------------------------------------------------

    async def adapt_plan(self, dag: TaskDAG) -> AdaptationResult:
        """
        Evaluate the current DAG execution progress and decide whether
        pending nodes need adjustment based on intermediate results.

        评估当前 DAG 执行进度，根据中间结果决定待执行节点是否需要调整。

        This is called between super-steps by the DAGExecutor.
        Unlike replan_subtree() which only fires after failure,
        adapt_plan() proactively adjusts the plan based on new information.

        此方法在超步之间由 DAGExecutor 调用。
        与 replan_subtree()（仅在失败后触发）不同，
        adapt_plan() 基于新信息主动调整计划。

        Returns:
            AdaptationResult: 包含是否需要调整、理由和具体调整操作列表。
        """
        self.last_failure_reason = None
        completed_summary = "\n".join(
            f"- {nid} [COMPLETED]: {dag.state.node_results.get(nid, '(no result)')[:300]}"
            for nid, n in dag.nodes.items()
            if n.status == NodeStatus.COMPLETED and n.node_type == NodeType.ACTION
        )
        pending_summary = "\n".join(
            f"- {n.id} [PENDING]: {n.description} (exit: {n.exit_criteria.description})"
            for n in dag.get_pending_action_nodes()
        )

        if not pending_summary:
            return AdaptationResult(should_adapt=False, reasoning="No pending nodes to adapt.")

        self.reset()
        prompt = (
            f"You are evaluating whether an execution plan needs mid-flight adjustments.\n\n"
            f"ORIGINAL TASK: {dag.state.task}\n\n"
            f"COMPLETED RESULTS SO FAR:\n{completed_summary}\n\n"
            f"PENDING ACTIONS (not yet executed):\n{pending_summary}\n\n"
            f"Based on the completed results, evaluate each pending action:\n"
            f"- Are any pending actions now UNNECESSARY based on what we learned?\n"
            f"- Do any pending actions need their description/goal MODIFIED?\n"
            f"- Are there NEW actions needed that weren't in the original plan?\n\n"
            f"If no changes are needed, set should_adapt=false.\n\n"
            f"Respond with JSON:\n"
            f"{{\n"
            f'  "should_adapt": true/false,\n'
            f'  "reasoning": "why adaptation is or is not needed",\n'
            f'  "adaptations": [\n'
            f"    {{\n"
            f'      "action": "keep" | "modify" | "remove" | "add",\n'
            f'      "target_node_id": "existing node id or new node id",\n'
            f'      "reason": "why this change",\n'
            f'      "new_description": "for modify/add only",\n'
            f'      "new_exit_criteria": "for modify/add only",\n'
            f'      "parent_node_id": "for add only - parent subgoal id",\n'
            f'      "dependencies": ["dep_id1"] // for add only\n'
            f"    }}\n"
            f"  ]\n"
            f"}}"
        )

        logger.info("[Planner] Evaluating plan adaptation after %d completed actions",
                     dag.get_completed_action_count())

        try:
            data = await self.think_json(prompt, temperature=self.temperature)
            adaptations = []
            for a in data.get("adaptations", []):
                action_str = a.get("action", "keep").lower()
                try:
                    action = AdaptAction(action_str)
                except ValueError:
                    action = AdaptAction.KEEP

                adaptations.append(PlanAdaptation(
                    action=action,
                    target_node_id=a.get("target_node_id", ""),
                    reason=a.get("reason", ""),
                    new_description=a.get("new_description", ""),
                    new_exit_criteria=a.get("new_exit_criteria", ""),
                    parent_node_id=a.get("parent_node_id", ""),
                    dependencies=a.get("dependencies", []),
                ))

            result = AdaptationResult(
                should_adapt=data.get("should_adapt", False),
                reasoning=data.get("reasoning", ""),
                adaptations=[a for a in adaptations if a.action != AdaptAction.KEEP],
            )

            if result.should_adapt and result.adaptations:
                logger.info("[Planner] Plan adaptation needed: %d changes (%s)",
                            len(result.adaptations), result.reasoning[:100])
            else:
                logger.info("[Planner] No plan adaptation needed: %s", result.reasoning[:100])

            return result

        except Exception as exc:
            logger.warning("[Planner] adapt_plan failed: %s. Continuing without adaptation.", exc)
            self.last_failure_reason = self._classify_model_failure(exc)
            return AdaptationResult(should_adapt=False, reasoning=f"Adaptation evaluation failed: {exc}")

    def apply_adaptations(self, dag: TaskDAG, adaptations: list[PlanAdaptation]) -> list[str]:
        """
        Apply a list of adaptation actions to the DAG.
        将一组调整操作应用到 DAG。

        Returns list of change descriptions for logging/UI.
        返回变更描述列表，供日志/UI 展示。
        """
        changes: list[str] = []
        for adapt in adaptations:
            if adapt.action == AdaptAction.REMOVE:
                if dag.remove_pending_node(adapt.target_node_id):
                    changes.append(f"Removed node '{adapt.target_node_id}': {adapt.reason}")

            elif adapt.action == AdaptAction.MODIFY:
                desc = adapt.new_description or None
                exit_c = adapt.new_exit_criteria or None
                if dag.modify_node(adapt.target_node_id, description=desc, exit_criteria_desc=exit_c):
                    changes.append(f"Modified node '{adapt.target_node_id}': {adapt.reason}")

            elif adapt.action == AdaptAction.ADD:
                new_node = TaskNode(
                    id=adapt.target_node_id,
                    node_type=NodeType.ACTION,
                    description=adapt.new_description or f"Dynamic action: {adapt.target_node_id}",
                    exit_criteria=ExitCriteria(
                        description=adapt.new_exit_criteria or "Action completed",
                        validation_prompt=f"Has this been achieved? {adapt.new_exit_criteria or 'Action completed'}",
                    ),
                    parent_id=adapt.parent_node_id or None,
                )
                if dag.add_dynamic_node(new_node):
                    if adapt.parent_node_id:
                        dag.add_dynamic_edge(TaskEdge(
                            source=adapt.parent_node_id,
                            target=adapt.target_node_id,
                            edge_type=EdgeType.DEPENDENCY,
                        ))
                    for dep_id in adapt.dependencies:
                        dag.add_dynamic_edge(TaskEdge(
                            source=dep_id,
                            target=adapt.target_node_id,
                            edge_type=EdgeType.DEPENDENCY,
                        ))
                    changes.append(f"Added node '{adapt.target_node_id}': {adapt.new_description}")

        if changes:
            logger.info("[Planner] Applied %d adaptations: %s", len(changes), "; ".join(changes))
        return changes

    # ------------------------------------------------------------------
    # Parsing: LLM JSON -> TaskDAG
    # 解析：LLM JSON 输出 -> TaskDAG
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dag(task: str, data: Any, context: str = "") -> TaskDAG:
        """
        Parse LLM JSON output into a TaskDAG with nodes and edges.
        将 LLM 输出的 JSON 解析为带节点和边的 TaskDAG。
        """
        if not isinstance(data, dict):
            raise ValueError(
                "DAG planner response must be an object, got "
                f"{type(data).__name__}"
            )

        raw_subgoals = data.get("subgoals")
        if not isinstance(raw_subgoals, list) or not raw_subgoals:
            raise ValueError(
                "DAG planner response must contain a non-empty subgoals array"
            )
        if any(not isinstance(subgoal, dict) for subgoal in raw_subgoals):
            raise ValueError("Every DAG subgoal must be an object")
        if not any(
            isinstance(subgoal.get("actions"), list) and subgoal["actions"]
            for subgoal in raw_subgoals
        ):
            raise ValueError(
                "DAG planner response must contain at least one executable action"
            )

        nodes: dict[str, TaskNode] = {}
        edges: list[TaskEdge] = []

        # --- Goal node ---
        # --- 创建顶层 Goal 节点 ---
        goal_desc = data.get("goal", task)
        goal_exit = data.get("goal_exit_criteria", "Task completed successfully")
        goal_node = TaskNode(
            id="goal_1",
            node_type=NodeType.GOAL,
            description=goal_desc,
            exit_criteria=ExitCriteria(
                description=goal_exit,
                validation_prompt=f"Has the following goal been achieved? Goal: {goal_exit}",
            ),
        )
        nodes["goal_1"] = goal_node

        # --- SubGoal + Action nodes ---
        # --- 创建 SubGoal 节点和 Action 节点 ---
        all_action_ids: list[str] = []  # 跟踪跨子目标的全局 Action ID 列表（必须在 subgoals 循环外，否则跨子目标回退失效）

        for sg in raw_subgoals:
            sg_id = str(sg.get("id", f"sub_{len(nodes)}"))
            sg_node = TaskNode(
                id=sg_id,
                node_type=NodeType.SUBGOAL,
                description=sg.get("description", ""),
                exit_criteria=ExitCriteria(
                    description=sg.get("exit_criteria", "Subgoal completed"),
                    validation_prompt=f"Has this subgoal been achieved? {sg.get('exit_criteria', '')}",
                ),
                risk=RiskAssessment(
                    confidence=float(sg.get("confidence", 0.8)),
                    risk_level=sg.get("risk_level", "low"),
                    fallback_strategy=sg.get("fallback_strategy", ""),
                ),
                parent_id="goal_1",  # 所有 SubGoal 的父节点都是 goal_1
            )
            nodes[sg_id] = sg_node

            # Goal -> SubGoal 依赖边（SubGoal 需在 Goal 之后执行）
            edges.append(TaskEdge(source="goal_1", target=sg_id, edge_type=EdgeType.DEPENDENCY))

            # SubGoal 间的依赖边（如 sub_2 依赖 sub_1）
            for dep_id in sg.get("dependencies", []):
                edges.append(TaskEdge(source=str(dep_id), target=sg_id, edge_type=EdgeType.DEPENDENCY))

            # --- Action nodes under this subgoal ---
            # --- 创建该 SubGoal 下的 Action 节点 ---
            subgoal_action_ids: list[str] = []  # 追踪该 SubGoal 下的所有 Action ID
            for act in sg.get("actions", []):
                act_id = str(act.get("id", f"act_{sg_id}_{len(nodes)}"))
                act_node = TaskNode(
                    id=act_id,
                    node_type=NodeType.ACTION,
                    description=act.get("description", ""),
                    exit_criteria=ExitCriteria(
                        description=act.get("exit_criteria", "Action completed"),
                        validation_prompt=f"Has this action been completed? {act.get('exit_criteria', '')}",
                    ),
                    risk=RiskAssessment(
                        confidence=float(act.get("confidence", 0.8)),
                        risk_level=act.get("risk_level", "low"),
                        fallback_strategy=act.get("fallback_strategy", ""),
                    ),
                    parent_id=sg_id,
                    rollback_action=act.get("rollback"),
                )
                nodes[act_id] = act_node
                subgoal_action_ids.append(act_id)
                all_action_ids.append(act_id)

                # SubGoal -> Action 依赖边（Action 需在所属 SubGoal 之后执行）
                edges.append(TaskEdge(source=sg_id, target=act_id, edge_type=EdgeType.DEPENDENCY))

                # Action 间的依赖边（如 act_1_2 依赖 act_1_1）
                for dep_id in act.get("dependencies", []):
                    edges.append(TaskEdge(
                        source=str(dep_id), target=act_id, edge_type=EdgeType.DEPENDENCY,
                    ))

                # 修复 High #6: conditional 边应指向产生结果的 ACTION 节点，而不是 SubGoal
                # 因为 SubGoal 不执行也不写入 node_results，条件判断会失败
                condition = act.get("condition")
                if condition:
                    # 条件边的 source 优先指向同子目标前一个 ACTION，
                    # 其次指向跨子目标的上一个 ACTION，最后回退到 subgoal 自身
                    cond_source = (
                        subgoal_action_ids[-2] if len(subgoal_action_ids) >= 2
                        else all_action_ids[-2] if len(all_action_ids) >= 2
                        else sg_id
                    )
                    edges.append(TaskEdge(
                        source=cond_source, target=act_id,
                        edge_type=EdgeType.CONDITIONAL,
                        condition=condition,
                    ))

                # 修复 High #6: 当 action 有 rollback_action 时，生成 ROLLBACK 边
                rollback_desc = act.get("rollback")
                # 过滤 LLM 输出的"无"占位符（中英文常见 null 替代词）
                if rollback_desc and rollback_desc.strip().lower() not in ('无', 'none', 'n/a', 'null', 'na', '-'):
                    rollback_id = f"rb_{act_id}"
                    rollback_node = TaskNode(
                        id=rollback_id,
                        node_type=NodeType.ACTION,
                        description=rollback_desc,
                        parent_id=sg_id,
                    )
                    nodes[rollback_id] = rollback_node
                    edges.append(TaskEdge(
                        source=act_id, target=rollback_id,
                        edge_type=EdgeType.ROLLBACK,
                    ))

        # === Infer subgoal-level dependencies from cross-subgoal action dependencies ===
        # 如果 act_2_1 (parent=sub_2) 依赖 act_1_1 (parent=sub_1)，
        # 则自动推断 sub_2 依赖 sub_1（如果 LLM 未显式设置）
        inferred_sg_deps: set[tuple[str, str]] = set()
        for e in edges:
            if e.edge_type == EdgeType.DEPENDENCY:
                src = nodes.get(e.source)
                tgt = nodes.get(e.target)
                if (src and tgt
                        and src.parent_id and tgt.parent_id
                        and src.parent_id != tgt.parent_id):
                    inferred_sg_deps.add((src.parent_id, tgt.parent_id))

        existing_sg_deps = {
            (e.source, e.target) for e in edges
            if e.edge_type == EdgeType.DEPENDENCY
            and e.source in nodes and e.target in nodes
            and nodes[e.source].node_type == NodeType.SUBGOAL
            and nodes[e.target].node_type == NodeType.SUBGOAL
        }
        for src_sg, tgt_sg in inferred_sg_deps:
            if (src_sg, tgt_sg) not in existing_sg_deps:
                edges.append(TaskEdge(
                    source=src_sg, target=tgt_sg,
                    edge_type=EdgeType.DEPENDENCY,
                ))
                existing_sg_deps.add((src_sg, tgt_sg))
                logger.debug(
                    "[Planner] Inferred subgoal dependency: %s -> %s (from action-level deps)",
                    src_sg, tgt_sg,
                )

        # === Fallback dependency rewiring ===
        # 当 fallback 节点（CONDITIONAL 边的 target）作为下游 DEPENDENCY 边的 source 时，
        # 将 DEPENDENCY 边的 source 重定向为 primary 路径节点（CONDITIONAL 边的 source）。
        # 这避免 fallback 被跳过时级联跳过其下游节点。
        fallback_to_primary: dict[str, str] = {}
        for e in edges:
            if e.edge_type == EdgeType.CONDITIONAL:
                fallback_to_primary[e.target] = e.source

        rewired_edges: list[TaskEdge] = []
        for e in edges:
            if e.edge_type == EdgeType.DEPENDENCY and e.source in fallback_to_primary:
                primary = fallback_to_primary[e.source]
                rewired = TaskEdge(
                    source=primary, target=e.target,
                    edge_type=EdgeType.DEPENDENCY,
                )
                rewired_edges.append(rewired)
                logger.debug(
                    "[Planner] Rewiring fallback dependency: %s -> %s => %s -> %s",
                    e.source, e.target, primary, e.target,
                )
            else:
                rewired_edges.append(e)

        edges = rewired_edges

        # Deduplicate edges（去重，避免重复边导致计算错误）
        seen: set[tuple] = set()
        unique_edges: list[TaskEdge] = []
        for e in edges:
            key = (e.source, e.target, e.edge_type.value)
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        # Filter orphan edges — edges whose source/target don't exist in nodes.
        # Occurs during replan when LLM references completed node IDs from the old DAG
        # in its dependency specifications. These edges become valid again after _merge_dags().
        valid_node_ids = set(nodes.keys())
        valid_edges: list[TaskEdge] = []
        filtered_edges: list[TaskEdge] = []
        for e in unique_edges:
            if e.source not in valid_node_ids or e.target not in valid_node_ids:
                logger.warning(
                    "[Planner] Filtering orphan edge %s (%s -> %s): "
                    "endpoint not in parsed nodes (likely reference to old DAG completed node)",
                    e.edge_type.value, e.source, e.target,
                )
                filtered_edges.append(e)
            else:
                valid_edges.append(e)

        dag = TaskDAG(task=task, nodes=nodes, edges=valid_edges, context=context)
        dag._filtered_edges = filtered_edges  # 保存供 _merge_dags() 重建
        return dag

    # ------------------------------------------------------------------
    # DAG merging for partial replan
    # DAG 合并（用于局部重规划）
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_dags(old_dag: TaskDAG, new_dag: TaskDAG, parent_id: str) -> TaskDAG:
        """
        Merge a new (replanned) DAG into the old DAG, replacing the
        subtree under `parent_id` while keeping completed nodes.

        将新（重规划的）DAG 合并到旧 DAG 中：
        替换 `parent_id` 下的子树，同时保留所有已完成的节点。
        这是局部重规划的核心：只替换失败部分，保留成功成果。
        """
        merged_nodes: dict[str, TaskNode] = {}
        merged_edges: list[TaskEdge] = []

        # 确定需要移除的节点：失败子树中尚未完成的节点
        failed_subtree = set(old_dag.get_downstream(parent_id)) | {parent_id}
        for nid, node in old_dag.nodes.items():
            if nid not in failed_subtree or node.status == NodeStatus.COMPLETED:
                merged_nodes[nid] = node  # 保留已完成节点和不在失败子树中的节点

        # 保留不涉及被移除节点的边
        removed = failed_subtree - set(merged_nodes.keys())
        for edge in old_dag.edges:
            if edge.source not in removed and edge.target not in removed:
                merged_edges.append(edge)

        # 将新 DAG 的节点和边加入（避免 ID 冲突）
        for nid, node in new_dag.nodes.items():
            if nid not in merged_nodes:
                merged_nodes[nid] = node

        # 修复 M3: 合并边时去重，避免重复边
        seen_edges = {(e.source, e.target, e.edge_type.value) for e in merged_edges}
        for edge in new_dag.edges:
            edge_key = (edge.source, edge.target, edge.edge_type.value)
            if edge_key not in seen_edges:
                merged_edges.append(edge)
                seen_edges.add(edge_key)

        # Rebuild dependency edges from new nodes to old completed nodes.
        # During _parse_dag(), the LLM may reference completed nodes from the old DAG
        # (e.g., "dependencies": ["act_2_2"]) which got filtered as orphan edges.
        # After merge, both endpoints exist — reconstruct these cross-DAG dependency edges.
        merged_node_ids = set(merged_nodes.keys())
        if hasattr(new_dag, '_filtered_edges'):
            for edge in new_dag._filtered_edges:
                if edge.source in merged_node_ids and edge.target in merged_node_ids:
                    edge_key = (edge.source, edge.target, edge.edge_type.value)
                    if edge_key not in seen_edges:
                        merged_edges.append(edge)
                        seen_edges.add(edge_key)
                        logger.debug(
                            "[Planner] Reconstructed cross-DAG edge: %s (%s -> %s)",
                            edge.edge_type.value, edge.source, edge.target,
                        )

        # Ensure structural edges: parent -> new node (fallback for missing parent in new DAG)
        new_node_ids = set(new_dag.nodes.keys())
        for node_id in new_node_ids:
            node_obj = new_dag.nodes.get(node_id)
            if node_obj and node_obj.parent_id and node_obj.parent_id in merged_node_ids:
                structural_key = (node_obj.parent_id, node_id, EdgeType.DEPENDENCY.value)
                if structural_key not in seen_edges:
                    merged_edges.append(TaskEdge(
                        source=node_obj.parent_id, target=node_id,
                        edge_type=EdgeType.DEPENDENCY,
                    ))
                    seen_edges.add(structural_key)

        result_dag = TaskDAG(
            task=old_dag.state.task,
            nodes=merged_nodes,
            edges=merged_edges,
            context=old_dag.state.context,
        )

        # 修复 Medium #9: 清理被移除节点的 node_results，防止历史输出污染反思评估
        # 只保留仍在 merged_nodes 中的节点的旧结果
        valid_node_ids = set(merged_nodes.keys())
        result_dag.state.node_results = {
            k: v for k, v in old_dag.state.node_results.items()
            if k in valid_node_ids
        }

        # 继承旧 DAG 的 checkpoints，保留时间旅行调试能力
        result_dag._checkpoints = list(old_dag._checkpoints)

        return result_dag
