"""
Manus Demo - Interactive CLI entry point.
Manus Demo —— 交互式命令行入口。

Launches the multi-agent pipeline with a rich console UI that displays
each phase of execution: task classification, DAG/flat planning,
super-step parallel execution, per-node validation, reflection, etc.
启动多智能体流水线，提供 Rich 控制台 UI，实时展示执行的每个阶段：
任务分类、DAG/扁平规划、Super-step 并行执行、逐节点验证、反思等。

v2: DAG-based execution with Rich Tree visualization.
v4: Hybrid routing UI — shows classification result and both v1/v2 plan views.
v2：基于 DAG 的 Rich Tree 可视化。
v4：混合路由 UI——展示分类结果及 v1/v2 两种计划视图。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from agents.orchestrator import OrchestratorAgent
from dag.graph import TaskDAG
from llm.client import LLMClient
from schema import NodeType, Plan, Reflection, Step, StepResult, TaskEdge, TaskNode
from tools.code_executor import CodeExecutorTool
from tools.file_ops import FileOpsTool
from tools.web_search import WebSearchTool

console = Console()

# Status -> Rich style mapping
# 节点状态 -> Rich 样式映射（用于 DAG 树形可视化中的颜色标注）
_STATUS_STYLES = {
    "pending": "dim",            # 等待中：暗色
    "ready": "yellow",           # 就绪：黄色
    "running": "bold yellow",    # 运行中：粗体黄色
    "completed": "green",        # 已完成：绿色
    "failed": "red",             # 失败：红色
    "skipped": "dim strike",     # 跳过：删除线
    "rolled_back": "magenta",    # 已回滚：洋红色
}


# ======================================================================
# DAG Tree Visualization
# DAG 树形可视化
# ======================================================================

def _build_dag_tree(dag: TaskDAG) -> Tree:
    """
    Build a Rich Tree showing the DAG hierarchy: Goal > SubGoals > Actions.
    构建 Rich Tree，展示 DAG 的层级结构：Goal > SubGoals > Actions。
    每个节点旁显示当前状态和风险信息，颜色编码方便快速识别。
    """
    goal_nodes = [n for n in dag.nodes.values() if n.node_type == NodeType.GOAL]
    root_label = "Task DAG"
    if goal_nodes:
        g = goal_nodes[0]
        style = _STATUS_STYLES.get(g.status.value, "white")
        # 树根标签：加粗的 Goal 描述 + 带颜色的状态标签
        root_label = f"[bold]{g.description}[/bold] [{style}]({g.status.value})[/{style}]"

    tree = Tree(root_label)

    # 为每个 SubGoal 创建树分支
    subgoals = [n for n in dag.nodes.values() if n.node_type == NodeType.SUBGOAL]
    for sg in subgoals:
        sg_style = _STATUS_STYLES.get(sg.status.value, "white")
        sg_label = (
            f"[cyan]{sg.id}[/cyan]: {sg.description} "
            f"[{sg_style}]({sg.status.value})[/{sg_style}] "
            f"[dim]conf={sg.risk.confidence:.1f} risk={sg.risk.risk_level}[/dim]"
            # conf=置信度 risk=风险等级
        )
        sg_branch = tree.add(sg_label)

        # 在 SubGoal 分支下添加其所属的 Action 叶节点
        actions = [n for n in dag.nodes.values()
                   if n.node_type == NodeType.ACTION and n.parent_id == sg.id]
        for act in actions:
            act_style = _STATUS_STYLES.get(act.status.value, "white")
            act_label = (
                f"[white]{act.id}[/white]: {act.description} "
                f"[{act_style}]({act.status.value})[/{act_style}]"
            )
            if act.exit_criteria and act.exit_criteria.description != "Step completed successfully":
                # 非默认完成判据时显示，帮助用户了解节点的成功标准
                act_label += f"\n  [dim]exit: {act.exit_criteria.description}[/dim]"
            sg_branch.add(act_label)

    return tree


# ======================================================================
# UI Event Handler - Pretty-prints pipeline events
# UI 事件处理器 —— 美化打印流水线事件
# ======================================================================

def on_event(event: str, data: Any) -> None:
    """
    Handle events from the orchestrator/DAGExecutor and display them.
    处理来自 Orchestrator/DAGExecutor 的事件并在控制台展示。
    这是事件驱动 UI 的核心：所有智能体和执行引擎通过 emit 发出事件，
    此函数统一将其渲染为可读的 Rich 格式输出。
    """

    if event == "task_start":
        # 任务开始：显示蓝色边框的任务面板
        console.print()
        console.print(Panel(
            f"[bold]{data['task']}[/bold]",
            title="[bold blue]New Task[/bold blue]",
            border_style="blue",
        ))

    elif event == "phase":
        # 流水线阶段切换提示（如「Planning...」「Executing...」）
        console.print(f"\n[bold cyan]>>> {data}[/bold cyan]")

    elif event == "memory":
        # 长期记忆检索结果（有相关记忆时显示）
        if "No relevant" not in str(data):
            console.print(Panel(str(data), title="[yellow]Long-term Memory[/yellow]", border_style="yellow"))

    elif event == "knowledge":
        # 知识库检索结果（有相关知识时显示）
        if "No relevant" not in str(data):
            console.print(Panel(str(data), title="[green]Knowledge Retrieved[/green]", border_style="green"))

    # --- Hybrid routing events (v4) ---
    # --- 混合路由事件（v4 新增）---

    elif event == "task_complexity":
        complexity = data.get("complexity", "unknown")
        style = "green" if complexity == "simple" else "magenta"
        label = "Simple (v1 flat plan)" if complexity == "simple" else "Complex (v2 DAG)"
        console.print(f"  [bold {style}]Task complexity: {label}[/bold {style}]")

    elif event == "plan":
        plan: Plan = data
        table = Table(title="Simple Plan (v1)", border_style="cyan", show_lines=True)
        table.add_column("Step", style="cyan", width=6)
        table.add_column("Description", style="white")
        table.add_column("Status", style="dim", width=10)
        table.add_column("Deps", style="dim", width=8)
        for s in plan.steps:
            deps = ", ".join(str(d) for d in s.dependencies) if s.dependencies else "-"
            table.add_row(str(s.id), s.description, s.status.value, deps)
        console.print(table)

    elif event == "step_start":
        step: Step = data["step"]
        idx: int = data["index"]
        console.print(f"    [yellow]>> Step {step.id}:[/yellow] {step.description}")

    elif event == "step_complete":
        step: Step = data["step"]
        result: StepResult = data["result"]
        console.print(f"    [green]<< Step {step.id} completed.[/green]")
        output_preview = result.output[:500]
        console.print(Panel(output_preview, title=f"Step {step.id} Output", border_style="green"))

    elif event == "step_failed":
        step: Step = data["step"]
        result: StepResult = data["result"]
        console.print(f"    [red]<< Step {step.id} FAILED.[/red]")
        console.print(Panel(result.output[:500], title=f"Step {step.id} Error", border_style="red"))

    # --- DAG events (v2) ---
    # --- DAG 执行事件（v2 新增）---

    elif event == "dag_created":
        # DAG 创建完成：以树形结构可视化展示整个规划
        dag: TaskDAG = data
        console.print()
        tree = _build_dag_tree(dag)
        console.print(Panel(tree, title="[bold magenta]Task DAG[/bold magenta]", border_style="magenta"))
        console.print(f"  [dim]{dag.summary()}[/dim]")  # 单行状态摘要

    elif event == "superstep":
        # Super-step 开始：显示本轮并行执行的节点列表
        step = data["step"]
        nodes = data["nodes"]
        total = data.get("total_ready", len(nodes))
        parallel_note = " (parallel)" if len(nodes) > 1 else ""  # 多节点时标注并行
        console.print(
            f"\n  [bold yellow]--- Super-step {step} ---[/bold yellow] "
            f"Running {len(nodes)}/{total} nodes{parallel_note}: "
            f"[cyan]{', '.join(nodes)}[/cyan]"
        )

    elif event == "node_running":
        # 节点开始执行
        node: TaskNode = data["node"]
        console.print(f"    [yellow]>> {node.id}:[/yellow] {node.description}")

    elif event == "node_completed":
        # 节点执行成功：显示工具调用记录和输出预览
        node: TaskNode = data["node"]
        result: StepResult = data["result"]
        console.print(f"    [green]<< {node.id} completed.[/green]")
        if result.tool_calls_log:
            # 显示每次工具调用的工具名、参数和结果预览
            for tc in result.tool_calls_log:
                console.print(f"      [dim]Tool: {tc.tool_name}({tc.parameters})[/dim]")
                console.print(f"      [dim]  -> {tc.result[:200]}[/dim]")
        output_preview = result.output[:500]  # 只显示前 500 字符避免刷屏
        console.print(Panel(output_preview, title=f"{node.id} Output", border_style="green"))

    elif event == "node_failed":
        # 节点执行失败：显示失败原因和错误输出
        node: TaskNode = data["node"]
        result: StepResult = data["result"]
        reason = data.get("reason", "unknown")  # execution（执行失败）或 exit_criteria（判据未通过）
        console.print(f"    [red]<< {node.id} FAILED ({reason}).[/red]")
        console.print(Panel(result.output[:500], title=f"{node.id} Error", border_style="red"))

    elif event == "node_rollback":
        # 节点回滚完成
        node: TaskNode = data["node"]
        console.print(f"    [magenta]<< {node.id} rolled back.[/magenta]")

    elif event == "node_transition":
        pass  # Handled implicitly by node_running/completed/failed events
              # 状态转移事件：已由 node_running/completed/failed 事件隐式处理，此处静默

    elif event == "condition_evaluated":
        # 条件边评估结果：显示条件是否满足
        edge: TaskEdge = data["edge"]
        met: bool = data["met"]
        style = "green" if met else "red"
        action = "TAKEN" if met else "SKIPPED"  # 满足则激活，否则跳过
        console.print(
            f"    [dim]Condition '{edge.condition}' on {edge.source}->{edge.target}: "
            f"[{style}]{action}[/{style}][/dim]"
        )

    # --- Adaptive Planning (v3) ---
    # --- 自适应规划（v3 新增）---

    elif event == "plan_adaptation":
        adapted: bool = data.get("adapted", False)
        reasoning: str = data.get("reasoning", "")
        step_num: int = data.get("step", 0)
        if adapted:
            changes: list = data.get("changes", [])
            content = f"[bold]Reasoning:[/bold] {reasoning}\n\n"
            if changes:
                content += "[bold]Changes applied:[/bold]\n"
                for c in changes:
                    content += f"  • {c}\n"
            console.print(Panel(
                content,
                title=f"[bold yellow]Plan Adapted (after super-step {step_num})[/bold yellow]",
                border_style="yellow",
            ))
        else:
            console.print(f"    [dim]Adaptive check (step {step_num}): no changes needed — {reasoning[:80]}[/dim]")

    # --- Reflection ---
    # --- 反思阶段 ---

    elif event == "reflection":
        # 反思结果：显示通过/需要重做、质量评分和改进建议
        ref: Reflection = data
        style = "green" if ref.passed else "red"
        verdict = "PASSED" if ref.passed else "NEEDS REWORK"
        console.print(Panel(
            f"Verdict: [{style}]{verdict}[/{style}]  |  Score: {ref.score:.2f}\n\n"
            f"{ref.feedback}\n\n"
            + (f"Suggestions:\n" + "\n".join(f"  - {s}" for s in ref.suggestions) if ref.suggestions else ""),
            title="[bold]Reflection[/bold]",
            border_style=style,
        ))

    elif event == "memory_stored":
        # 长期记忆已存储提示
        console.print("[dim]   (Result stored in long-term memory)[/dim]")

    elif event == "task_complete":
        # 任务完成：显示绿色边框的最终答案面板
        console.print(Panel(
            data["answer"][:2000],  # 最多显示 2000 字符
            title="[bold green]Final Answer[/bold green]",
            border_style="green",
        ))


# ======================================================================
# Main
# 主函数
# ======================================================================

def setup_logging(verbose: bool = False) -> None:
    """
    Configure logging with rich handler.
    使用 Rich 处理器配置日志系统。
    verbose=True 时启用 DEBUG 级别，显示所有内部调试信息。
    同时抑制 httpx/openai/httpcore 的低优先级日志，减少噪音。
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run_interactive() -> None:
    """
    Interactive multi-turn conversation loop.
    多轮交互对话循环。
    每轮对话共享同一个 Orchestrator 实例，因此长期记忆会在多轮之间积累。
    """
    console.print(Panel(
        "[bold]Manus Demo v4[/bold] - Hybrid Multi-Agent System with Intelligent Plan Routing\n\n"
        "This demo implements:\n"
        "  [green]NEW[/green] [bold]Hybrid plan routing[/bold] — auto-selects simple (v1) or complex (v2) path (v4)\n"
        "  [green]NEW[/green] [bold]Two-stage classifier[/bold] — rules fast-filter + LLM fallback (v4)\n"
        "  [cyan]1.[/cyan] Simple path: flat 2-6 step plan, sequential execution\n"
        "  [cyan]2.[/cyan] Complex path: hierarchical DAG (Goal -> SubGoals -> Actions)\n"
        "  [cyan]3.[/cyan] DAG execution with parallel super-steps\n"
        "  [cyan]4.[/cyan] Per-node exit criteria validation\n"
        "  [cyan]5.[/cyan] Node state machine (PENDING->READY->RUNNING->COMPLETED/FAILED)\n"
        "  [cyan]6.[/cyan] Conditional branches and rollback\n"
        "  [cyan]7.[/cyan] Partial replanning (failed subtree only)\n"
        "  [cyan]8.[/cyan] Centralized DAGState (inspired by LangGraph)\n"
        "  [cyan]9.[/cyan] Short-term & long-term memory + knowledge retrieval\n"
        "  [cyan]10.[/cyan] Adaptive planning — plan evolves during execution (v3)\n"
        "  [cyan]11.[/cyan] Tool router — failure-based tool switching hints (v3)\n"
        "  [cyan]12.[/cyan] Dynamic DAG mutation — add/remove/modify nodes at runtime (v3)\n\n"
        "Type your task and press Enter. Type [bold]quit[/bold] to exit.\n"
        "Set PLAN_MODE=simple|complex to force a specific path (default: auto).",
        title="[bold blue]Welcome[/bold blue]",
        border_style="blue",
    ))

    llm_client = LLMClient()
    # 注册三个工具：网络搜索、Python 代码执行、文件读写
    tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]
    orchestrator = OrchestratorAgent(
        llm_client=llm_client,
        tools=tools,
        on_event=on_event,  # 绑定 UI 事件回调
    )

    while True:
        console.print()
        try:
            user_input = console.input("[bold blue]You > [/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            break  # Ctrl+C 或 EOF 退出

        if not user_input:
            continue  # 跳过空输入
        if user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye![/dim]")
            break

        try:
            answer = await orchestrator.run(user_input)
        except KeyboardInterrupt:
            console.print("\n[yellow]Task interrupted.[/yellow]")
        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]")
            logging.exception("Unhandled error")


async def run_single(task: str) -> None:
    """
    Run a single task (non-interactive mode).
    运行单个任务（非交互模式），执行完毕后退出。
    用于 `python main.py "任务描述"` 的命令行用法。
    """
    llm_client = LLMClient()
    tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]
    orchestrator = OrchestratorAgent(
        llm_client=llm_client,
        tools=tools,
        on_event=on_event,
    )
    await orchestrator.run(task)


def main() -> None:
    """
    程序入口：解析命令行参数，决定运行模式。
    - 有位置参数：单任务模式（python main.py "任务"）
    - 无位置参数：交互模式（python main.py）
    - -v / --verbose：启用调试日志
    """
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    setup_logging(verbose)

    # 过滤掉以 - 开头的选项参数，保留位置参数
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        task = " ".join(args)
        asyncio.run(run_single(task))
    else:
        asyncio.run(run_interactive())


if __name__ == "__main__":
    main()
