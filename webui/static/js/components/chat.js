// 聊天流：用户气泡 + 事件卡 + 答案气泡 + 内联 HITL 输入 + 输入框
// Chat stream: user bubbles + event cards + answer bubbles + inline
// HITL input + composer.
// Phase 3：事件先用 raw 折叠卡；Phase 4 引入富渲染器。
// Phase 3: raw collapsible cards; rich renderers arrive in Phase 4.

import { html, useEffect, useRef, useState } from "/static/vendor/preact-htm-standalone.mjs";
import { EventCard } from "./event_card.js";
import { SubagentCard, TodoCard } from "./event_renderers.js";

function RunStarted({ entry }) {
  const chips = Object.entries(entry.overrides || {});
  return html`
    <div class="msg-user">
      <div class="bubble user">
        ${entry.runKind === "resume"
          ? html`<span class="badge warn">恢复任务</span> ${entry.task}`
          : entry.task}
      </div>
      ${chips.length > 0 && html`
        <div class="override-chips">
          ${chips.map(([k, v]) => html`<span class="badge" title="本会话配置覆盖">${k}=${String(v)}</span>`)}
        </div>
      `}
    </div>
  `;
}

function RunFinished({ entry }) {
  if (entry.status === "failed") {
    return html`
      <div class="msg-agent">
        <div class="bubble error">
          <div class="bubble-head">✗ 任务失败</div>
          <pre class="answer-text">${entry.error || "未知错误"}</pre>
        </div>
      </div>
    `;
  }
  return html`
    <div class="msg-agent">
      <div class="bubble answer">
        <div class="bubble-head">
          ✓ 最终答案
          ${entry.trace && html`
            <a class="badge run" href=${entry.trace.url} target="_blank">Trace ↗</a>
          `}
        </div>
        <pre class="answer-text">${entry.answer || "(空)"}</pre>
      </div>
    </div>
  `;
}

function HitlPrompt({ prompt, send }) {
  const [text, setText] = useState("");
  const [remaining, setRemaining] = useState(null);

  useEffect(() => {
    if (!prompt.timeoutSeconds) return;
    const tick = () => {
      const elapsed = (Date.now() - prompt.receivedAt) / 1000;
      setRemaining(Math.max(0, Math.round(prompt.timeoutSeconds - elapsed)));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [prompt.promptId]);

  const submit = () => {
    if (!text.trim()) return;
    send({ type: "hitl_response", prompt_id: prompt.promptId, text: text.trim() });
    setText("");
  };

  return html`
    <div class="hitl-prompt">
      <div class="hitl-question">
        🙋 Agent 提问：${prompt.question}
        ${remaining !== null && html`<span class="hitl-countdown">${remaining}s</span>`}
      </div>
      <div class="hitl-input-row">
        <input type="text" value=${text} autoFocus
          placeholder="输入回答后回车…"
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter") submit(); }} />
        <button class="primary" onClick=${submit}>回答</button>
        <button onClick=${() => send({ type: "hitl_cancel", prompt_id: prompt.promptId })}>取消</button>
      </div>
    </div>
  `;
}

function Composer({ state, onSend }) {
  const [text, setText] = useState("");
  const disabled = state.running || !state.connected;

  const submit = () => {
    const value = text.trim();
    if (!value || disabled) return;
    onSend(value);
    setText("");
  };

  return html`
    <div class="composer">
      <textarea rows="2" value=${text} disabled=${disabled}
        placeholder=${state.running
          ? "任务运行中…"
          : state.connected ? "输入任务，Ctrl+Enter 发送" : "连接中…"}
        onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); }
        }} />
      <button class="primary" disabled=${disabled} onClick=${submit}>发送</button>
    </div>
  `;
}

export function Chat({ state, send, onSendTask }) {
  const listRef = useRef(null);

  // 新消息自动滚底 / auto-scroll on new entries
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.chat.length, state.pendingPrompt && state.pendingPrompt.promptId]);

  return html`
    <div class="chat">
      <div class="chat-list" ref=${listRef}>
        ${state.chat.length === 0 && html`
          <div class="placeholder">
            发送一个任务开始调试。<br/>
            首次发送会自动以当前配置创建会话；<br/>
            也可以先在右侧调整配置后「应用并新建会话」。
          </div>
        `}
        ${state.chat.map((entry) => {
          if (entry.kind === "run_started") return html`<${RunStarted} key=${entry.seq} entry=${entry} />`;
          if (entry.kind === "run_finished") return html`<${RunFinished} key=${entry.seq} entry=${entry} />`;
          if (entry.kind === "todo_card")
            return html`<${TodoCard} key=${entry.seq} state=${state} runId=${entry.runId} />`;
          if (entry.kind === "subagent_card")
            return html`<${SubagentCard} key=${entry.seq} state=${state} subagentId=${entry.subagentId} />`;
          if (entry.kind === "error")
            return html`<div class="ws-error" key=${"e" + entry.ts}>⚠ ${entry.code}: ${entry.message}</div>`;
          return html`<${EventCard} key=${entry.seq} entry=${entry} />`;
        })}
        ${state.pendingPrompt && html`<${HitlPrompt} prompt=${state.pendingPrompt} send=${send} />`}
      </div>
      <${Composer} state=${state} onSend=${onSendTask} />
    </div>
  `;
}
