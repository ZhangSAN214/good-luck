// 把服务端返回的会话渲染成 HTML。这里只做展示，不发请求。
// 匿名讨论在揭晓前，服务端不返回模型和渠道，界面只用代号（组员甲 / 乙 / 丙…、统筹）；
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
};
// 由统筹执行的步骤
export const COORD_STEPS = new Set(['synthesize', 'decompose', 'assign', 'merge']);
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

// 模型输出是 Markdown：只把 **加粗** 转成粗体，其余原样保留（换行由 CSS 保留）
export function md(t) {
  return esc(t).replace(/(^|[^*\w])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![*\w])/g, '$1<b>$2</b>');
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

// --- 名称 --------------------------------------------------------------------------

export class Names {
  constructor(session, prefix = '组员') {
    this.prefix = prefix;
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
  // 发言者：头像字、名称、揭晓后的模型
  speaker(tableNo, code) {
    if (code) return { ch: code, name: this.member(code), ai: this.model(tableNo, 'member', code) };
    return { ch: '统', name: '统筹', ai: this.model(tableNo, 'coordinator'), coord: true };
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
      const outs = outputs.filter((o) => o.table_no === t.table_no && o.step === step);
      const live = ctx.live.table === t.table_no && ctx.live.step === step;
      cpAt((d) => d.table_no === t.table_no && d.step === step);
      if (!outs.length && !live) continue;
      if (step === 'reveal') continue;
      push(`s${t.table_no}:${step}`, `<div class="phase">${esc(STEP_LABELS[step] || step)}</div>`);
      outs.forEach((o, i) => {
        const html = output(o, names, lazy);
        if (html) push(`o${t.table_no}:${step}:${o.kind}:${o.code || ''}:${i}`, html);
      });
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
  if (sv.anonymous) parts.push('匿名');
  return parts.join(' · ');
}

// 被标记为敷衍的产出：`桌:步骤:代号`
export function lazyKeys(outputs) {
  const keys = new Set();
  for (const o of outputs) {
    if (o.kind !== 'effort') continue;
    const d = parse(o.content);
    if (d && d.status === 'lazy') keys.add(`${o.table_no}:${o.step}:${o.code}`);
  }
  return keys;
}

const LAZY_TAG = `<span class="tagp bad" title="重做后仍没有通过实质内容检查">敷衍</span>`;

function effort(o, sp, data) {
  const what = STEP_LABELS[o.step] || o.step;
  const first = (data.reasons || []).join('；');
  if (data.status === 'lazy') {
    const final = (data.final_reasons || data.reasons || []).join('；');
    const redo = data.redone ? '重做后仍不合格，' : '';
    return sys('检查', `${esc(sp.name)} 的${esc(what)}${redo}标记为敷衍：${esc(final)}`, 'bad');
  }
  return sys('检查', `${esc(sp.name)} 的${esc(what)}没有实质内容（${esc(first)}），已打回重做，重做后合格`);
}

function output(o, names, lazy = new Set()) {
  const sp = names.speaker(o.table_no, o.code);
  if (lazy.has(`${o.table_no}:${o.step}:${o.code}`)) sp.tag = LAZY_TAG;
  if (o.kind === 'answer') return msg(sp, '作答', `<div class="bubble">${md(o.content)}</div>`);
  if (o.kind === 'effort') {
    const d = parse(o.content);
    return d ? effort(o, sp, d) : '';
  }
  if (o.kind === 'dropout') {
    return sys('统筹', `${esc(sp.name)} 退出：调用失败，已有的内容仍参与汇总`, 'bad');
  }
  const data = parse(o.content);
  if (!data) return msg(sp, o.kind, `<div class="bubble">${md(o.content)}</div>`);
  if (o.kind === 'review') return msg(sp, '互评', review(data, names, o.table_no));
  const collab = collabOutput(o, sp, data, names);
  if (collab !== null) return collab;
  if (o.kind === 'revision') {
    if (data.skipped) return sys('统筹', `${esc(sp.name)} 没有收到有效评审，沿用原答案`);
    const resp = data.responses
      ? `<details class="more"><summary>对审阅意见的回应</summary><div>${md(data.responses)}</div></details>`
      : '';
    return msg(sp, '修订', `<div class="bubble">${md(data.answer)}</div>${resp}`);
  }
  if (o.kind === 'synthesis') return synthesis(o.table_no, data, names);
  return '';
}

function collabOutput(o, sp, data, names) {
  const t = o.table_no;
  const li = (xs) => `<ul>${xs.join('')}</ul>`;
  if (o.kind === 'subtasks') {
    const items = data.subtasks.map(
      (x) =>
        `<li><b>${esc(x.id)} ${md(x.title)}</b>${x.requirements ? `：${md(x.requirements)}` : ''}${x.acceptance ? `<div class="meta">验收：${md(x.acceptance)}</div>` : ''}${x.depends_on && x.depends_on.length ? `<div class="meta">依赖：${esc(x.depends_on.join('、'))}</div>` : ''}</li>`,
    );
    const note = data.degraded ? '<div class="meta">拆分不可用，整道题作为一个子任务由全员各自完成</div>' : '';
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
    return msg(sp, `完成 ${names.subtask(t, data.subtask)}`, `<div class="bubble">${md(data.text)}</div>`);
  }
  if (o.kind === 'cross_review') return msg(sp, '交叉审查', review(data, names, t));
  if (o.kind === 'rework') {
    const what = names.subtask(t, data.subtask);
    if (data.skipped) return sys('统筹', `${esc(sp.name)} 的 ${esc(what)} 没有收到有效审查，沿用原成果`);
    const resp = data.responses
      ? `<details class="more"><summary>对审查意见的回应</summary><div>${md(data.responses)}</div></details>`
      : '';
    return msg(sp, `修改 ${what}`, `<div class="bubble">${md(data.answer)}</div>${resp}`);
  }
  if (o.kind === 'merge') return merged(t, data, names);
  return null;
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
    [...lazyKeys(sv?.outputs || [])].filter((k) => k.startsWith(`${tableNo}:`)).map((k) => k.split(':')[2]),
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
    h += seat(COORD_POS, `inner ${speaking}`, avatar(sp), '统筹', sp.ai || '汇总 · 不作答');
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

export function legendHTML() {
  return `<div class="row"><svg width="22" height="8"><line x1="1" y1="4" x2="21" y2="4" stroke="var(--brass)" stroke-width="2" stroke-dasharray="3 3"/></svg><span><b>金色虚线</b> 互评时组员两两交换匿名答案；汇总时修订稿交给统筹</span></div>
<div class="row"><span style="width:22px;display:inline-grid;place-items:center"><span style="width:12px;height:12px;border-radius:30%;background:var(--c-coord);display:inline-block"></span></span><span><b>方形头像</b> 统筹：只读修订稿并汇总，不兼任组员</span></div>
<div class="row"><span style="width:22px;text-align:center">甲</span><span><b>代号</b> 每题随机分配，模型之间只用代号称呼；开启匿名时界面也只显示代号</span></div>`;
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
      const who = COORD_STEPS.has(step) ? '统筹' : step === 'reveal' ? (sv.anonymous ? '讨论结束后由你点「揭晓身份」' : '匿名关闭，身份一直公开') : t.codes.join(' · ');
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
  return `<h4>${esc(title)} ${money(s.limit_usd)}</h4><div class="bar${s.warn ? ' warn' : ''}"><i style="width:${pct.toFixed(1)}%"></i></div><div class="hint">已用 ${money(s.spent_usd)}（${pct.toFixed(1)}%）${s.exhausted ? ' · 已用满' : s.warn ? ' · 接近上限' : ''} · ${esc(s.resets_at.slice(0, 10))} 重置（UTC）</div>`;
}

export function usagePanel(sv, budget) {
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
      h += '<h4>按步骤</h4><table><thead><tr><th>步骤</th><th class="num">调用</th><th class="num">token</th><th class="num">费用</th></tr></thead><tbody>';
      for (const g of groups.values()) {
        const label = `${g.table !== null && g.table !== undefined ? `第 ${g.table + 1} 桌 · ` : ''}${STEP_LABELS[g.step] || g.step}`;
        h += `<tr><td>${esc(label)}${g.failed ? ` <span class="pill badp">失败 ${g.failed}</span>` : ''}</td><td class="num">${g.n}</td><td class="num">${g.i + g.o}</td><td class="num">${money(g.cost)}</td></tr>`;
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
  const tierLabel = { flagship: '旗舰', budget: '便宜档' };
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
      h += `<tr><td>${esc(r.model_id)}</td><td class="num">${r.sessions}</td>${cells(r.counts)}</tr>`;
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
