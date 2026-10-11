// 把服务端返回的会话渲染成 HTML。这里只做展示，不发请求。
// 匿名讨论在揭晓前，服务端不返回模型和渠道，界面只用塔罗牌代号（愚者、魔术师…）和「统筹」；
// 匿名关闭时代号就是「昵称·模式」（鲸鱼娘·全力），统筹显示为「昵称·模式（统筹）」；
// 匿名关闭时服务端直接给出模型，界面在代号旁显示。

export const STEP_LABELS = {
  plan: '规划',
  answer: '作答',
  review: '互评',
  revise: '修订',
  synthesize: '汇总',
  reveal: '揭晓',
  decompose: '拆分子任务',
  volunteer: '自荐',
  assign: '分配',
  work: '完成子任务',
  cross_review: '交叉审查',
  rework: '修改',
  merge: '合并',
  media: '生成媒体',
  style: '风格规范',
  style_gate: '风格校验',
};
export const MEDIA_LABELS = { image: '图片', speech: '语音', video: '视频' };
const JOB_STATE = {
  submitted: ['已提交', ''],
  pending: ['排队中', 'wait'],
  running: ['生成中', 'wait'],
  completed: ['完成', 'ok'],
  failed: ['失败', 'bad'],
  timeout: ['超时', 'bad'],
};
// 由统筹执行的步骤
export const COORD_STEPS = new Set(['synthesize', 'decompose', 'assign', 'merge']);
// 协同流水线的子任务类型名（来自 /api/status 的 collab_kinds，不写死在前端）
let KIND_LABELS_CFG = {};
export function setKindLabels(map) {
  KIND_LABELS_CFG = map || {};
}
const kindLabel = (k) => (k ? KIND_LABELS_CFG[k] || k : '');
const kindPill = (k) => (k ? `<span class="pill kind">${esc(kindLabel(k))}</span> ` : '');

const STANCE = { want: ['想做', 'ok'], can: ['可以做', ''], unfit: ['不适合', 'bad'] };
const LEVEL = { full: ['全部采用', 'ok'], partial: ['部分采用', 'wait'], none: ['未采用', ''] };
const LENGTH = { simple: '短', medium: '中等', hard: '长' };
const SOURCE = { rule: '规则判断', model: '规划员判断', default: '默认' };
const VERDICT = {
  correct: ['正确', 'ok'],
  partially_correct: ['部分正确', 'wait'],
  incorrect: ['有误', 'bad'],
  unclear: ['无法判断', ''],
};
const CONFIDENCE = { high: ['高', 'ok'], medium: ['中', 'wait'], low: ['低', 'bad'] };
const CARD_TITLES = {
  cost: '花费确认',
  escalation: '是否升级',
  budget: '预算',
  members: '组员不足',
  overrun: '花费超出预估',
  media: '媒体生成',
};
export const STATUS_LABELS = {
  created: '准备中',
  running: '进行中',
  awaiting_confirmation: '等待你决定',
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
};
const KIND_LABELS = { aggregator: '聚合平台', direct: '官方直连', local: '本地' };

export function esc(t) {
  return String(t ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}

// 模型输出是 Markdown：支持 **加粗** 和表格（单元格里的 <br> 变成换行），其余原样保留（换行由 CSS 保留）
const BR = /&lt;br\s*\/?&gt;/gi;
const bold = (h) => h.replace(/(^|[^*\w])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![*\w])/g, '$1<b>$2</b>');
const splitRow = (line) => {
  let t = line.trim();
  if (t.startsWith('|')) t = t.slice(1);
  if (t.endsWith('|')) t = t.slice(0, -1);
  return t.split('|').map((c) => c.trim());
};
const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
const isSep = (l) => isRow(l) && splitRow(l).every((c) => /^:?-{2,}:?$/.test(c));

export function md(t) {
  const lines = String(t ?? '').split('\n');
  const out = [];
  for (let i = 0; i < lines.length; i += 1) {
    // 表头 + 分隔行 + 若干数据行 = 一张表
    if (isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = splitRow(lines[i]);
      let j = i + 2;
      const rows = [];
      while (j < lines.length && isRow(lines[j])) rows.push(splitRow(lines[j++]));
      const cell = (c) => bold(esc(c)).replace(BR, '<br>');
      const th = head.map((c) => `<th>${cell(c)}</th>`).join('');
      const tr = rows.map((r) => `<tr>${head.map((_, k) => `<td>${cell(r[k] ?? '')}</td>`).join('')}</tr>`).join('');
      out.push(`<table class="mdt"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`);
      i = j - 1;
    } else {
      out.push(bold(esc(lines[i])).replace(BR, '\n'));
    }
  }
  // 表格是块级元素，前后的换行不再重复显示
  return out.join('\n').replace(/\n?(<table[\s\S]*?<\/table>)\n?/g, '$1');
}

// 重置时间：UTC + 浏览器本地时间（时区与 UTC 相同时不重复）
export function resetText(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  const p = (n) => String(n).padStart(2, '0');
  const utc = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
  const off = -d.getTimezoneOffset();
  if (off === 0) return utc;
  const sign = off >= 0 ? '+' : '-';
  const hh = Math.floor(Math.abs(off) / 60);
  const mm = Math.abs(off) % 60;
  const local = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  return `${utc}（本地时间 ${local}，UTC${sign}${hh}${mm ? ':' + p(mm) : ''}）`;
}

export function money(v) {
  if (v === null || v === undefined) return '—';
  return Math.abs(v) >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}

function parse(content) {
  try {
    return JSON.parse(content);
  } catch {
    return null;
  }
}

function tag(text, cls = '') {
  return `<span class="tagp ${cls}">${esc(text)}</span>`;
}

const TOOL_LABELS = { python: '运行代码', write_file: '写文件', generate_image: '生成图片', search: '联网搜索', fetch: '读取网页' };
const TOOL_STATUS = { ok: ['成功', 'ok'], error: ['出错', 'bad'], timeout: ['超时', 'bad'], rejected: ['被拒绝', 'bad'], limit: ['额度用完', 'wait'] };
const KIND_ICONS = { image: '图片', text: '文本', code: '代码', table: '表格', document: '文档' };

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function safeUrl(u) {
  return /^https?:\/\//i.test(u || '') ? u : null;
}

/** 来源编号 → 链接：按成员在本桌内连续编号（S1、S2…），key 为 `桌:代号`。 */
export function sourceIndex(sv) {
  const idx = {};
  for (const t of sv?.tool_calls || []) {
    if (t.tool !== 'search') continue;
    const m = (idx[`${t.table_no}:${t.code}`] ||= {});
    for (const src of t.input?.sources || []) m[src.id] = src;
  }
  return idx;
}

/** md() 之后把 [S1] 换成可点击的来源链接（只认该成员检索到的来源，且只允许 http/https）。 */
export function cite(html, sources) {
  if (!sources) return html;
  return html.replace(/\[S(\d+)\]/g, (all, n) => {
    const src = sources[`S${n}`];
    const url = src && safeUrl(src.url);
    if (!url) return all;
    return `<a class="cite" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(src.title || url)}">[S${n}]</a>`;
  });
}

// --- 名称 --------------------------------------------------------------------------

export class Names {
  constructor(session, prefix = '组员') {
    this.prefix = prefix;
    this.sid = session?.id;
    this.revealed = !!session?.revealed;
    this.seats = session?.seats || [];
    // 协同模式：每桌的子任务标题与成果编号（W1… → [子任务, 代号]），与服务端的编号规则一致
    this.subtasks = {};
    this.items = {};
    for (const o of session?.outputs || []) {
      const d = o.kind === 'subtasks' || o.kind === 'assignment' ? parse(o.content) : null;
      if (!d) continue;
      if (o.kind === 'subtasks') {
        this.subtasks[o.table_no] = Object.fromEntries(d.subtasks.map((x) => [x.id, x.title]));
      } else {
        const items = {};
        for (const a of d.assignments) for (const c of a.members) items[`W${Object.keys(items).length + 1}`] = [a.subtask, c];
        this.items[o.table_no] = items;
      }
    }
  }
  subtask(tableNo, id) {
    const title = (this.subtasks[tableNo] || {})[id];
    return title ? `${id}「${title}」` : id;
  }
  // 互评对象：讨论模式是代号，协同模式是成果编号
  target(tableNo, target) {
    const item = (this.items[tableNo] || {})[target];
    return item ? `${target}（${item[0]} · ${this.member(item[1])}）` : this.member(target);
  }
  member(code) {
    return `${this.prefix}${code}`;
  }
  model(tableNo, role, code) {
    if (!this.revealed) return null;
    const s = this.seats.find(
      (x) => x.table_no === tableNo && x.role === role && (role !== 'member' || x.code === code),
    );
    return s ? s.model_id : null;
  }
  // 匿名关闭时服务端给统筹的称呼（昵称·模式（统筹））；匿名时没有，只叫「统筹」
  coordinatorName(tableNo) {
    const s = this.seats.find((x) => x.table_no === tableNo && x.role === 'coordinator');
    return (s && s.label) || '统筹';
  }
  // 发言者：头像字、名称、揭晓后的模型
  speaker(tableNo, code) {
    if (code) return { ch: [...String(code)][0] || '?', name: this.member(code), ai: this.model(tableNo, 'member', code) };
    return { ch: '统', name: this.coordinatorName(tableNo), ai: this.model(tableNo, 'coordinator'), coord: true };
  }
}

function avatar(sp, extra = '') {
  return `<div class="av ${sp.coord ? 'coord' : ''} ${extra}">${esc(sp.ch)}</div>`;
}

function msg(sp, kind, body) {
  const ai = sp.ai ? `<span class="ai">${esc(sp.ai)}</span>` : '';
  return `<div class="msg">${avatar(sp)}<div class="body"><div class="who"><b>${esc(sp.name)}</b>${ai}<span class="kind">${esc(kind)}</span>${sp.tag || ''}</div>${body}</div></div>`;
}

function sys(role, text, tone = '', extra = '') {
  return `<div class="sys ${tone}"><span class="role">${esc(role)}</span><span>${text}</span>${extra}</div>`;
}

// --- 过程区 ------------------------------------------------------------------------

