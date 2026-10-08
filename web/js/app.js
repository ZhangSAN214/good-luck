// 页面控制：状态、事件流、交互。渲染在 view.js，通信在 api.js。
import * as API from './api.js';
import {
  STATUS_LABELS,
  STEP_LABELS,
  castPanel,
  channelsPanel,
  esc,
  feedItems,
  flowPanel,
  historyPanel,
  legendHTML,
  money,
  reviewsPanel,
  stageHTML,
  usagePanel,
} from './view.js';

const $ = (q) => document.querySelector(q);
const RESTING = new Set(['completed', 'stopped', 'failed', 'awaiting_confirmation', 'paused']);

const S = {
  status: null, // /api/status
  budget: null,
  history: null,
  sid: null,
  session: null,
  tab: 'flow',
  mode: 'auto',
  preset: null,
  live: freshLive(),
  stream: null, // AbortController
  cardError: null,
  busy: false,
};

function freshLive() {
  return { table: null, step: null, act: '', speaking: new Set(), warnings: [] };
}

const prefix = () => S.status?.code_prefix || '组员';
const plans = () => S.status?.plans || {};

// --- 渲染 -----------------------------------------------------------------------

function render() {
  renderStage();
  renderFeed();
  renderPanel();
  renderHeader();
}

function renderStage() {
  $('#stage').innerHTML = stageHTML(S.session, S.live, prefix());
}

function renderHeader() {
  const sv = S.session;
  const b = S.budget;
  const parts = [`本场 ${money(sv ? sv.cost_usd : 0)}`];
  if (b?.month) parts.push(`本月 ${money(b.month.spent_usd)} / ${money(b.month.limit_usd)}`);
  if (b?.day) parts.push(`今日 ${money(b.day.spent_usd)} / ${money(b.day.limit_usd)}`);
  const meter = $('#meter');
  meter.textContent = parts.join(' · ');
  const warn = !!(b && ((b.month && b.month.warn) || (b.day && b.day.warn)));
  meter.classList.toggle('warn', warn);
  meter.title = warn ? '预算已用超过提醒比例' : '';
  const rv = $('#reveal');
  rv.disabled = !(sv && sv.can_reveal);
  rv.textContent = sv && sv.revealed ? '已揭晓' : '揭晓身份';
  $('#pstate').textContent = sv
    ? sv.running && sv.status !== 'awaiting_confirmation'
      ? '进行中'
      : STATUS_LABELS[sv.status] || sv.status
    : '空闲';
  const q = $('#question');
  q.hidden = !sv;
  if (sv) q.innerHTML = `<span class="tag">题目</span>${esc(sv.question)}`;
}

const rendered = new Map(); // key -> {html, el}

function renderFeed() {
  const list = $('#feed-list');
  const feed = $('#feed');
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
  if (!S.session) {
    rendered.clear();
    list.innerHTML = welcomeHTML();
  } else {
    const items = feedItems(S.session, {
      prefix: prefix(),
      plans: plans(),
      presets: S.status?.presets || {},
      threshold: S.status?.confirm_threshold_usd,
      live: S.live,
      cardError: S.cardError,
    });
    const els = [];
    const keep = new Set();
    for (const it of items) {
      let r = rendered.get(it.key);
      if (!r || r.html !== it.html) {
        const tpl = document.createElement('template');
        tpl.innerHTML = it.html.trim();
        r = { html: it.html, el: tpl.content.firstElementChild };
        rendered.set(it.key, r);
      } else {
        r.el.classList.add('seen');
      }
      keep.add(it.key);
      els.push(r.el);
    }
    for (const k of [...rendered.keys()]) if (!keep.has(k)) rendered.delete(k);
    list.replaceChildren(...els);
  }
  renderTyping();
  if (atBottom) feed.scrollTop = feed.scrollHeight;
}

