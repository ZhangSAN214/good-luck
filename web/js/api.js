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
  // 平板上登录过期（或电脑上点了「让所有平板退出」）：回到登录页
  if (r.status === 401 && location.pathname !== '/login.html') location.assign('/login.html');
  if (!r.ok) throw new ApiError(errorText(data, r.status), r.status);
  return data;
}

function errorText(data, status) {
  const d = data && data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) return d.map((x) => x.msg).join('；');
  return `请求失败（${status}）`;
}

export const carry = (sid) => api(`/api/sessions/${sid}/carry`);
export const exportOptions = () => api('/api/export/options');
export const exportSave = (sid, body) => api(`/api/sessions/${sid}/export/save`, { method: 'POST', body });
export const estimate = (body) => api('/api/estimate', { method: 'POST', body });
export const filePreview = (sid, fid) => api(`/api/sessions/${sid}/files/${fid}/preview`);
export const fileUrl = (sid, fid, inline = false) =>
  `/api/sessions/${sid}/files/${fid}${inline ? '?inline=1' : ''}`;

// 上传一个文件：请求体是原始字节，文件名放在查询参数里。
export async function upload(file) {
  const r = await fetch(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  });
  let data = null;
  try {
    data = await r.json();
  } catch {
    data = null;
  }
  if (!r.ok) throw new ApiError(errorText(data, r.status), r.status);
  return data;
}

