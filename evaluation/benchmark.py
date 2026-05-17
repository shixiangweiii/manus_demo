"""
Benchmark task definitions with ground truth for evaluation.

Each benchmark task specifies:
  - task description (input)
  - expected complexity classification (simple/complex/emergent)
  - expected tools needed
  - difficulty tier
  - ground truth: expected step structure, success criteria, reference output

Design inspired by:
  - WorkBench: outcome-centric evaluation
  - SLATE: multiple valid trajectories, not single ground truth
  - SWE-bench: execution-based verification

评测基准任务定义，包含参考答案。

基准任务涵盖不同难度和不同工具组合，
每种任务对三种规划模式分别运行，收集对比数据。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evaluation.metrics import PlanMode, TaskDifficulty


# ======================================================================
# Benchmark Task Definition
# ======================================================================

class GroundTruth(BaseModel):
    """
    Ground truth for a benchmark task.
    单个基准任务的参考答案。

    Not all fields are required — partial GT still provides value:
    - classification only → evaluate routing accuracy
    - step_structure only → evaluate plan coverage
    - success_criteria only → evaluate task outcome
    """
    # Expected routing classification
    expected_complexity: str = ""               # simple / complex / emergent

    # Expected plan structure
    expected_step_count_range: tuple[int, int] = (1, 10)  # 步骤数合理范围 (min, max)
    expected_tools: list[str] = Field(default_factory=list)  # 期望使用的工具列表
    expected_subtasks: list[str] = Field(default_factory=list)  # 期望覆盖的子任务描述

    # Success criteria
    success_criteria: str = ""                  # 任务成功判定标准（自然语言）
    must_include_keywords: list[str] = Field(default_factory=list)  # 输出必须包含的关键词
    must_not_include: list[str] = Field(default_factory=list)       # 输出不应包含的内容

    # Reference output (optional, for similarity comparison)
    reference_output: str = ""                  # 参考输出文本

    # v8 GroundTruth for new feature coverage (HITL / SubAgent / Goal-Driven)
    # v8 新特性覆盖的 GroundTruth 字段（None = 不验证该维度）
    expected_hitl_calls: tuple[int, int] | None = None       # (min, max) ask_user 调用次数区间
    expected_subagent_calls: tuple[int, int] | None = None   # (min, max) SubAgent 调用次数区间
    expected_goal_features: list[str] | None = None          # 期望出现的 goal-driven 事件名列表
    simulated_responses: list[str] | None = None             # HITL 任务的预设用户回答（按 FIFO 消费）


class BenchmarkTask(BaseModel):
    """
    A single benchmark task with metadata and ground truth.
    单个评测任务及其元数据和参考答案。
    """
    task_id: str = Field(description="Unique task identifier")
    task_description: str = Field(description="The task prompt to send to the agent")
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    tags: list[str] = Field(default_factory=list, description="Task tags for filtering")
    ground_truth: GroundTruth = Field(default_factory=GroundTruth)


# ======================================================================
# Benchmark Dataset
# ======================================================================

BENCHMARK_TASKS: list[BenchmarkTask] = [
    # ==================================================================
    # EASY tasks — should be classified as "simple"
    # ==================================================================
    BenchmarkTask(
        task_id="easy_001",
        task_description="搜索一下今天的天气",
        difficulty=TaskDifficulty.EASY,
        tags=["search", "single_step"],
        ground_truth=GroundTruth(
            expected_complexity="simple",
            expected_step_count_range=(1, 2),
            expected_tools=["web_search"],
            success_criteria="返回包含天气信息的文本",
            must_include_keywords=["天气"],
        ),
    ),
    BenchmarkTask(
        task_id="easy_002",
        task_description="Calculate the factorial of 10 using Python",
        difficulty=TaskDifficulty.EASY,
        tags=["code", "single_step"],
        ground_truth=GroundTruth(
            expected_complexity="simple",
            expected_step_count_range=(1, 2),
            expected_tools=["execute_python"],
            success_criteria="Returns 3628800",
            must_include_keywords=["3628800"],
        ),
    ),
    BenchmarkTask(
        task_id="easy_003",
        task_description="Write 'Hello World' to a file named test_output.txt",
        difficulty=TaskDifficulty.EASY,
        tags=["file_ops", "single_step"],
        ground_truth=GroundTruth(
            expected_complexity="simple",
            expected_step_count_range=(1, 2),
            expected_tools=["file_ops"],
            success_criteria="File written successfully",
            must_include_keywords=["test_output.txt"],
        ),
    ),
    BenchmarkTask(
        task_id="easy_004",
        task_description="列出当前目录下的所有文件",
        difficulty=TaskDifficulty.EASY,
        tags=["shell", "single_step"],
        ground_truth=GroundTruth(
            expected_complexity="simple",
            expected_step_count_range=(1, 2),
            expected_tools=["shell"],
            success_criteria="返回文件列表",
            must_include_keywords=["文件", "file"],
        ),
    ),

    # ==================================================================
    # MEDIUM tasks — should be classified as "complex"
    # ==================================================================
    BenchmarkTask(
        task_id="medium_001",
        task_description="搜索Python和JavaScript的区别，然后用代码演示它们在列表操作上的不同",
        difficulty=TaskDifficulty.MEDIUM,
        tags=["search", "code", "multi_step"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(2, 5),
            expected_tools=["web_search", "execute_python"],
            expected_subtasks=[
                "搜索Python和JavaScript的区别",
                "演示列表操作差异的代码",
            ],
            success_criteria="包含对比信息和代码示例",
            must_include_keywords=["Python", "JavaScript", "列表", "list"],
        ),
    ),
    BenchmarkTask(
        task_id="medium_002",
        task_description="Research the current population of Tokyo, then write a Python function that calculates population density given area, and test it with Tokyo's data",
        difficulty=TaskDifficulty.MEDIUM,
        tags=["search", "code", "multi_step", "sequential_dependency"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(2, 4),
            expected_tools=["web_search", "execute_python"],
            expected_subtasks=[
                "搜索东京人口数据",
                "编写人口密度计算函数",
                "用东京数据测试函数",
            ],
            success_criteria="包含搜索结果和正确的计算结果",
            must_include_keywords=["Tokyo", "population", "density"],
        ),
    ),
    BenchmarkTask(
        task_id="medium_003",
        task_description="先创建一个CSV文件包含3个学生的成绩数据，然后用Python读取并计算平均分，最后把结果保存到新文件中",
        difficulty=TaskDifficulty.MEDIUM,
        tags=["file_ops", "code", "multi_step", "sequential_dependency"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(3, 5),
            expected_tools=["file_ops", "execute_python"],
            expected_subtasks=[
                "创建CSV文件",
                "读取并计算平均分",
                "保存结果到新文件",
            ],
            success_criteria="CSV文件创建成功，平均分计算正确，结果已保存",
            must_include_keywords=["CSV", "平均", "avg"],
        ),
    ),
    BenchmarkTask(
        task_id="medium_004",
        task_description="Use Python to generate a list of 100 random numbers, save them to a file, then read the file and calculate mean and standard deviation",
        difficulty=TaskDifficulty.MEDIUM,
        tags=["code", "file_ops", "multi_step", "sequential_dependency"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(3, 5),
            expected_tools=["execute_python", "file_ops"],
            expected_subtasks=[
                "生成随机数",
                "保存到文件",
                "读取文件并计算统计量",
            ],
            success_criteria="正确计算均值和标准差",
            must_include_keywords=["mean", "standard deviation", "平均"],
        ),
    ),

    # ==================================================================
    # HARD tasks — complex or emergent
    # ==================================================================
    BenchmarkTask(
        task_id="hard_001",
        task_description="研究2024年全球AI发展趋势，分析主要技术方向，然后用Python生成一份数据摘要报告，将报告保存到文件中，同时搜索相关领域的最新论文",
        difficulty=TaskDifficulty.HARD,
        tags=["search", "code", "file_ops", "multi_step", "parallel"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(4, 8),
            expected_tools=["web_search", "execute_python", "file_ops"],
            expected_subtasks=[
                "搜索AI发展趋势",
                "分析技术方向",
                "生成数据摘要报告",
                "保存报告到文件",
                "搜索最新论文",
            ],
            success_criteria="包含研究分析、数据报告和论文搜索结果",
            must_include_keywords=["AI", "报告", "report"],
        ),
    ),
    BenchmarkTask(
        task_id="hard_002",
        task_description="调研不同的Python Web框架（Flask、FastAPI、Django），对比它们的优缺点，写一个简单的FastAPI示例应用，测试运行并记录结果",
        difficulty=TaskDifficulty.HARD,
        tags=["search", "code", "multi_step", "exploratory"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(3, 7),
            expected_tools=["web_search", "execute_python", "shell"],
            expected_subtasks=[
                "调研Web框架",
                "对比优缺点",
                "编写FastAPI示例",
                "测试运行",
            ],
            success_criteria="包含框架对比、代码示例和运行结果",
            must_include_keywords=["Flask", "FastAPI", "Django"],
        ),
    ),
    BenchmarkTask(
        task_id="hard_003",
        task_description="探索如何用Python实现一个简单的文本分类器，分析可能的方法，选择一种实现，测试效果并优化",
        difficulty=TaskDifficulty.HARD,
        tags=["code", "exploratory", "iterative"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(3, 8),
            expected_tools=["execute_python", "web_search"],
            expected_subtasks=[
                "探索文本分类方法",
                "实现分类器",
                "测试效果",
                "优化改进",
            ],
            success_criteria="包含实现代码和测试结果，有优化过程",
            must_include_keywords=["分类", "classifier", "class"],
        ),
    ),
    BenchmarkTask(
        task_id="hard_004",
        task_description="设计并实现一个学生成绩管理系统：支持添加学生、录入成绩、查询成绩排名、导出CSV。要求代码结构清晰，包含错误处理。",
        difficulty=TaskDifficulty.HARD,
        tags=["code", "file_ops", "multi_step", "complex_design"],
        ground_truth=GroundTruth(
            expected_complexity="complex",
            expected_step_count_range=(4, 7),
            expected_tools=["execute_python", "file_ops"],
            expected_subtasks=[
                "设计数据结构",
                "实现学生管理功能",
                "实现成绩录入和查询",
                "实现排名功能",
                "实现CSV导出",
            ],
            success_criteria="功能完整，代码可运行，包含错误处理",
            must_include_keywords=["学生", "成绩", "CSV", "student"],
        ),
    ),

    # ==================================================================
    # v8 HITL tasks — require ask_user clarification (auto-answered by SimulatedUser)
    # v8 人机交互任务：要求 ask_user 澄清，模拟用户自动回答
    # Tag "hitl" triggers HITL_ENABLED=true + interactive=True + SimulatedUser injection
    # ==================================================================
    BenchmarkTask(
        task_id="hitl_easy_001",
        task_description="帮我查询附近的咖啡馆推荐",
        difficulty=TaskDifficulty.EASY,
        tags=["hitl", "search", "ambiguous_location"],
        ground_truth=GroundTruth(
            expected_complexity="simple",
            expected_step_count_range=(2, 4),
            expected_tools=["get_user_location", "ask_user", "web_search"],
            expected_subtasks=["确认用户城市", "搜索咖啡馆"],
            success_criteria="使用用户确认的城市进行搜索，返回咖啡馆列表",
            must_include_keywords=["上海", "咖啡"],  # 模拟用户回答上海
            expected_hitl_calls=(1, 3),
            simulated_responses=["上海", "中山公园附近"],
        ),
    ),
    BenchmarkTask(
        task_id="hitl_hard_001",
        task_description="为我推荐一篇近期会让我感兴趣的论文",
        difficulty=TaskDifficulty.HARD,
        tags=["hitl", "search", "preference_required"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(2, 5),
            expected_tools=["ask_user", "web_search"],
            expected_subtasks=["询问研究兴趣方向", "根据兴趣搜索论文"],
            success_criteria="根据用户偏好返回相关论文",
            must_include_keywords=["LLM", "agent"],
            expected_hitl_calls=(1, 3),
            simulated_responses=[
                "我对大语言模型的智能体方向感兴趣，特别是 LLM agent 的评测与推理",
                "近 6 个月内的工作",
            ],
        ),
    ),

    # ==================================================================
    # v9 SubAgent tasks — should trigger SubAgent delegation
    # v9 子智能体任务：应触发 SubAgent 委托独立调研
    # Tag "subagent" triggers SUBAGENT_ENABLED=true at runtime
    # ==================================================================
    BenchmarkTask(
        task_id="subagent_easy_001",
        task_description="分别调研 Flask、FastAPI、Django 三个 Python web 框架的核心特点和适用场景，给出对比总结",
        difficulty=TaskDifficulty.EASY,
        tags=["subagent", "search", "delegation"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(3, 7),
            expected_tools=["subagent", "web_search"],
            expected_subtasks=["调研 Flask", "调研 FastAPI", "调研 Django", "对比总结"],
            success_criteria="包含三个框架的对比信息",
            must_include_keywords=["Flask", "FastAPI", "Django"],
            expected_subagent_calls=(1, 4),
        ),
    ),
    BenchmarkTask(
        task_id="subagent_hard_001",
        task_description=(
            "对 Manus Demo 项目做架构梳理：(1) 列出 agents/ 目录下的所有 agent 模块及其角色 "
            "(2) 列出 tools/ 目录下的所有 tool 及其用途 (3) 输出 markdown 格式的架构总结"
        ),
        difficulty=TaskDifficulty.HARD,
        tags=["subagent", "file_ops", "delegation", "multi_step"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(3, 8),
            expected_tools=["subagent", "shell", "file_ops"],
            expected_subtasks=["扫描 agents 模块", "扫描 tools 模块", "生成架构总结"],
            success_criteria="包含 agents 和 tools 的清单及职责",
            must_include_keywords=["agent", "tool", "架构"],
            expected_subagent_calls=(1, 3),
        ),
    ),

    # ==================================================================
    # v8 Goal-Driven tasks — should trigger goal_anchor / goal_reflection
    # v8 目标驱动任务：应触发 goal_anchor / goal_reflection 事件
    # Tag "goal_driven" triggers ENABLE_GOAL_DRIVEN_PLANNER=true at runtime
    # ==================================================================
    BenchmarkTask(
        task_id="goal_easy_001",
        task_description="持续生成质数，直到累计找到 30 个为止，输出最后一个质数",
        difficulty=TaskDifficulty.EASY,
        tags=["goal_driven", "code", "condition_termination"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(2, 5),
            expected_tools=["execute_python"],
            expected_subtasks=["实现质数生成", "累计到 30 个后终止"],
            success_criteria="正确输出第 30 个质数（113）",
            must_include_keywords=["113"],
            expected_goal_features=["goal_anchor"],
        ),
    ),
    BenchmarkTask(
        task_id="goal_hard_001",
        task_description=(
            "优化下面的 fibonacci 函数代码使其计算 fib(35) 用时低于 1 秒（请实施代码、测量、若不达标继续优化）：\n"
            "def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)"
        ),
        difficulty=TaskDifficulty.HARD,
        tags=["goal_driven", "code", "iterative_optimization"],
        ground_truth=GroundTruth(
            expected_complexity="emergent",
            expected_step_count_range=(3, 8),
            expected_tools=["execute_python"],
            expected_subtasks=["测量基线", "优化实现", "重新测量", "达标终止"],
            # Relaxed: "fib" + ANY of {memoization, 缓存, cache, lru_cache, dp, 动态规划}
            # 任一同义词命中即可；过严会让 LLM judge 反复兜底，增加 token 成本
            success_criteria="给出优化后的 fibonacci 代码（如 memoization/lru_cache/迭代/动态规划），并提供测量证据证明用时 <1s",
            must_include_keywords=["fib"],
            must_not_include=[],
            expected_goal_features=["goal_anchor", "goal_reflection"],
        ),
    ),
]


def get_benchmark_tasks(
    difficulty: TaskDifficulty | None = None,
    tags: list[str] | None = None,
    task_ids: list[str] | None = None,
) -> list[BenchmarkTask]:
    """
    Filter benchmark tasks by criteria.
    按条件筛选基准任务。
    """
    tasks = BENCHMARK_TASKS
    if difficulty:
        tasks = [t for t in tasks if t.difficulty == difficulty]
    if tags:
        tasks = [t for t in tasks if any(tag in t.tags for tag in tags)]
    if task_ids:
        tasks = [t for t in tasks if t.task_id in task_ids]
    return tasks