export function feedItems(sv, ctx) {
  const names = new Names(sv, ctx.prefix);
  const items = [];
  const push = (key, html) => items.push({ key, html });
  const r = sv.routing;
  const tables = sv.tables || [];
  const cps = sv.checkpoints || [];
  const placed = new Set();
  const cpAt = (pred) => {
    for (const cp of cps) {
      if (placed.has(cp.id) || !pred(cp.card.details || {}, cp)) continue;
      placed.add(cp.id);
      push(`cp${cp.id}:${cp.status}`, card(cp, ctx, names));
    }
  };

  push('q', `<div class="msg me"><div class="av me">你</div><div class="body"><div class="who"><b>你</b><span class="kind">${esc(modeText(sv, ctx))}</span></div><div class="bubble">${esc(sv.question)}</div></div></div>`);
  cpAt((d) => d.stage === 'routing');

  if (r) {
    const label = (k) => (k === 'custom' ? ctx.customLabel : ctx.plans[k] || k);
    const opts = Object.entries(r.options || {})
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${label(k)} ${money(v)}`)
      .join(' · ');
    const t0 = (sv.tables || [])[0];
    const people = t0 ? ` · ${t0.codes.length} 位组员 + 统筹` : '';
    const absent = r.absent && r.absent.length ? ` · ${r.absent.length} 个模型缺席（无可用渠道）` : '';
    push('route', sys('规划', `档位：<b>${esc(label(r.plan))}</b>${esc(people)}${esc(absent)} · 预计 ${money(r.estimated_cost_usd)}${opts ? `<br>各档位预估：${esc(opts)}` : ''}`));
  }

  const outputs = sv.outputs || [];
  const lazy = lazyKeys(outputs);
  const sources = sourceIndex(sv);
  const toolRows = sv.tool_calls || [];
  const fileRows = (sv.files || []).filter((f) => f.latest);
  const MAIN = new Set(['answer', 'revision', 'work', 'rework', 'merge']);
  for (const t of tables) {
    cpAt((d, cp) => cp.kind === 'escalation' && d.table_no === t.table_no);
    if (t.table_no === 0) cpAt((d, cp) => cp.kind === 'cost' && d.table_no === 0);
    if (t.status === 'pending' && !outputs.some((o) => o.table_no === t.table_no)) continue;
    const label = tables.length > 1 ? `第 ${t.table_no + 1} 桌 · ${t.plan_label}` : t.plan_label;
    push(`t${t.table_no}`, `<div class="phase">${esc(label)} · ${t.codes.length} 位组员</div>`);
    if (t.table_no > 0 && t.escalation_reason) {
      push(`esc${t.table_no}`, sys('统筹', `升级到「${esc(t.plan_label)}」：${esc(t.escalation_reason)}`, 'bad'));
    }
    for (const step of t.pipeline) {
      // 风格校验的记录跟在生成它的那一步后面（phase 1 = 完成子任务，2 = 按审查修改）
      const gateStep = (o) => {
        if (o.step !== 'style_gate') return null;
        return (parse(o.content)?.phase ?? 1) === 2 ? 'rework' : 'work';
      };
      const outs = outputs.filter((o) => o.table_no === t.table_no && (o.step === step || gateStep(o) === step));
      const live = ctx.live.table === t.table_no && ctx.live.step === step;
      cpAt((d) => d.table_no === t.table_no && d.step === step && !(step === 'media' && d.key));
      const mediaJobs = step === 'media' ? (sv.media || []).filter((j) => j.table_no === t.table_no && j.step === 'media') : [];
      const mediaCards = step === 'media' ? cps.filter((c) => c.card.details?.key?.startsWith(`media:${t.table_no}:`)) : [];
      if (!outs.length && !live && !mediaJobs.length && !mediaCards.length) continue;
      if (step === 'reveal') continue;
      push(`s${t.table_no}:${step}`, `<div class="phase">${esc(STEP_LABELS[step] || step)}</div>`);
      if (step === 'media') {
        const roundOf = (o) => parse(o.content)?.round ?? 0;
        const rounds = [...new Set([...mediaJobs.map((j) => j.round), ...outs.map(roundOf), ...mediaCards.map((c) => Number(c.card.details.key.split(':')[2]))])].sort((a, b) => a - b);
        for (const r of rounds) {
          cpAt((d) => d.key === `media:${t.table_no}:${r}`);
          for (const j of mediaJobs.filter((x) => x.round === r)) push(`mj${j.id}:${j.state}`, mediaJobCard(j, sv, names));
          const mine = outs.filter((o) => roundOf(o) === r);
          for (const kind of ['media_review', 'media_decision']) {
            mine.filter((o) => o.kind === kind).forEach((o, i) => {
              const html = output(o, names, lazy, null);
              if (html) push(`mo${t.table_no}:${r}:${kind}:${o.code || ''}:${i}`, html);
            });
          }
        }
        continue;
      }
      const shown = new Set();
      const showTools = (code) => {
        const k = code || '';
        if (shown.has(k)) return;
        shown.add(k);
        const mine = toolRows.filter((x) => x.table_no === t.table_no && x.step === step && (x.code || '') === k);
        for (const x of mine) push(`tc${x.id}`, toolItem(x, names, sv.id));
      };
      outs.forEach((o, i) => {
        if (MAIN.has(o.kind)) showTools(o.code);
        const html = output(o, names, lazy, sources[`${t.table_no}:${o.code}`]);
        if (html) push(`o${t.table_no}:${step}:${o.kind}:${o.code || ''}:${i}`, html);
        if (MAIN.has(o.kind)) {
          const mine = fileRows.filter((f) => f.table_no === t.table_no && f.step === step && (f.code || '') === (o.code || ''));
          if (mine.length) push(`f${t.table_no}:${step}:${o.code || ''}:${mine.map((f) => f.id).join()}`, fileBar(mine, sv.id));
        }
      });
      for (const x of toolRows) if (x.table_no === t.table_no && x.step === step) showTools(x.code);
    }
  }
  cpAt(() => true);

  for (const [i, w] of (ctx.live.warnings || []).entries()) {
    push(`w${i}`, sys('预算', esc(w), 'bad'));
  }
  push(`end:${sv.status}:${sv.revealed}:${sv.running}`, ending(sv, ctx));
  return items.filter((x) => x.html);
}

function modeText(sv, ctx) {
  const tier = sv.tier === 'custom' ? ctx.customLabel : ctx.plans[sv.tier] || sv.tier;
  const parts = [sv.workflow === 'collab' ? '协同' : '讨论', tier];
  if (sv.media_kind) parts.push(`输出${MEDIA_LABELS[sv.media_kind] || sv.media_kind}`);
  if (sv.anonymous) parts.push('匿名');
  return parts.join(' · ');
}

// 被标记为敷衍的产出：`桌:步骤:代号`
// 返回 Map：键 → 判定原因（重做后仍不合格的原因），`has()` 即是否被标记
export function lazyKeys(outputs) {
  const keys = new Map();
  for (const o of outputs) {
    if (o.kind !== 'effort') continue;
    const d = parse(o.content);
    if (d && d.status === 'lazy') keys.set(`${o.table_no}:${o.step}:${o.code}`, (d.final_reasons || d.reasons || []).join('；'));
  }
  return keys;
}

// 标记旁直接显示判定原因（含字数 / 门槛、相似度等数值），悬停可看完整文字
function lazyTag(reason) {
  const why = reason ? ` <span class="why" title="${esc(reason)}">${esc(reason)}</span>` : '';
  return `<span class="tagp bad" title="${esc(reason || '重做后仍没有通过实质内容检查')}">敷衍</span>${why}`;
}

function effort(o, sp, data) {
  const what = STEP_LABELS[o.step] || o.step;
  const first = (data.reasons || []).join('；');
  if (data.status === 'truncated') {
    // 原因形如「输出被长度上限截断（内容过短（…））」：句子里已经说了截断，括号里只放原问题
    const why = (data.reasons || []).map((r) => r.replace(/^输出被长度上限截断（([\s\S]*)）$/, '$1')).join('；');
    return sys('检查', `${esc(sp.name)} 的${esc(what)}输出被长度上限截断，未判为敷衍（${esc(why)}）`);
  }
  if (data.status === 'lazy') {
    const final = (data.final_reasons || data.reasons || []).join('；');
    const redo = data.redone ? '重做后仍不合格，' : '';
    return sys('检查', `${esc(sp.name)} 的${esc(what)}${redo}标记为敷衍：${esc(final)}`, 'bad');
  }
  return sys('检查', `${esc(sp.name)} 的${esc(what)}没有实质内容（${esc(first)}），已打回重做，重做后合格`);
}

function output(o, names, lazy = new Map(), sources = null) {
  const sp = names.speaker(o.table_no, o.code);
  const lazyWhy = lazy.get(`${o.table_no}:${o.step}:${o.code}`);
  if (lazyWhy !== undefined) sp.tag = lazyTag(lazyWhy);
  if (o.kind === 'answer') return msg(sp, '作答', `<div class="bubble">${cite(md(o.content), sources)}</div>`);
  if (o.kind === 'media_review' || o.kind === 'media_decision') return mediaOutput(o, sp);
  if (o.kind === 'effort') {
    const d = parse(o.content);
    return d ? effort(o, sp, d) : '';
  }
  if (o.kind === 'dropout') {
    return sys('统筹', `${esc(sp.name)} 退出：调用失败，已有的内容仍参与汇总`, 'bad');
  }
  const data = parse(o.content);
  if (!data) return msg(sp, o.kind, `<div class="bubble">${md(o.content)}</div>`);
  if (o.kind === 'style_spec') return styleCard(o, data);
  if (o.kind === 'style_gate') return styleGateLine(sp, data, names);
  if (o.kind === 'style_redraw') {
    return msg(sp, `按风格意见改提示词 ${data.subtask}`, `<details class="more"><summary>修改后的提示词（第 ${esc(data.attempt)} 次重画）</summary><div>${md(data.prompt)}</div></details>`);
  }
  if (o.kind === 'review') return msg(sp, '互评', review(data, names, o.table_no));
  const collab = collabOutput(o, sp, data, names, sources);
  if (collab !== null) return collab;
  if (o.kind === 'revision') {
    if (data.skipped) return sys('统筹', `${esc(sp.name)} 没有收到有效评审，沿用原答案`);
    const resp = data.responses
      ? `<details class="more"><summary>对审阅意见的回应</summary><div>${md(data.responses)}</div></details>`
      : '';
    return msg(sp, '修订', `<div class="bubble">${cite(md(data.answer), sources)}</div>${resp}`);
  }
  if (o.kind === 'synthesis') return synthesis(o.table_no, data, names);
  return '';
}

/** 交接：下游子任务开始时，谁把什么交给了谁（只含代号；上游生成的图片显示缩略图）。 */
function handoffLine(h, names, sid) {
  const thumbs = (h.files || [])
    .filter((f) => /\.(png|jpe?g|gif|webp)$/i.test(f.name))
    .map((f) => `<img class="thumb" alt="${esc(f.name)}" src="/api/sessions/${esc(sid)}/files/${esc(f.id)}?inline=1">`)
    .join('');
  const text = `${esc(names.member(h.from_code))} 把〈${esc(h.gives)}〉交给 ${esc(names.member(h.to_code))}<span class="meta">（${esc(h.from_subtask)} → ${esc(h.to_subtask)}）</span>`;
  return sys('交接', text, '', thumbs ? `<span class="thumbs">${thumbs}</span>` : '');
}

function collabOutput(o, sp, data, names, sources = null) {
  const t = o.table_no;
  const li = (xs) => `<ul>${xs.join('')}</ul>`;
  if (o.kind === 'subtasks') {
    const items = data.subtasks.map(
      (x) =>
        `<li>${kindPill(x.kind)}<b>${esc(x.id)} ${md(x.title)}</b>${x.requirements ? `：${md(x.requirements)}` : ''}${x.acceptance ? `<div class="meta">验收：${md(x.acceptance)}</div>` : ''}${x.depends_on && x.depends_on.length ? `<div class="meta">依赖：${esc(x.depends_on.map((d) => (x.gives && x.gives[d] ? `${d}（${x.gives[d]}）` : d)).join('、'))}</div>` : ''}</li>`,
    );
    const info = data.info || {};
    const note = data.pipeline
      ? info.source === 'template'
        ? `<div class="meta">统筹两次都没有拆出合格的流水线，已按模板「${esc(info.template_label || info.template)}」生成</div>`
        : info.source === 'retry'
          ? '<div class="meta">第一次拆分不符合流水线规则，已按指出的问题重拆</div>'
          : ''
      : data.degraded
        ? '<div class="meta">拆分不可用，整道题作为一个子任务由全员各自完成</div>'
        : '';
    return msg(sp, `拆分为 ${data.subtasks.length} 个子任务`, `<div class="bubble">${li(items)}${note}</div>`);
  }
  if (o.kind === 'volunteer') {
    const prefs = data.preferences.map((p) => {
      const [st, sc] = STANCE[p.stance] || [p.stance, ''];
      return `<li>${esc(names.subtask(t, p.subtask))} ${tag(st, sc)}${p.reason ? ` ${md(p.reason)}` : ''}</li>`;
    });
    const strengths = data.strengths ? `<div>擅长：${md(data.strengths)}</div>` : '';
    return msg(sp, '自荐', `<div class="bubble">${strengths}${li(prefs)}</div>`);
  }
  if (o.kind === 'assignment') {
    const rows = data.assignments.map(
      (a) => `<li>${esc(names.subtask(t, a.subtask))}：${esc(a.members.map((c) => names.member(c)).join('、'))}</li>`,
    );
    const why = data.rationale ? `<div class="meta">理由：${md(data.rationale)}</div>` : '';
    const fixed = data.repaired && data.repaired.length
      ? `<details class="more"><summary>分配不符合规则，已由代码补齐</summary><div>${esc(data.repaired.join('\n'))}</div></details>`
      : '';
    return msg(sp, '分配', `<div class="bubble">${li(rows)}${why}</div>${fixed}`);
  }
  if (o.kind === 'work') {
    return msg(sp, `完成 ${names.subtask(t, data.subtask)}`, `<div class="bubble">${cite(md(data.text), sources)}</div>${mediaFoot(data.media)}`);
  }
  if (o.kind === 'handoff') {
    return handoffLine(data, names, names.sid);
  }
  if (o.kind === 'cross_review') return msg(sp, '交叉审查', review(data, names, t));
  if (o.kind === 'rework') {
    const what = names.subtask(t, data.subtask);
    if (data.skipped) return sys('统筹', `${esc(sp.name)} 的 ${esc(what)} 没有收到有效审查，沿用原成果`);
    const resp = data.responses
      ? `<details class="more"><summary>对审查意见的回应</summary><div>${md(data.responses)}</div></details>`
      : '';
    return msg(sp, `修改 ${what}`, `<div class="bubble">${cite(md(data.answer), sources)}</div>${resp}${mediaFoot(data.media)}`);
  }
  if (o.kind === 'merge') return merged(t, data, names);
  return null;
}

function toolSummary(x) {
  const i = x.input || {};
  if (x.tool === 'python') return `${(i.code || '').split('\n').length} 行代码`;
  if (x.tool === 'write_file') return i.path || '';
  if (x.tool === 'generate_image') return i.path || '';
  if (x.tool === 'search') return i.query || '';
  if (x.tool === 'fetch') return i.source || '';
  return '';
}

/** 一次工具调用：代码 / 搜索词折叠显示，展开后是输出、来源。 */
export function toolItem(x, names, sid) {
  const sp = names.speaker(x.table_no, x.code);
  const [st, sc] = TOOL_STATUS[x.status] || [x.status, ''];
  const i = x.input || {};
  let body = '';
  if (x.tool === 'python') body += `<div class="k">代码</div><pre>${esc(i.code)}</pre>`;
  else if (x.tool === 'write_file') body += `<div class="k">写入 ${esc(i.path)}（${esc(i.chars ?? '')} 字）</div>`;
  else if (x.tool === 'generate_image') body += `<div class="k">画面描述</div><pre>${esc(i.description)}</pre>`;
  else if (x.tool === 'search') body += `<div class="k">搜索词</div><pre>${esc(i.query)}</pre>`;
  else if (x.tool === 'fetch') body += `<div class="k">读取来源 ${esc(i.source)}${i.url ? ` · ${esc(i.url)}` : ''}</div>`;
  const srcs = (i.sources || [])
    .map((s) => {
      const u = safeUrl(s.url);
      return `<li>${esc(s.id)} ${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(s.title || u)}</a>` : esc(s.title)}</li>`;
    })
    .join('');
  if (srcs) body += `<div class="k">来源</div><ul class="src">${srcs}</ul>`;
  if (x.output) body += `<div class="k">输出</div><pre>${esc(x.output)}</pre>`;
  const dur = x.duration_s ? ` · ${x.duration_s.toFixed(1)} 秒` : '';
  return `<details class="tool" data-tool="${esc(x.tool)}"><summary><span class="tn">${esc(TOOL_LABELS[x.tool] || x.tool)}</span><span>${esc(sp.name)} · 第 ${esc(x.round)} 轮${esc(dur)}</span>${tag(st, sc)}<span class="meta">${esc(toolSummary(x))}</span></summary>${body}</details>`;
}

