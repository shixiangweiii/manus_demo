// REST 请求封装 / fetch helpers for /api/webui/*

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  let data = null;
  try { data = await resp.json(); } catch { /* 非 JSON 响应 / non-JSON */ }
  if (!resp.ok) {
    const err = new Error((data && (data.message || data.error)) || `HTTP ${resp.status}`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  getConfigSchema: () => request("GET", "/api/webui/config/schema"),
  getConfigValues: () => request("GET", "/api/webui/config/values"),
  createSession: (overrides) => request("POST", "/api/webui/session", { overrides }),
  getSession: () => request("GET", "/api/webui/session"),
  deleteSession: () => request("DELETE", "/api/webui/session"),
  listCheckpoints: () => request("GET", "/api/webui/checkpoints"),
  getStatus: () => request("GET", "/api/webui/status"),
};
