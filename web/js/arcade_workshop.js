// 游戏工坊（docs/ARCADE.md「游戏工坊」）：大厅下面的一块。写要求 → 工作台的编程助手做游戏 → 做好的列在这里，点「玩」在沙箱里打开。
// 这里只拼页面；数据和操作都走 /api/arcade/workshop。

import { esc } from './util.js';

const STATUS = {
  building: '主做的助手在写游戏',
  reviewing: '辅助的助手在试玩、找错、修改',
  checking: '程序在浏览器里试玩检查',
  fixing: '检查发现问题，交回作者返工',
  done: '做好了',
  failed: '没做成',
  cancelled: '你叫停了',
};
const ROLE = { build: '主做', review: '试玩修错', fix: '返工' };
export const LIVE = new Set(['building', 'reviewing', 'checking', 'fixing']);

function teamLine(team) {
  if (!team?.length) return '';
  const lead = team.filter((m) => m.role === '主做').map((m) => m.label);
  const help = team.filter((m) => m.role === '辅助').map((m) => m.label);
  const off = team.filter((m) => m.blocked).map((m) => `${m.label}（${m.blocked}）`);
  return `${esc(lead.join('、'))} 主做${help.length ? `，${esc(help.join('、'))} 依次试玩修错` : ''}。走工作台的包月订阅，不花圆桌的 API 额度。${off.length ? `<br><span class="ac-wsoff">现在用不了：${esc(off.join('；'))}</span>` : ''}`;
}

/** 正在做（或刚做完）的那一个：状态、每一步谁干的、最近几条记录。 */
export function liveHTML(job) {
  if (!job) return '';
  const live = LIVE.has(job.status);
  const steps = (job.steps || [])
    .map((s) => {
      const mark = s.ok === null || s.ok === undefined ? '…' : s.ok ? '✓' : '✗';
      return `<li class="ac-wsstep${s.ok === false ? ' bad' : ''}"><b>${mark}</b> ${esc(ROLE[s.role] || s.role)} · ${esc(s.label || '挑人中')}</li>`;
    })
    .join('');
  const logs = (job.log || [])
    .slice(-4)
    .map((l) => `<li>${esc(l.text)}</li>`)
    .join('');
  return `<div class="ac-wsjob${live ? ' live' : ''}" data-ws-status="${esc(job.status)}">
    <div class="ac-row"><span class="ac-wsdot" aria-hidden="true"></span><b>${esc(STATUS[job.status] || job.status)}</b><span class="ac-sub">「${esc(job.text.length > 40 ? `${job.text.slice(0, 39)}…` : job.text)}」</span>
    ${live ? '<span class="ac-grow"></span><button type="button" class="btn" data-act="ws-cancel">叫停</button>' : ''}</div>
    ${steps ? `<ol class="ac-wssteps">${steps}</ol>` : ''}
    ${logs ? `<ul class="ac-wslog">${logs}</ul>` : ''}
  </div>`;
}

/** 做好的游戏。 */
export function gamesHTML(games, confirmDel) {
  if (!games?.length) return '<p class="ac-sub">还没有做好的游戏。写一句想玩什么，点「开做」。</p>';
  return `<div class="ac-wsgames">${games
    .map((g) => {
      const warn = g.issues?.length ? `<span class="ac-wswarn" title="${esc(g.issues.join('\n'))}">还有 ${g.issues.length} 个小问题</span>` : '';
      const del = confirmDel === g.id ? '再点一下删除' : '删除';
      return `<div class="ac-wsgame hb-glass" data-ws-game="${esc(g.id)}">
        <div class="ac-wsgt">${esc(g.title || '没起名的游戏')}${warn}</div>
        ${g.summary ? `<p class="ac-sub">${esc(g.summary)}</p>` : ''}
        ${g.controls ? `<p class="ac-sub">操作：${esc(g.controls)}</p>` : ''}
        <div class="ac-row"><button type="button" class="btn primary" data-ws-play="${esc(g.id)}">玩</button><button type="button" class="btn${confirmDel === g.id ? ' danger' : ''}" data-ws-del="${esc(g.id)}">${del}</button></div>
      </div>`;
    })
    .join('')}</div>`;
}

/** 大厅里的整块工坊。 */
export function workshopHTML(ws, draft, confirmDel) {
  if (!ws) return '';
  const blocked = ws.unavailable;
  const busy = ws.busy;
  const max = ws.max_request_chars || 2000;
  return `<section class="ac-shop hb-glass" aria-label="游戏工坊">
    <h3>游戏工坊 <span class="ac-sub">按你的要求自动做游戏</span></h3>
    <p class="ac-sub">${teamLine(ws.team)}做出来的游戏是原创的网页小游戏，在沙箱里打开，碰不到圆桌的其他东西。</p>
    <textarea class="ac-wstext" data-ws-text rows="3" maxlength="${max}" placeholder="比如：一个躲陨石的太空小游戏，越玩越快，手机上也能玩" aria-label="想要一个什么游戏">${esc(draft)}</textarea>
    <div class="ac-row">
      <button type="button" class="btn primary" data-act="ws-make" ${busy || blocked ? 'disabled' : ''}>开做</button>
      <span class="ac-sub">${busy ? '正在做上一个，做完才能开新的' : blocked ? esc(blocked) : '一般要十几分钟到半小时'}</span>
    </div>
    <div data-ws-live>${liveHTML(ws.job)}</div>
    <div data-ws-games>${gamesHTML(ws.games, confirmDel)}</div>
  </section>`;
}

/** 玩一个做好的游戏：沙箱 iframe（只许跑脚本，不同源，碰不到圆桌的接口和存储）。 */
export function playHTML(game) {
  const src = `/arcade-games/${encodeURIComponent(game.id)}/game.html`;
  return `<div class="ac-wsplay">
    <div class="ac-row"><button type="button" class="btn" data-act="ws-back">回大厅</button><b>${esc(game.title || '游戏')}</b>${game.controls ? `<span class="ac-sub">${esc(game.controls)}</span>` : ''}</div>
    <iframe class="ac-wsframe" data-ws-frame sandbox="allow-scripts" src="${src}" title="${esc(game.title || '工坊做的游戏')}" allow="autoplay"></iframe>
  </div>`;
}
