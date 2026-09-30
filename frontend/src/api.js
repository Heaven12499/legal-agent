// 后端 API：VITE_API_BASE 可覆盖（默认走 Vite dev proxy /api）
// M9 鉴权：token 存 localStorage，所有请求自动带 Authorization: Bearer，401 统一清 token
const BASE = import.meta.env.VITE_API_BASE || "/api";
const TOKEN_KEY = "auth_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) clearToken(); // 未登录 / token 失效
    const err = new Error(data.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ---- 认证 ----
export function register(username, password) {
  return request("/register", { method: "POST", body: JSON.stringify({ username, password }) });
}
export function login(username, password) {
  return request("/login", { method: "POST", body: JSON.stringify({ username, password }) });
}
export function me() {
  return request("/me");
}

export function sendChat(message, sessionId, contract = undefined, contractName = undefined) {
  const payload = { message, session_id: sessionId };
  // undefined = 普通追问，后端保留历史附件；null = 用户明确移除附件。
  if (contract !== undefined) {
    payload.contract = contract;
    if (contractName !== undefined) payload.contract_name = contractName;
  }
  return request("/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// 合同审查走持久化长任务：创建后轮询状态，页面请求不会一直占用 HTTP 连接。
export function createReview(message, sessionId, contract = undefined, contractName = undefined) {
  const payload = { message, session_id: sessionId };
  if (contract !== undefined) {
    payload.contract = contract;
    if (contractName !== undefined) payload.contract_name = contractName;
  }
  return request("/reviews", { method: "POST", body: JSON.stringify(payload) });
}

export function getReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}`);
}

export function retryReview(jobId) {
  return request(`/reviews/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
}

export async function sendReview(message, sessionId, contract = undefined, contractName = undefined) {
  let job = await createReview(message, sessionId, contract, contractName);
  // 10 分钟上限只是浏览器等待保护；任务仍在服务端继续执行，可凭 job_id 再查询。
  const deadline = Date.now() + 10 * 60 * 1000;
  while (Date.now() < deadline) {
    if (job.status === "succeeded") return job.result;
    if (job.status === "failed") throw new Error(job.error || "合同审查失败");
    await new Promise((resolve) => setTimeout(resolve, 800));
    job = await getReview(job.job_id);
  }
  throw new Error(`审查仍在后台执行，任务编号：${job.job_id}`);
}

export function listSessions() {
  return request("/chat/sessions");
}

export function getHistory(sessionId) {
  return request(`/chat/sessions/${encodeURIComponent(sessionId)}/history`);
}

export function removeSession(sessionId) {
  return request(`/chat/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

// 修改重发：截断到 fromId 之前，让位给重新发送的一轮
export function truncateHistory(sessionId, fromId) {
  return request(`/chat/sessions/${encodeURIComponent(sessionId)}/truncate`, {
    method: "POST",
    body: JSON.stringify({ from_id: fromId }),
  });
}

// 重新生成：后端删掉最后一条回答，对最后一条用户问题重跑 agent
export function regenerateChat(sessionId) {
  return request(`/chat/sessions/${encodeURIComponent(sessionId)}/regenerate`, { method: "POST" });
}

// 上传合同：multipart，不能带 JSON Content-Type，但仍带 Bearer
export function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(`${BASE}/upload`, { method: "POST", body: fd, headers }).then(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 401) clearToken();
      throw new Error(data.detail || res.statusText);
    }
    return data;
  });
}
