// 按事件族的富渲染器 + Agent Loop 任务快照 + SubAgent 归组卡
// Per-family rich renderers + Agent Loop task snapshot + grouped SubAgent card.
//
// 每个渲染器：{ icon, cls, summary(data), Body(data)?, open? }
// 未注册的事件由 event_card.js 走 raw JSON 兜底卡。
// Unregistered events fall back to the raw JSON card in event_card.js.

import { html } from "/static/vendor/preact-htm-standalone.mjs";

// ---------------------------------------------------------------------
// 通用小组件 / small shared widgets
// ---------------------------------------------------------------------

function Raw({ data }) {
  return html`<pre class="event-json">${JSON.stringify(data, null, 2)}</pre>`;
}

function KV({ data, keys }) {
  const entries = keys
    ? keys.filter((k) => data && data[k] !== undefined).map((k) => [k, data[k]])
    : Object.entries(data || {});
  return html`
    <div class="kv">
      ${entries.map(([k, v]) => html`
        <div class="kv-row">
          <span class="kv-key">${k}</span>
          <span class="kv-val">${typeof v === "string" ? v : JSON.stringify(v)}</span>
        </div>
      `)}
    </div>
  `;
}

function ToolCalls({ log }) {
  if (!Array.isArray(log) || log.length === 0) return null;
  return html`
    <div class="tool-calls">
      ${log.map((rec) => html`
        <details class="tool-call ${String(rec.result || "").startsWith("Error:") ? "failed" : ""}">
          <summary>
            <span class="tool-name">🔧 ${rec.tool_name}</span>
            <span class="event-summary">${JSON.stringify(rec.parameters || {}).slice(0, 100)}</span>
          </summary>
          <pre class="event-json">${rec.result || "(无返回)"}</pre>
        </details>
      `)}
    </div>
  `;
}

function StepResultBody({ result }) {
  if (!result) return null;
  return html`
    <div>
      <div class="step-result-head">
        <span class="badge ${result.success ? "ok" : "err"}">${result.success ? "成功" : "失败"}</span>
        ${result.iterations_completed > 0 &&
          html`<span class="badge">${result.iterations_completed} 轮迭代</span>`}
        ${Array.isArray(result.tool_calls_log) &&
          html`<span class="badge">${result.tool_calls_log.length} 次工具调用</span>`}
      </div>
      ${result.output && html`<pre class="event-json">${result.output}</pre>`}
      <${ToolCalls} log=${result.tool_calls_log} />
    </div>
  `;
}

const fmtTokens = (n) => (n >= 10000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));

function TokenTable({ data }) {
  if (!data || !data.total) return html`<${Raw} data=${data} />`;
  const rows = (dict) => Object.entries(dict || {});
  return html`
    <div>
      <div class="token-total">
        总计 <b>${fmtTokens(data.total.total_tokens)}</b>
        （输入 ${fmtTokens(data.total.prompt_tokens)} / 输出 ${fmtTokens(data.total.completion_tokens)}${
          data.total.reasoning_tokens ? ` / 推理 ${fmtTokens(data.total.reasoning_tokens)}` : ""}）
        · ${(data.call_records || []).length} 次调用
      </div>
      ${["by_model", "by_caller"].map((key) => rows(data[key]).length > 0 && html`
        <table class="token-table">
          <thead><tr>
            <th>${key === "by_model" ? "模型" : "调用方"}</th>
            <th>输入</th><th>输出</th><th>总计</th>
          </tr></thead>
          <tbody>
            ${rows(data[key]).map(([name, usage]) => html`
              <tr>
                <td>${name}</td>
                <td>${fmtTokens(usage.prompt_tokens)}</td>
                <td>${fmtTokens(usage.completion_tokens)}</td>
                <td>${fmtTokens(usage.total_tokens)}</td>
              </tr>
            `)}
          </tbody>
        </table>
      `)}
    </div>
  `;
}

