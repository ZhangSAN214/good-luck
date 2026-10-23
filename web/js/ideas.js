// 头脑风暴的「灵感结果」：同一场头脑风暴的数据（点子、投票、杂交关系、统筹的归类与方案）
// 用几种真正不同的方式呈现，每种回答一个问题：
//   灵感墙   —— 一共想出了什么？（按主题分组的便利贴，越大越受票）
//   象限图   —— 哪些又新又能做？（新颖 × 可行，来自组员投票的平均分）
//   点子榜   —— 大家最看好哪些？（总分排名）
//   杂交谱系 —— 新点子是怎么碰撞出来的？（父点子 → 杂交 / 反转出的子点子）
//   路线图   —— 第一名怎么落地？（最小试验 → 三步走 → 下一步）
//   故事板   —— 做成了是什么样子？（前几名的场景故事）
//   对比矩阵 —— 前几名各有什么代价？（投票分数条 + 统筹的文字对比）
//   黑马     —— 哪个大胆点子值得一试？
//   导图     —— 全貌的结构（卡片式，原 SVG 仍可下载）
// 页签用单选按钮切换（不需要脚本）；页签名只由桌号决定，重绘时选中的页签不变。
import { esc, md } from './view.js';

const KIND = { idea: '', hybrid: '杂交', flip: '反转放大', new: '全新', combine: '组合' };
const HUES = ['#3a6fb0', '#2f8a5a', '#c07a1c', '#a1478c', '#2a8a8c', '#c0503f', '#6a5acd', '#7a8a2a'];

const pill = (text, cls = '') => `<span class="tagp ${cls}">${esc(text)}</span>`;
const short = (t, n = 28) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

function index(b) {
  const by = new Map((b.ranking || []).map((r) => [r.id, r]));
  const voted = (b.ranking || []).filter((r) => r.votes > 0 || r.dark_votes > 0);
  const place = new Map(voted.map((r, i) => [r.id, i + 1]));
  const top = new Set(voted.filter((r) => r.votes > 0).slice(0, b.top || 5).map((r) => r.id));
  return { by, voted, place, top };
}

function badges(ids, ix, b) {
  const out = [];
  const best = Math.min(...ids.map((i) => ix.place.get(i) || 99));
  if (best <= 3) out.push(pill(`第 ${best} 名`, 'ok'));
  else if (ids.some((i) => ix.top.has(i))) out.push(pill('前列'));
  if (b.dark_horse && ids.includes(b.dark_horse)) out.push(pill('黑马', 'wait'));
  return out.join(' ');
}

// --- 灵感墙 -----------------------------------------------------------------------

function wall(data, b, ix) {
  const map = data.map || {};
  let groups;
  if (!map.degraded && (map.branches || []).length) {
    groups = map.branches.map((br) => ({
      name: br.name,
      notes: br.ideas.map((n) => ({ text: n.text, refs: n.refs || [], who: n.sources || [] })),
    }));
  } else {
    // 统筹没能归类：按“第一轮的点子 / 碰撞出的新点子”分两组
    const all = b.ranking || [];
    groups = [
      { name: '第一轮的点子', notes: all.filter((r) => r.kind === 'idea') },
      { name: '碰撞出的新点子', notes: all.filter((r) => r.kind !== 'idea') },
    ]
      .map((g) => ({ name: g.name, notes: g.notes.map((r) => ({ text: r.text, refs: [r.id], who: r.authors || [] })) }))
      .filter((g) => g.notes.length);
  }
  const score = (refs) => refs.reduce((s, i) => s + (ix.by.get(i)?.score || 0), 0);
  const max = Math.max(1, ...groups.flatMap((g) => g.notes.map((n) => score(n.refs))));
  let k = 0;
  const cols = groups.map((g, gi) => {
    const hue = HUES[gi % HUES.length];
    const notes = g.notes
      .map((n) => {
        const s = score(n.refs);
        const size = s >= max * 0.66 ? 'l' : s >= max * 0.33 ? 'm' : 's';
        const tilt = [-1.2, 0.8, -0.4, 1.1, -0.9, 0.5][k++ % 6];
        const refs = n.refs.length ? `<span class="refs">${esc(n.refs.join(' '))}</span>` : '';
        const dots = s ? `<span class="dots" title="得分 ${s}">${'●'.repeat(Math.max(1, Math.round((s / max) * 5)))}</span>` : '';
        return `<div class="note ${size}" style="--hue:${hue};--tilt:${tilt}deg"><div class="nt">${esc(n.text)}</div><div class="nf">${badges(n.refs, ix, b)}${refs}${dots}</div></div>`;
      })
      .join('');
    return `<section class="wgroup" style="--hue:${hue}"><h4>${esc(g.name)}<small>${g.notes.length}</small></h4><div class="notes">${notes}</div></section>`;
  });
  const total = (b.ranking || []).length;
  return `<p class="lead">一共 ${total} 个点子，按主题贴成 ${groups.length} 组；便利贴越大、圆点越多，组员投票越看好。</p><div class="wall">${cols.join('')}</div>`;
}

