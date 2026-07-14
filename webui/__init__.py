"""
WebUI - Local debugging web interface for the multi-agent system.
WebUI —— 多智能体系统的本地调试 Web 界面。

A ChatAI-style single-user debugging console (`python -m webui`):
chat-mode agent runs, visual config panel (grouped env settings),
live execution event stream over WebSocket, trace viewing (reuses
the tracing viewer), and checkpoint task resume.
ChatAI 风格的单用户调试控制台（`python -m webui`）：
对话模式运行 agent、可视化分组配置面板、WebSocket 实时事件流、
trace 查看（复用 tracing viewer）、checkpoint 任务恢复。

IMPORTANT: this package must stay import-side-effect free —
process env bootstrap happens ONLY in `webui/__main__.py` BEFORE
any project import (tracing captures TRACING_* at import time).
重要：本包必须保持 import 无副作用——进程级 env 引导只发生在
`webui/__main__.py` 中、且先于任何项目模块 import（tracing 在
import 时捕获 TRACING_* 配置）。
"""
