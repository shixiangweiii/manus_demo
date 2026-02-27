"""
Planner Agent - Hybrid Plan-and-Execute with automatic routing.
Planner 智能体 —— 混合规划，自动路由 v1 扁平计划 / v2 DAG 分层计划。

v1 (simple):  Flat list of 2-6 Steps, sequential execution.
v2 (complex): Hierarchical DAG: Goal -> SubGoals -> Actions.
v4 (current): Two-stage hybrid classifier automatically selects v1 or v2:
    Stage 1 — rule-based fast filter (zero cost, < 1ms)
    Stage 2 — lightweight LLM call (only for ambiguous cases, ~60 tokens)

v1（简单模式）：2-6 个扁平步骤的线性列表，顺序执行。
v2（复杂模式）：分层 DAG：Goal -> SubGoals -> Actions。
v4（当前）：两阶段混合分类器自动选择 v1 或 v2：
    Stage 1 —— 规则快筛（零成本，< 1ms）
    Stage 2 —— 轻量 LLM 调用（仅对模糊区间，~60 tokens）

Design rationale (inspired by DAAO & RouteLLM, ICLR 2025):
  Rule-based heuristics handle the obvious 60-70% of requests at zero cost.
  Only the ambiguous 30-40% triggers an LLM classification call.
  This balances token savings with routing accuracy.

设计依据（参考 DAAO 和 RouteLLM，ICLR 2025）：
  规则启发式处理 60-70% 显然的请求（零成本），
  仅 30-40% 模糊区间触发 LLM 分类调用，
  在 token 节省和路由准确率之间取得最佳平衡。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import config
from agents.base import BaseAgent
from context.manager import ContextManager
from dag.graph import TaskDAG
from llm.client import LLMClient
from schema import (
    AdaptAction,
    AdaptationResult,
    EdgeType,
    ExitCriteria,
    NodeStatus,
    NodeType,
    Plan,
    PlanAdaptation,
    RiskAssessment,
    Step,
    StepResult,
    StepStatus,
    TaskEdge,
    TaskNode,
)

logger = logging.getLogger(__name__)

# ======================================================================
# System prompts
# 系统提示词
# ======================================================================

SIMPLE_PLANNER_SYSTEM_PROMPT = """\
You are a task planning agent. Your job is to decompose a complex user task
into a clear, ordered sequence of executable steps.

Rules:
1. Break the task into 2-6 concrete, actionable steps.
2. Each step should be independently executable by an executor agent that
   has access to tools: web_search, execute_python, file_ops.
3. Order steps logically; specify dependencies if a step requires output
   from a prior step.
4. Keep step descriptions clear and specific.

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

PLANNER_SYSTEM_PROMPT = """\
You are a hierarchical task planning agent. Your job is to decompose a
complex user task into a structured, three-level plan:

  Goal (1) -> SubGoals (2-5) -> Actions (1-3 per subgoal)

Rules:
1. Create exactly ONE goal that captures the overall objective.
2. Break the goal into 2-5 subgoals (logical groupings of work).
3. Each subgoal contains 1-3 concrete, executable actions.
4. Actions should be independently executable by a tool-using agent
   with access to: web_search, execute_python, file_ops.
5. For each node, specify:
   - exit_criteria: what defines "done" for this node
   - confidence: 0.0-1.0 how likely this will succeed
   - risk_level: "low" / "medium" / "high"
6. Specify dependencies between subgoals and between actions.
   Dependencies reference IDs within the SAME level.
7. If a step might fail, you can add:
   - condition: a keyword that must appear in a prior result for this action to run
   - rollback: description of how to undo this action
   - fallback_strategy: alternative approach if this fails

You MUST respond with a valid JSON object in this exact format:
{
  "goal": "Overall goal description",
  "goal_exit_criteria": "What defines complete success",
  "subgoals": [
    {
      "id": "sub_1",
      "description": "Subgoal description",
      "exit_criteria": "What defines success for this subgoal",
      "confidence": 0.8,
      "risk_level": "low",
      "fallback_strategy": "",
      "dependencies": [],
      "actions": [
        {
          "id": "act_1_1",
          "description": "Concrete action description",
          "exit_criteria": "What defines success for this action",
          "confidence": 0.9,
          "risk_level": "low",
          "fallback_strategy": "",
          "dependencies": [],
          "condition": null,
          "rollback": null
        }
      ]
    }
  ]
}
"""