// --- 象限图 -----------------------------------------------------------------------

function quadrant(b, ix) {
  const pts = ix.voted.filter((r) => r.votes > 0);
  if (pts.length < 2) return '';
  const W = 420, H = 320, L = 36, T = 14, R = 14, B = 34;
  // 坐标轴留出半分的边，1 分和 5 分的点不会贴边被裁掉
  const x = (v) => L + ((v - 0.5) / 5) * (W - L - R);
  const y = (v) => T + (1 - (v - 0.5) / 5) * (H - T - B);
  const mx = x(3), my = y(3);
  const maxVotes = Math.max(...pts.map((r) => r.votes));
  const zones = [
    ['star', '明星：又新又能做', (r) => r.novelty >= 3 && r.feasibility >= 3],
    ['bet', '远景：大胆但难', (r) => r.novelty >= 3 && r.feasibility < 3],
    ['quick', '快赢：稳妥好做', (r) => r.novelty < 3 && r.feasibility >= 3],
    ['park', '再想想', (r) => r.novelty < 3 && r.feasibility < 3],
  ];
  const dots = pts
    .map((r) => {
      const rad = 6 + (r.votes / maxVotes) * 8;
      const cls = r.kind === 'idea' ? 'p' : 'p hy';
      return `<g class="${cls}"><title>${esc(`${r.id} ${r.text}（新颖 ${r.novelty}，可行 ${r.feasibility}，${r.votes} 票）`)}</title><circle cx="${x(r.feasibility).toFixed(1)}" cy="${y(r.novelty).toFixed(1)}" r="${rad.toFixed(1)}"/><text x="${x(r.feasibility).toFixed(1)}" y="${(y(r.novelty) + 3.5).toFixed(1)}">${esc(r.id)}</text></g>`;
    })
    .join('');
  const svg = `<svg class="quad" viewBox="0 0 ${W} ${H}" role="img" aria-label="新颖与可行象限图">
<rect class="z star" x="${mx}" y="${T}" width="${W - R - mx}" height="${my - T}"/><rect class="z bet" x="${L}" y="${T}" width="${mx - L}" height="${my - T}"/>
<rect class="z quick" x="${mx}" y="${my}" width="${W - R - mx}" height="${H - B - my}"/><rect class="z park" x="${L}" y="${my}" width="${mx - L}" height="${H - B - my}"/>
<text class="zl" x="${W - R - 6}" y="${T + 14}" text-anchor="end">明星</text><text class="zl" x="${L + 6}" y="${T + 14}">远景</text>
<text class="zl" x="${W - R - 6}" y="${H - B - 6}" text-anchor="end">快赢</text><text class="zl" x="${L + 6}" y="${H - B - 6}">再想想</text>
<text class="ax" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle">可行 →</text><text class="ax" transform="translate(12 ${(T + H - B) / 2}) rotate(-90)" text-anchor="middle">新颖 →</text>
${dots}</svg>`;
  const lists = zones
    .map(([cls, name, test]) => {
      const mine = pts.filter(test);
      if (!mine.length) return '';
      return `<div class="zone ${cls}"><b>${esc(name)}</b><ul>${mine.map((r) => `<li><b>${esc(r.id)}</b> ${esc(short(r.text, 40))}</li>`).join('')}</ul></div>`;
    })
    .join('');
  return `<p class="lead">横轴可行、纵轴新颖，都是组员投票的平均分；圆越大票越多，虚线圈是碰撞出的新点子。</p><div class="quadwrap">${svg}<div class="zones">${lists}</div></div>`;
}

// --- 点子榜 -----------------------------------------------------------------------