const PLAYABLE_AUDIO = /^audio\/(mpeg|wav|ogg)$/;
const PLAYABLE_VIDEO = /^video\/(mp4|webm)$/;

/** 图片直接显示；音频、视频在页面里播放（只播放服务端允许内联的类型，其余只给下载）。 */
export function mediaPlayer(f, sid) {
  const src = `/api/sessions/${esc(sid)}/files/${esc(f.id)}?inline=1`;
  if (f.kind === 'image' && /^image\/(png|jpeg|gif|webp)$/.test(f.mime)) {
    return `<img class="thumb" data-preview="${esc(f.id)}" alt="${esc(f.path)}" loading="lazy" src="${src}">`;
  }
  if (PLAYABLE_AUDIO.test(f.mime)) {
    return `<audio class="player" controls preload="metadata" src="${src}" aria-label="${esc(f.path)}"></audio>`;
  }
  if (PLAYABLE_VIDEO.test(f.mime)) {
    return `<video class="player" controls preload="metadata" src="${src}" aria-label="${esc(f.path)}"></video>`;
  }
  return '';
}

/** 成员生成的文件：预览 / 下载；图片显示缩略图，音频 / 视频可以直接播放。 */
export function fileBar(files, sid) {
  const chips = files.map((f) => fileChip(f, sid)).join('');
  const players = files.map((f) => mediaPlayer(f, sid)).join('');
  return `<div class="filebar-wrap" style="margin-left:44px"><div class="filebar">${chips}</div>${players}</div>`;
}

export function fileChip(f, sid, withAuthor = '') {
  const name = f.path.split('/').pop();
  return `<span class="fchip" data-file="${esc(f.id)}">${esc(withAuthor)}<b>${esc(name)}</b><span class="meta">${esc(KIND_ICONS[f.kind] || f.kind)} · ${bytes(f.size)}</span><button type="button" data-preview="${esc(f.id)}">预览</button><a href="/api/sessions/${esc(sid)}/files/${esc(f.id)}" download>下载</a></span>`;
}

/** 预览对话框内容。HTML、SVG 只作为源代码文本显示，从不渲染。 */
export function previewHTML(sid, p) {
  const f = p.file;
  const head = `<div class="vh"><b>${esc(f.path)}</b><span class="meta">${bytes(f.size)}</span><a class="btn sm" href="/api/sessions/${esc(sid)}/files/${esc(f.id)}" download>下载</a><button class="btn sm" type="button" data-close>关闭</button></div>`;
  let body;
  if (p.type === 'image') body = `<img alt="${esc(f.path)}" src="/api/sessions/${esc(sid)}/files/${esc(f.id)}?inline=1">`;
  else if (p.type === 'audio' || p.type === 'video') body = mediaPlayer({ ...f, kind: p.type === 'audio' ? 'audio' : 'video' }, sid) || '<p>这种格式不能在页面中播放，请下载。</p>';
  else if (p.type === 'text') body = `<pre>${esc(p.text)}</pre>${p.truncated ? '<p class="meta">内容较长，已截断；完整内容请下载。</p>' : ''}`;
  else if (p.type === 'table') {
    body = (p.sheets || [])
      .map((sh) => `<h4>${esc(sh.name)}</h4><table><tbody>${sh.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`)
      .join('');
    if (p.truncated) body += '<p class="meta">只显示前几行。</p>';
  } else body = `<p>${esc(p.reason || '这个文件无法预览，请下载查看。')}</p>`;
  return `${head}<div class="vb">${body}</div>`;
}

/** 协同模式媒体子任务：成员写的是生成提示词，这里注明生成的轮次、花费或失败原因。 */
function mediaFoot(m) {
  if (!m) return '';
  const what = MEDIA_LABELS[m.kind] || m.kind;
  if (!m.ok) return `<div class="meta bad">${esc(what)}生成失败（第 ${esc(m.round)} 轮）：${esc(m.error || '')}</div>`;
  const refs = m.references ? ` · 参考图 ${esc(m.references)} 张` : '';
  const warn = m.warning ? `<div class="meta bad">${esc(m.warning)}</div>` : '';
  let style = '';
  if (m.style && m.style.passed !== null && m.style.passed !== undefined) {
    style = m.style.passed
      ? `<div class="meta">${tag('风格校验通过', 'ok')}${m.style.attempts > 1 ? ` 重画 ${esc(m.style.attempts - 1)} 次后通过` : ''}</div>`
      : `<div class="meta">${tag('风格未通过', 'bad')} 重画 ${esc(Math.max(0, m.style.attempts - 1))} 次后仍不符合风格清单，保留最后一版</div>`;
  }
  return `<div class="meta">已生成${esc(what)}（第 ${esc(m.round)} 轮 · ${money(m.cost_usd)}${refs}）</div>${warn}${style}`;
}

