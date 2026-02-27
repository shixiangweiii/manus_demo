"""
Planner Agent - Hierarchical Plan-and-Execute with DAG output.
Planner 智能体 —— 分层 Plan-and-Execute，输出 TaskDAG。

v1 (legacy): Decomposed tasks into a flat list of 2-6 Steps.
v2 (current): Decomposes tasks into a hierarchical DAG:
    Goal -> SubGoals -> Actions

v1（旧版）：将任务分解为 2-6 个扁平步骤的线性列表。
v2（当前）：将任务分解为分层 DAG：
    Goal（目标）-> SubGoals（子目标）-> Actions（可执行动作）

The planner prompts the LLM to output a nested JSON structure in a single
call, then flattens it into TaskNode objects and TaskEdge connections to
build a TaskDAG.

Planner 通过单次 LLM 调用输出嵌套 JSON 结构，
然后将其展开为 TaskNode 对象和 TaskEdge 连接，构建 TaskDAG。

Key upgrade over v1:
  - Three-level hierarchy (Goal / SubGoal / Action)
  - Each node has exit_criteria and risk assessment
  - Edges support DEPENDENCY, CONDITIONAL, and ROLLBACK types
  - Partial replanning: replan_subtree() only regenerates the failed branch

相对 v1 的关键升级：
  - 三层层级（Goal / SubGoal / Action）
  - 每个节点附带完成判据和风险评估
  - 边支持 DEPENDENCY、CONDITIONAL 和 ROLLBACK 三种类型
  - 局部重规划：replan_subtree() 仅重新生成失败的子树分支
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import BaseAgent
from context.manager import ContextManager
from dag.graph import TaskDAG
from llm.client import LLMClient
from schema import (
    EdgeType,
    ExitCriteria,
    NodeStatus,
    NodeType,
    RiskAssessment,
    StepResult,
    TaskEdge,
    TaskNode,
)

logger = logging.getLogger(__name__)

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
    Hierarchical planner that creates TaskDAG from user tasks.
    分层规划智能体，将用户任务转化为 TaskDAG。

    Workflow:
      1. create_dag()        -> LLM generates nested plan, parsed into TaskDAG
      2. replan_subtree()    -> LLM replans a failed branch (partial replan)

    工作流程：
      1. create_dag()        -> LLM 生成嵌套计划，解析为 TaskDAG
      2. replan_subtree()    -> LLM 重新规划失败分支（局部重规划，保留已完成工作）
    """

    def __init__(self, llm_client: LLMClient, context_manager: ContextManager | None = None):
        super().__init__(
            name="Planner",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # 主入口
    # ------------------------------------------------------------------

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