function PlanBody({ data }) {
  const steps = (data && data.steps) || [];
  return html`
    <ol class="plan-steps">
      ${steps.map((s) => html`
        <li>
          ${s.description}
          ${Array.isArray(s.dependencies) && s.dependencies.length > 0 &&
            html`<span class="plan-deps">依赖: ${s.dependencies.join(", ")}</span>`}
        </li>
      `)}
    </ol>
  `;
}

// ---------------------------------------------------------------------
// 渲染器注册表 / renderer registry
// ---------------------------------------------------------------------

const str = (v) => (v == null ? "" : String(v));

export const RENDERERS = {
  // --- 生命周期 / lifecycle ---
  task_started: {
    icon: "▶", cls: "info",
    summary: (d) => `任务开始 · ${str(d && d.task).slice(0, 100)}`,
  },
  engine_started: {
    icon: "🧭", cls: "info",
    summary: (d) => `引擎: ${str(d && d.engine)}`,
  },
  engine_completed: {
    icon: "🧭",
    cls: (d) => d && d.success ? "ok" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => d && d.success
      ? `引擎完成 · ${str(d && d.engine)}`
      : `引擎停止 · ${str(d && d.engine)} (${str(d && d.stop_reason)})`,
  },
  agent_loop_started: {
    icon: "🔁", cls: "info",
    summary: (d) => `AgentLoop 开始 · 最多 ${d && d.max_turns} 轮`,
  },
  agent_turn_started: {
    icon: "↻", cls: "info",
    summary: (d) => `模型轮次 ${d && d.turn} 开始`,
  },
  agent_turn_completed: {
    icon: "↻", cls: "ok",
    summary: (d) => `模型轮次 ${d && d.turn} 完成${d && d.final ? " · 最终回答" : ""}${d && d.tool_calls ? ` · ${d.tool_calls} 个工具调用` : ""}`,
  },
  agent_loop_completed: {
    icon: "🔁", cls: (d) => d && d.success ? "ok" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => `AgentLoop ${d && d.success ? "完成" : `停止 (${str(d && d.stop_reason)})`}`,
  },
  action_started: {
    icon: "▸", cls: "info",
    summary: (d) => str(d && d.action && d.action.description).slice(0, 100),
  },
  action_turn_started: {
    icon: "↻", cls: "info",
    summary: (d) => `动作 ${str(d && d.action_id)} · 模型轮次 ${d && d.turn} 开始`,
  },
  action_turn_completed: {
    icon: "↻",
    cls: (d) => d && d.success ? "ok" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => `动作 ${str(d && d.action_id)} · 模型轮次 ${d && d.turn} ${d && d.success ? "完成" : "停止"}${d && d.tool_calls ? ` · ${d.tool_calls} 个工具调用` : ""}`,
  },
  action_completed: {
    icon: "✓",
    cls: (d) => d && d.success ? "ok" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => `动作 ${str(d && d.action_id)} ${d && d.success ? "完成" : "未完成"}`,
    Body: (d) => html`<${Raw} data=${d} />`,
  },
  action_failed: {
    icon: "✗", cls: "err", open: true,
    summary: (d) => `动作 ${str(d && d.action_id)} 失败: ${str(d && d.error).slice(0, 80)}`,
  },
  tool_started: {
    icon: "🔧", cls: "info",
    summary: (d) => `调用工具: ${str(d && d.tool)}`,
  },
  tool_completed: {
    icon: "🔧", cls: (d) => d && d.success ? "info" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => `${str(d && d.tool)}: ${d && d.success ? "成功" : "失败"}`,
    Body: (d) => html`<${Raw} data=${d} />`,
  },
  task_completed: {
    icon: "🏁",
    cls: (d) => d && d.success ? "ok" : "err",
    open: (d) => !(d && d.success),
    summary: (d) => d && d.success
      ? "任务完成"
      : `任务停止 (${str(d && d.stop_reason)})`,
  },
  task_failed: {
    icon: "💥", cls: "err", open: true,
    summary: (d) => `任务失败: ${str(d && d.error).slice(0, 100)}`,
  },
  task_cancelled: {
    icon: "■", cls: "err", open: true,
    summary: (d) => `任务已取消: ${str(d && d.error).slice(0, 100)}`,
  },
  task_start: { icon: "▶", cls: "info", summary: (d) => str(d && d.task).slice(0, 100) },
  task_complete: { icon: "🏁", cls: "ok", summary: () => "任务完成（最终答案见下方气泡）" },
  token_usage_summary: {
    icon: "🪙", cls: "info", open: true,
    summary: (d) => `Token 用量：${d && d.total ? fmtTokens(d.total.total_tokens) : "?"}`,
    Body: (d) => html`<${TokenTable} data=${d} />`,
  },

  // --- 计划 / plan ---
  plan: {
    icon: "📋", cls: "info", open: true,
    summary: (d) => `计划：${((d && d.steps) || []).length} 步`,
    Body: (d) => html`<${PlanBody} data=${d} />`,
  },
  plan_created: {
    icon: "📋", cls: "info", open: true,
    summary: (d) => `顺序计划：${((d && d.steps) || []).length} 步`,
    Body: (d) => html`<${PlanBody} data=${d} />`,
  },
  plan_adaptation: {
    icon: "🔀", cls: "warn",
    summary: (d) => (d && d.adapted ? `计划调整: ${str(d.reasoning).slice(0, 80)}` : "无需调整"),
    Body: (d) => html`<${KV} data=${d} keys=${["adapted", "reasoning", "changes"]} />`,
  },
  step_start: {
    icon: "▸", cls: "info",
    summary: (d) => `Step ${d && d.step ? d.step.id : "?"}: ${str(d && d.step && d.step.description).slice(0, 90)}`,
  },
  step_complete: {
    icon: "✓", cls: "ok",
    summary: (d) => `Step ${d && d.step ? d.step.id : "?"} 完成`,
    Body: (d) => html`<${StepResultBody} result=${d && d.result} />`,
  },
  step_failed: {
    icon: "✗", cls: "err", open: true,
    summary: (d) => `Step ${d && d.step ? d.step.id : "?"} 失败`,
    Body: (d) => html`<${StepResultBody} result=${d && d.result} />`,
  },
  step_skipped: {
    icon: "⤼", cls: "warn",
    summary: (d) => `Step ${d && d.step ? d.step.id : "?"} 跳过${d && d.reason ? `: ${d.reason}` : ""}`,
  },
  reflection: {
    icon: "🪞", cls: "info", open: (d) => !(d && d.passed),
    summary: (d) => `反思: ${d && d.passed ? "通过 ✓" : "未通过 ✗"} score=${d && d.score}`,
    Body: (d) => html`<${KV} data=${d} keys=${["feedback", "suggestions"]} />`,
  },
  planner_started: { icon: "📋", cls: "info", summary: (d) => `Planner: ${str(d && d.operation)} 开始` },
  planner_completed: { icon: "📋", cls: "ok", summary: (d) => `Planner: ${str(d && d.operation)} 完成` },
  reflector_started: { icon: "🪞", cls: "info", summary: (d) => `Reflector: ${str(d && d.operation)} 开始` },
  reflector_completed: { icon: "🪞", cls: "info", summary: (d) => `Reflector: ${str(d && d.operation)} ${d && d.success ? "通过" : "未通过"}` },

  // --- DAG ---
  dag_created: {
    icon: "🕸", cls: "info",
    summary: (d) => `DAG 已创建（${d && d.nodes ? Object.keys(d.nodes).length : "?"} 节点）`,
    Body: (d) => html`<${Raw} data=${d} />`,
  },
  dag_execution_started: { icon: "🕸", cls: "info", summary: () => "DAG 执行开始" },
  dag_execution_completed: { icon: "🕸", cls: "ok", summary: () => "DAG 执行完成" },
  superstep: {
    icon: "⏩", cls: "info",
    summary: (d) => `Super-step ${d && d.step}：${d && Array.isArray(d.nodes) ? d.nodes.length : "?"} 节点就绪`,
  },
  node_running: {
    icon: "▸", cls: "info",
    summary: (d) => `节点 ${d && d.node ? (d.node.id || d.node.name || "") : "?"} 运行中`,
  },
  node_completed: {
    icon: "✓", cls: "ok",
    summary: (d) => `节点 ${d && d.node ? (d.node.id || d.node.name || "") : "?"} 完成`,
    Body: (d) => html`<${StepResultBody} result=${d && d.result} />`,
  },
  node_failed: {
    icon: "✗", cls: "err", open: true,
    summary: (d) => `节点 ${d && d.node ? (d.node.id || d.node.name || "") : "?"} 失败${d && d.reason ? `: ${d.reason}` : ""}`,
    Body: (d) => html`<${StepResultBody} result=${d && d.result} />`,
  },
  node_rollback: {
    icon: "↩", cls: "warn",
    summary: (d) => `节点 ${d && d.node ? (d.node.id || d.node.name || "") : "?"} 回滚`,
  },
  condition_evaluated: {
    icon: "❓", cls: "info",
    summary: (d) => `条件边评估: ${d && d.met ? "满足" : "不满足"}`,
  },
  execution_error: {
    icon: "💥", cls: "err", open: true,
    summary: (d) => `执行错误: ${str(d && d.reason).slice(0, 80)}`,
    Body: (d) => html`<${KV} data=${d} />`,
  },

  // --- 记忆 / memory ---
  memory: { icon: "🧠", cls: "info", summary: (d) => str(d).slice(0, 100) },
  knowledge: { icon: "📚", cls: "info", summary: (d) => str(d).slice(0, 100) },
  memory_stored: { icon: "🧠", cls: "info", summary: () => "记忆已写入" },
  memory_search_start: { icon: "🔍", cls: "info", summary: () => "检索记忆…" },
  memory_search_result: { icon: "🔍", cls: "info", summary: (d) => `记忆命中 ${d && d.count != null ? d.count : "?"} 条` },
  memory_store: { icon: "🧠", cls: "info", summary: () => "memory_store 调用" },
  memory_revoke: { icon: "🗑", cls: "warn", summary: (d) => `记忆撤销: ${str(d).slice(0, 80)}` },
  memory_consolidate: { icon: "🧠", cls: "info", summary: () => "记忆巩固" },

  // --- 自演化 / evolution ---
  experience_learned: { icon: "🌱", cls: "ok", summary: (d) => `经验学习: ${str(d && d.summary).slice(0, 80)}` },
  failure_lesson_stored: { icon: "🌧", cls: "warn", summary: (d) => `失败教训: ${str(d && d.failure_reason).slice(0, 80)}` },
  avoidance_hints_injected: { icon: "🛡", cls: "info", summary: () => "已注入避坑提示" },
  preference_hints_injected: { icon: "💡", cls: "info", summary: () => "已注入偏好提示" },
  preference_learned: { icon: "💡", cls: "ok", summary: (d) => `偏好学习: ${str(d && d.value).slice(0, 80)}` },

  // --- 护栏 / guardrail ---
  guardrail_blocked: {
    icon: "🛡", cls: "err", open: true,
    summary: (d) => `护栏拦截 ${d && d.tool}: ${str(d && d.reason).slice(0, 80)}`,
    Body: (d) => html`<${KV} data=${d} keys=${["tool", "reason", "risk"]} />`,
  },
  guardrail_injection_neutralized: {
    icon: "🛡", cls: "warn",
    summary: (d) => `注入已中和（${d && d.tool}）`,
  },
  guardrail_output_redacted: { icon: "🛡", cls: "warn", summary: (d) => `输出已脱敏: ${str(d && d.reason).slice(0, 80)}` },
  guardrail_write_confirm: { icon: "🛡", cls: "warn", open: true, summary: (d) => `写操作确认（${d && d.tool}）` },
  guardrail_violation: { icon: "🛡", cls: "err", open: true, summary: (d) => `护栏违规: ${str(d && d.reason).slice(0, 80)}` },

  // --- 技能 / skills ---
  skills_discovered: {
    icon: "🧩", cls: "info",
    summary: (d) => `发现 ${d && d.count} 个技能${d && Array.isArray(d.names) ? `: ${d.names.join(", ").slice(0, 60)}` : ""}`,
  },
  skill_activated: { icon: "🧩", cls: "ok", summary: (d) => `技能激活: ${d && d.name}` },
  skill_activation_failed: { icon: "🧩", cls: "err", summary: (d) => `技能激活失败 ${d && d.name}: ${str(d && d.error).slice(0, 60)}` },
  skill_content_guarded: { icon: "🧩", cls: "warn", summary: (d) => `技能内容护栏 ${d && d.name} (${d && d.trust_level}/${d && d.action})` },
  skill_allowed_tools_blocked: { icon: "🧩", cls: "warn", summary: (d) => `技能工具过滤: ${d && Array.isArray(d.blocked_tools) ? d.blocked_tools.join(", ") : ""}` },
  skill_auto_created: { icon: "🧩", cls: "ok", summary: (d) => `技能自动蒸馏: ${d && d.name}` },
  skill_optimization_applied: { icon: "🧩", cls: "ok", summary: () => "技能优化已应用", Body: (d) => html`<${Raw} data=${d} />` },
  skill_optimization_report: { icon: "🧩", cls: "info", summary: () => "技能优化报告", Body: (d) => html`<${Raw} data=${d} />` },

  // --- HITL ---
  ask_user_prompt: {
    icon: "🙋", cls: "warn", open: true,
    summary: (d) => `提问用户: ${str(d && d.question).slice(0, 90)}`,
  },
  ask_user_response: { icon: "💬", cls: "ok", summary: (d) => `用户回答: ${str(d && d.response).slice(0, 90)}` },
  ask_user_timeout: { icon: "⏰", cls: "warn", summary: (d) => `用户 ${d && d.timeout ? d.timeout + "s " : ""}未回答，LLM 自主继续` },
  ask_user_cancelled: { icon: "🚫", cls: "warn", summary: () => "用户取消回答" },

  // --- checkpoint ---
  checkpoint_saved: {
    icon: "💾", cls: "info",
    summary: (d) => `Checkpoint 已保存（${d && d.task_id}, ${d && d.state}）`,
  },

  // --- MCP ---
  mcp_tools_discovered: { icon: "🔌", cls: "info", summary: (d) => `MCP 工具发现: ${str(d && (d.count ?? "")).slice(0, 60)}`, Body: (d) => html`<${Raw} data=${d} />` },
  mcp_tool_executed: { icon: "🔌", cls: "info", summary: (d) => `MCP 工具执行`, Body: (d) => html`<${Raw} data=${d} />` },
  mcp_schema_error: { icon: "🔌", cls: "err", summary: (d) => `MCP schema 错误`, Body: (d) => html`<${Raw} data=${d} />` },
};