/** 风格规范卡片：规范、可逐条检查的风格清单、来源与提示。 */
function styleCard(o, d) {
  const how = d.text_only ? '来自图片的文字版' : `${(d.extractors || []).length} 位成员看了原图`;
  const warn = d.warning ? `<div class="meta bad">${esc(d.warning)}</div>` : '';
  const items = (d.checklist || []).map((x) => `<li>${md(x)}</li>`).join('');
  const list = items ? `<div class="k">风格清单（${d.checklist.length} 条）</div><ol class="checklist">${items}</ol>` : '';
  return sys(
    '风格',
    `风格规范已提取（${esc(how)}）<details class="more stylecard" data-style="${esc(o.table_no)}"><summary>查看风格规范</summary><div class="md">${md(d.spec)}</div>${list}</details>${warn}`,
    d.warning ? 'bad' : '',
  );
}

/** 一次风格校验：对照风格清单逐条符合 / 不符合。 */
function styleGateLine(sp, d, names) {
  const checks = (d.checks || [])
    .map((c) => `<li>${c.ok ? tag('符合', 'ok') : tag('不符合', 'bad')} ${md(c.item)}${c.note ? `<span class="meta"> ${md(c.note)}</span>` : ''}</li>`)
    .join('');
  const who = (d.reviewers || []).map((c) => names.member(c)).join('、');
  const verdict = d.passed ? tag('通过', 'ok') : d.final ? tag('未通过（保留最后一版）', 'bad') : tag('不通过，退回重画', 'wait');
  return msg(
    sp,
    `风格校验 ${d.subtask} · 第 ${d.attempt} 次`,
    `<div class="bubble">${verdict} <span class="meta">${esc(who)} 对照风格清单判定</span>${checks ? `<ul class="gate">${checks}</ul>` : ''}</div>`,
  );
}

/** 讨论模式 media 步骤中的评审与统筹决定。 */
function mediaOutput(o, sp) {
  const d = parse(o.content);
  if (!d) return '';
  if (o.kind === 'media_review') {
    if (d.degraded || !d.valid) return sys('检查', `${esc(sp.name)} 的评审格式有误或没有写明依据，未采用`, 'bad');
    const problems = (d.problems || [])
      .map((p) => `<li><b>${md(p.what)}</b>${p.fix ? ` → ${md(p.fix)}` : ''}</li>`)
      .join('');
    const verdict = d.satisfied ? tag('可以交付', 'ok') : tag('需要改进', 'wait');
    const checks = (d.checks || [])
      .map((c) => `<li>${c.ok ? tag('符合', 'ok') : tag('不符合', 'bad')} ${md(c.item)}${c.note ? `<span class="meta"> ${md(c.note)}</span>` : ''}</li>`)
      .join('');
    const list = checks ? `<div class="k">对照风格清单</div><ul class="gate">${checks}</ul>` : '';
    return msg(sp, `评审第 ${d.round} 轮`, `<div class="bubble">${verdict}${list}${problems ? `<ul>${problems}</ul>` : ''}${d.checked ? `<div class="meta">检查了：${md(d.checked)}</div>` : ''}</div>`);
  }
  const text = d.satisfied ? '认为可以交付，不再重新生成' : `决定修改提示词重新生成：${md(d.reason)}`;
  const next = d.satisfied ? '' : `<details class="more"><summary>修改后的提示词</summary><div>${md(d.prompt)}</div></details>`;
  return msg(sp, `决定（第 ${d.round} 轮）`, `<div class="bubble">${text}</div>${next}`);
}

/** 一次媒体生成：第几轮、状态、花费、提示词，以及可以直接显示 / 播放的成果。 */
export function mediaJobCard(j, sv, names) {
  const [st, sc] = JOB_STATE[j.state] || [j.state, ''];
  const file = j.file_id ? (sv.files || []).find((f) => f.id === j.file_id) : null;
  const who = j.code ? names.member(j.code) : '统筹';
  const where = j.subtask ? `${who} · ${j.subtask}` : STEP_LABELS[j.step] || j.step;
  const model = j.model_id ? `<span class="ai">${esc(j.model_id)}${j.channel ? ` · ${esc(j.channel)}` : ''}</span>` : '';
  const players = file ? mediaPlayer(file, sv.id) : '';
  const dl = file ? `<a class="btn sm" href="/api/sessions/${esc(sv.id)}/files/${esc(file.id)}" download>下载</a>` : '';
  const err = j.error ? `<div class="meta bad">${esc(j.error)}</div>` : '';
  const refs = j.reference_count ? `<span class="kind">参考图 ${esc(j.reference_count)} 张</span>` : '';
  const warn = j.warning ? `<div class="meta bad">${esc(j.warning)}</div>` : '';
  return `<div class="mediajob" data-job="${j.id}" data-state="${esc(j.state)}"><div class="who"><b>${esc(MEDIA_LABELS[j.kind] || j.kind)}</b><span class="kind">第 ${j.round} 轮${j.attempt > 1 ? ` · 第 ${j.attempt} 次尝试` : ''}</span><span class="kind">${esc(where)}</span>${refs}${tag(st, sc)}<span class="meta">${money(j.cost_usd)}</span>${model}</div>${players}${warn}${err}<details class="more"><summary>生成提示词</summary><div>${esc(j.prompt)}</div></details>${dl}</div>`;
}

function merged(tableNo, d, names) {
  const sp = names.speaker(tableNo, null);
  const [ct, cc] = CONFIDENCE[d.confidence] || [d.confidence, ''];
  const list = (xs) => `<ul>${xs.map((x) => `<li>${md(x)}</li>`).join('')}</ul>`;
  const adoption = (d.subtasks || [])
    .map((s) => {
      const parts = s.adopted.map((a) => {
        const [lt, lc] = LEVEL[a.level] || [a.level, ''];
        return `${esc(names.member(a.member))} ${tag(lt, lc)}${a.reason ? ` ${md(a.reason)}` : ''}`;
      });
      return `<li><b>${esc(names.subtask(tableNo, s.subtask))}</b>：${parts.join('；')}</li>`;
    })
    .join('');
  const ai = sp.ai ? ` · ${esc(sp.ai)}` : '';
  return `<div class="final"><h3>合并成果<small>统筹${ai} · 把握程度 ${tag(ct, cc)}</small></h3>
<div class="answer">${md(d.result || '')}</div>
${adoption ? `<div class="sec">采纳情况</div><ul>${adoption}</ul>` : ''}
${d.gaps && d.gaps.length ? `<div class="sec">缺失</div>${list(d.gaps)}` : ''}
${d.open_questions && d.open_questions.length ? `<div class="sec">仍存疑</div>${list(d.open_questions)}` : ''}
${d.degraded ? '<div class="note">合并格式有误，以上为各份成果原文（把握程度记为低）。</div>' : ''}</div>`;
}

function review(data, names, tableNo = 0) {
  const reviews = data.reviews || [];
  if (!reviews.length) {
    const why = data.degraded ? '格式错误，本轮没有可用的评审' : '没有评审';
    return `<div class="bubble invalid"><span class="meta">${esc(why)}</span></div>`;
  }
  return `<div class="bubble">${reviews.map((rv) => reviewItem(rv, names, tableNo)).join('')}</div>`;
}

function reviewItem(rv, names, tableNo = 0) {
  const [vt, vc] = VERDICT[rv.verdict] || [rv.verdict, ''];
  const invalid = rv.invalid_reasons && rv.invalid_reasons.length;
  const issues = (rv.issues || [])
    .map(
      (i) =>
        `<li>${i.severity === 'major' ? tag('重要', 'bad') + ' ' : ''}<b>${md(i.location || '—')}</b>：${md(i.problem)}${i.suggestion ? ` → ${md(i.suggestion)}` : ''}</li>`,
    )
    .join('');
  return `<div class="rv"><div class="hd">→ <b>${esc(names.target(tableNo, rv.target))}</b> ${tag(vt, vc)}${invalid ? ' ' + tag('无效：' + rv.invalid_reasons.join('；'), 'bad') : ''}</div>${issues ? `<ul>${issues}</ul>` : ''}${rv.checked ? `<div class="meta">检查了：${md(rv.checked)}</div>` : ''}${rv.strengths ? `<div class="meta">优点：${md(rv.strengths)}</div>` : ''}</div>`;
}

function synthesis(tableNo, d, names) {
  const sp = names.speaker(tableNo, null);
  const [ct, cc] = CONFIDENCE[d.confidence] || [d.confidence, ''];
  const list = (xs) => `<ul>${xs.map((x) => `<li>${md(x)}</li>`).join('')}</ul>`;
  const dis = (d.disagreements || [])
    .map((x) => {
      const pos = (x.positions || [])
        .map((p) => `<li>${esc((p.members || []).map((c) => names.member(c)).join('、'))}：${md(p.view)}</li>`)
        .join('');
      return `<li><b>${md(x.point)}</b> ${x.resolved ? tag('已裁定', 'ok') : tag('未裁定', 'bad')}${pos ? `<ul>${pos}</ul>` : ''}${x.assessment ? `<div>${md(x.assessment)}</div>` : ''}</li>`;
    })
    .join('');
  const ai = sp.ai ? ` · ${esc(sp.ai)}` : '';
  return `<div class="final"><h3>统筹汇总<small>统筹${ai} · 把握程度 ${tag(ct, cc)}</small></h3>
<div class="sec">最终答案</div><div class="answer">${md(d.final_answer || '')}</div>
${d.consensus && d.consensus.length ? `<div class="sec">共识</div>${list(d.consensus)}` : ''}
${dis ? `<div class="sec">分歧</div><ul>${dis}</ul>` : ''}
${d.open_questions && d.open_questions.length ? `<div class="sec">仍存疑</div>${list(d.open_questions)}` : ''}
${d.degraded ? '<div class="note">汇总格式有误，以下为兜底结果（把握程度记为低）。</div>' : ''}</div>`;
}

