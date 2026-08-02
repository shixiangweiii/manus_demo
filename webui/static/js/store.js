// 全局状态 reducer / global state reducer
// WS 消息是唯一的运行态来源（多标签页天然同步）；seq 去重支持重放。
// WS messages are the single source of run state (multi-tab sync for
// free); seq-based dedupe supports replay after reconnect.
//
// todo_updated / subagent_* events fold into aggregate state instead of
// appending one card each: one live todo snapshot per run, one grouped
// card per (run_id, subagent_id).

export const initialState = {
  connected: false,
  lastSeq: 0,
  session: null,
  running: false,
  runId: null,
  pendingPrompt: null,   // {promptId, question, timeoutSeconds, receivedAt}
  chat: [],              // 交错条目 / interleaved entries
  checkpoints: [],
  todoState: {},         // runId → {items:{id→item}, order:[], summary}
  subagents: {},         // runId::subagentId → {events:[], status, runId, subagentId}
};

const TODO_EVENTS = ["todo_updated"];

const SUBAGENT_STATUS_BY_EVENT = {
  subagent_start: "running",
  subagent_complete: "completed",
  subagent_failed: "failed",
  subagent_timed_out: "timed_out",
  subagent_cancelled: "cancelled",
};

function pushChat(state, entry) {
  return { ...state, chat: [...state.chat, entry] };
}

function todoSnapshot(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data.todos)) return data.todos;
  if (Array.isArray(data.items)) return data.items;
  if (Array.isArray(data.snapshot)) return data.snapshot;
  return [];
}

function foldTodoEvent(state, msg) {
  const runId = msg.run_id || "unknown";
  const items = {};
  const order = [];
  todoSnapshot(msg.data).forEach((todo, index) => {
    const value = typeof todo === "string" ? { description: todo } : (todo || {});
    const id = value.id ?? index;
    order.push(id);
    items[id] = {
      id,
      description: value.content || value.description || value.task || value.title || "",
      status: value.status || "pending",
    };
  });
  const next = {
    items,
    order,
    summary: msg.data && typeof msg.data === "object" ? (msg.data.reason || null) : null,
  };

  let newState = { ...state, todoState: { ...state.todoState, [runId]: next } };
  if (!state.todoState[runId]) {
    newState = pushChat(newState, { kind: "todo_card", seq: msg.seq, runId });
  }
  return newState;
}

function foldSubagentEvent(state, msg) {
  const runId = msg.run_id || "unknown";
  const sid = msg.data.subagent_id;
  const stateKey = `${runId}::${sid}`;
  const prev = state.subagents[stateKey] || {
    events: [], status: "running", runId, subagentId: sid,
  };
  const next = {
    ...prev,
    events: [...prev.events, { event: msg.event, data: msg.data, seq: msg.seq }],
    status: SUBAGENT_STATUS_BY_EVENT[msg.event] || prev.status,
  };
  let newState = { ...state, subagents: { ...state.subagents, [stateKey]: next } };
  if (!state.subagents[stateKey]) {
    newState = pushChat(newState, {
      kind: "subagent_card", seq: msg.seq, subagentId: stateKey, runId,
    });
  }
  return newState;
}

function handleAgentEvent(state, msg) {
  // 折叠聚合 / aggregate folds
  if (TODO_EVENTS.includes(msg.event)) return foldTodoEvent(state, msg);
  // Every child-namespaced event belongs to the child card, including events
  // emitted internally by skill, memory, MCP, or future optional tools.
  if (msg.data && msg.data.subagent_id) {
    return foldSubagentEvent(state, msg);
  }

  const entry = {
    kind: "event",
    seq: msg.seq,
    runId: msg.run_id,
    event: msg.event,
    data: msg.data,
    ts: msg.ts,
    truncated: msg.truncated,
  };
  let next = pushChat(state, entry);

  // HITL 未决提问跟踪 / pending HITL prompt tracking
  if (msg.event === "ask_user_prompt" && msg.data) {
    next = {
      ...next,
      pendingPrompt: {
        promptId: msg.data.prompt_id,
        question: msg.data.question,
        timeoutSeconds: msg.data.timeout_seconds,
        receivedAt: Date.now(),
      },
    };
  } else if (["ask_user_response", "ask_user_timeout", "ask_user_cancelled"].includes(msg.event)) {
    next = { ...next, pendingPrompt: null };
  }
  return next;
}

export function reducer(state, action) {
  if (action.type === "connected") {
    return { ...state, connected: action.value };
  }
  if (action.type === "session") {
    return { ...state, session: action.session };
  }
  if (action.type === "checkpoints") {
    return { ...state, checkpoints: action.tasks };
  }
  if (action.type !== "ws") return state;

  const msg = action.msg;

  // seq 去重（重放期间跳过已渲染的消息）/ seq dedupe during replay
  if (typeof msg.seq === "number") {
    if (msg.seq <= state.lastSeq && msg.type !== "state") return state;
    state = { ...state, lastSeq: Math.max(state.lastSeq, msg.seq) };
  }

  switch (msg.type) {
    case "state":
      return {
        ...state,
        session: msg.session,
        running: msg.running,
        runId: msg.run_id,
        pendingPrompt: msg.pending_prompt
          ? {
              promptId: msg.pending_prompt.prompt_id,
              question: msg.pending_prompt.question,
              timeoutSeconds: msg.pending_prompt.timeout_seconds,
              receivedAt: Date.now(),
            }
          : null,
        lastSeq: Math.max(state.lastSeq, msg.seq || 0),
      };

    case "run_started":
      return pushChat(
        { ...state, running: true, runId: msg.run_id },
        {
          kind: "run_started",
          seq: msg.seq,
          runId: msg.run_id,
          task: msg.task,
          runKind: msg.kind,
          overrides: msg.overrides || {},
          ts: msg.ts,
        }
      );

    case "agent_event":
      return handleAgentEvent(state, msg);

    case "run_finished":
      return pushChat(
        { ...state, running: false, runId: null, pendingPrompt: null },
        {
          kind: "run_finished",
          seq: msg.seq,
          runId: msg.run_id,
          status: msg.status,
          answer: msg.answer,
          error: msg.error,
          stopReason: msg.stop_reason,
          trace: msg.trace,
          ts: msg.ts,
        }
      );

    case "error":
      return pushChat(state, {
        kind: "error",
        code: msg.code,
        message: msg.message,
        ts: Date.now() / 1000,
      });

    case "pong":
      return state;

    default:
      return state;
  }
}
