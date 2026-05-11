
# Trace Web 可视化查看器 - 任务清单

## 后端服务

- [x] 创建 `tracing/__main__.py`：CLI 入口（argparse 解析 --port/--dir 参数，启动 uvicorn）
- [x] 创建 `tracing/server.py`：FastAPI 应用（含 API 路由 + 页面路由 + trace 文件扫描逻辑）

## 前端模板

- [x] 创建 `tracing/templates/base.html`：基础模板（暗色主题 CSS + 页面骨架）
- [x] 创建 `tracing/templates/trace_list.html`：Trace 列表页（表格展示，点击跳转详情）
- [x] 创建 `tracing/templates/trace_detail.html`：Trace 详情页（树形结构 + 属性展开面板）

## 依赖与文档

- [x] 更新 `requirements.txt`：添加 fastapi、uvicorn、jinja2 依赖
- [x] 验证服务可正常启动并渲染页面


---
生成时间: 2026/5/11 11:29:11
planId: 8d4551ac-9d41-483a-a8f8-b8f79cba01b3