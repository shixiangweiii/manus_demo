// 侧栏：会话信息 + checkpoint 任务列表（可恢复）
// Sidebar: session info + checkpointed task list (resumable).
// PAUSED_WAITING_USER 高亮为琥珀色并提供「恢复」按钮。
// PAUSED_WAITING_USER is highlighted amber with a Resume button.

import { html } from "/static/vendor/preact-htm-standalone.mjs";

const STATE_BADGE = {
  running: ["run", "运行中"],
  paused_waiting_user: ["warn", "等待用户"],
  completed: ["ok", "已完成"],
  failed: ["err", "失败"],
};

function relativeTime(epochSeconds) {
  if (!epochSeconds) return "";
  const delta = Date.now() / 1000 - epochSeconds;
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  return `${Math.floor(delta / 86400)} 天前`;
}

export function Sidebar({ state, onResume, onNewSession, onRefresh }) {
  return html`
    <div class="sidebar-inner">
      <div class="sidebar-section">
        <div class="sidebar-title">会话</div>
        ${state.session
          ? html`
            <div class="session-info">
              <code>${state.session.session_id}</code>
              <span class="badge">${state.session.turn_count} 轮</span>
              ${Object.keys(state.session.overrides || {}).length > 0 &&
                html`<span class="badge warn">${Object.keys(state.session.overrides).length} 项覆盖</span>`}
            </div>
          `
          : html`<div class="sidebar-hint">发送消息将自动创建会话</div>`}
        <button disabled=${state.running} onClick=${onNewSession}>新建会话（默认配置）</button>
      </div>

      <div class="sidebar-section">
        <div class="sidebar-title">
          历史任务
          <button class="sidebar-refresh" onClick=${onRefresh} title="刷新">⟳</button>
        </div>
        ${state.checkpoints.length === 0 &&
          html`<div class="sidebar-hint">暂无 checkpoint 任务</div>`}
        ${state.checkpoints.map((t) => {
          const [cls, label] = STATE_BADGE[t.state] || ["", t.state];
          const resumable = t.state !== "completed";
          return html`
            <div class="ckpt-item ${t.state === "paused_waiting_user" ? "paused" : ""}" key=${t.task_id}>
              <div class="ckpt-task" title=${t.task}>${t.task}</div>
              <div class="ckpt-meta">
                <span class="badge ${cls}">${label}</span>
                <span class="badge">${t.engine || "?"}</span>
                <span class="badge">${t.executor || "?"}</span>
                <span class="ckpt-time">${relativeTime(t.updated_at)}</span>
                ${resumable && html`
                  <button class="ckpt-resume" disabled=${state.running}
                    onClick=${() => onResume(t.task_id)}>恢复</button>
                `}
              </div>
            </div>
          `;
        })}
      </div>
    </div>
  `;
}