function card(cp, ctx, names) {
  const c = cp.card;
  const pending = cp.status === 'pending';
  const sit = String(c.situation || '')
    .split('\n')
    .filter(Boolean)
    .map((x) => `<li>${esc(x)}</li>`)
    .join('');
  const opts = c.options
    .map((o) => {
      const rec = o.key === c.recommendation ? '<span class="rec">推荐</span>' : '';
      const chosen = !pending && o.key === cp.response ? ' chosen' : '';
      const cost = o.cost_usd === null || o.cost_usd === undefined ? '' : money(o.cost_usd);
      return `<button type="button" class="opt${chosen}" data-cp="${cp.id}" data-opt="${esc(o.key)}"${pending ? '' : ' disabled'}><b>${esc(o.label)}${rec}</b><span class="c">${cost}</span>${o.note ? `<span class="n">${esc(o.note)}</span>` : ''}</button>`;
    })
    .join('');
  const small = c.kind === 'cost' ? `超过单题门槛 ${money(ctx.threshold)}` : '';
  let tail = '';
  if (!pending) {
    const o = c.options.find((x) => x.key === cp.response);
    tail = `<div class="result">已选择：${esc(o ? o.label : cp.response || '—')}${cp.note ? ` · 附言：${esc(cp.note)}` : ''}</div>`;
  } else if (ctx.cardError) {
    tail = `<div class="err">${esc(ctx.cardError)}</div>`;
  }
  return `<div class="card${pending ? '' : ' resolved'}" data-kind="${esc(c.kind)}"><h3>需要你决定 · ${esc(CARD_TITLES[c.kind] || c.kind)}<small>${small}</small></h3>
<div class="k">现状</div><ul>${sit}</ul>
<div class="k">选项</div><div class="opts">${opts}</div>
${c.reason ? `<div class="reason">推荐理由：${esc(c.reason)}</div>` : ''}${tail}</div>`;
}

function ending(sv, ctx) {
  if (sv.running) return '';
  const cost = money(sv.cost_usd);
  if (sv.status === 'completed') {
    if (!sv.anonymous) return sys('统筹', `讨论完成 · 本场花费 ${cost}`, 'ok');
    const tail = sv.revealed ? '身份已揭晓' : '可以点「揭晓身份」查看各代号对应的模型';
    return sys('统筹', `讨论完成 · 本场花费 ${cost} · ${tail}`, 'ok');
  }
  if (sv.status === 'stopped') return sys('统筹', `已停止 · 本场花费 ${cost}`, 'bad');
  if (sv.status === 'failed') return sys('统筹', `失败：${esc(sv.error || '未知错误')}`, 'bad');
  if (sv.status === 'paused') {
    return sys('统筹', `已暂停：${esc(sv.error || '')}`, 'bad', '<button class="btn sm" type="button" data-act="resume">继续</button>');
  }
  return '';
}

// --- 圆桌 ------------------------------------------------------------------------

function seatPositions(n) {
  const right = Math.ceil(n / 2);
  const left = n - right;
  const pos = [];
  const R = 40;
  const at = (deg) => [50 + R * Math.cos((deg * Math.PI) / 180), 50 + R * Math.sin((deg * Math.PI) / 180)];
  for (let j = 0; j < right; j++) pos.push(at(-90 + (180 * (j + 1)) / (right + 1)));
  for (let j = 0; j < left; j++) pos.push(at(90 + (180 * (j + 1)) / (left + 1)));
  return pos;
}

const COORD_POS = [50, 8];
const ME_POS = [50, 92];

export function stageHTML(sv, live, prefix) {
  const names = new Names(sv, prefix);
  const tables = sv?.tables || [];
  // 正在进行的桌子；否则是最后一张已经开始的桌子（待确认的升级桌还没有入座）
  const started = tables.filter((t) => t.status !== 'pending' && t.status !== 'approved');
  const last = started.length ? started[started.length - 1] : tables[0];
  const tableNo = live.table ?? (last ? last.table_no : null);
  const table = tables.find((t) => t.table_no === tableNo);
  const codes = table ? table.codes : [];
  const hasCoord = !!sv && sv.seats.some((s) => s.table_no === tableNo && s.role === 'coordinator');
  const dropped = new Set(
    (sv?.outputs || []).filter((o) => o.kind === 'dropout' && o.table_no === tableNo).map((o) => o.code),
  );
  const lazyCodes = new Set(
    [...lazyKeys(sv?.outputs || []).keys()].filter((k) => k.startsWith(`${tableNo}:`)).map((k) => k.split(':')[2]),
  );
  const pos = seatPositions(codes.length);
  let ph = '圆桌';
  let act = '提交题目后入座';
  if (sv) {
    ph = live.step ? STEP_LABELS[live.step] || live.step : STATUS_LABELS[sv.status] || sv.status;
    act = live.act || (table ? `${table.plan_label} · ${codes.length} 位组员` : '');
  }
  let h = '<div class="table"></div>';
  h += `<div class="plate"><div><div class="ph" id="plate-step">${esc(ph)}</div><div class="act">${esc(act)}</div></div></div>`;

  let sv2 = '<svg class="flows" viewBox="0 0 100 100" aria-hidden="true"><defs><marker id="ah" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0L6 3L0 6z" fill="var(--brass)"/></marker></defs>';
  const line = (a, b, arrow) => {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const L = Math.hypot(dx, dy) || 1;
    const s = [a[0] + (dx / L) * 8, a[1] + (dy / L) * 8];
    const e = [b[0] - (dx / L) * 9, b[1] - (dy / L) * 9];
    return `<line class="flowline" x1="${s[0]}" y1="${s[1]}" x2="${e[0]}" y2="${e[1]}" stroke="var(--brass)" stroke-width=".7" stroke-dasharray="2 2"${arrow ? ' marker-end="url(#ah)"' : ''}/>`;
  };
  if ((live.step === 'review' || live.step === 'cross_review') && live.table === tableNo) {
    for (let i = 0; i < codes.length; i++)
      for (let j = i + 1; j < codes.length; j++) sv2 += line(pos[i], pos[j], false);
  }
  if ((live.step === 'synthesize' || live.step === 'merge') && live.table === tableNo && hasCoord) {
    codes.forEach((c, i) => {
      if (!dropped.has(c)) sv2 += line(pos[i], COORD_POS, true);
    });
  }
  h += sv2 + '</svg>';

  const seat = ([x, y], cls, av, nm, rl) =>
    `<div class="seat ${cls}" style="left:${x}%;top:${y}%">${av}<div class="nm">${esc(nm)}</div><div class="rl">${esc(rl)}</div></div>`;
  if (hasCoord) {
    const sp = names.speaker(tableNo, null);
    const speaking = live.speaking.has('统') ? 'speaking' : '';
    h += seat(COORD_POS, `inner ${speaking}`, avatar(sp), sp.name, sp.ai || '汇总 · 不作答');
  }
  codes.forEach((c, i) => {
    const sp = names.speaker(tableNo, c);
    const cls = [live.speaking.has(c) ? 'speaking' : '', dropped.has(c) ? 'dropped' : '', lazyCodes.has(c) ? 'lazy' : ''].join(' ');
    const role = dropped.has(c) ? '已退出' : lazyCodes.has(c) ? '敷衍' : '组员';
    h += seat(pos[i], cls, avatar(sp), sp.name, sp.ai ? `${sp.ai}${lazyCodes.has(c) ? ' · 敷衍' : ''}` : role);
  });
  h += seat(ME_POS, '', '<div class="av me">你</div>', '你', '提问');
  return h;
}

/** 左下角说明：随匿名开关与模式变化（会话打开时按会话本身的设置，否则按提问区的选择）。 */
export function legendHTML({ anonymous = false, workflow = 'discussion' } = {}) {
  const collab = workflow === 'collab';
  const swap = anonymous ? '匿名' : '';
  const line = collab
    ? `交叉审查时成员互相审查对方的${swap}成果（不审自己的）；合并时各份成果交给统筹`
    : `互评时组员两两交换${swap}答案；汇总时修订稿交给统筹`;
  const coord = collab ? '统筹：拆分子任务、分配并合并成果，不兼任组员' : '统筹：只读修订稿并汇总，不兼任组员';
  const codes = anonymous
    ? '每题随机分配；已开启匿名，界面只显示代号，结束后可揭晓身份'
    : '每题随机分配，模型之间只用代号称呼；未开启匿名，界面在代号旁显示真实模型';
  return `<div class="row"><svg width="22" height="8"><line x1="1" y1="4" x2="21" y2="4" stroke="var(--brass)" stroke-width="2" stroke-dasharray="3 3"/></svg><span><b>金色虚线</b> ${line}</span></div>
<div class="row"><span style="width:22px;display:inline-grid;place-items:center"><span style="width:12px;height:12px;border-radius:30%;background:var(--c-coord);display:inline-block"></span></span><span><b>方形头像</b> ${coord}</span></div>
<div class="row"><span style="width:22px;text-align:center">甲</span><span><b>代号</b> ${codes}</span></div>`;
}

// --- 右侧面板 -----------------------------------------------------------------------

export function flowPanel(sv, live, ctx) {
  if (!sv) {
    return `<p class="empty">提交题目后，这里显示档位、各步骤进度和花费预估。</p>
<h4>规则</h4><table><tbody>
<tr><td>单题确认门槛</td><td class="num">${money(ctx.threshold)}</td></tr>
<tr><td>最多组员</td><td class="num">${esc(ctx.maxMembers ?? '—')}</td></tr>
</tbody></table>`;
  }
  let h = '';
  for (const t of sv.tables || []) {
    const pill = t.status === 'done' ? 'okp' : t.status === 'running' ? 'run' : 'wait';
    h += `<div class="tbl-hd"><b>${esc(`第 ${t.table_no + 1} 桌 · ${t.plan_label}`)}</b><span class="pill ${pill}">${esc(TABLE_STATUS[t.status] || t.status)}</span></div>`;
    h += '<div class="pipe">';
    for (const step of t.pipeline) {
      const done = t.steps_done.includes(step);
      const now = !done && live.table === t.table_no && live.step === step;
      const cls = done ? 'done' : now ? 'now' : '';
      const who = step === 'media' ? '评审者与统筹' : COORD_STEPS.has(step) ? '统筹' : step === 'reveal' ? (sv.anonymous ? '讨论结束后由你点「揭晓身份」' : '匿名关闭，身份一直公开') : t.codes.join(' · ');
      const label = step === 'reveal' ? (sv.anonymous ? '可揭晓' : '结束') : STEP_LABELS[step] || step;
      h += `<div class="pn ${cls}" data-step="${esc(step)}"><span class="dot"></span><span class="t">${esc(label)}<small>${esc(who)}</small></span><span class="pill ${done ? 'okp' : now ? 'run' : ''}">${done ? '完成' : now ? '进行中' : '待开始'}</span></div>`;
    }
    h += '</div>';
  }
  if (!h) h = '<p class="empty">正在安排座位、预估花费…</p>';
  const r = sv.routing;
  if (r) {
    h += '<h4>花费预估与档位对比</h4><table><thead><tr><th>档位</th><th class="num">预估</th></tr></thead><tbody>';
    for (const [k, v] of Object.entries(r.options || {})) {
      if (v === null || v === undefined) continue;
      const cur = k === r.plan;
      const over = v > ctx.threshold ? ' · 需确认' : '';
      const name = k === 'custom' ? ctx.customLabel : ctx.plans[k] || k;
      h += `<tr class="${cur ? 'cur' : ''}"><td>${esc(name)}${cur ? ' ✓' : ''}</td><td class="num">${money(v)}${over}</td></tr>`;
    }
    h += `</tbody></table><p class="hint">超过单题门槛 ${money(ctx.threshold)} 时先请你确认。</p>`;
    h += '<h4>路由记录</h4><table><tbody>';
    const rows = [
      ['答案长度', `${LENGTH[r.difficulty] || r.difficulty}（${SOURCE[r.difficulty_source] || r.difficulty_source}）`],
      ['缺席', r.absent ? `${r.absent.length} 个模型（无可用渠道）` : '—'],
      ['题型', r.task_type || '—'],
      ['判断理由', r.assessment_reason || '—'],
      ['预计花费', money(r.estimated_cost_usd)],
      ['实际花费', money(r.actual_cost_usd ?? sv.cost_usd)],
      ['升级', r.escalated ? `是 → ${ctx.plans[r.escalated_plan] || r.escalated_plan}（${r.escalation_reason || ''}）` : r.escalation_reason ? `建议过，未升级（${r.escalation_reason}）` : '否'],
    ];
    for (const [k, v] of rows) h += `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`;
    h += '</tbody></table>';
  }
  return h;
}

