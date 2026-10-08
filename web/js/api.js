// 与后端通信：只用 HTTP 接口和 SSE（项目规则 §2.7）。

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const r = await fetch(path, init);
  let data = null;
  try {
    data = await r.json();
  } catch {
    data = null;
  }
  if (!r.ok) throw new ApiError(errorText(data, r.status), r.status);
  return data;
}

function errorText(data, status) {
  const d = data && data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) return d.map((x) => x.msg).join('；');
  return `请求失败（${status}）`;
}

export const getStatus = () => api('/api/status');
export const getBudget = () => api('/api/budget');
export const listSessions = () => api('/api/sessions?limit=50');
export const getContributions = () => api('/api/contributions');
export const getSession = (id) => api(`/api/sessions/${id}`);
export const createSession = (body) => api('/api/sessions', { method: 'POST', body });
export const respond = (id, response, note) =>
  api(`/api/sessions/${id}/respond`, { method: 'POST', body: { response, note } });
export const resume = (id) => api(`/api/sessions/${id}/resume`, { method: 'POST' });
export const reveal = (id) => api(`/api/sessions/${id}/reveal`, { method: 'POST' });

// 订阅事件流。服务端在讨论停下来时发出 "state" 并关闭连接；这里不自动重连，
// 需要继续时（回复确认卡片、恢复）由调用方重新订阅。
export async function streamEvents(id, onEvent, signal) {
  const r = await fetch(`/api/sessions/${id}/events`, {
    signal,
    headers: { Accept: 'text/event-stream' },
  });
  if (!r.ok || !r.body) throw new ApiError(`事件流连接失败（${r.status}）`, r.status);
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block
        .split('\n')
        .filter((l) => l.startsWith('data: '))
        .map((l) => l.slice(6))
        .join('\n');
      if (data) onEvent(JSON.parse(data));
    }
  }
}
