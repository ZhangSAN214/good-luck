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
export const postChat = (text, room = 'main', media = [], quote = null) => api('/api/chat', { method: 'POST', body: { text, room, media, quote } });
export const patChat = (member, room = 'main') => api('/api/chat/pat', { method: 'POST', body: { member, room } });
export const forwardChat = (id, room = 'main') => api('/api/chat/forward', { method: 'POST', body: { id, room } });
export const recallChat = (id, room = 'main') => api('/api/chat/recall', { method: 'POST', body: { id, room } });
export const clearChat = (room = 'main') => api(`/api/chat?room=${encodeURIComponent(room)}`, { method: 'DELETE' });
export const getChatRooms = () => api('/api/chat/rooms');
export const chatPersonas = () => api('/api/chat/personas');
export const setChatPersona = (id, persona) => api(`/api/chat/personas/${encodeURIComponent(id)}`, { method: 'PUT', body: { persona } });
export const memberVoices = () => api('/api/chat/voices');
// 成员专属表情包：列表、画一张（新画的待审）、审（approved / rejected / pending）
export const memberStickers = () => api('/api/stickers');
export const drawSticker = (id, name) => api(`/api/stickers/${encodeURIComponent(id)}/${encodeURIComponent(name)}/draw`, { method: 'POST' });
export const reviewSticker = (id, name, status) =>
  api(`/api/stickers/${encodeURIComponent(id)}/${encodeURIComponent(name)}/review`, { method: 'POST', body: { status } });
