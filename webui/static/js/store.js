// 全局状态 reducer / global state reducer
// WS 消息是唯一的运行态来源（多标签页天然同步）；seq 去重支持重放。
// WS messages are the single source of run state (multi-tab sync for
// free); seq-based dedupe supports replay after reconnect.
//
// todo_* / subagent_* 事件不逐条成卡，而是折叠进聚合状态：
// 每个 run 一张实时 TODO 卡、每个 subagent_id 一张归组卡。
// todo_*/subagent_* events fold into aggregate state instead of
// appending one card each: one live TODO card per run, one grouped
// card per subagent_id.

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
  subagents: {},         // subagentId → {events:[], status, runId}
};

const TODO_EVENTS = [
  "todo_list_initialized", "todo_list_update",
  "todo_start", "todo_complete", "todo_blocked", "todo_failed",
];

const SUBAGENT_GROUP_EVENTS = [
  "subagent_start", "subagent_iteration",
  "subagent_complete", "subagent_failed", "subagent_timed_out",
];

const TODO_STATUS_BY_EVENT = {
  todo_start: "in_progress",
  todo_complete: "completed",
  todo_blocked: "blocked",
  todo_failed: "failed",
};

const SUBAGENT_STATUS_BY_EVENT = {
  subagent_complete: "completed",
  subagent_failed: "failed",
  subagent_timed_out: "timed_out",
};

function pushChat(state, entry) {
  return { ...state, chat: [...state.chat, entry] };
}

// 归一化两种 TODO 列表 payload：emergent 是字符串摘要，goal-driven 是
// {total, todos:[str]} —— 见 emergent_planner.py / goal_driven_planner.py。
// Normalize both todo-list payload shapes (emergent: string summary;
// goal-driven: {total, todos:[str]}).
function normalizeTodoSummary(data) {
  if (typeof data === "string") return data;
  if (data && Array.isArray(data.todos)) return data.todos;
  if (data == null) return null;
  try { return JSON.stringify(data); } catch { return String(data); }
}

function foldTodoEvent(state, msg) {
  const runId = msg.run_id || "unknown";
  const prev = state.todoState[runId] || { items: {}, order: [], summary: null };
  const next = { ...prev, items: { ...prev.items }, order: [...prev.order] };

  if (msg.event === "todo_list_initialized" || msg.event === "todo_list_update") {
    next.summary = normalizeTodoSummary(msg.data);
  } else if (msg.data && typeof msg.data.todo === "object" && msg.data.todo) {
    const todo = msg.data.todo;
    const id = todo.id ?? next.order.length;
    if (!(id in next.items)) next.order.push(id);
    next.items[id] = {
      id,
      description: todo.description || "",
      status: TODO_STATUS_BY_EVENT[msg.event] || todo.status || "pending",
      retry: todo.retry_count || 0,
    };
  }

  let newState = { ...state, todoState: { ...state.todoState, [runId]: next } };
  if (!state.todoState[runId]) {
    newState = pushChat(newState, { kind: "todo_card", seq: msg.seq, runId });
  }
  return newState;
}

function foldSubagentEvent(state, msg) {
  const sid = msg.data.subagent_id;
  const prev = state.subagents[sid] || { events: [], status: "running", runId: msg.run_id };
  const next = {
    ...prev,
    events: [...prev.events, { event: msg.event, data: msg.data, seq: msg.seq }],
    status: SUBAGENT_STATUS_BY_EVENT[msg.event] || prev.status,
  };
  let newState = { ...state, subagents: { ...state.subagents, [sid]: next } };
  if (!state.subagents[sid]) {
    newState = pushChat(newState, {
      kind: "subagent_card", seq: msg.seq, subagentId: sid, runId: msg.run_id,
    });
  }
  return newState;
}

function handleAgentEvent(state, msg) {
  // 折叠聚合 / aggregate folds
  if (TODO_EVENTS.includes(msg.event)) return foldTodoEvent(state, msg);
  if (SUBAGENT_GROUP_EVENTS.includes(msg.event) && msg.data && msg.data.subagent_id) {
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
