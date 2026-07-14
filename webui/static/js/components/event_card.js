// 事件卡分发：富渲染器优先，raw JSON 折叠卡兜底
// Event card dispatch: rich renderer first, raw JSON card as fallback.
// `phase` 事件渲染为分隔行而非卡片。
// The `phase` event renders as a divider row, not a card.

import { html } from "/static/vendor/preact-htm-standalone.mjs";
import { RENDERERS } from "./event_renderers.js";

function shortSummary(data) {
  if (data == null) return "";
  if (typeof data === "string") return data.slice(0, 120);
  try {
    return JSON.stringify(data).slice(0, 120);
  } catch {
    return String(data).slice(0, 120);
  }
}

export function EventCard({ entry }) {
  // 阶段分隔行 / phase divider row
  if (entry.event === "phase") {
    return html`<div class="phase-divider"><span>${String(entry.data)}</span></div>`;
  }

  const renderer = RENDERERS[entry.event];

  // 未注册事件 → raw 兜底卡 / unregistered event → raw fallback card
  if (!renderer) {
    return html`
      <details class="event-card raw">
        <summary>
          <span class="event-name">${entry.event}</span>
          <span class="event-summary">${shortSummary(entry.data)}</span>
          ${entry.truncated && html`<span class="badge warn" title="payload 已截断">截断</span>`}
        </summary>
        <pre class="event-json">${JSON.stringify(entry.data, null, 2)}</pre>
      </details>
    `;
  }

  const open = typeof renderer.open === "function" ? renderer.open(entry.data) : !!renderer.open;
  let summary = "";
  try {
    summary = renderer.summary ? renderer.summary(entry.data) : "";
  } catch {
    summary = shortSummary(entry.data);
  }

  // 无 Body 的渲染器仍提供 raw JSON 展开 / renderers without a Body
  // still expose the raw JSON on expand
  const body = renderer.Body
    ? renderer.Body(entry.data)
    : html`<pre class="event-json">${JSON.stringify(entry.data, null, 2)}</pre>`;

  return html`
    <details class="event-card ${renderer.cls || "info"}" open=${open}>
      <summary>
        <span class="event-icon">${renderer.icon || "•"}</span>
        <span class="event-name">${entry.event}</span>
        <span class="event-summary">${summary}</span>
        ${entry.truncated && html`<span class="badge warn" title="payload 已截断">截断</span>`}
      </summary>
      <div class="event-body">${body}</div>
    </details>
  `;
}