function renderTyping() {
  const t = $('#typing');
  const who = [...S.live.speaking].map((c) => (c === '统' ? '统筹' : `${prefix()}${c}`));
  if (!S.session || !S.live.step || !who.length) {
    t.hidden = true;
    return;
  }
  t.hidden = false;
  t.innerHTML = `<i></i><i></i><i></i><span>${esc(who.join('、'))} 正在${esc(STEP_LABELS[S.live.step] || S.live.step)}…</span>`;
}

function welcomeHTML() {
  const th = money(S.status?.confirm_threshold_usd ?? 0.3);
  return `<div class="welcome"><h2>把题目交给圆桌</h2>
<ol><li>先判断难度：简单题由一个便宜模型直接回答；中等题开小圆桌；难题开旗舰圆桌。</li>
<li>组员匿名独立作答 → 互相评审 → 根据评审修订 → 统筹汇总共识与分歧。</li>
<li>小圆桌出现未裁定的分歧或把握低时会升级；预计花费超过 ${th} 先问你。</li>
<li>讨论结束后点「揭晓身份」，才显示各代号对应的模型。</li></ol>
<p>也可以在下方切换「预设」（省钱 / 均衡 / 最强）或「手动」勾选组员。你的选择永远优先于自动判断。</p></div>`;
}

function renderPanel() {
  document
    .querySelectorAll('#tabs button')
    .forEach((b) => b.setAttribute('aria-selected', String(b.dataset.t === S.tab)));
  const ctx = {
    plans: plans(),
    threshold: S.status?.confirm_threshold_usd,
    maxMembers: S.status?.max_members,
  };
  let h = '';
  if (S.tab === 'flow') h = flowPanel(S.session, S.live, ctx);
  if (S.tab === 'reviews') h = reviewsPanel(S.session, prefix());
  if (S.tab === 'usage') h = usagePanel(S.session, S.budget);
  if (S.tab === 'channels') h = channelsPanel(S.status);
  if (S.tab === 'history') h = historyPanel(S.history, S.sid);
  if (S.tab === 'cast') h = castPanel(S.session, prefix());
  $('#pbody').innerHTML = h;
}

// --- 提问区 ---------------------------------------------------------------------

function renderComposer() {
  document
    .querySelectorAll('#mode button')
    .forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === S.mode)));
  const presets = S.status?.presets || {};
  const seg = $('#preset');
  seg.hidden = S.mode !== 'preset';
  if (!S.preset) S.preset = 'balanced' in presets ? 'balanced' : Object.keys(presets)[0];
  seg.innerHTML = Object.entries(presets)
    .map(([k, label]) => `<button type="button" data-v="${esc(k)}" aria-pressed="${k === S.preset}">${esc(label)}</button>`)
    .join('');
  $('#manual').hidden = S.mode !== 'manual';
  const hints = {
    auto: `先按规则判断难度，判断不了再请最便宜的规划员；预计超过 ${money(S.status?.confirm_threshold_usd ?? 0.3)} 先问你`,
    preset: '按预设方案执行，不调用规划员',
    manual: `勾选 2–${S.status?.max_members ?? 4} 个组员（勾 1 个即单人快答）；统筹不能兼任组员`,
  };
  $('#mode-hint').textContent = hints[S.mode];
}

function renderPicks() {
  const models = S.status?.models || [];
  const tierLabel = { flagship: '旗舰', budget: '便宜档' };
  $('#picks').innerHTML = models
    .map(
      (m) =>
        `<label class="pick${m.available ? '' : ' off'}"><input type="checkbox" value="${esc(m.id)}"${m.available ? '' : ' disabled'}>${esc(m.id)}<span class="tier">${esc(tierLabel[m.tier] || m.tier)} · $${m.price.input}/$${m.price.output}</span></label>`,
    )
    .join('');
  $('#coordinator').innerHTML =
    '<option value="">自动选择</option>' +
    models
      .filter((m) => m.available)
      .map((m) => `<option value="${esc(m.id)}">${esc(m.id)}</option>`)
      .join('');
}

