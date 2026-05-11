
# 全链路 Tracing 模块 - 任务清单

## Phase 1: 设计文档与基础设施

- [x] 编写 `sxw_aicoding/docs/tracing-design.md` 完整设计文档（需求分析 + 架构图 + 集成策略）
- [x] 创建 `tracing/` 目录结构和 `tracing/__init__.py` 模块入口
- [x] 实现 `tracing/config.py` —— Tracing 配置管理
- [x] 修改 `config.py` —— 新增 Tracing 环境变量读取
- [x] 修改 `requirements.txt` —— 添加 OpenTelemetry 依赖
- [x] 修改 `.env.example` —— 添加 Tracing 配置示例

## Phase 2: 核心 Tracing 实现

- [x] 实现 `tracing/spans.py` —— Span 名称常量和 Attribute 键名定义
- [x] 实现 `tracing/provider.py` —— TracerProvider 工厂（Resource + Exporter + Sampler）
- [x] 实现 `tracing/exporters.py` —— FileSpanExporter + RichConsoleExporter
- [x] 实现 `tracing/decorators.py` —— @traced / @traced_llm_call / @traced_tool_call 装饰器
- [x] 实现 `tracing/bridge.py` —— TracingBridge 事件桥接器

## Phase 3: 系统集成

- [x] 修改 `agents/orchestrator.py` —— 初始化 TracingBridge + 多播事件分发
- [x] 修改 `llm/client.py` —— LLM 调用 Span 埋点
- [x] 修改 `tools/base.py` —— 工具执行 Span 埋点（通过 traced_execute 模板方法 + 调用侧切换）

## Phase 4: 测试与验证

- [x] 编写 `tests/test_tracing.py` —— 单元测试（Bridge 映射、装饰器、Exporter）
- [x] 运行完整测试套件，确认 TRACING_ENABLED=false 时零副作用
- [x] 端到端验证：TRACING_BACKEND=file 模式输出正确的 JSON trace


---
生成时间: 2026/5/11 10:29:12
planId: 0d3c4309-1c63-47ae-8ce0-ccf48690257d