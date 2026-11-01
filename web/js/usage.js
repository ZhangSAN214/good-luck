// 余额与用量：账户余额（渠道实时查）、本月预算、对账、钱折算成 token、详细使用情况（本机账本）。
// 只读：不改预算、不上传；key 只在服务端，这里永远拿不到。渠道名、成员名全部来自接口，不写死。
// 唯一能改的是「走哪条路」：同一成员有几条渠道时先走哪条、每条渠道每月附送的额度（存本机）。
import { tzOpt } from './hometz.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
const REFRESH = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12a7 7 0 1 1-2.05-4.95M19 4v4h-4"/></svg>';
const RANGES = [
  ['day', '今天'],
  ['week', '本周'],
  ['month', '本月'],
  ['all', '全部'],
];
// 各功能在图表里的颜色序号（颜色在 usage.css 里按序号定义）
const FEATURE_ORDER = ['work', 'office', 'chat', 'life', 'evolution'];
const BASIS_KEY = 'roundtable.usage.basis';
const KIND_LABEL = { direct: '官方直连', aggregator: '中转站', local: '本机' };

/** 美元：小额多给几位小数，免得全是 $0.00。 */
export function usd(v) {
  const n = Number(v) || 0;
  const a = Math.abs(n);
  const digits = a === 0 ? 2 : a < 0.01 ? 4 : a < 1 ? 3 : 2;
  return `${n < 0 ? '−' : ''}$${a.toFixed(digits)}`;
}

/** token 数用中文单位：1.2 万、3.4 亿。 */
export function tokens(v) {
  if (v == null) return '—';
  const n = Number(v) || 0;
  if (n >= 1e8) return `${(n / 1e8).toFixed(n >= 1e9 ? 0 : 1)} 亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(n >= 1e6 ? 0 : 1)} 万`;
  return String(Math.round(n));
}

const pct = (v) => `${Math.round((Number(v) || 0) * 100)}%`;

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 存不了就只在这次生效 */
  }
}

/** 对账的一句大白话：渠道记的本月已用和本机账本差多少。 */
export function reconcileLine(row) {
  if (row.diff_month_usd == null) return '渠道没有返回本月已用，暂时没法对账。';
  const d = row.diff_month_usd;
  if (Math.abs(d) < 0.01) return '对得上：渠道记的本月已用和本机账本相差不到 1 美分。';
  if (d > 0)
    return `渠道记的比本机账本多 ${usd(d)}。多半是同一个 key 还被别的程序用过，或者有几次调用的费用渠道晚一点才结算。`;
  return `本机账本比渠道记的多 ${usd(-d)}。多半是有几次调用渠道没报费用，本机按配置价估了个数（通常偏保守）。`;
}

export class Usage {
  /**
   * @param {HTMLDialogElement} dialog
   * @param {{api:{usage:Function, balance:Function, priority?:Function, setPriority?:Function}}} opts
   */
  constructor(dialog, { api }) {
    this.dlg = dialog;
    this.api = api;
    this.range = 'month';
    this.data = null;
    this.bal = null;
    this.balError = '';
    this.msg = '';
    this.basis = load(BASIS_KEY) === 'budget' ? 'budget' : 'balance';
    this.pri = null; // 渠道优先顺序（编辑中的草稿）
    this.priMsg = '';
    this.dlg.addEventListener('click', (e) => this.onClick(e));
  }

  async open() {
    if (!this.dlg.open) this.dlg.showModal();
    this.render();
    await Promise.all([this.loadUsage(), this.loadBalance(false), this.loadPriority()]);
  }

  async loadPriority() {
    if (!this.api.priority) return;
    try {
      this.pri = await this.api.priority();
      this.priMsg = '';
    } catch (e) {
      this.priMsg = e.message;
    }
    if (this.dlg.open) this.render();
  }

  /** 把输入框里还没保存的额度读回草稿（重画前调用，免得打的字丢掉）。 */
  readCredits() {
    for (const input of this.dlg.querySelectorAll('[data-credit]')) {
      const row = this.pri?.channels.find((c) => c.channel === input.dataset.credit);
      if (!row) continue;
      const v = input.value.trim();
      row.credit_usd = v === '' ? null : Math.max(0, Number(v) || 0);
    }
  }

