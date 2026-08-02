# Manus Demo

这是一个用于本地学习和比较 Agent 编排方式的 Python 项目，不面向生产环境。统一运行时显式运行三种引擎：

- `sequential`：先规划，再按顺序完成步骤。
- `dag`：构建依赖图并执行就绪节点；可配置并发，当前学习配置为串行，便于复现实验。
- `agent_loop`：模型在一个原生工具调用循环中持续决定下一步，并用完整 todo 快照记录进度。

项目没有自动引擎选择器、用户可选择的 executor 维度或声明式 Workflow。Plan-and-Execute 内部仍通过一个 `ToolCallingActionExecutor` 完成单个 Action；AgentLoop 不经过它。各引擎共享 `core` 契约、工具注册表、事件总线和底层工具执行协议。

```text
CLI / WebUI / Evaluation
          |
     AgentRuntime ---- EventBus ---- Console / Tracing / WebUI / Metrics
          |
 sequential | dag | agent_loop
          |
   native tool-calling loops
          |
      ToolRegistry
```

模型通过结构化 `tool_calls` 发起动作，运行时执行工具并以 `role="tool"` 消息回传结果。推理模型可在同一协议中使用独立 reasoning 预算；代码不解析字面 `Thought:` / `Action:` / `Observation:` 文本。

Subagent、记忆、知识库、技能、自演化、Guardrails 和 Checkpoint 作为可选工具或生命周期能力接入，不改变引擎身份。

## 快速开始

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py --help
python main.py chat
python main.py run "整理当前目录结构" --engine sequential --effort low
python main.py run "按依赖图比较三个方案" --engine dag --effort medium
python main.py run "探索代码并持续更新计划" --engine agent_loop --effort high
```

普通配置写入 `settings.toml`；`.env` 只保存 API Key；CLI 参数仅覆盖当前任务。

基础默认值是 Shell `restricted`、Python `disabled`；当前学习配置 `settings.toml` 已显式设置为 `shell_mode = "trusted"` 和 `python_mode = "trusted"`。Trusted 模式拥有当前本机用户权限，不是安全沙箱。

## 本地服务与评测

```bash
python -m webui --help
python -m webui
python -m tracing --help
python -m evaluation --help
python -m evaluation run --dry-run
python -m evaluation serve
```

评测题库位于 `evaluation/cases/`。矩阵维度是 `engine × effort × capabilities`；报告分别展示成功率、Verifier、LLM 调用、工具调用、reasoning token、Subagent 调用、延迟和重复运行稳定性，不计算单一综合分。生成结果默认保存到 `~/.manus_demo/evaluation`。

更多说明见 [架构](docs/architecture.md)、[引擎](docs/engines.md)、[配置](docs/configuration.md) 和 [评测](docs/evaluation.md)。