function formError(text) {
  const e = $('#formerr');
  e.hidden = !text;
  e.textContent = text || '';
}

async function submit(ev) {
  ev.preventDefault();
  const question = $('#ask').value.trim();
  if (!question) {
    formError('请先输入题目');
    return;
  }
  const body = { question, mode: S.mode };
  if (S.mode === 'preset') body.preset = S.preset;
  if (S.mode === 'manual') {
    body.members = [...document.querySelectorAll('#picks input:checked')].map((i) => i.value);
    if (!body.members.length) {
      formError('手动模式请至少勾选一个组员');
      return;
    }
    const coord = $('#coordinator').value;
    if (coord) body.coordinator = coord;
  }
  formError('');
  $('#submit').disabled = true;
  try {
    const { session_id } = await API.createSession(body);
    $('#ask').value = '';
    await open(session_id);
  } catch (e) {
    formError(e.message);
  } finally {
    $('#submit').disabled = false;
  }
}

// --- 会话与事件流 -----------------------------------------------------------------

async function open(sid) {
  stopStream();
  S.sid = sid;
  S.live = freshLive();
  S.cardError = null;
  rendered.clear();
  try {
    history.replaceState(null, '', `#s=${sid}`);
  } catch {
    /* 忽略 */
  }
  await refresh();
  connect();
}

function stopStream() {
  if (S.stream) S.stream.abort();
  S.stream = null;
}

function connect() {
  const sid = S.sid;
  if (!sid) return;
  stopStream();
  const ctrl = new AbortController();
  S.stream = ctrl;
  API.streamEvents(sid, (e) => onEvent(sid, e), ctrl.signal)
    .catch((err) => {
      if (err.name !== 'AbortError') console.warn('事件流中断', err);
    })
    .finally(() => {
      if (S.stream === ctrl) S.stream = null;
      if (S.sid === sid) refresh(true);
    });
}

let timer = null;
function scheduleRefresh() {
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    refresh();
  }, 120);
}

async function refresh(all = false) {
  const sid = S.sid;
  if (!sid) {
    render();
    return;
  }
  try {
    const sv = await API.getSession(sid);
    if (S.sid !== sid) return;
    S.session = sv;
    if (!sv.running) {
      S.live.step = null;
      S.live.speaking.clear();
      S.live.act = '';
    }
  } catch (e) {
    if (e.status === 404) {
      S.sid = null;
      S.session = null;
    }
  }
  if (all) await Promise.all([loadBudget(), loadHistory()]);
  render();
}

function onEvent(sid, e) {
  if (sid !== S.sid) return;
  const L = S.live;
  switch (e.type) {
    case 'snapshot':
      break;
    case 'routed':
      L.act = `${plans()[e.data.plan] || e.data.plan} · 预计 ${money(e.data.estimate_usd)}`;
      break;
    case 'table_started':
      L.table = e.table_no;
      L.act = '';
      break;
    case 'step_started': {
      L.table = e.table_no;
      L.step = e.step;
      const t = S.session?.tables?.find((x) => x.table_no === e.table_no);
      const dropped = new Set(
        (S.session?.outputs || []).filter((o) => o.kind === 'dropout' && o.table_no === e.table_no).map((o) => o.code),
      );
      L.speaking = new Set();
      if (e.step === 'synthesize') L.speaking.add('统');
      else if (e.step !== 'reveal') (t?.codes || []).filter((c) => !dropped.has(c)).forEach((c) => L.speaking.add(c));
      L.act = `${STEP_LABELS[e.step] || e.step}中`;
      break;
    }
    case 'call_done':
    case 'call_failed':
      L.speaking.delete(e.code || '统');
      break;
    case 'step_finished':
      L.step = null;
      L.speaking = new Set();
      loadBudget();
      break;
    case 'escalating':
      L.act = `升级中：${e.data.reason || ''}`;
      break;
    case 'budget_warning':
      L.warnings.push(e.data.message);
      break;
    case 'checkpoint':
      L.step = null;
      L.speaking = new Set();
      L.act = '等待你决定';
      break;
    case 'state':
      if (RESTING.has(e.status)) {
        L.step = null;
        L.speaking = new Set();
      }
      break;
    default:
      break;
  }
  renderStage();
  renderTyping();
  scheduleRefresh();
}