class PlannerAgent(BaseAgent):
    """
    Hybrid planner with automatic v1/v2 routing.
    混合规划智能体，自动路由 v1 扁平计划 / v2 DAG 分层计划。

    Workflow:
    工作流程：
      1. classify_task()     -> Two-stage hybrid classifier (rules + LLM fallback)
                                两阶段混合分类器（规则快筛 + LLM 兜底）
      2a. create_plan()      -> v1 flat plan (simple tasks / 简单任务)
      2b. create_dag()       -> v2 hierarchical DAG (complex tasks / 复杂任务)
      3. replan/replan_subtree() -> Re-planning for respective path
                                    各路径对应的重规划方法
    """

    # 规则快筛用到的关键词模式（编译一次，复用多次）
    _MULTI_STEP_PATTERN = re.compile(
        r"然后|接着|之后|随后|再|首先.*然后|第[一二三四五六七八九十\d]+步"
        r"|first\b|then\b|next\b|finally\b|after that\b|step\s*\d"
        r"|afterwards\b|subsequently\b|followed by\b",
        re.IGNORECASE,
    )
    _CONDITIONAL_PATTERN = re.compile(
        r"如果|假如|若是|取决于|根据.*决定|分情况"
        r"|\bif\b|\bdepending\b|\bbased on\b|\bwhether\b|\bin case\b|\bwhen\b.*\bthen\b",
        re.IGNORECASE,
    )
    _PARALLEL_PATTERN = re.compile(
        r"同时|并行|另外|此外|与此同时|一方面.*另一方面"
        r"|\bmeanwhile\b|\bsimultaneously\b|\bin parallel\b|\badditionally\b|\balso\b.*\band\b",
        re.IGNORECASE,
    )
    _ACTION_VERB_PATTERN = re.compile(
        r"搜索|查找|分析|计算|生成|创建|编写|下载|上传|保存|对比|总结|翻译|转换|部署|测试|爬取|抓取|整理|汇总|调研"
        r"|\bsearch\b|\bfind\b|\banalyze\b|\bcalculate\b|\bgenerate\b|\bcreate\b"
        r"|\bwrite\b|\bdownload\b|\bsave\b|\bcompare\b|\bsummarize\b|\btranslate\b"
        r"|\bbuild\b|\bdeploy\b|\btest\b|\bscrape\b|\bcrawl\b|\bcollect\b|\bresearch\b",
        re.IGNORECASE,
    )

    def __init__(self, llm_client: LLMClient, context_manager: ContextManager | None = None):
        super().__init__(
            name="Planner",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )

    # ==================================================================
    # Task Complexity Classification (v4 — two-stage hybrid)
    # 任务复杂度分类（v4 —— 两阶段混合分类器）
    # ==================================================================

    async def classify_task(self, task: str) -> str:
        """
        Determine whether a task is 'simple' or 'complex'.
        判断任务是"简单"还是"复杂"。

        Routing logic:
          0. config.PLAN_MODE override (for testing/debugging)
          1. Stage 1: rule-based fast filter -> simple / complex / ambiguous
          2. Stage 2: lightweight LLM call (only if Stage 1 returns ambiguous)

        路由逻辑：
          0. config.PLAN_MODE 强制覆盖（用于测试/调试）
          1. Stage 1：规则快筛 -> simple / complex / ambiguous
          2. Stage 2：轻量 LLM 调用（仅当 Stage 1 返回 ambiguous 时触发）

        Returns:
            "simple" or "complex"
        """
        if config.PLAN_MODE in ("simple", "complex"):
            logger.info("[Planner] PLAN_MODE override: %s", config.PLAN_MODE)
            return config.PLAN_MODE

        rule_result = self._rule_classify(task)
        if rule_result != "ambiguous":
            logger.info("[Planner] Rule classifier: %s (skipping LLM)", rule_result)
            return rule_result

        logger.info("[Planner] Rule classifier: ambiguous, invoking LLM classifier")
        return await self._llm_classify(task)

    def _rule_classify(self, task: str) -> str:
        """
        Stage 1: Rule-based heuristic classifier.
        Stage 1：基于规则启发式的快速分类器。

        Scores the task text on multiple dimensions. Returns:
          - "simple"   if score <= -2  (strongly simple signals)
          - "complex"  if score >= 3   (strongly complex signals)
          - "ambiguous" otherwise       (needs LLM to decide)

        按多个维度对任务文本打分。返回：
          - "simple"    分数 <= -2（强简单信号）
          - "complex"   分数 >= 3 （强复杂信号）
          - "ambiguous" 其他（需要 LLM 裁决）
        """
        score = 0

        text_len = len(task)
        if text_len < 30:
            score -= 2
        elif text_len < 60:
            score -= 1
        elif text_len > 200:
            score += 2
        elif text_len > 120:
            score += 1

        multi_step_hits = len(self._MULTI_STEP_PATTERN.findall(task))
        if multi_step_hits >= 2:
            score += 3
        elif multi_step_hits == 1:
            score += 1

        if self._CONDITIONAL_PATTERN.search(task):
            score += 2

        if self._PARALLEL_PATTERN.search(task):
            score += 2

        action_verb_count = len(self._ACTION_VERB_PATTERN.findall(task))
        if action_verb_count >= 3:
            score += 2
        elif action_verb_count == 2:
            score += 1
        elif action_verb_count <= 1:
            score -= 1

        logger.debug("[Planner] Rule score for '%s...': %d", task[:40], score)

        if score <= -2:
            return "simple"
        elif score >= 3:
            return "complex"
        return "ambiguous"

    async def _llm_classify(self, task: str) -> str:
        """
        Stage 2: Lightweight LLM classification for ambiguous tasks.
        Stage 2：针对模糊任务的轻量级 LLM 分类。

        Prompt is kept minimal (~60 input tokens) with temperature=0.0
        for deterministic output. Defaults to "complex" on failure.

        Prompt 保持极简（~60 输入 tokens），temperature=0.0 确保确定性输出。
        失败时默认降级为 "complex"。
        """
        self.reset()
        prompt = (
            'Classify as "simple" or "complex":\n'
            "- simple: single clear action, 1-2 steps, no parallel/conditional needs\n"
            "- complex: multi-phase, 3+ steps, parallel work, conditional logic, or research+analysis\n\n"
            f"Task: {task}\n\n"
            'JSON: {{"complexity": "simple"|"complex", "reason": "..."}}'
        )

        try:
            data = await self.think_json(prompt, temperature=0.0)
            result = data.get("complexity", "complex").lower()
            reason = data.get("reason", "")
            if result not in ("simple", "complex"):
                result = "complex"
            logger.info("[Planner] LLM classifier: %s (%s)", result, reason[:80])
            return result
        except Exception as exc:
            logger.warning("[Planner] LLM classify failed: %s. Defaulting to complex.", exc)
            return "complex"

    # ==================================================================
    # v1 Simple Planning (flat step list)
    # v1 简单规划（扁平步骤列表）
    # ==================================================================

    async def create_plan(self, task: str, context: str = "") -> Plan:
        """
        Create a flat step-based plan (v1 path).
        创建扁平步骤计划（v1 路径）。

        Uses SIMPLE_PLANNER_SYSTEM_PROMPT for a lightweight 2-6 step plan.
        使用 SIMPLE_PLANNER_SYSTEM_PROMPT 生成 2-6 步的轻量级计划。
        """
        self.reset()
        self.system_prompt = SIMPLE_PLANNER_SYSTEM_PROMPT

        prompt = f"Create an execution plan for this task:\n\nTask: {task}"
        if context:
            prompt += f"\n\nRelevant context:\n{context}"

        logger.info("[Planner] Creating simple plan for: %s", task[:80])
        result = await self.think_json(prompt, temperature=0.3)
        plan = self._parse_plan(task, result)

        self.system_prompt = PLANNER_SYSTEM_PROMPT
        return plan

    async def replan(
        self,
        task: str,
        completed_results: list[StepResult],
        failed_step: Step | None = None,
        feedback: str = "",
    ) -> Plan:
        """
        Revise the flat plan based on execution progress and feedback (v1 path).
        基于执行进度和反馈修订扁平计划（v1 路径）。
        """
        self.reset()
        self.system_prompt = SIMPLE_PLANNER_SYSTEM_PROMPT

        completed_summary = "\n".join(
            f"- Step {r.step_id}: {'SUCCESS' if r.success else 'FAILED'} - {r.output[:200]}"
            for r in completed_results
        )

        prompt = (
            f"The original task needs re-planning.\n\n"
            f"Task: {task}\n\n"
            f"Completed steps so far:\n{completed_summary}\n"
        )
        if failed_step:
            prompt += f"\nFailed step: {failed_step.description}\n"
        if feedback:
            prompt += f"\nFeedback: {feedback}\n"
        prompt += (
            "\nCreate a NEW plan for the REMAINING work. "
            "Do not repeat already-completed steps. "
            "Account for the feedback and any failures."
        )

        logger.info("[Planner] Re-planning (v1) task: %s", task[:80])
        result = await self.think_json(prompt, temperature=0.3)
        plan = self._parse_plan(task, result)

        self.system_prompt = PLANNER_SYSTEM_PROMPT
        return plan

    @staticmethod
    def _parse_plan(task: str, data: Any) -> Plan:
        """
        Parse LLM JSON output into a flat Plan model (v1).
        将 LLM 的 JSON 输出解析为扁平 Plan 模型（v1）。
        """
        steps = []
        raw_steps = data.get("steps", []) if isinstance(data, dict) else []
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
    # v2 DAG Planning (hierarchical)
    # v2 DAG 规划（分层）
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
        result = await self.think_json(prompt, temperature=0.3)
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

        This is a key upgrade over v1's full replan — we keep successful work.
        这是相对 v1 整体重规划的关键升级——保留所有已成功的工作成果。
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
            "Respond in the same JSON format as before."
        )

        logger.info("[Planner] Replanning subtree from %s", parent_id)
        self.reset()  # 清空历史，以全新视角重规划
        result = await self.think_json(prompt, temperature=0.3)
        new_dag = self._parse_dag(dag.state.task, result, dag.state.context)

        # 将新子树合并回原 DAG（保留已完成节点）
        return self._merge_dags(dag, new_dag, parent_id)

    # ------------------------------------------------------------------
    # Adaptive planning (v3) — mid-execution plan adjustment
    # 自适应规划（v3）—— 执行中动态调整计划
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
            data = await self.think_json(prompt, temperature=0.3)
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
            logger.error("[Planner] LLM returned non-dict: %s", type(data))
            data = {}

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
        raw_subgoals = data.get("subgoals", [])
        if not raw_subgoals:
            # 降级处理：LLM 未返回 subgoals 时，创建单个子目标兜底
            raw_subgoals = [{"id": "sub_1", "description": task, "actions": [
                {"id": "act_1_1", "description": task}
            ]}]

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
                    parent_id=sg_id,           # Action 的父节点是所属 SubGoal
                    rollback_action=act.get("rollback"),  # 可选的回滚操作描述
                )
                nodes[act_id] = act_node

                # SubGoal -> Action 依赖边（Action 需在所属 SubGoal 之后执行）
                edges.append(TaskEdge(source=sg_id, target=act_id, edge_type=EdgeType.DEPENDENCY))

                # Action 间的依赖边（如 act_1_2 依赖 act_1_1）
                for dep_id in act.get("dependencies", []):
                    edges.append(TaskEdge(
                        source=str(dep_id), target=act_id, edge_type=EdgeType.DEPENDENCY,
                    ))

                # 条件边（若 action 有条件限制，添加 CONDITIONAL 边）
                condition = act.get("condition")
                if condition:
                    edges.append(TaskEdge(
                        source=sg_id, target=act_id,
                        edge_type=EdgeType.CONDITIONAL,
                        condition=condition,
                    ))

        # Deduplicate edges（去重，避免重复边导致计算错误）
        seen: set[tuple] = set()
        unique_edges: list[TaskEdge] = []
        for e in edges:
            key = (e.source, e.target, e.edge_type.value)
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        return TaskDAG(task=task, nodes=nodes, edges=unique_edges, context=context)

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
        merged_edges.extend(new_dag.edges)

        result_dag = TaskDAG(
            task=old_dag.state.task,
            nodes=merged_nodes,
            edges=merged_edges,
            context=old_dag.state.context,
        )
        # 携带已完成节点的结果（避免重新执行）
        result_dag.state.node_results = dict(old_dag.state.node_results)

        return result_dag
