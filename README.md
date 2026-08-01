# Manus Demo

这是一个用于本地学习和比较 Agent 编排方式的 Python 项目。核心结构是统一运行时、可替换任务引擎和可替换动作执行器；项目不面向生产环境。

## 架构概览

`AgentRuntime` 接收任务并由 `EngineSelector` 选择编排引擎。Sequential、DAG、TODO 和 Goal 引擎都通过同一个 `ActionExecutor` 执行动作；Workflow 只接受显式工作流文件。React 与 Thinking 执行器的自动选择只依据 `llm.supports_reasoning`。

```text
CLI / WebUI / Evaluation
          |
     AgentRuntime ---- EventBus ---- Console / Tracing / WebUI / Metrics
          |
 TaskEngine: sequential | dag | todo | goal | workflow
          |
 ActionExecutor: react | thinking
          |
      ToolRegistry
```

A2A、远程 Agent、Subagent、记忆、知识库、技能、自演化、Guardrails 和 Checkpoint 作为可选工具或生命周期钩子接入，不参与核心路由判断。

## 快速开始

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py --help
python main.py chat
python main.py run "整理当前目录结构" --engine sequential --executor react --effort low
python main.py workflow workflow_spec.json
python main.py mcp-server
```

普通配置写入 `settings.toml`；`.env` 只保存 API Key。CLI 参数仅覆盖当前任务。

## 本地服务与评测

```bash
python -m webui --help
python -m webui
python -m tracing --help
python -m evaluation --help
python -m evaluation run --dry-run
python -m evaluation serve
```

评测题库位于 `evaluation/cases/`。生成的文档、题集、运行结果和报告默认保存到 `~/.manus_demo/evaluation`，不会写入仓库。评测分别报告成功率、Verifier、Token、延迟、工具次数、迭代、重规划、稳定性和自动选择准确率，不使用单一综合分替代各维度。

更多说明见 [架构](docs/architecture.md)、[引擎](docs/engines.md)、[配置](docs/configuration.md) 和 [评测](docs/evaluation.md)。