const TABLE_STATUS = { pending: '待确认', approved: '待开始', running: '进行中', done: '完成' };

export function reviewsPanel(sv, prefix) {
  const names = new Names(sv, prefix);
  const reviews = (sv?.outputs || []).filter((o) => o.kind === 'review' || o.kind === 'cross_review');
  if (!reviews.length) return '<p class="empty">互评开始后，这里显示谁评了谁、结论和问题数。</p>';
  let h = '';
  for (const t of sv.tables || []) {
    const rows = reviews.filter((o) => o.table_no === t.table_no);
    if (!rows.length) continue;
    h += `<h4>${esc(`第 ${t.table_no + 1} 桌 · ${t.plan_label}`)}</h4><table><thead><tr><th>评审者</th><th>对象</th><th>结论</th><th class="num">问题</th></tr></thead><tbody>`;
    for (const o of rows) {
      const d = parse(o.content);
      if (!d || !(d.reviews || []).length) {
        h += `<tr><td>${esc(names.member(o.code))}</td><td colspan="3"><span class="pill badp">本轮无评审</span></td></tr>`;
        continue;
      }
      for (const rv of d.reviews) {
        const [vt, vc] = VERDICT[rv.verdict] || [rv.verdict, ''];
        const pill = { ok: 'okp', wait: 'wait', bad: 'badp' }[vc] || '';
        const invalid = rv.invalid_reasons && rv.invalid_reasons.length;
        h += `<tr><td>${esc(names.member(rv.reviewer))}</td><td>${esc(names.target(o.table_no, rv.target))}</td><td><span class="pill ${pill}${invalid ? ' strike' : ''}">${esc(vt)}</span>${invalid ? ' <span class="pill badp">无效</span>' : ''}</td><td class="num">${(rv.issues || []).length}</td></tr>`;
      }
    }
    h += '</tbody></table>';
  }
  return h + '<p class="hint">无效评审（问题没写全、空泛、判错却不指出问题）不转给作者；没有有效评审的组员沿用原答案。</p>';
}

function budgetBlock(title, s) {
  if (!s) return `<h4>${esc(title)}</h4><p class="empty">未设上限</p>`;
  const pct = Math.min(100, s.ratio * 100);
  return `<h4>${esc(title)} ${money(s.limit_usd)}</h4><div class="bar${s.warn ? ' warn' : ''}"><i style="width:${pct.toFixed(1)}%"></i></div><div class="hint">已用 ${money(s.spent_usd)}（${pct.toFixed(1)}%）${s.exhausted ? ' · 已用满' : s.warn ? ' · 接近上限' : ''} · ${esc(resetText(s.resets_at))} 重置</div>`;
}

/** token 用量显示为「大米」：1 粒 = grain_tokens，1 勺 = spoon_grains 粒，1 碗 = bowl_spoons 勺（比例来自 /api/status）。金额不走这里。 */
export function riceText(tokens, rice) {
  const r = rice || { grain_name: '粒', spoon_name: '勺', bowl_name: '碗', grain_tokens: 1000, spoon_grains: 100, bowl_spoons: 30 };
  let grains = Math.max(0, Number(tokens) || 0) / r.grain_tokens;
  if (grains >= 10) grains = Math.round(grains);
  if (grains < r.spoon_grains) {
    const text = grains === 0 ? '0' : grains < 10 ? String(Math.max(0.1, Math.round(grains * 10) / 10)) : String(grains);
    return `${text} ${r.grain_name}`;
  }
  const spoons = Math.floor(grains / r.spoon_grains);
  const restGrains = grains % r.spoon_grains;
  if (spoons < r.bowl_spoons) return `${spoons} ${r.spoon_name}${restGrains ? ` ${restGrains} ${r.grain_name}` : ''}`;
  const bowls = Math.floor(spoons / r.bowl_spoons);
  const restSpoons = spoons % r.bowl_spoons;
  return `${bowls} ${r.bowl_name}${restSpoons ? ` ${restSpoons} ${r.spoon_name}` : ''}`;
}

export function usagePanel(sv, budget, rice) {
  let h = '';
  if (sv) {
    h += `<h4>本场累计</h4><div class="big" id="session-cost">${money(sv.cost_usd)}</div>`;
    const groups = new Map();
    for (const c of sv.calls || []) {
      const key = `${c.table_no ?? '-'}:${c.step}`;
      const g = groups.get(key) || { table: c.table_no, step: c.step, n: 0, i: 0, o: 0, cost: 0, failed: 0 };
      g.n += 1;
      g.i += c.input_tokens || 0;
      g.o += c.output_tokens || 0;
      g.cost += c.cost_usd || 0;
      g.failed += c.failed ? 1 : 0;
      groups.set(key, g);
    }
    if (groups.size) {
      h += '<h4>按步骤</h4><table><thead><tr><th>步骤</th><th class="num">调用</th><th class="num">大米</th><th class="num">费用</th></tr></thead><tbody>';
      for (const g of groups.values()) {
        const label = `${g.table !== null && g.table !== undefined ? `第 ${g.table + 1} 桌 · ` : ''}${STEP_LABELS[g.step] || g.step}`;
        h += `<tr><td>${esc(label)}${g.failed ? ` <span class="pill badp">失败 ${g.failed}</span>` : ''}</td><td class="num">${g.n}</td><td class="num" title="${g.i + g.o} token">${esc(riceText(g.i + g.o, rice))}</td><td class="num">${money(g.cost)}</td></tr>`;
      }
      h += '</tbody></table>';
    }
  }
  if (budget) {
    h += budgetBlock('本月预算', budget.month);
    h += budgetBlock('今日上限', budget.day);
    const ch = Object.entries(budget.by_channel || {});
    h += '<h4>按渠道花费（累计）</h4>';
    if (!ch.length) h += '<p class="empty">还没有调用记录。</p>';
    else {
      h += '<table><thead><tr><th>渠道</th><th class="num">调用</th><th class="num">失败尝试</th><th class="num">费用</th></tr></thead><tbody>';
      for (const [name, v] of ch) {
        h += `<tr><td>${esc(name)}</td><td class="num">${v.calls}</td><td class="num">${v.failed_attempts}</td><td class="num">${money(v.cost_usd)}</td></tr>`;
      }
      h += `</tbody></table><p class="hint">累计 ${money(budget.total_usd)}。按渠道汇总不对应任何座位；单次调用走的渠道揭晓后在「名册」里显示。</p>`;
    }
  }
  return h || '<p class="empty">加载中…</p>';
}

export function channelsPanel(status) {
  if (!status) return '<p class="empty">加载中…</p>';
  // 渠道模式：auto / direct / 只用聚合平台（前端不写死任何渠道名）
  const modeLabel = { auto: '自动（按模型的渠道顺序，失败切换）', direct: '只用官方直连与本地' };
  let h = `<h4>渠道模式</h4><p>${esc(modeLabel[status.channel_mode] || '只用聚合平台')}</p>`;
  h += '<h4>渠道状态</h4><table><thead><tr><th>渠道</th><th>类型</th><th>状态</th></tr></thead><tbody>';
  for (const [name, c] of Object.entries(status.channels || {})) {
    const st = c.available ? '<span class="pill okp">可用</span>' : `<span class="pill badp">不可用</span><div class="hint">${esc(c.reason || '')}</div>`;
    h += `<tr><td>${esc(name)}</td><td>${esc(KIND_LABELS[c.kind] || c.kind)}</td><td>${st}</td></tr>`;
  }
  h += '</tbody></table>';
  const tiers = {};
  for (const m of status.models || []) {
    const t = (tiers[m.tier] ||= { all: 0, ok: 0 });
    t.all += 1;
    t.ok += m.available ? 1 : 0;
  }
  const tierLabel = { flagship: '全力', budget: '节电' };
  h += '<h4>可用模型</h4><table><tbody>';
  for (const [k, v] of Object.entries(tiers)) {
    h += `<tr><td>${esc(tierLabel[k] || k)}</td><td class="num">${v.ok} / ${v.all}</td></tr>`;
  }
  h += '</tbody></table><p class="hint">没有 key 的渠道自动跳过；一个渠道都不可用的模型不参与抽座。key 只放在项目根目录的 .env 中。</p>';
  return h;
}

export function historyPanel(list, currentId) {
  if (!list) return '<p class="empty">加载中…</p>';
  if (!list.length) return '<p class="empty">还没有讨论记录。</p>';
  let h = '<table><thead><tr><th>题目</th><th>状态</th><th class="num">花费</th></tr></thead><tbody>';
  for (const s of list) {
    const q = s.question.length > 40 ? s.question.slice(0, 40) + '…' : s.question;
    const pill = { completed: 'okp', running: 'run', awaiting_confirmation: 'wait', paused: 'wait', failed: 'badp', stopped: 'badp' }[s.status] || '';
    h += `<tr class="click${s.id === currentId ? ' cur' : ''}" data-sid="${esc(s.id)}"><td>${esc(q)}<div class="hint">${esc(s.created_at.slice(0, 16).replace('T', ' '))}${s.anonymous ? (s.revealed ? ' · 匿名 · 已揭晓' : ' · 匿名') : ''}</div></td><td><span class="pill ${pill}">${esc(STATUS_LABELS[s.status] || s.status)}</span></td><td class="num">${money(s.cost_usd)}</td></tr>`;
  }
  return h + '</tbody></table><p class="hint">点击打开；已暂停或等待确认的讨论可以继续。</p>';
}