  async savePriority() {
    this.readCredits();
    const p = this.pri;
    try {
      this.pri = await this.api.setPriority({
        strategy: p.strategy,
        order: p.channels.map((c) => c.channel),
        credits: Object.fromEntries(p.channels.map((c) => [c.channel, c.credit_usd])),
      });
      this.priMsg = '已保存，下一次调用起生效。';
    } catch (e) {
      this.priMsg = `没保存成功：${e.message}`;
    }
    this.render();
  }

  close() {
    this.dlg.close();
  }

  async loadUsage() {
    try {
      this.data = await this.api.usage(this.range);
      this.msg = '';
    } catch (e) {
      this.msg = e.message;
    }
    if (this.dlg.open) this.render();
  }

  async loadBalance(refresh) {
    this.balLoading = true;
    if (this.dlg.open) this.render();
    try {
      this.bal = await this.api.balance(refresh);
      this.balError = '';
    } catch (e) {
      this.balError = e.message;
    }
    this.balLoading = false;
    if (this.dlg.open) this.render();
  }

  /** 第一个查到余额的渠道（折算 token 用）。 */
  account() {
    return (this.bal?.channels || []).find((c) => c.ok && c.remaining != null) || null;
  }

  render() {
    const keep = this.dlg.querySelector('.vb')?.scrollTop || 0;
    const d = this.data;
    this.dlg.innerHTML = `
      <div class="vh sheet-bar"><button type="button" class="sheet-icon" data-close aria-label="关闭">${BACK}</button><b>余额与用量</b><button type="button" class="sheet-icon" data-refresh aria-label="重新查询余额">${REFRESH}</button></div>
      <div class="vb us">
        ${this.msg ? `<p class="pp-msg" role="alert">${esc(this.msg)}</p>` : ''}
        ${this.balanceSection()}
        ${this.prioritySection()}
        ${d ? this.budgetSection(d) : '<p class="hint us-wait">正在读本机账本…</p>'}
        ${d ? this.tokenSection(d) : ''}
        ${d ? this.detailSection(d) : ''}
        <p class="grp-foot us-note">这里的数都从这台电脑上的账本和渠道账户读，只读不改，不上传。${d?.outside ? esc(d.outside) : ''}</p>
      </div>`;
    const vb = this.dlg.querySelector('.vb');
    if (vb) vb.scrollTop = keep;
  }

  balanceSection() {
    const chans = this.bal?.channels;
    let body;
    if (this.balError) body = `<div class="grp-row us-bad">查询失败：${esc(this.balError)}。下面先用本机账本估算。</div>`;
    else if (!chans) body = `<div class="grp-row hint">正在向渠道查询余额…</div>`;
    else if (!chans.length) body = `<div class="grp-row hint">配置里没有能查余额的渠道。下面用本机账本和本月预算估算。</div>`;
    else body = chans.map((c) => this.channelCard(c)).join('');
    const when = this.bal?.fetched_at ? new Date(this.bal.fetched_at).toLocaleTimeString('zh-CN', tzOpt({ hour12: false })) : '';
    return `<section class="grp">
      <h4 class="grp-h">账户余额 <small>${this.balLoading ? '查询中…' : when ? `${esc(when)} 查的` : ''}</small></h4>
      <div class="grp-box">${body}</div>
    </section>`;
  }