// 用成员的固定音色念一句（text 省略时念试听句）：返回音频 Blob
export async function memberVoice(id, text = null) {
  const r = await fetch(`/api/chat/voices/${encodeURIComponent(id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (r.status === 401 && location.pathname !== '/login.html') location.assign('/login.html');
  if (!r.ok) {
    let data = null;
    try {
      data = await r.json();
    } catch {
      data = null;
    }
    throw new ApiError(errorText(data, r.status), r.status);
  }
  return r.blob();
}
// 关于你（成员系统 P8）：8 位共用的用户画像
export const aboutYou = () => api('/api/chat/about');
export const aboutAdd = (text, domain) => api('/api/chat/about/facts', { method: 'POST', body: { text, domain } });
export const aboutEdit = (id, text) => api(`/api/chat/about/facts/${id}`, { method: 'PATCH', body: { text } });
export const aboutDel = (id) => api(`/api/chat/about/facts/${id}`, { method: 'DELETE' });
export const aboutReject = (id) => api(`/api/chat/about/facts/${id}/reject`, { method: 'POST' });
export const aboutLock = (id, locked) => api(`/api/chat/about/facts/${id}/lock`, { method: 'POST', body: { locked } });
export const aboutDomain = (domain, on) => api('/api/chat/about/domains', { method: 'POST', body: { domain, on } });
export const aboutForget = (i) => api(`/api/chat/about/rejected/${i}`, { method: 'DELETE' });
export const aboutClear = (rejected = false) => api(`/api/chat/about${rejected ? '?rejected=true' : ''}`, { method: 'DELETE' });
export const aboutExportUrl = (fmt) => `/api/chat/about/export?fmt=${encodeURIComponent(fmt)}`;
export const memberProfiles = () => api('/api/chat/profiles');
export const memberProfile = (id) => api(`/api/chat/profiles/${encodeURIComponent(id)}`);
export const changeMemberProfile = (id, patch, reason = '') => api(`/api/chat/profiles/${encodeURIComponent(id)}`, { method: 'PATCH', body: { patch, reason } });
export const lockMemberProfile = (id, path, locked = true) => api(`/api/chat/profiles/${encodeURIComponent(id)}/lock`, { method: 'POST', body: { path, locked } });
export const revertMemberProfile = (id, version) => api(`/api/chat/profiles/${encodeURIComponent(id)}/revert`, { method: 'POST', body: { version } });
export const clearMemberProfile = (id) => api(`/api/chat/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const importMemberCards = (text, mapping = null, dry_run = false) => api('/api/chat/profiles/import', { method: 'POST', body: { text, mapping, dry_run } });
export const chatMemories = (id) => api(`/api/chat/memories${id ? `?member=${encodeURIComponent(id)}` : ''}`);
export const addChatMemory = (member, text) => api('/api/chat/memories', { method: 'POST', body: { member, text } });
export const editChatMemory = (mid, text) => api(`/api/chat/memories/${mid}`, { method: 'PATCH', body: { text } });
export const delChatMemory = (mid) => api(`/api/chat/memories/${mid}`, { method: 'DELETE' });
export const clearChatMemories = (id) => api(`/api/chat/memories${id ? `?member=${encodeURIComponent(id)}` : ''}`, { method: 'DELETE' });
export const tidyChatMemories = () => api('/api/chat/memories/tidy', { method: 'POST' });
// 记忆与联想（P2）：记忆件、挂着的事、习惯、翻日记
export const memoryItems = (id, q = '') => api(`/api/chat/memory/${encodeURIComponent(id)}/items${q ? `?q=${encodeURIComponent(q)}` : ''}`);
export const editMemoryItem = (iid, text) => api(`/api/chat/memory/items/${iid}`, { method: 'PATCH', body: { text } });
export const delMemoryItem = (iid) => api(`/api/chat/memory/items/${iid}`, { method: 'DELETE' });
export const dropHanging = (hid) => api(`/api/chat/memory/hanging/${hid}`, { method: 'DELETE' });
export const readDiary = (id, q) => api(`/api/chat/memory/${encodeURIComponent(id)}/diary?q=${encodeURIComponent(q)}`);
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
export const lifeReports = () => api('/api/life/reports');
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
export const getDirectory = () => api('/api/models/directory');
export const refreshDirectory = () => api('/api/models/directory/refresh', { method: 'POST' });
export const getPluginBoard = () => api('/api/plugins/board');
export const getChangelog = () => api('/api/changelog');
export const getCalendar = () => api('/api/calendar');
export const addCalendarDate = (body) => api('/api/calendar/dates', { method: 'POST', body });
export const removeCalendarDate = (id) => api(`/api/calendar/dates/${encodeURIComponent(id)}`, { method: 'DELETE' });
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
export const getChannelPriority = () => api('/api/channels/priority');
export const setChannelPriority = (body) => api('/api/channels/priority', { method: 'PUT', body });
export const getUsageBalance = (refresh = false) => api(`/api/usage/balance${refresh ? '?refresh=1' : ''}`);

// 局域网访问（平板）：只能在电脑本机上调用
export const lanStatus = () => api('/api/lan');
export const lanSetPassword = (password) => api('/api/lan/password', { method: 'POST', body: { password } });
export const lanPair = (base) => api('/api/lan/pair', { method: 'POST', body: { base } });
export const lanSignOut = () => api('/api/lan/signout', { method: 'POST' });
export const lanLogout = () => api('/api/lan/logout', { method: 'POST' });

// 投屏陪伴（docs/WATCH.md）：开始 / 预览只能在电脑本机上；平板上能聊天和停止
export const watchStatus = (devices = false) => api(`/api/watch${devices ? '?devices=true' : ''}`);
export const watchStart = (source, watchers, consent) =>
  api('/api/watch/start', { method: 'POST', body: { source, watchers, consent } });
export const watchStop = () => api('/api/watch/stop', { method: 'POST' });
export const watchQuiet = (quiet) => api('/api/watch/quiet', { method: 'POST', body: { quiet } });
export const watchMessages = (after = 0) => api(`/api/watch/messages?after=${after}`);
export const watchSay = (text) => api('/api/watch/say', { method: 'POST', body: { text } });
export const watchScrcpy = (source) => api('/api/watch/scrcpy', { method: 'POST', body: { source } });
export const watchFrameUrl = () => `/api/watch/frame?t=${Date.now()}`;
export const watchAdbPair = (host, port, code) =>
  api('/api/watch/adb/pair', { method: 'POST', body: { host, port, code } });
export const watchAdbConnect = (host, port) =>
  api('/api/watch/adb/connect', { method: 'POST', body: { host, port } });
export const wanderStatus = () => api('/api/wander');
export const wanderStart = (member, site) => api('/api/wander/start', { method: 'POST', body: { member, site } });
export const wanderStop = () => api('/api/wander/stop', { method: 'POST' });
export const wanderLog = (member, after = 0) =>
  api(`/api/wander/log?member=${encodeURIComponent(member)}&after=${after}`);
export const wanderFrameUrl = () => `/api/wander/frame?t=${Date.now()}`;
// 工作台（dsh 边车，docs/DSH.md）
export const dshStatus = () => api('/api/dsh');
export const dshStart = () => api('/api/dsh/start', { method: 'POST' });
export const dshStop = () => api('/api/dsh/stop', { method: 'POST' });
export const dshCheck = () => api('/api/dsh/check', { method: 'POST' });

// 超级电脑：几位编程助手平级、按分数派活（docs/SUPERCOMPUTER.md）
export const superStatus = () => api('/api/super');
export const superSubmit = (text) => api('/api/super/tasks', { method: 'POST', body: { text } });
export const superCancel = () => api('/api/super/cancel', { method: 'POST' });
export const superTask = (id) => api(`/api/super/tasks/${encodeURIComponent(id)}`);
export const superLimit = (worker, limit) =>
  api(`/api/super/workers/${encodeURIComponent(worker)}/limit`, { method: 'PUT', body: { limit } });
export const superWake = (worker) =>
  api(`/api/super/workers/${encodeURIComponent(worker)}/wake`, { method: 'POST' });
