// 根组件：装配 store + WS + 聊天 + 配置面板
// Root component: wires store + WS + chat + config panel.

import {
  html, render, useEffect, useReducer, useRef, useState,
} from "/static/vendor/preact-htm-standalone.mjs";
import { api } from "./api.js";
import { initialState, reducer } from "./store.js";
import { createWS } from "./ws.js";
import { Chat } from "./components/chat.js";
import { ConfigPanel } from "./components/config_panel.js";
import { Sidebar } from "./components/sidebar.js";

function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [configOpen, setConfigOpen] = useState(true);

  // WS 回调需要读取最新 state / WS callbacks need the latest state
  const stateRef = useRef(state);
  stateRef.current = state;
  const wsRef = useRef(null);

  useEffect(() => {
    wsRef.current = createWS({
      onMessage: (msg) => dispatch({ type: "ws", msg }),
      onStatus: (value) => dispatch({ type: "connected", value }),
      getLastSeq: () => stateRef.current.lastSeq,
    });
    return () => wsRef.current && wsRef.current.close();
  }, []);

  const send = (msg) => wsRef.current && wsRef.current.send(msg);

  // checkpoint 列表：挂载时 + 每次运行状态翻转时刷新
  // checkpoint list: refetch on mount and whenever running flips
  const refreshCheckpoints = async () => {
    try {
      const resp = await api.listCheckpoints();
      dispatch({ type: "checkpoints", tasks: resp.tasks });
    } catch { /* 静默 / silent */ }
  };
  useEffect(() => { refreshCheckpoints(); }, [state.running]);

  // 发送任务：无会话时自动以当前配置建会话（零覆盖）
  // send a task: auto-create a zero-override session when none exists
  const onSendTask = async (text) => {
    let session = stateRef.current.session;
    if (!session) {
      try {
        const resp = await api.createSession({});
        session = resp.session;
        dispatch({ type: "session", session });
      } catch (e) {
        dispatch({ type: "ws", msg: { type: "error", code: "no_session", message: e.message } });
        return;
      }
    }
    send({ type: "user_message", session_id: session.session_id, text });
  };

  // 配置面板「应用并新建会话」 / config panel "apply & new session"
  const onApply = async (overrides) => {
    const resp = await api.createSession(overrides);
    dispatch({ type: "session", session: resp.session });
  };

  // 恢复 checkpoint 任务（无会话时先自动建会话）
  // resume a checkpointed task (auto-create session first if needed)
  const onResume = async (taskId) => {
    let session = stateRef.current.session;
    if (!session) {
      try {
        const resp = await api.createSession({});
        session = resp.session;
        dispatch({ type: "session", session });
      } catch (e) {
        dispatch({ type: "ws", msg: { type: "error", code: "no_session", message: e.message } });
        return;
      }
    }
    send({ type: "resume_task", session_id: session.session_id, task_id: taskId });
  };

  const onNewSession = async () => {
    try {
      const resp = await api.createSession({});
      dispatch({ type: "session", session: resp.session });
    } catch (e) {
      dispatch({ type: "ws", msg: { type: "error", code: "busy", message: e.message } });
    }
  };

  return html`
    <div class="layout ${configOpen ? "" : "config-collapsed"}">
      <aside class="sidebar">
        <${Sidebar} state=${state} onResume=${onResume}
          onNewSession=${onNewSession} onRefresh=${refreshCheckpoints} />
      </aside>
      <main class="main">
        <div class="topbar">
          <span class="title">Manus Demo 调试台</span>
          <span class="badge ${state.connected ? "ok" : "err"}">
            ${state.connected ? "已连接" : "连接断开"}
          </span>
          ${state.session
            ? html`<span class="badge">会话 ${state.session.session_id}</span>`
            : html`<span class="badge">无会话</span>`}
          ${state.running && html`<span class="badge run">运行中 ${state.runId}</span>`}
          <span class="spacer"></span>
          <a class="badge" href="/traces" target="_blank">Trace 查看 ↗</a>
          <button onClick=${() => setConfigOpen(!configOpen)}>
            ⚙ ${configOpen ? "收起配置" : "展开配置"}
          </button>
        </div>
        <${Chat} state=${state} send=${send} onSendTask=${onSendTask} />
      </main>
      <aside class="config-drawer">
        <${ConfigPanel} running=${state.running} onApply=${onApply}
          sessionOverrides=${state.session && state.session.overrides} />
      </aside>
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