  channelCard(c) {
    if (!c.ok) {
      return `<div class="us-acct">
        <div class="us-acct-h"><b>${esc(c.channel)}</b><span class="us-pill bad">查询失败</span></div>
        <p class="us-why">${esc(c.error || '没有返回')}</p>
        <p class="us-sub">本机账本记的：本月 ${usd(c.ledger_month_usd)} · 今天 ${usd(c.ledger_day_usd)}</p>
      </div>`;
    }
    const big = c.remaining != null ? usd(c.remaining) : '—';
    const parts = [];
    if (c.total_credits != null) parts.push(`累计充值 ${usd(c.total_credits)}`);
    if (c.total_usage != null) parts.push(`累计已用 ${usd(c.total_usage)}`);
    if (c.key_limit != null) parts.push(`这个 key 的额度 ${usd(c.key_limit)}`);
    const key = [
      c.usage_daily != null ? `今天 ${usd(c.usage_daily)}` : '',
      c.usage_weekly != null ? `本周 ${usd(c.usage_weekly)}` : '',
      c.usage_monthly != null ? `本月 ${usd(c.usage_monthly)}` : '',
    ].filter(Boolean);
    return `<div class="us-acct">
      <div class="us-acct-h"><b>${esc(c.channel)}</b><span class="us-pill">${c.total_credits != null ? '账户余额' : c.key_remaining != null ? 'key 剩余额度' : '只查到已用'}</span></div>
      <div class="us-big">${big}</div>
      ${parts.length ? `<p class="us-sub">${parts.join(' · ')}</p>` : ''}
      ${key.length ? `<p class="us-sub">渠道记的这个 key 已用：${key.join(' · ')}</p>` : ''}
      <p class="us-sub">本机账本记的：本月 ${usd(c.ledger_month_usd)} · 今天 ${usd(c.ledger_day_usd)}</p>
      <p class="us-recon">${esc(reconcileLine(c))}</p>
      ${c.notes?.length ? `<p class="us-why">部分没查到：${esc(c.notes.join('；'))}</p>` : ''}
    </div>`;
  }

  prioritySection() {
    const p = this.pri;
    if (!p) return this.priMsg ? `<section class="grp"><h4 class="grp-h">走哪条路</h4><div class="grp-box"><div class="grp-row us-bad">${esc(this.priMsg)}</div></div></section>` : '';
    const custom = p.strategy === 'custom';
    const rows = p.channels
      .map((c, i) => {
        const left = c.credit_usd == null ? '' : c.credit_left_usd > 0 ? `本月额度还剩 ${usd(c.credit_left_usd)}，优先走` : '本月额度已用完，排到最后';
        const move = custom
          ? `<span class="us-pmove"><button type="button" class="sheet-icon" data-pmove="${i}:-1" aria-label="往前"${i ? '' : ' disabled'}>↑</button><button type="button" class="sheet-icon" data-pmove="${i}:1" aria-label="往后"${i < p.channels.length - 1 ? '' : ' disabled'}>↓</button></span>`
          : '';
        return `<div class="us-prow${c.available ? '' : ' off'}">
          <div class="us-acct-h">${custom ? `<small class="us-pno">${i + 1}</small>` : ''}<b>${esc(c.channel)}</b><span class="us-pill">${esc(KIND_LABEL[c.kind] || c.kind)}</span>${c.available ? '' : `<span class="us-pill bad">${esc(c.reason || '未启用')}</span>`}${move}</div>
          ${c.members.length ? `<p class="us-sub">经过它的成员：${esc(c.members.join('、'))}</p>` : ''}
          <label class="us-sub us-credit">每月附送额度 $<input type="number" min="0" step="1" inputmode="decimal" data-credit="${esc(c.channel)}" value="${c.credit_usd ?? ''}" placeholder="没有就空着" aria-label="${esc(c.channel)} 每月附送额度"></label>
          <p class="us-sub">本机账本记的本月已用 ${usd(c.spent_month_usd)}${left ? ` · ${left}` : ''}</p>
        </div>`;
      })
      .join('');
    return `<section class="grp">
      <h4 class="grp-h">走哪条路 <small>同一位成员有几条渠道时先走哪条</small></h4>
      <div class="us-tabs" role="group" aria-label="渠道顺序">
        <button type="button" class="us-tab" data-pstrat="auto" aria-pressed="${!custom}">自动判断</button>
        <button type="button" class="us-tab" data-pstrat="custom" aria-pressed="${custom}">我来排</button>
      </div>
      <div class="grp-box">${rows}</div>
      <p class="grp-foot">${custom ? '按上面的先后走；' : '按各成员配置里的顺序走（一般官方直连在前、中转站兜底）；'}填了每月附送额度的渠道（比如会员送的 API 额度），本月没用完时最先走，用完自动排到最后。出错的渠道会临时排后。只决定走哪条路，不影响谁上桌。</p>
      <div class="us-psave"><button type="button" class="btn" data-psave>保存</button>${this.priMsg ? `<span class="hint">${esc(this.priMsg)}</span>` : ''}</div>
    </section>`;
  }