function ranking(b, ix) {
  const rank = ix.voted.slice(0, Math.max(10, (b.top || 5) * 2));
  if (!rank.length) return '';
  const max = Math.max(...rank.map((r) => r.score), 1);
  const rows = rank.map((r, i) => {
    const kind = KIND[r.kind] ? `${pill(KIND[r.kind])} ` : '';
    const from = r.refs && r.refs.length ? `<span class="meta">由 ${esc(r.refs.join(' × '))} 而来</span>` : '';
    const dark = r.id === b.dark_horse ? ` ${pill('黑马', 'wait')}` : '';
    return `<li class="${ix.top.has(r.id) ? 'hot' : ''}"><div class="sbar" style="width:${Math.round((r.score / max) * 100)}%"></div><div class="row"><span class="no">${i + 1}</span><div>${kind}<b>${esc(r.id)}</b> ${esc(r.text)}${dark}<div class="meta">${r.score} 分 · ${r.votes} 票 · ${esc((r.authors || []).join('、'))} ${from}</div></div></div></li>`;
  });
  return `<ol class="rank">${rows.join('')}</ol><p class="meta">组员匿名投票（不能投自己），按 新颖 + 可行 + 价值 总分排名。</p>`;
}

// --- 杂交谱系 ---------------------------------------------------------------------

function lineage(b, ix) {
  const kids = (b.ranking || []).filter((r) => r.refs && r.refs.length && r.kind !== 'idea');
  if (!kids.length) return '';
  const card = (r, cls = '') =>
    r ? `<div class="lc ${cls}"><b>${esc(r.id)}</b> ${esc(short(r.text, 46))}<div class="meta">${esc((r.authors || []).join('、'))}${r.score ? ` · ${r.score} 分` : ''}</div></div>` : '';
  const rows = kids.map((k) => {
    const parents = k.refs.map((i) => card(ix.by.get(i)) || `<div class="lc"><b>${esc(i)}</b></div>`).join('<span class="plus">×</span>');
    return `<div class="lrow"><div class="parents">${parents}</div><div class="arrow">${esc(KIND[k.kind] || '演变')} →</div>${card(k, 'kid')}</div>`;
  });
  return `<p class="lead">碰撞杂交这一轮里，组员把别人的点子两两杂交或反过来想，得到的新点子：</p><div class="lineage">${rows.join('')}</div>`;
}

// --- 路线图 -----------------------------------------------------------------------

function roadmap(data, b, ix) {
  const p = b.plan;
  if (!p) return '';
  const r = ix.by.get(p.id);
  const stops = [];
  if (p.experiment) stops.push(['最小试验', p.experiment, 'exp']);
  (p.steps || []).forEach((s, i) => stops.push([`第 ${i + 1} 步`, s, '']));
  const later = data.map?.next_steps || [];
  if (later.length) stops.push(['之后可以做', later.map((s) => `· ${s}`).join('\n'), 'later']);
  const items = stops.map(([h, t, cls], i) => `<li class="${cls}"><span class="dot">${i + 1}</span><div class="h">${esc(h)}</div><div class="t">${md(t)}</div></li>`).join('');
  return `<div class="goal">落地目标：<b>${esc(p.id)}</b> ${esc(r ? r.text : '')}</div><ol class="road">${items}</ol>`;
}

// --- 故事板 -----------------------------------------------------------------------

function storyboard(b, ix) {
  if (!(b.stories || []).length) return '';
  const panels = b.stories.map((s, i) => {
    const r = ix.by.get(s.id);
    return `<figure class="sframe"><div class="snum">${i + 1}</div><figcaption><b>${esc(s.id)}</b> ${esc(r ? short(r.text, 36) : '')}</figcaption><p>${md(s.story)}</p></figure>`;
  });
  return `<p class="lead">如果这些点子真的做成了，某个人会经历什么：</p><div class="board3">${panels.join('')}</div>`;
}

// --- 对比矩阵 ---------------------------------------------------------------------

