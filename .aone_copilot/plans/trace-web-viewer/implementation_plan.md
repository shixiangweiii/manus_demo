
# Trace Web 可视化查看器

为 Tracing 模块新增一个轻量级 Web 可视化界面，用户可通过浏览器查看本地 `traces/` 目录中保存的 Trace 文件，支持树形结构展示 Span 层级关系。

## Proposed Changes

### 后端服务（FastAPI）

#### [NEW] [server.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/tracing/server.py)

FastAPI 应用，提供以下 API 端点：

- `GET /` — 重定向到 Trace 列表页
- `GET /traces` — Trace 列表页（HTML）
- `GET /traces/{trace_id}` — 单 Trace 详情页（HTML，树形结构展示）
- `GET /api/traces` — JSON 接口，返回所有 Trace 文件列表（trace_id, exported_at, span_count）
- `GET /api/traces/{trace_id}` — JSON 接口，返回单个 Trace 的完整 Span 数据

核心逻辑：
- 扫描 `traces/` 目录中的 `*.json` 文件
- 解析 JSON 获取 trace 元数据（trace_id、时间、span 数量、根 span 名称）
- 为详情页重建 Span 树形结构（基于 parent_span_id）

#### [NEW] [__main__.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/tracing/__main__.py)

CLI 入口，支持 `python -m tracing` 启动 Web 服务：

```python
# 用法：
# python -m tracing                    # 默认端口 8600
# python -m tracing --port 9000        # 自定义端口
# python -m tracing --dir ./my_traces  # 自定义 traces 目录
```

---

### 前端模板（内嵌 HTML/JS/CSS）

#### [NEW] [templates/base.html](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/tracing/templates/base.html)

基础 HTML 模板，包含：
- 页面骨架（header、nav、content）
- 内嵌 CSS 样式（暗色主题，类似 DevTools 风格）
- 公共 JS 工具函数

#### [NEW] [templates/trace_list.html](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/tracing/templates/trace_list.html)

Trace 列表页：
- 表格展示所有 Trace（ID、根 Span 名称、Span 数量、导出时间、总耗时）
- 每行可点击跳转到详情页
- 按时间倒序排列（最新的在上面）

#### [NEW] [templates/trace_detail.html](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/tracing/templates/trace_detail.html)

Trace 详情页（核心页面）：
- **树形结构展示**：基于 parent_span_id 重建 Span 树，使用可折叠的缩进层级
- 每个 Span 节点显示：图标（与 RichConsoleExporter 一致）、名称、耗时、状态（✅/❌）
- 点击 Span 节点展开详细属性面板（attributes、events）
- 树形连接线，视觉清晰

前端 JS 逻辑：
- 从 `/api/traces/{trace_id}` 获取数据
- 递归构建 DOM 树
- 折叠/展开交互

---

### 依赖更新

#### [MODIFY] [requirements.txt](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/requirements.txt)

新增依赖：
```diff
+ fastapi>=0.100.0
+ uvicorn[standard]>=0.20.0
+ jinja2>=3.1.0
```

---

## Verification Plan

### Manual Verification

1. 启动服务：`python -m tracing --port 8600`
2. 浏览器打开 `http://localhost:8600/traces`
3. 验证 Trace 列表页正确展示已有 trace 文件
4. 点击某个 Trace 进入详情页
5. 验证树形结构正确展示 Span 父子层级
6. 验证 Span 展开后显示完整 attributes


---
生成时间: 2026/5/11 11:29:11
planId: 8d4551ac-9d41-483a-a8f8-b8f79cba01b3
plan_status: review