  budgetSection(d) {
    const m = d.budget?.month;
    const day = d.budget?.day;
    if (!m) return '';
    const bar = (s, label) => {
      const ratio = Math.min(1, s.ratio || 0);
      const state = s.exhausted ? 'bad' : s.warn ? 'warn' : '';
      return `<div class="us-meter">
        <div class="us-meter-h"><span>${label}</span><b>${usd(s.spent_usd)} <small>/ ${usd(s.limit_usd)}</small></b></div>
        <div class="us-track" role="meter" aria-valuemin="0" aria-valuemax="${s.limit_usd}" aria-valuenow="${s.spent_usd}" aria-label="${label}已用"><i class="${state}" style="width:${(ratio * 100).toFixed(1)}%"></i></div>
        <div class="us-meter-f"><span>剩 ${usd(Math.max(0, s.limit_usd - s.spent_usd))}（${pct(1 - ratio)}）</span><span>${new Date(s.resets_at).toLocaleString('zh-CN', tzOpt({ hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }))} 重置</span></div>
      </div>`;
    };
    return `<section class="grp">
      <h4 class="grp-h">预算 <small>和顶栏「本月已用」是同一笔账</small></h4>
      <div class="grp-box us-pad">${bar(m, '本月')}${day ? bar(day, '今天') : ''}</div>
      <p class="grp-foot">预算是圆桌自己给自己定的上限，到了就停；账户余额是渠道那边真实还剩的钱，两者分开算。</p>
    </section>`;
  }

  tokenSection(d) {
    const acct = this.account();
    const useBal = this.basis === 'balance' && acct;
    const amount = useBal ? acct.remaining : d.equivalents.amount_usd;
    const share = d.output_share;
    const rows = (d.prices || []).map((p) => {
      const tin = p.input_price > 0 ? (amount / p.input_price) * 1e6 : null;
      const tout = p.output_price > 0 ? (amount / p.output_price) * 1e6 : null;
      const mix = share != null && (p.input_price || p.output_price) ? (amount / ((1 - share) * p.input_price + share * p.output_price)) * 1e6 : null;
      return { ...p, tin, tout, mix };
    });
    const avg = rows.filter((r) => r.key.startsWith('tier:'));
    const each = rows.filter((r) => !r.key.startsWith('tier:'));
    const line = (r) => `<li class="grp-row us-eq"><span class="us-eq-n"><b>${esc(r.name)}</b><small>每百万 token：读 ${usd(r.input_price)} · 写 ${usd(r.output_price)}</small></span>
        <span class="us-eq-v"><span><small>全读</small>${tokens(r.tin)}</span><span><small>全写</small>${tokens(r.tout)}</span><span class="mix"><small>混合</small>${tokens(r.mix)}</span></span></li>`;
    return `<section class="grp">
      <h4 class="grp-h">折算成 token <small>按配置价，粗略</small></h4>
      <div class="us-chips" role="group" aria-label="按哪笔钱折算">
        <button type="button" class="hb-chip us-chip" data-basis="balance" aria-pressed="${!!useBal}"${acct ? '' : ' disabled title="没查到账户余额"'}>账户余额${acct ? ` ${usd(acct.remaining)}` : ''}</button>
        <button type="button" class="hb-chip us-chip" data-basis="budget" aria-pressed="${!useBal}">本月预算剩余 ${usd(d.equivalents.amount_usd)}</button>
      </div>
      <ul class="grp-box">${avg.map(line).join('')}</ul>
      <details class="us-more"><summary>每位成员分别能用多少</summary><ul class="grp-box">${each.map(line).join('')}</ul></details>
      <p class="grp-foot">「读」是发给成员的字（题目、上下文），「写」是成员写出来的字；写比读贵好几倍。「混合」按你以往的比例算${share != null ? `（写出来的占 ${pct(share)}）` : '（还没有记录，暂不显示）'}，最接近实际能用多少。中文大约 1 个字 ≈ 1 个 token。</p>
    </section>`;
  }