function matrix(b, ix) {
  if (!(b.compare || []).length) return '';
  const bar = (v) => `<span class="mbar" title="${v}/5"><i style="width:${Math.round((v / 5) * 100)}%"></i></span>`;
  const rows = b.compare.map((c) => {
    const r = ix.by.get(c.id) || {};
    return `<tr><th><b>${esc(c.id)}</b> ${esc(short(r.text || '', 30))}</th><td>${bar(r.novelty || 0)}${esc(c.novelty)}</td><td>${bar(r.feasibility || 0)}${esc(c.feasibility)}</td><td>${bar(r.value || 0)}${esc(c.cost)}</td><td>${esc(c.risk)}</td></tr>`;
  });
  return `<p class="lead">分数条是组员投票的平均分（满分 5），文字是统筹的对比。</p><div class="tbl"><table class="matrix"><thead><tr><th>点子</th><th>新颖</th><th>可行</th><th>价值 · 投入</th><th>风险</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

// --- 黑马 -------------------------------------------------------------------------

function darkHorse(b, ix) {
  if (!b.dark_horse) return '';
  const r = ix.by.get(b.dark_horse) || { text: '' };
  return `<div class="horse"><div class="hk">黑马</div><div class="ht"><b>${esc(b.dark_horse)}</b> ${esc(r.text)}</div>${b.dark_horse_why ? `<p>${md(b.dark_horse_why)}</p>` : ''}<div class="meta">${r.dark_votes || 0} 位组员把它点为黑马${r.score ? `，总分 ${r.score}` : ''}；得分不在前列，但最大胆、值得小成本试一下。</div></div>`;
}

// --- 导图（卡片式） ---------------------------------------------------------------

function mapCards(data, b, ix) {
  const m = data.map || {};
  const src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(data.svg || '')}`;
  const branches = (m.branches || [])
    .map((br, i) => {
      const hue = HUES[i % HUES.length];
      const items = br.ideas
        .map((n) => `<li>${esc(n.text)}${(n.refs || []).length ? ` <span class="refs">${esc(n.refs.join(' '))}</span>` : ''}${badges(n.refs || [], ix, b)}</li>`)
        .join('');
      return `<section class="mbranch" style="--hue:${hue}"><h4>${esc(br.name)}</h4><ul>${items}</ul></section>`;
    })
    .join('');
  const note = m.degraded ? '<div class="meta">统筹没能归类，这里按成员分别列出了想法。</div>' : '';
  return `${note}<div class="mapc"><div class="mroot">${esc(m.title || '头脑风暴')}</div><div class="mbranches">${branches}</div></div>
<div class="mm-actions"><a download="mindmap.svg" href="${src}">下载 SVG 导图</a></div>
<details class="more"><summary>Mermaid 代码</summary><pre>${esc(data.mermaid || '')}</pre></details>`;
}

/** 灵感结果页。data = 整合步骤的产出（map / svg / mermaid / markdown / board / document）。 */
export function ideasCard(data, tableNo) {
  const b = data.board;
  const ix = index(b);
  const panes = [
    ['灵感墙', wall(data, b, ix)],
    ['象限图', quadrant(b, ix)],
    ['点子榜', ranking(b, ix)],
    ['杂交谱系', lineage(b, ix)],
    ['路线图', roadmap(data, b, ix)],
    ['故事板', storyboard(b, ix)],
    ['对比矩阵', matrix(b, ix)],
    ['黑马', darkHorse(b, ix)],
    ['导图', mapCards(data, b, ix)],
  ].filter(([, h]) => h);
  const lenses = Object.entries(b.lenses || {});
  const lensLine = lenses.length
    ? `<div class="lenses">本场抽签：${lenses.map(([who, l]) => `<span class="lens">${esc(who)}<b>${esc(l)}</b></span>`).join('')}</div>`
    : '';
  const doc = data.document || '';
  const exp = doc
    ? `<a class="btn ghost" download="头脑风暴.md" href="data:text/markdown;charset=utf-8,${encodeURIComponent(doc)}">导出文档（Markdown）</a>`
    : '';
  const n = `bt${tableNo}`;
  const radios = panes.map((_, i) => `<input type="radio" name="${n}" id="${n}-${i}"${i === 0 ? ' checked' : ''}>`).join('');
  const labels = panes.map(([t], i) => `<label for="${n}-${i}">${esc(t)}</label>`).join('');
  const bodies = panes.map(([t, h]) => `<div class="pane" data-view="${esc(t)}">${h}</div>`).join('');
  const total = (b.ranking || []).length;
  return `<div class="final ideas"><h3>灵感结果<small>${total} 个点子 · ${panes.length} 种看法</small>${exp}</h3>${lensLine}${radios}<div class="btabs" role="tablist">${labels}</div><div class="panes">${bodies}</div></div>`;
}
