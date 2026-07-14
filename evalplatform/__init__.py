"""
Evaluation Platform (v21) — document-driven benchmark generation, execution,
reporting, and cross-run analysis on top of the evaluation/ framework.

评测平台（v21）—— 基于 evaluation/ 评测框架之上的四段式流水线：
  1. 文档上传 → 2. LLM 自动生成评测集 → 3. 执行评测产出报告 → 4. 聚合分析给出优化建议

Entry points / 入口：
  python -m evalplatform serve          # Web 平台（上传/生成/运行/报告/分析）
  python -m evalplatform upload <file>  # CLI 流水线（详见 python -m evalplatform -h）
"""