  detailSection(d) {
    const t = d.totals;
    const tabs = RANGES.map(([k, label]) => `<button type="button" class="us-tab" data-range="${k}" aria-pressed="${this.range === k}">${label}</button>`).join('');
    const tiles = [
      ['花费', usd(t.cost_usd)],
      ['调用', `${t.calls} 次${t.failed ? `<small>（失败 ${t.failed}）</small>` : ''}`],
      ['读入 token', tokens(t.input_tokens)],
      ['写出 token', tokens(t.output_tokens)],
      ['缓存命中', tokens(t.cached_tokens)],
      ['思考 token', tokens(t.reasoning_tokens)],
    ]
      .map(([k, v]) => `<div class="us-tile"><small>${k}</small><b>${v}</b></div>`)
      .join('');
    const maxF = Math.max(...d.by_feature.map((f) => f.cost_usd), 1e-9);
    const feats = d.by_feature.length
      ? d.by_feature
          .map(
            (f) => `<li class="grp-row us-bar"><span class="us-sw f${FEATURE_ORDER.indexOf(f.key)}" aria-hidden="true"></span><span class="us-bn">${esc(f.label)}</span>
            <span class="us-bt"><i class="f${FEATURE_ORDER.indexOf(f.key)}" style="width:${((f.cost_usd / maxF) * 100).toFixed(1)}%"></i></span>
            <span class="us-bv"><b>${usd(f.cost_usd)}</b><small>${f.calls} 次 · ${tokens(f.input_tokens + f.output_tokens)} token</small></span></li>`,
          )
          .join('')
      : '<li class="grp-row hint">这段时间没有调用。</li>';
    const maxM = Math.max(...d.by_member.map((m) => m.cost_usd), 1e-9);
    const members = d.by_member
      .map(
        (m) => `<li class="grp-row us-bar"><span class="us-bn">${esc(m.name)}</span>
          <span class="us-bt"><i style="width:${((m.cost_usd / maxM) * 100).toFixed(1)}%"></i></span>
          <span class="us-bv"><b>${usd(m.cost_usd)}</b><small>${m.calls} 次 · 读 ${tokens(m.input_tokens)} · 写 ${tokens(m.output_tokens)}</small></span></li>`,
      )
      .join('');
    const hidden = d.hidden
      ? `<li class="grp-row us-bar"><span class="us-bn">匿名进行中</span><span class="us-bt"></span><span class="us-bv"><b>${usd(d.hidden.cost_usd)}</b><small>${d.hidden.calls} 次 · 揭晓后再按成员算</small></span></li>`
      : '';
    const chans = Object.entries(d.by_channel || {})
      .map(([k, c]) => `<li class="grp-row us-bar"><span class="us-bn">${esc(k)}</span><span class="us-bt"></span><span class="us-bv"><b>${usd(c.cost_usd)}</b><small>${c.calls} 次</small></span></li>`)
      .join('');
    return `<section class="grp">
      <h4 class="grp-h">详细使用情况 <small>本机账本，按 UTC 日期</small></h4>
      <div class="us-tabs" role="group" aria-label="时间范围">${tabs}</div>
      <div class="us-tiles">${tiles}</div>
      <div class="grp-box us-pad">${this.chart(d)}</div>
      <h4 class="grp-h us-sub-h">按功能</h4>
      <ul class="grp-box">${feats}</ul>
      <h4 class="grp-h us-sub-h">按成员</h4>
      <ul class="grp-box">${members}${hidden}${members || hidden ? '' : '<li class="grp-row hint">这段时间没有成员的调用。</li>'}</ul>
      ${chans ? `<h4 class="grp-h us-sub-h">按渠道</h4><ul class="grp-box">${chans}</ul>` : ''}
    </section>`;
  }