const CONTRIB_COLUMNS = [
  ['adopted', '被采纳'],
  ['volunteer_accepted', '自荐被采纳'],
  ['valid_issue', '有效问题'],
  ['issue_accepted', '问题被采纳'],
  ['redo', '重做'],
  ['lazy', '敷衍'],
];

export function contributionsPanel(sv, history, prefix) {
  const names = new Names(sv, prefix);
  const head = `<tr><th>成员</th>${CONTRIB_COLUMNS.map(([, t]) => `<th class="num">${t}</th>`).join('')}</tr>`;
  const cells = (counts) =>
    CONTRIB_COLUMNS.map(([k]) => `<td class="num${k === 'lazy' && counts[k] ? ' warnc' : ''}">${counts[k] || 0}</td>`).join('');
  let h = '<h4>本场</h4>';
  const rows = sv?.contributions || [];
  if (!rows.length) h += `<p class="empty">${sv ? '讨论结束后统计。' : '提交题目后显示。'}</p>`;
  else {
    const multi = new Set(rows.map((r) => r.table_no)).size > 1;
    h += `<table><thead>${head}</thead><tbody>`;
    for (const r of rows) {
      const sp = names.speaker(r.table_no, r.code);
      const who = `${multi ? `第 ${r.table_no + 1} 桌 · ` : ''}${sp.name}${sp.ai ? ` · ${sp.ai}` : ''}`;
      h += `<tr><td>${esc(who)}${r.counts.dropped ? ' <span class="pill badp">退出</span>' : ''}</td>${cells(r.counts)}</tr>`;
    }
    h += '</tbody></table>';
  }
  h += '<p class="hint">被采纳：统筹汇总时注明来自该成员的要点数；有效问题：有效评审中指出的问题；问题被采纳：作者修订时明确采纳的问题数。</p>';
  h += '<h4>历史（按模型）</h4>';
  if (!history) h += '<p class="empty">加载中…</p>';
  else if (!history.length) h += '<p class="empty">还没有可统计的讨论。匿名讨论揭晓后才计入，避免反推身份。</p>';
  else {
    h += `<table><thead><tr><th>模型</th><th class="num">场次</th>${CONTRIB_COLUMNS.map(([, t]) => `<th class="num">${t}</th>`).join('')}</tr></thead><tbody>`;
    for (const r of history) {
      h += `<tr><td>${esc(r.label || r.model_id)}<div class="hint">${esc(r.model_id)}</div></td><td class="num">${r.sessions}</td>${cells(r.counts)}</tr>`;
    }
    h += '</tbody></table><p class="hint">这些数据为以后按历史表现分工积累，目前不影响谁上桌、谁当统筹。</p>';
  }
  return h;
}

export function castPanel(sv, prefix) {
  if (!sv) return '<p class="empty">提交题目后显示座位。</p>';
  const names = new Names(sv, prefix);
  let h = sv.revealed ? '' : '<p class="empty">匿名进行中：讨论结束后点「揭晓身份」才显示各代号对应的模型，以及每次调用走的渠道。</p>';
  if (sv.routing && sv.routing.absent && sv.routing.absent.length) {
    h += `<p class="hint">缺席（没有可用渠道）：${esc(sv.routing.absent.join('、'))}</p>`;
  }
  const calls = sv.calls || [];
  const callLines = (pred) =>
    calls
      .filter(pred)
      .map((c) => {
        const tries = (c.attempts || []).length > 1
          ? ` · 切换：${c.attempts.map((a) => `${a.channel}${a.ok ? ' ✓' : ` ✗${a.error ? `(${a.error})` : ''}`}`).join(' → ')}`
          : '';
        return `<div>${esc(STEP_LABELS[c.step] || c.step)} · ${esc(c.channel || '—')} · ${money(c.cost_usd)}${c.failed ? ` · ${esc(c.error || '失败')}` : ''}${esc(tries)}</div>`;
      })
      .join('');
  for (const t of sv.tables || []) {
    h += `<h4>${esc(`第 ${t.table_no + 1} 桌 · ${t.plan_label}`)}</h4><div class="ros">`;
    const seats = sv.seats.filter((s) => s.table_no === t.table_no);
    for (const s of seats) {
      const sp = names.speaker(t.table_no, s.role === 'member' ? s.code : null);
      const role = s.role === 'member' ? '组员' : '统筹';
      const body = sv.revealed
        ? `<p>${esc(role)}</p><div class="calls">${callLines((c) => c.table_no === t.table_no && c.role === s.role && (s.role !== 'member' || c.code === s.code))}</div>`
        : `<p>${esc(role)} · 身份隐藏</p>`;
      h += `<div class="ro">${avatar(sp)}<b>${esc(sp.name)}${sp.ai ? ` <span class="ai">· ${esc(sp.ai)}</span>` : ''}</b>${body}</div>`;
    }
    h += '</div>';
  }
  if (sv.revealed) {
    const planner = calls.filter((c) => c.role === 'planner');
    if (planner.length) {
      h += `<h4>规划员</h4><div class="ros"><div class="ro"><div class="av coord">规</div><b>规划员 <span class="ai">· ${esc(planner[0].model_id || '')}</span></b><p>估计答案长度</p><div class="calls">${callLines((c) => c.role === 'planner')}</div></div></div>`;
    }
  }
  return h;
}

// --- 分工（协同模式） -----------------------------------------------------------------

/** 交接链：按依赖分层从左到右；节点 = 子任务（类型、标题、负责人、状态），节点里写从哪个上游收到什么。 */
function chainGraph(subs, assign, works, handoffs, names, tableNo) {
  const list = subs.subtasks;
  const layerOf = {};
  const depth = (s, seen = new Set()) => {
    if (layerOf[s.id] !== undefined) return layerOf[s.id];
    if (seen.has(s.id)) return 0;
    seen.add(s.id);
    const ups = (s.depends_on || []).map((d) => list.find((x) => x.id === d)).filter(Boolean);
    layerOf[s.id] = ups.length ? Math.max(...ups.map((u) => depth(u, seen))) + 1 : 0;
    return layerOf[s.id];
  };
  list.forEach((s) => depth(s));
  const cols = [];
  for (const s of list) (cols[layerOf[s.id]] ||= []).push(s);
  const sid = names.sid;
  let h = '<h4>交接链</h4><div class="chain">';
  cols.forEach((col, i) => {
    h += `<div class="col"><div class="colh">第 ${i + 1} 段</div>`;
    for (const s of col) {
      const owners = (assign?.assignments.find((x) => x.subtask === s.id)?.members || []).map((c) => esc(names.member(c))).join('、') || '—';
      const done = works.length && (assign?.assignments.find((x) => x.subtask === s.id)?.members || []).every((c) => works.some((w) => w.o.code === c && w.d.subtask === s.id));
      const incoming = new Map();
      for (const x of handoffs.filter((x) => x.to_subtask === s.id)) {
        const e = incoming.get(x.from_subtask) || { gives: x.gives, files: [] };
        e.files.push(...(x.files || []));
        incoming.set(x.from_subtask, e);
      }
      let ins = '';
      for (const [from, e] of incoming) {
        const imgs = [...new Map(e.files.filter((f) => /\.(png|jpe?g|gif|webp)$/i.test(f.name)).map((f) => [f.id, f])).values()]
          .map((f) => `<img class="thumb" alt="${esc(f.name)}" src="/api/sessions/${esc(sid)}/files/${esc(f.id)}?inline=1">`)
          .join('');
        ins += `<div class="in">← ${esc(from)}：${esc(e.gives)}${imgs}</div>`;
      }
      if (!incoming.size && (s.depends_on || []).length) {
        ins = s.depends_on.map((d) => `<div class="in wait">← ${esc(d)}：${esc((s.gives || {})[d] || '')}（尚未交接）</div>`).join('');
      }
      h += `<div class="node${done ? ' done' : ''}">${kindPill(s.kind)}<b>${esc(s.id)} ${esc(s.title)}</b><div class="who">${owners}</div><div>${done ? '<span class="pill okp">已完成</span>' : '<span class="pill">未完成</span>'}</div>${ins}</div>`;
    }
    h += '</div>';
  });
  return `${h}</div>`;
}

export function splitPanel(sv, prefix) {
  if (!sv) return '<p class="empty">提交题目后显示。</p>';
  if (sv.workflow !== 'collab') {
    return '<p class="empty">这是讨论模式，没有分工。选择「协同模式」提问后，这里显示子任务、自荐、负责人、成果与采纳情况。</p>';
  }
  const names = new Names(sv, prefix);
  const lazy = lazyKeys(sv.outputs || []);
  let h = '';
  for (const t of sv.tables || []) {
    const outs = (sv.outputs || []).filter((o) => o.table_no === t.table_no);
    const pick = (kind) => outs.filter((o) => o.kind === kind).map((o) => ({ o, d: parse(o.content) })).filter((x) => x.d);
    const subs = pick('subtasks')[0]?.d;
    if (!subs) continue;
    const assign = pick('assignment')[0]?.d;
    const merge = pick('merge')[0]?.d;
    const works = pick('work');
    const vols = pick('volunteer');
    h += `<h4>${esc(`第 ${t.table_no + 1} 桌 · 子任务与负责人`)}</h4>`;
    h += '<table><thead><tr><th>子任务</th><th>负责人</th><th>成果</th><th>采纳</th></tr></thead><tbody>';
    let n = 0;
    for (const st of subs.subtasks) {
      const a = assign?.assignments.find((x) => x.subtask === st.id);
      const owners = a ? a.members : [];
      const cells = owners.map((c) => {
        n += 1;
        const w = works.find((x) => x.o.code === c && x.d.subtask === st.id);
        const why = lazy.get(`${t.table_no}:work:${c}`);
        const flag = why !== undefined ? ` <span class="pill badp" title="${esc(why)}">敷衍</span>` : '';
        return { id: `W${n}`, c, done: !!w, flag };
      });
      const adopted = merge ? (merge.subtasks || []).find((x) => x.subtask === st.id)?.adopted || [] : null;
      const who = owners.length ? owners.map((c) => esc(names.member(c))).join('<br>') : '—';
      const res = cells.length
        ? cells.map((x) => `${x.id} ${x.done ? '<span class="pill okp">已完成</span>' : '<span class="pill">进行中</span>'}${x.flag}`).join('<br>')
        : '—';
      let ad = '—';
      if (adopted) {
        ad = adopted.length
          ? adopted.map((x) => `${esc(names.member(x.member))} ${esc((LEVEL[x.level] || [x.level])[0])}`).join('<br>')
          : '未采用';
      }
      const deps = st.depends_on && st.depends_on.length ? `<div class="hint">依赖 ${esc(st.depends_on.join('、'))}</div>` : '';
      h += `<tr><td>${kindPill(st.kind)}<b>${esc(st.id)}</b> ${esc(st.title)}${deps}</td><td>${who}</td><td>${res}</td><td>${ad}</td></tr>`;
    }
    h += '</tbody></table>';
    if (subs.pipeline) h += chainGraph(subs, assign, works, pick('handoff').map((x) => x.d), names, t.table_no);
    if (assign?.repaired?.length) h += '<p class="hint">统筹的分配不符合规则，已由代码按规则调整（回避自己审查自己、均衡负担）。</p>';
    if (vols.length) {
      h += '<h4>自荐表态</h4><table><thead><tr><th>成员</th>' + subs.subtasks.map((s) => `<th>${esc(s.id)}</th>`).join('') + '</tr></thead><tbody>';
      for (const { d, o } of vols) {
        const cells = subs.subtasks.map((s) => {
          const p = d.preferences.find((x) => x.subtask === s.id);
          const [lt, lc] = p ? STANCE[p.stance] || [p.stance, ''] : ['—', ''];
          return `<td>${lc === 'ok' ? `<span class="pill okp">${esc(lt)}</span>` : lc === 'bad' ? `<span class="pill badp">${esc(lt)}</span>` : esc(lt)}</td>`;
        });
        h += `<tr><td>${esc(names.member(o.code))}</td>${cells.join('')}</tr>`;
      }
      h += '</tbody></table>';
    }
  }
  return h || '<p class="empty">统筹拆分子任务后显示。</p>';
}