// ---------------------------------------------------------------------
// Agent-loop todo snapshot card
// ---------------------------------------------------------------------

const TODO_ICONS = {
  pending: "○",
  in_progress: "▸",
  completed: "✓",
};

export function TodoCard({ state, runId }) {
  const todo = state.todoState[runId];
  if (!todo) return null;
  const hasItems = todo.order.length > 0;
  return html`
    <div class="todo-card">
      <div class="todo-head">📝 TODO 列表 <span class="badge">${
        todo.order.filter((id) => todo.items[id].status === "completed").length
      }/${todo.order.length}</span></div>
      ${hasItems && html`
        <ul class="todo-list">
          ${todo.order.map((id) => {
            const item = todo.items[id];
            return html`
              <li class="todo-item ${item.status}">
                <span class="todo-icon">${TODO_ICONS[item.status] || "○"}</span>
                <span class="todo-desc">${item.description}</span>
              </li>
            `;
          })}
        </ul>
      `}
      ${!hasItems && todo.summary && (
        Array.isArray(todo.summary)
          ? html`<ul class="todo-list">${todo.summary.map((line) => html`<li class="todo-item"><span class="todo-desc">${line}</span></li>`)}</ul>`
          : html`<pre class="event-json">${todo.summary}</pre>`
      )}
    </div>
  `;
}