  /** 最近 30 天每天的花费：按功能堆叠的柱状图（纯 SVG，鼠标悬停看当天明细）。 */
  chart(d) {
    const days = d.daily || [];
    const W = 600;
    const H = 150;
    const pad = { l: 34, r: 6, t: 8, b: 20 };
    const max = Math.max(...days.map((x) => x.cost_usd), 0);
    const top = max > 0 ? niceCeil(max) : 0.1;
    const bw = (W - pad.l - pad.r) / Math.max(days.length, 1);
    const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / top);
    const bars = days
      .map((x, i) => {
        let base = 0;
        const segs = FEATURE_ORDER.filter((k) => x.features?.[k])
          .map((k) => {
            const v = x.features[k];
            const y1 = y(base + v);
            const h = Math.max(0.5, y(base) - y1);
            base += v;
            return `<rect class="f${FEATURE_ORDER.indexOf(k)}" x="${(pad.l + i * bw + bw * 0.18).toFixed(1)}" y="${y1.toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${h.toFixed(1)}" rx="1.5"/>`;
          })
          .join('');
        const detail = Object.entries(x.features || {})
          .map(([k, v]) => `${d.features?.[k] || k} ${usd(v)}`)
          .join('，');
        return `<g><title>${esc(x.day)}：${usd(x.cost_usd)}，${x.calls} 次${detail ? `（${esc(detail)}）` : ''}</title><rect class="us-hit" x="${(pad.l + i * bw).toFixed(1)}" y="${pad.t}" width="${bw.toFixed(1)}" height="${H - pad.t - pad.b}"/>${segs}</g>`;
      })
      .join('');
    const grid = [0, 0.5, 1]
      .map((k) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(top * k).toFixed(1)}" y2="${y(top * k).toFixed(1)}"/><text x="${pad.l - 4}" y="${(y(top * k) + 3).toFixed(1)}" text-anchor="end">${usd(top * k)}</text>`)
      .join('');
    const lab = (i) => (days[i] ? days[i].day.slice(5).replace('-', '/') : '');
    const xl = [0, Math.floor(days.length / 2), days.length - 1]
      .map((i, n) => `<text x="${(pad.l + i * bw + bw / 2).toFixed(1)}" y="${H - 5}" text-anchor="${['start', 'middle', 'end'][n]}">${lab(i)}</text>`)
      .join('');
    const legend = FEATURE_ORDER.filter((k) => days.some((x) => x.features?.[k]))
      .map((k) => `<span><i class="us-sw f${FEATURE_ORDER.indexOf(k)}"></i>${esc(d.features?.[k] || k)}</span>`)
      .join('');
    return `<div class="us-chart-h"><b>最近 30 天每天花费</b><span class="us-legend">${legend}</span></div>
      <svg class="us-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="最近 30 天每天的花费">${grid}${bars}${xl}</svg>`;
  }

  onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    if (t.closest('[data-refresh]')) return this.loadBalance(true);
    const r = t.closest('[data-range]');
    if (r && r.dataset.range !== this.range) {
      this.range = r.dataset.range;
      return this.loadUsage();
    }
    const ps = t.closest('[data-pstrat]');
    if (ps && this.pri) {
      this.readCredits();
      this.pri.strategy = ps.dataset.pstrat;
      this.priMsg = '';
      return this.render();
    }
    const pm = t.closest('[data-pmove]');
    if (pm && !pm.disabled && this.pri) {
      this.readCredits();
      const [i, d] = pm.dataset.pmove.split(':').map(Number);
      const list = this.pri.channels;
      [list[i], list[i + d]] = [list[i + d], list[i]];
      this.priMsg = '';
      return this.render();
    }
    if (t.closest('[data-psave]') && this.pri) return this.savePriority();
    const b = t.closest('[data-basis]');
    if (b && !b.disabled) {
      this.basis = b.dataset.basis;
      store(BASIS_KEY, this.basis);
      return this.render();
    }
    if (t === this.dlg) this.close(); // 点弹窗外的遮罩
  }
}

/** 图表纵轴的上限：取 1 / 2 / 5 × 10ⁿ 里刚好盖住最大值的那个。 */
export function niceCeil(v) {
  const p = 10 ** Math.floor(Math.log10(v));
  for (const k of [1, 2, 5, 10]) if (v <= k * p) return k * p;
  return 10 * p;
}
