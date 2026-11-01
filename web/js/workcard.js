// 聊天里的活交给办公室（docs/CHAT.md G14）：交办卡片、办公室做好的结果卡片、选项按钮、聊天里发的文件。
// - 交办卡片：成员说一两句后挂一张卡片，「交给办公室」你点了才开工（会花钱），「先不用」收起。
// - 结果卡片：以那位成员的名义发回来；标题、结论（表格照常显示，```mermaid 画成图，$$…$$ 排成公式）、
//   生成的文件、去办公室看。Mermaid 和 KaTeX 在 /vendor/ 本地，卡片里有图或公式时才加载。
// - 选项按钮：成员给几个选项，点哪个就替你发哪句。
// 模型写的内容一律转义后再排版；不执行任何模型写的脚本或网页。

import { md } from './view.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const STATE = {
  pending: '',
  running: '办公室在做，做好了会发回来',
  done: '办公室做好了',
  failed: '办公室没办成',
  dismissed: '先不用了',
};

/** 交办卡片（挂在成员那条消息下面）。 */
export function handoffHTML(card, msgId, canOpen) {
  if (!card || !card.task) return '';
  const state = card.state || 'pending';
  const look = card.session_id && canOpen ? ` <button type="button" class="link" data-session="${esc(card.session_id)}">去办公室看</button>` : '';
  const act =
    state === 'pending'
      ? `<div class="wc-acts"><button type="button" class="btn primary" data-handoff-start="${msgId}" title="按办公室模式开一件事（会花钱，超过单题门槛时还会问你）">交给办公室</button><button type="button" class="btn ghost" data-handoff-dismiss="${msgId}">先不用</button></div>`
      : `<small class="wc-state ${esc(state)}">${esc(STATE[state] || '')}${look}</small>`;
  return `<div class="wc-handoff${state === 'dismissed' ? ' off' : ''}"><span class="wc-kicker">交给办公室</span><p>${esc(card.task)}</p>${act}</div>`;
}

/** 选项按钮：点了替你发这句。 */
export function choicesHTML(list) {
  if (!Array.isArray(list) || list.length < 2) return '';
  return `<div class="wc-choices">${list.map((c) => `<button type="button" class="btn ghost" data-choice="${esc(c)}">${esc(c)}</button>`).join('')}</div>`;
}

/** 你在聊天里发的文件：一排小卡片（只显示名字和大小）。 */
export function filesHTML(list) {
  if (!Array.isArray(list) || !list.length) return '';
  const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round((n || 0) / 1024))} KB`);
  return `<div class="wc-files">${list.map((f) => `<span class="wc-file"><b>${esc(f.name)}</b><small>${esc(size(f.size))}</small></span>`).join('')}</div>`;
}

/** 结论正文：```mermaid 块 → 图，其它代码块原样，$$…$$ / \[…\] → 公式，其余按聊天里的轻量 Markdown。 */
export function bodyHTML(text) {
  const out = [];
  const parts = String(text || '').split(/^```/m);
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf('\n');
      const lang = (nl < 0 ? part : part.slice(0, nl)).trim().toLowerCase();
      const code = nl < 0 ? '' : part.slice(nl + 1).replace(/\n$/, '');
      out.push(lang === 'mermaid' ? `<pre class="wc-mermaid">${esc(code)}</pre>` : `<pre class="wc-code"><code>${esc(code)}</code></pre>`);
      return;
    }
    const chunks = part.split(/(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\])/);
    chunks.forEach((c, j) => {
      if (j % 2 === 1) {
        const tex = c.startsWith('$$') ? c.slice(2, -2) : c.slice(2, -2);
        out.push(`<div class="wc-math" data-tex="${esc(tex.trim())}">${esc(tex.trim())}</div>`);
      } else if (c.trim()) {
        out.push(`<div class="wc-md">${md(c.trim())}</div>`);
      }
    });
  });
  return out.join('');
}

/** 办公室做好的结果卡片。 */
export function workCardHTML(card, canOpen) {
  if (!card) return '';
  const sid = esc(card.session_id || '');
  const file = (f) => {
    const href = `/api/sessions/${sid}/files/${encodeURIComponent(f.id)}`;
    if (f.kind === 'image' || /^image\//.test(f.mime || '')) return `<a class="wc-img" href="${href}?inline=1" target="_blank" rel="noopener"><img src="${href}?inline=1" alt="${esc(f.name)}" loading="lazy"></a>`;
    return `<a class="wc-file" href="${href}" download><b>${esc(f.name)}</b><small>下载</small></a>`;
  };
  const files = (card.files || []).length ? `<div class="wc-files">${card.files.map(file).join('')}</div>` : '';
  const body = card.ok ? bodyHTML(card.text) : '<p class="wc-fail">这件事没办成，去办公室看看原因。</p>';
  const more = card.cut ? '<small class="hint">只显示了前一部分，全文去办公室看。</small>' : '';
  const look = canOpen && sid ? `<button type="button" class="link" data-session="${sid}">去办公室看</button>` : '';
  const cost = typeof card.cost_usd === 'number' ? `<small>花了 $${card.cost_usd.toFixed(4)}</small>` : '';
  return `<article class="wc-card${card.ok ? '' : ' bad'}"><header><span class="wc-kicker">${card.ok ? '办公室做好了' : '办公室没办成'}</span><b>${esc(card.title || '')}</b></header><details class="wc-body" open><summary>结论</summary>${body}${more}</details>${files}<footer>${cost}${look}</footer></article>`;
}

// --- 图表和公式：按需加载本地库 ------------------------------------------------------

const loaded = {};
function script(src) {
  loaded[src] ||= new Promise((ok, bad) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => ok();
    s.onerror = () => bad(new Error(`加载失败：${src}`));
    document.head.appendChild(s);
  });
  return loaded[src];
}
function style(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = href;
  document.head.appendChild(l);
}

let seq = 0;
/** 把 root 里还没画的 Mermaid 图和公式画出来；库加载失败时保留原文。 */
export async function enhance(root) {
  if (!root) return;
  const maths = [...root.querySelectorAll('.wc-math:not([data-done])')];
  const charts = [...root.querySelectorAll('.wc-mermaid:not([data-done])')];
  if (maths.length) {
    try {
      style('/vendor/katex/katex.min.css');
      await script('/vendor/katex/katex.min.js');
      for (const el of maths) {
        el.dataset.done = '1';
        window.katex.render(el.dataset.tex || '', el, { displayMode: true, throwOnError: false, trust: false });
      }
    } catch {
      /* 没加载上就显示原文 */
    }
  }
  if (charts.length) {
    try {
      await script('/vendor/mermaid/mermaid.min.js');
      const dark = matchMedia('(prefers-color-scheme: dark)').matches;
      window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'neutral' });
      for (const el of charts) {
        el.dataset.done = '1';
        try {
          const { svg } = await window.mermaid.render(`wc-m${(seq += 1)}`, el.textContent);
          const box = document.createElement('div');
          box.className = 'wc-chart';
          box.innerHTML = svg;
          el.replaceWith(box);
        } catch {
          el.classList.add('bad');
        }
      }
    } catch {
      /* 没加载上就显示原文 */
    }
  }
}