// ---------------------------------------------------------------------
// SubAgent 归组卡 / grouped SubAgent card
// ---------------------------------------------------------------------

const SUBAGENT_BADGE = {
  running: ["run", "运行中"],
  completed: ["ok", "完成"],
  failed: ["err", "失败"],
  timed_out: ["warn", "超时"],
  cancelled: ["warn", "已取消"],
};

function subagentLine(evt) {
  const d = evt.data || {};
  switch (evt.event) {
    case "subagent_start":
      return `▶ 启动: ${str(d.task_description).slice(0, 120)}`;
    case "agent_loop_started":
      return `· Child AgentLoop 开始（最多 ${d.max_turns ?? "?"} 轮）`;
    case "subagent_iteration":
      return `· 迭代 ${d.iteration}（${d.tool_calls_count ?? "?"} 次工具调用）`;
    case "agent_turn_started":
      return `· 模型轮次 ${d.turn} 开始`;
    case "agent_turn_completed":
      return `· 模型轮次 ${d.turn} 完成${d.tool_calls ? `（${d.tool_calls} 个工具调用）` : ""}`;
    case "tool_started":
      return `· 调用工具 ${str(d.tool)}`;
    case "tool_completed":
      return `· 工具 ${str(d.tool)} ${d.success ? "成功" : "失败"}`;
    case "agent_loop_completed":
      return `· Child AgentLoop ${d.success ? "完成" : `停止（${str(d.stop_reason)}）`}`;
    case "subagent_todo_updated": {
      const todos = Array.isArray(d.todos) ? d.todos : [];
      const completed = todos.filter((todo) => todo && todo.status === "completed").length;
      return `· Todo ${completed}/${todos.length} 已完成`;
    }
    case "subagent_complete":
      return `✓ 完成（${d.turns ?? "?"} 轮 / ${fmtTokens(d.tokens ?? 0)} tokens / ${d.tool_calls ?? 0} 次工具调用）`;
    case "subagent_failed":
      return `✗ 失败: ${str(d.error).slice(0, 120)}`;
    case "subagent_timed_out":
      return `⏰ 超时（${d.timeout}s）`;
    case "subagent_cancelled":
      return `■ 已取消: ${str(d.error).slice(0, 120)}`;
    default:
      return evt.event;
  }
}

export function SubagentCard({ state, subagentId }) {
  const sub = state.subagents[subagentId];
  if (!sub) return null;
  const [badgeCls, badgeText] = SUBAGENT_BADGE[sub.status] || ["", sub.status];
  return html`
    <details class="event-card subagent" open=${sub.status === "running"}>
      <summary>
        <span class="event-name">🤖 SubAgent ${sub.subagentId}</span>
        <span class="badge ${badgeCls}">${badgeText}</span>
      </summary>
      <div class="subagent-timeline">
        ${sub.events.map((evt) => html`<div class="subagent-line" key=${evt.seq}>${subagentLine(evt)}</div>`)}
      </div>
    </details>
  `;
}
