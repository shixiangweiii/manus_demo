// WS 客户端：自动重连（指数退避）+ last_seq 增量重放
// WS client: auto-reconnect (exponential backoff) + last_seq replay.

const RETRY_MIN = 500;
const RETRY_MAX = 8000;

export function createWS({ onMessage, onStatus, getLastSeq }) {
  let socket = null;
  let retry = RETRY_MIN;
  let closed = false;
  const sendQueue = [];

  function connect() {
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws`);

    socket.onopen = () => {
      retry = RETRY_MIN;
      onStatus(true);
      // hello 触发服务端重放 + state 快照 / hello triggers replay + snapshot
      socket.send(JSON.stringify({ type: "hello", last_seq: getLastSeq() }));
      while (sendQueue.length) socket.send(sendQueue.shift());
    };

    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        /* 忽略坏消息 / ignore malformed */
      }
    };

    socket.onclose = () => {
      onStatus(false);
      if (!closed) {
        setTimeout(connect, retry);
        retry = Math.min(retry * 2, RETRY_MAX);
      }
    };

    socket.onerror = () => socket.close();
  }

  connect();

  return {
    send(msg) {
      const raw = JSON.stringify(msg);
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(raw);
      else sendQueue.push(raw); // 开启后补发 / flush after (re)open
    },
    close() {
      closed = true;
      if (socket) socket.close();
    },
  };
}