// --- 工具与文件 ------------------------------------------------------------------------

export function toolsPanel(sv, prefix) {
  if (!sv) return '<p class="empty">提交题目后显示。成员作答时可以运行代码、写文件、生成图片、联网搜索。</p>';
  const names = new Names(sv, prefix);
  const calls = sv.tool_calls || [];
  const files = (sv.files || []).filter((f) => f.latest);
  const billed = (sv.calls || []).filter((c) => c.role === 'tool');
  const cost = billed.reduce((a, c) => a + (c.cost_usd || 0), 0);
  let h = `<h4>工具花费</h4><div class="big" id="tool-cost">${money(cost)}</div><div class="hint">共 ${calls.length} 次工具调用、${billed.length} 次计费（搜索、图像生成）；运行代码本地进行、不计费，但每一轮都会多一次模型调用。</div>`;
  h += '<h4>工具调用</h4>';
  if (!calls.length) h += '<p class="empty">这场没有使用工具。</p>';
  else {
    h += '<table><thead><tr><th>成员</th><th>步骤</th><th>工具</th><th>状态</th></tr></thead><tbody>';
    for (const x of calls) {
      const sp = names.speaker(x.table_no, x.code);
      const [st, sc] = TOOL_STATUS[x.status] || [x.status, ''];
      const pill = { ok: 'okp', wait: 'wait', bad: 'badp' }[sc] || '';
      h += `<tr><td>${esc(sp.name)}</td><td>${esc(STEP_LABELS[x.step] || x.step)} · 第 ${esc(x.round)} 轮</td><td>${esc(TOOL_LABELS[x.tool] || x.tool)}<div class="hint">${esc(toolSummary(x))}</div></td><td><span class="pill ${pill}">${esc(st)}</span></td></tr>`;
    }
    h += '</tbody></table>';
  }
  h += '<h4>生成的文件</h4>';
  if (!files.length) h += '<p class="empty">还没有文件。</p>';
  else {
    h += '<div class="filebar" style="flex-direction:column;align-items:stretch">';
    for (const f of files) h += fileChip(f, sv.id, `${names.speaker(f.table_no, f.code).name} · `);
    h += '</div><p class="hint">只保留每个文件的最新版本；HTML、SVG 只显示源代码，不会在页面中运行。</p>';
  }
  return h;
}

// --- 提问区：附件与提交前预估 ----------------------------------------------------------------

export function attachmentsHTML(list) {
  return list
    .map((a, i) => {
      if (a.pending) return `<span class="chip">${esc(a.name)} · 上传中…</span>`;
      if (a.error) return `<span class="chip bad" title="${esc(a.error)}">${esc(a.name)} · ${esc(a.error)}<button class="x" type="button" data-rm="${i}" aria-label="移除">×</button></span>`;
      const warn = (a.warnings || []).length ? ` <span class="w" title="${esc(a.warnings.join('；'))}">⚠ ${esc(a.warnings[0])}</span>` : '';
      const sref = a.kind === 'image'
        ? ` <label class="sref" title="勾选：提取成风格规范，并作为参考图传给画图模型；取消：只当普通附件"><input type="checkbox" data-sref="${i}"${a.style_ref === false ? '' : ' checked'}> 风格参考</label>`
        : '';
      return `<span class="chip" data-att="${esc(a.id)}">${esc(a.name)} · ${bytes(a.size)}${a.pages ? ` · ${a.pages} 页` : ''}${warn}${sref}<button class="x" type="button" data-rm="${i}" aria-label="移除">×</button></span>`;
    })
    .join('');
}

// 提交前的建议（如：讨论模式下题目需要多个媒体文件 → 建议切换到协同模式）
function adviceHTML(est, workflow) {
  return (est.advice || [])
    .filter((a) => a.applies_to === workflow)
    .map(
      (a) =>
        `<div class="advice" data-advice="${esc(a.kind)}"><b>建议切换到协同模式</b><div>${esc(a.message)}</div>` +
        `<button class="btn sm" type="button" data-switch-workflow="${esc(a.suggest_workflow)}">切换到协同模式</button></div>`,
    )
    .join('');
}

export function estimateHTML(est, { workflow, anonymous, plans, customLabel }) {
  if (!est) return '';
  const opts = (est.options || []).filter((o) => o.workflow === workflow);
  if (!opts.length) return '';
  let h = adviceHTML(est, workflow);
  h += '<table><thead><tr><th>档位</th><th class="num">上桌</th><th class="num">预计</th><th class="num">最多约</th><th></th></tr></thead><tbody>';
  for (const o of opts) {
    const label = o.plan === 'custom' ? customLabel : plans[o.plan] || o.label;
    if (!o.available) {
      h += `<tr><th>${esc(label)}</th><td colspan="4">${esc(o.reason || '不可用')}</td></tr>`;
      continue;
    }
    const absent = o.absent ? `，缺席 ${o.absent}` : '';
    const over = o.over_threshold ? `<span class="over">超过门槛 ${money(est.confirm_threshold_usd)}，提交后先确认</span>` : '';
    h += `<tr class="${o.selected ? 'sel' : ''}${o.over_threshold ? ' over-row' : ''}" data-plan="${esc(o.plan)}"><th>${esc(label)}${o.selected ? ' ✓' : ''}</th><td class="num">${o.members + 1} 人${esc(absent)}</td><td class="num${o.over_threshold ? ' over' : ''}">${money(o.estimate_usd)}</td><td class="num">${money(o.max_usd)}</td><td>${over}</td></tr>`;
  }
  h += '</tbody></table>';
  const sel = opts.find((o) => o.selected && o.steps);
  if (sel) {
    h += `<details><summary>步骤明细</summary>${sel.steps.map((s) => `${esc(STEP_LABELS[s.step] || s.step)} ${money(s.cost_usd)}`).join(' · ')}</details>`;
    if (!anonymous && sel.lineup) h += `<div>上桌：${esc([...sel.lineup.members, sel.lineup.coordinator].filter(Boolean).join('、'))}${sel.lineup.absent.length ? `；缺席：${esc(sel.lineup.absent.join('、'))}` : ''}</div>`;
    if (anonymous) h += '<div>匿名已开启：不显示上桌名单。</div>';
  }
  return h;
}

// --- 媒体面板 ----------------------------------------------------------------------------

export function mediaPanel(sv, prefix) {
  if (!sv) return '<p class="empty">提交题目时可以选择输出图片、语音或视频；协同模式由统筹决定哪些子任务要生成媒体。</p>';
  const names = new Names(sv, prefix);
  const jobs = sv.media || [];
  const total = jobs.reduce((a, j) => a + (j.cost_usd || 0), 0);
  let h = `<h4>媒体花费</h4><div class="big" id="media-cost">${money(total)}</div><div class="hint">共 ${jobs.length} 次生成，已计入本月 / 今日预算。视频每次生成前都会先问你。</div>`;
  h += '<h4>每次生成</h4>';
  if (!jobs.length) return h + '<p class="empty">这场没有媒体生成。</p>';
  h += '<table><thead><tr><th>轮次</th><th>内容</th><th>状态</th><th class="num">花费</th></tr></thead><tbody>';
  for (const j of jobs) {
    const [st, sc] = JOB_STATE[j.state] || [j.state, ''];
    const pill = { ok: 'okp', wait: 'wait', bad: 'badp' }[sc] || '';
    const who = j.code ? names.member(j.code) : '统筹';
    const model = j.model_id ? `<div class="hint">${esc(j.model_id)}${j.channel ? ` · ${esc(j.channel)}` : ''}</div>` : '';
    h += `<tr><td>第 ${j.round} 轮${j.attempt > 1 ? `<div class="hint">第 ${j.attempt} 次尝试</div>` : ''}</td><td>${esc(MEDIA_LABELS[j.kind] || j.kind)} · ${esc(who)}${j.subtask ? ` · ${esc(j.subtask)}` : ''}${j.reference_count ? `<div class="hint">参考图 ${esc(j.reference_count)} 张</div>` : ''}${j.warning ? `<div class="hint bad">${esc(j.warning)}</div>` : ''}${model}<details class="more"><summary>提示词</summary><div>${esc(j.prompt)}</div></details></td><td><span class="pill ${pill}">${esc(st)}</span></td><td class="num">${money(j.cost_usd)}</td></tr>`;
  }
  h += '</tbody></table><h4>成果</h4><div class="filebar" style="flex-direction:column;align-items:stretch">';
  for (const j of jobs) {
    const file = (sv.files || []).find((f) => f.id === j.file_id);
    if (file) h += fileChip(file, sv.id, `第 ${j.round} 轮 · `) + mediaPlayer(file, sv.id);
  }
  return h + '</div>';
}