export const getStatus = () => api('/api/status');
export const listClips = () => api('/api/clips');
export const openClipPage = (url) => api('/api/clips/open', { method: 'POST', body: { url } });
export const searchClips = (query) => api('/api/clips/search', { method: 'POST', body: { query } });
export const saveClip = (clip) => api('/api/clips', { method: 'POST', body: clip });
export const clipText = (id) => api(`/api/clips/${encodeURIComponent(id)}`);
export const delClip = (id) => api(`/api/clips/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const attachClip = (id) => api(`/api/clips/${encodeURIComponent(id)}/attach`, { method: 'POST' });
export const getBudget = () => api('/api/budget');
export const members = () => api('/api/members');
export const standee = () => api('/api/standee');
export const listSessions = () => api('/api/sessions?limit=50');
export const getContributions = () => api('/api/contributions');
export const getOffice = () => api('/api/office');
export const resetPerformance = () => api('/api/office/performance/reset', { method: 'POST' });
export const getChat = (after = 0, q = '', room = 'main') =>
  api(`/api/chat?room=${encodeURIComponent(room)}&after=${after}${q ? `&q=${encodeURIComponent(q)}` : ''}`);
export const postChat = (text, room = 'main', media = []) => api('/api/chat', { method: 'POST', body: { text, room, media } });
export const clearChat = (room = 'main') => api(`/api/chat?room=${encodeURIComponent(room)}`, { method: 'DELETE' });
export const getChatRooms = () => api('/api/chat/rooms');
export const chatPersonas = () => api('/api/chat/personas');
export const setChatPersona = (id, persona) => api(`/api/chat/personas/${encodeURIComponent(id)}`, { method: 'PUT', body: { persona } });
export const chatMemories = (id) => api(`/api/chat/memories${id ? `?member=${encodeURIComponent(id)}` : ''}`);
export const addChatMemory = (member, text) => api('/api/chat/memories', { method: 'POST', body: { member, text } });
export const editChatMemory = (mid, text) => api(`/api/chat/memories/${mid}`, { method: 'PATCH', body: { text } });
export const delChatMemory = (mid) => api(`/api/chat/memories/${mid}`, { method: 'DELETE' });
export const clearChatMemories = (id) => api(`/api/chat/memories${id ? `?member=${encodeURIComponent(id)}` : ''}`, { method: 'DELETE' });
export const tidyChatMemories = () => api('/api/chat/memories/tidy', { method: 'POST' });
export const getMoments = (author, before) => {
  const q = new URLSearchParams();
  if (author) q.set('author', author);
  if (before) q.set('before', before);
  return api(`/api/moments${q.toString() ? `?${q}` : ''}`);
};
export const postMoment = (text, media = []) => api('/api/moments', { method: 'POST', body: { text, media } });
export const likeMoment = (id) => api(`/api/moments/${id}/like`, { method: 'POST' });
export const commentMoment = (id, text, reply_to) => api(`/api/moments/${id}/comments`, { method: 'POST', body: { text, reply_to } });
export const delMoment = (id) => api(`/api/moments/${id}`, { method: 'DELETE' });
export const delMomentComment = (cid) => api(`/api/moments/comments/${cid}`, { method: 'DELETE' });
export const chatFeeds = () => api('/api/chat/feeds');
export const setChatFeeds = (feeds) => api('/api/chat/feeds', { method: 'PUT', body: { feeds } });
export const refreshChatFeeds = () => api('/api/chat/feeds/refresh', { method: 'POST' });
export const getEvolution = () => api('/api/evolution');
export const evolutionMeet = (kind = null, theme = null) =>
  api('/api/evolution/meetings', { method: 'POST', body: { kind, theme } });
export const evolutionTheme = (theme) => api('/api/evolution/next-theme', { method: 'PUT', body: { theme } });
export const evolutionExport = (sid) => api(`/api/evolution/meetings/${encodeURIComponent(sid)}/export`, { method: 'POST' });
export const evolutionMerge = (sid, idea) =>
  api(`/api/evolution/meetings/${encodeURIComponent(sid)}/ideas/${encodeURIComponent(idea)}/merge`, { method: 'POST' });
export const evolutionPlugins = (sid) => api(`/api/evolution/meetings/${encodeURIComponent(sid)}/plugins`, { method: 'POST' });
export const evolutionScout =() => api('/api/evolution/scout', { method: 'POST' });
export const evolutionDecide = (sid, idea, decision) =>
  api(`/api/evolution/meetings/${encodeURIComponent(sid)}/ideas/${encodeURIComponent(idea)}`, { method: 'POST', body: { decision } });
export const chatHandoff = (text) => api('/api/chat/handoff', { method: 'POST', body: { text } });
// 「细看」：按精细模式重新看这条消息里的图片 / 视频 / 语音，再请一位成员回应
export const chatLook = (id, room = 'main') => api(`/api/chat/messages/${id}/look?room=${encodeURIComponent(room)}`, { method: 'POST' });
export const getApartment = (hour, weekday) => api(`/api/apartment?hour=${hour}&weekday=${weekday}`);
export const getCandidates = () => api('/api/models/candidates');
export const getPluginBoard = () => api('/api/plugins/board');
export const getChangelog = () => api('/api/changelog');
export const mailView = () => api('/api/mail');
export const mailSettings = (body) => api('/api/mail/settings', { method: 'PUT', body });
export const mailLogin = () => api('/api/mail/login', { method: 'POST' });
export const mailRefresh = (manual = true) => api('/api/mail/refresh', { method: 'POST', body: { manual } });
export const mailMessage = (id) => api(`/api/mail/messages/${encodeURIComponent(id)}`);
export const mailReminder = (id, action) => api(`/api/mail/reminders/${id}`, { method: 'POST', body: { action } });
export const mailLogout = () => api('/api/mail/logout', { method: 'POST' });
export const mailClear = () => api('/api/mail/cache', { method: 'DELETE' });
// 邮件 id 里有斜杠，放在请求体里
export const mailMark = (id, mark) => api('/api/mail/mark', { method: 'POST', body: { id, mark } });
export const mailCategory = (id, category) => api('/api/mail/category', { method: 'POST', body: { id, category } });
export const mailImages = (id) => api('/api/mail/images', { method: 'POST', body: { id } });
export const mailAI = (kind, id = null, again = false) => api(`/api/mail/ai/${kind}`, { method: 'POST', body: { id, again } });
export const refreshCandidates = () => api('/api/models/candidates/refresh', { method: 'POST' });
export const getPayslip = (id) => api(`/api/sessions/${id}/payslip`);
export const getSession = (id) => api(`/api/sessions/${id}`);
export const createSession = (body) => api('/api/sessions', { method: 'POST', body });
export const respond = (id, response, note) =>
  api(`/api/sessions/${id}/respond`, { method: 'POST', body: { response, note } });
export const addNote = (id, text) =>
  api(`/api/sessions/${id}/notes`, { method: 'POST', body: { text } });
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
// 余额与用量：本机账本汇总（range = day / week / month / all）与渠道账户余额（只读）
export const getUsage = (range = 'month') => api(`/api/usage?range=${encodeURIComponent(range)}`);
export const getUsageBalance = (refresh = false) => api(`/api/usage/balance${refresh ? '?refresh=1' : ''}`);

// 局域网访问（平板）：只能在电脑本机上调用
export const lanStatus = () => api('/api/lan');
export const lanSetPassword = (password) => api('/api/lan/password', { method: 'POST', body: { password } });
export const lanPair = (base) => api('/api/lan/pair', { method: 'POST', body: { base } });
export const lanSignOut = () => api('/api/lan/signout', { method: 'POST' });
export const lanLogout = () => api('/api/lan/logout', { method: 'POST' });
