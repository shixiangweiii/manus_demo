"""
WebUI - Local debugging web interface for the multi-agent system.
WebUI —— 多智能体系统的本地调试 Web 界面。

A ChatAI-style single-user debugging console (``python -m webui``) for
running tasks, choosing common runtime options, streaming structured events,
viewing traces, and resuming semantic checkpoints.
ChatAI 风格的单用户调试控制台（`python -m webui`）：
对话模式运行 agent、可视化分组配置面板、WebSocket 实时事件流、
trace 查看（复用 tracing viewer）、checkpoint 任务恢复。

The package is import-side-effect free; ``webui.__main__`` loads structured
settings and starts the app explicitly.
"""