async function answer(cpId, key) {
  if (S.busy || !S.sid) return;
  S.busy = true;
  S.cardError = null;
  document.querySelectorAll(`[data-cp="${cpId}"]`).forEach((b) => (b.disabled = true));
  try {
    await API.respond(S.sid, key);
    S.live.act = '';
    connect();
  } catch (e) {
    S.cardError = e.message;
    await refresh();
  } finally {
    S.busy = false;
  }
}

async function doResume() {
  if (!S.sid) return;
  try {
    await API.resume(S.sid);
    connect();
  } catch (e) {
    formError(e.message);
  }
}

async function doReveal() {
  if (!S.sid) return;
  $('#reveal').disabled = true;
  try {
    S.session = await API.reveal(S.sid);
    rendered.clear();
    await loadHistory();
    render();
  } catch (e) {
    formError(e.message);
    renderHeader();
  }
}

async function loadBudget() {
  try {
    S.budget = await API.getBudget();
  } catch {
    /* 下次再试 */
  }
  renderHeader();
  if (S.tab === 'usage') renderPanel();
}

async function loadHistory() {
  try {
    S.history = await API.listSessions();
  } catch {
    /* 下次再试 */
  }
  if (S.tab === 'history') renderPanel();
}

function newQuestion() {
  stopStream();
  S.sid = null;
  S.session = null;
  S.live = freshLive();
  try {
    history.replaceState(null, '', location.pathname);
  } catch {
    /* 忽略 */
  }
  render();
  $('#ask').focus();
}

// --- 启动 -----------------------------------------------------------------------

function bind() {
  $('#composer').addEventListener('submit', submit);
  $('#ask').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('#composer').requestSubmit();
  });
  $('#mode').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.mode = b.dataset.v;
    formError('');
    renderComposer();
  });
  $('#preset').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.preset = b.dataset.v;
    renderComposer();
  });
  $('#feed').addEventListener('click', (e) => {
    const opt = e.target.closest('[data-opt]');
    if (opt && !opt.disabled) answer(Number(opt.dataset.cp), opt.dataset.opt);
    if (e.target.closest('[data-act="resume"]')) doResume();
  });
  $('#tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    S.tab = b.dataset.t;
    if (S.tab === 'history') loadHistory();
    if (S.tab === 'usage') loadBudget();
    renderPanel();
  });
  $('#pbody').addEventListener('click', (e) => {
    const row = e.target.closest('[data-sid]');
    if (row) open(row.dataset.sid);
  });
  $('#reveal').addEventListener('click', doReveal);
  $('#new').addEventListener('click', newQuestion);
  $('#toggle').addEventListener('click', (e) => {
    const main = $('#main');
    main.classList.toggle('collapsed');
    const open_ = !main.classList.contains('collapsed');
    e.currentTarget.setAttribute('aria-expanded', String(open_));
    e.currentTarget.textContent = open_ ? '收起面板' : '展开面板';
  });
}

async function init() {
  bind();
  $('#legend').innerHTML = legendHTML();
  try {
    S.status = await API.getStatus();
    S.budget = S.status.budget;
  } catch (e) {
    formError(`无法连接服务：${e.message}`);
  }
  renderComposer();
  renderPicks();
  render();
  loadHistory();
  const m = location.hash.match(/s=([0-9a-f]+)/);
  if (m) await open(m[1]);
}

init();
