// 「游戏区」页（分段栏第六个入口，docs/ARCADE.md）：大厅（游戏卡片）→ 开桌设置 → 牌桌。
// 牌局规则、成员出牌、金币和押注都在后台；这里只画、只发你的操作。成员想的时候每 0.7 秒拉一次牌桌。
// 牌面、牌背、筹码都是自己画的（CSS），不用别家素材。
// 斗地主 / 跑得快：点手牌选中，「出牌」把选中的发给后台（后台认牌型、比大小）；「提示」在能出的组合里轮着挑。

import { esc } from './util.js';

const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
const RANK = { T: '10' };
const POLL_MS = 700;
const KIND = { fold: '弃牌', check: '过牌', call: '跟注', raise: '加注', allin: '全下' };
const fmt = (n) => Number(n || 0).toLocaleString('zh-CN');
const CARD_GAMES = { doudizhu: '斗地主', paodekuai: '跑得快' };
const ORDER = '3456789TJQKA2';
const cardValue = (c) => (c === 'BJ' ? 16 : c === 'RJ' ? 17 : ORDER.indexOf(c[0]) + 3);
const sortCards = (cs) => [...cs].sort((a, b) => cardValue(a) - cardValue(b) || a.localeCompare(b));

/** 一张牌：?? 是背面。 */
export function cardHTML(c, cls = '') {
  if (!c || c === '??') return `<span class="ac-card back ${cls}" aria-label="背面"></span>`;
  if (c === 'BJ' || c === 'RJ') {
    const big = c === 'RJ';
    return `<span class="ac-card joker ${big ? 'red' : ''} ${cls}" aria-label="${big ? '大王' : '小王'}"><b>${big ? '大' : '小'}</b><i>王</i></span>`;
  }
  const red = c[1] === 'h' || c[1] === 'd';
  const r = RANK[c[0]] || c[0];
  return `<span class="ac-card ${red ? 'red' : ''} ${cls}" aria-label="${esc(r + SUIT[c[1]])}"><b>${esc(r)}</b><i>${SUIT[c[1]]}</i></span>`;
}

export class ArcadePage {
  /** @param {HTMLElement} root  @param {{api: object, avatar?: (id: string, name: string) => string}} opts */
  constructor(root, { api, avatar = null }) {
    this.root = root;
    this.api = api;
    this.avatarOf = avatar;
    this.info = null; // GET /api/arcade
    this.table = null; // 当前牌桌
    this.screen = 'lobby'; // lobby / setup / table
    this.pick = new Set();
    this.sit = true;
    this.think = null; // null = 跟配置
    this.betOn = '';
    this.betAmount = 100;
    this.raiseTo = null;
    this.game = 'holdem'; // 开桌设置里选的游戏
    this.chosen = new Set(); // 出牌类：选中的手牌
    this.hintAt = -1;
    this.error = '';
    this.timer = null;
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('input', (e) => this.onInput(e));
  }

  // --- 显示 / 隐藏 ----------------------------------------------------------------

  async show() {
    this.root.hidden = false;
    await this.refresh();
  }

  hide() {
    this.root.hidden = true;
    this.stopPoll();
  }

  async refresh() {
    try {
      this.info = await this.api.status();
      this.table = this.info.table;
      if (this.table) this.screen = 'table';
      else if (this.screen === 'table') this.screen = 'lobby';
      this.error = '';
    } catch (e) {
      this.error = e.message;
    }
    this.render();
    this.maybePoll();
  }

  maybePoll() {
    this.stopPoll();
    if (!this.root.hidden && this.table && (this.table.busy || this.table.thinking)) {
      this.timer = setTimeout(() => this.poll(), POLL_MS);
    }
  }

  stopPoll() {
    clearTimeout(this.timer);
    this.timer = null;
  }

  async poll() {
    try {
      this.table = await this.api.table();
      this.error = '';
    } catch (e) {
      this.error = e.message;
    }
    this.render();
    this.maybePoll();
  }

  /** 发一个操作，拿回最新牌桌。 */
  async run(fn) {
    try {
      this.table = await fn();
      this.error = '';
      if (this.table && this.table.seats) this.screen = 'table';
    } catch (e) {
      this.error = e.message;
    }
    this.raiseTo = null;
    this.chosen.clear();
    this.hintAt = -1;
    this.render();
    this.maybePoll();
  }

  /** 出牌类：手牌里还在的选中牌（轮询刷新后清掉已经出掉的）。 */
  keepChosen() {
    const me = this.table?.seats?.find((s) => s.id === 'you');
    const have = new Set(me?.cards || []);
    for (const c of [...this.chosen]) if (!have.has(c)) this.chosen.delete(c);
  }

  // --- 事件 ----------------------------------------------------------------------

  onInput(e) {
    const t = e.target;
    if (t.matches('[data-raise]')) {
      this.raiseTo = Number(t.value);
      const out = this.root.querySelector('[data-raise-out]');
      if (out) out.textContent = fmt(this.raiseTo);
    } else if (t.matches('[data-bet-amount]')) {
      this.betAmount = Math.max(1, Number(t.value) || 0);
    } else if (t.matches('[data-bet-on]')) {
      this.betOn = t.value;
    }
  }

  async onClick(e) {
    const b = e.target.closest('button,[data-member],[data-hand]');
    if (!b || b.disabled) return;
    const d = b.dataset;
    if (d.hand) {
      if (this.chosen.has(d.hand)) this.chosen.delete(d.hand);
      else this.chosen.add(d.hand);
      this.hintAt = -1;
      this.render();
    } else if (d.game) {
      const g = (this.info?.games || []).find((x) => x.id === d.game);
      if (!g || !g.ready) return;
      this.game = d.game;
      this.screen = 'setup';
      this.fitPicks();
      this.render();
    } else if (d.member) {
      const max = this.maxPick();
      if (this.pick.has(d.member)) this.pick.delete(d.member);
      else if (this.pick.size < max) this.pick.add(d.member);
      this.render();
    } else if (d.sit !== undefined) {
      this.sit = d.sit === '1';
      this.fitPicks();
      this.render();
    } else if (d.think !== undefined) {
      this.think = d.think === '1';
      if (this.screen === 'table') await this.run(() => this.api.think(this.think));
      else this.render();
    } else if (d.act === 'back') {
      this.screen = 'lobby';
      this.render();
    } else if (d.act === 'open') {
      const body = { game: this.game, members: [...this.pick], sit: this.sit, think: this.thinkOn() };
      await this.run(() => this.api.open(body));
    } else if (d.act === 'leave') {
      try {
        await this.api.leave();
      } catch (err) {
        this.error = err.message;
      }
      this.table = null;
      this.screen = 'lobby';
      await this.refresh();
    } else if (d.act === 'deal') {
      const bet = this.table && !this.table.seated && this.betOn ? [this.betOn, this.betAmount] : [null, 0];
      await this.run(() => this.api.deal(...bet));
    } else if (d.move) {
      const to = d.move === 'raise' ? Number(this.raiseTo ?? d.to) : d.to ? Number(d.to) : null;
      await this.run(() => this.api.act(d.move, to));
    } else if (d.bid !== undefined) {
      await this.run(() => this.api.play('bid', [], Number(d.bid)));
    } else if (d.shed === 'pass') {
      await this.run(() => this.api.play('pass'));
    } else if (d.shed === 'play') {
      await this.run(() => this.api.play('play', [...this.chosen]));
    } else if (d.shed === 'hint') {
      const plays = (this.table?.options || []).filter((o) => o.kind === 'play');
      if (plays.length) {
        this.hintAt = (this.hintAt + 1) % plays.length;
        this.chosen = new Set(plays[this.hintAt].cards);
      }
      this.render();
    } else if (d.shed === 'clear') {
      this.chosen.clear();
      this.hintAt = -1;
      this.render();
    } else if (d.quick) {
      this.raiseTo = Number(d.quick);
      this.render();
    }
  }

  /** 出牌类三人一桌：你上桌再挑两位，旁观挑三位；德州按配置的上限。 */
  maxPick() {
    if (CARD_GAMES[this.game]) return this.sit ? 2 : 3;
    return this.info?.holdem?.max_members || 7;
  }

  minPick() {
    if (CARD_GAMES[this.game]) return this.maxPick();
    return this.sit ? 1 : 2;
  }

  /** 换游戏 / 换上桌方式后，把选的成员补齐或删到合适人数。 */
  fitPicks() {
    const max = this.maxPick();
    const want = CARD_GAMES[this.game] ? max : Math.min(3, max);
    for (const id of [...this.pick].slice(max)) this.pick.delete(id);
    if (this.pick.size < want && this.info) {
      for (const m of this.info.members.filter((x) => x.available)) {
        if (this.pick.size >= want) break;
        this.pick.add(m.id);
      }
    }
  }

  thinkOn() {
    return this.think ?? this.info?.members_think ?? true;
  }

  // --- 画 ------------------------------------------------------------------------

  render() {
    const w = this.table?.wallet || this.info?.wallet || { coins: 0 };
    const sp = this.table?.spend || this.info?.spend || { today: 0, cap: 0 };
    const gift = w.gifted ? `<span class="ac-gift">今天送了 ${fmt(w.gifted)}</span>` : '';
    const body = this.screen === 'table' && this.table ? this.tableHTML() : this.screen === 'setup' ? this.setupHTML() : this.lobbyHTML();
    this.root.innerHTML = `<div class="ac-wrap">
      <div class="ac-head">
        <div class="bn-title"><span class="bn-zh">游戏区</span><span class="bn-en">GAME ROOM</span></div>
        <div class="ac-meta">
          <span class="ac-coin hb-glass" title="圆桌里的虚拟金币：每天送一点，不能充值、不能换真钱"><span class="ac-chip" aria-hidden="true"></span>金币 <b data-coins>${fmt(w.coins)}</b>${gift}</span>
          <span class="ac-spend hb-glass" title="成员用自己的模型出牌，今天真掏钱的部分（送的额度不算）；到上限后本地电脑代打">成员出牌今天 $${Number(sp.today).toFixed(3)} / $${Number(sp.cap).toFixed(2)}</span>
        </div>
      </div>
      ${this.error ? `<p class="ac-err" role="alert">${esc(this.error)}</p>` : ''}
      ${body}
    </div>`;
  }

  lobbyHTML() {
    const games = this.info?.games || [];
    const icon = { holdem: '♠', doudizhu: '♣', paodekuai: '♦', shengji: '♥', mahjong: '中', tanks: '◆' };
    return `<div class="ac-lobby">${games
      .map(
        (g) => `<button type="button" class="ac-game hb-glass" data-game="${esc(g.id)}" ${g.ready ? '' : 'disabled aria-disabled="true"'}>
          <span class="ac-gicon" aria-hidden="true">${icon[g.id] || '◇'}</span>
          <span class="ac-gname">${esc(g.name)}</span>
          <span class="ac-gsub">${esc(g.players)}${g.ready ? '' : ' · 即将上线'}</span>
        </button>`,
      )
      .join('')}</div>
      <p class="ac-hint">对手是圆桌的成员：她们用自己的模型想怎么出（每天有花费上限，到了换本地电脑代打）。你可以亲自上桌，也可以旁观押谁赢。</p>`;
  }

  avatar(id, name) {
    const art = this.avatarOf ? this.avatarOf(id, name) : '';
    return art || `<span class="ac-av ini">${esc((name || '?').slice(0, 1))}</span>`;
  }

  setupHTML() {
    const info = this.info || { members: [], holdem: {} };
    const h = info.holdem || {};
    const members = info.members
      .map((m) => {
        const on = this.pick.has(m.id);
        return `<button type="button" class="ac-pick${on ? ' on' : ''}" data-member="${esc(m.id)}" aria-pressed="${on}" ${m.available ? '' : 'disabled title="现在叫不到她（模型不可用）"'}>${this.avatar(m.id, m.name)}<span>${esc(m.name)}</span></button>`;
      })
      .join('');
    const think = this.thinkOn();
    const need = this.minPick();
    const cardGame = CARD_GAMES[this.game];
    const cg = info.cards || {};
    const intro = cardGame
      ? `三个人一桌。${this.sit ? '你加上两位成员' : '选三位成员打，你在旁边看'}。${this.game === 'doudizhu' ? '叫分定地主，炸弹、王炸、春天翻倍。' : '每人 16 张，黑桃 3 先出，有大必压，下家报单要出最大的。'}`
      : `无限注，盲注 ${fmt(h.blinds?.[0])} / ${fmt(h.blinds?.[1])}，每人 ${fmt(h.stack)} 筹码。最多选 ${h.max_members} 位成员。`;
    const money = cardGame
      ? this.sit
        ? `每一局按得分算金币：1 分 = ${fmt(cg.coin_per_point)} 金币，输的最多输光手上的金币。`
        : `每一局开始前可以押一位成员赢，一局最多 ${fmt(cg.max_bet)} 金币；赢了按人数赔（斗地主押中农民两人平分）。`
      : this.sit
        ? `上桌会从金币里带 ${fmt(h.stack)}（不够就全带上），离桌时剩多少换回多少。`
        : `每一手开始前可以押一位成员赢，一手最多 ${fmt(h.max_bet)} 金币；赢了按这一手的人数赔。`;
    return `<div class="ac-setup hb-glass">
      <h3>${cardGame || '德州扑克'} · 开一桌</h3>
      <p class="ac-sub">${intro}</p>
      <div class="ac-picks" role="group" aria-label="选成员">${members}</div>
      <div class="ac-row" role="group" aria-label="怎么玩">
        <button type="button" class="ac-seg" data-sit="1" aria-pressed="${this.sit}">我亲自上桌</button>
        <button type="button" class="ac-seg" data-sit="0" aria-pressed="${!this.sit}">旁观押注</button>
      </div>
      <div class="ac-row" role="group" aria-label="成员怎么想">
        <button type="button" class="ac-seg" data-think="1" aria-pressed="${think}">成员自己想（用模型，每天上限 $${Number(info.spend?.cap || 0).toFixed(2)}）</button>
        <button type="button" class="ac-seg" data-think="0" aria-pressed="${!think}">本地电脑代打（免费）</button>
      </div>
      <p class="ac-sub">${money}</p>
      <div class="ac-row">
        <button type="button" class="btn" data-act="back">返回</button>
        <button type="button" class="btn primary" data-act="open" ${this.pick.size >= need ? '' : 'disabled'}>开桌</button>
        ${this.pick.size < need ? `<span class="ac-sub">再选 ${need - this.pick.size} 位</span>` : ''}
      </div>
    </div>`;
  }

  seatHTML(s, i, n) {
    // 座位沿椭圆排开；你（或第一位）在正下方
    const a = Math.PI / 2 + (2 * Math.PI * i) / n;
    const x = 50 + 44 * Math.cos(a);
    const y = 50 + 40 * Math.sin(a);
    const cls = ['ac-seat', s.turn ? 'turn' : '', s.folded ? 'folded' : '', s.out ? 'out' : '', s.id === 'you' ? 'you' : ''].join(' ');
    const cards = (s.hole || []).map((c) => cardHTML(c, 'ac-mini')).join('');
    const brain = s.brain === 'model' ? '<span class="ac-brain" title="这一步是她自己想的">想</span>' : s.brain === 'local' ? '<span class="ac-brain local" title="这一步本地电脑代打">代</span>' : '';
    return `<div class="${cls}" style="left:${x}%;top:${y}%" data-seat="${esc(s.id)}">
      ${s.say ? `<span class="ac-say">${esc(s.say)}</span>` : ''}
      <div class="ac-sname">${s.id === 'you' ? '<span class="ac-av ini you">你</span>' : this.avatar(s.id, s.name)}<b>${esc(s.name)}</b>${s.dealer ? '<span class="ac-btn" title="庄家">D</span>' : ''}${brain}</div>
      <div class="ac-cards">${cards}</div>
      <div class="ac-stack">${s.out ? '没筹码了' : `${fmt(s.stack)}`}${s.bet ? ` · 下 ${fmt(s.bet)}` : ''}</div>
      ${s.thinking ? '<div class="ac-last thinking">在想<span class="ac-dots"><i></i><i></i><i></i></span></div>' : s.last ? `<div class="ac-last">${esc(s.last)}</div>` : ''}
    </div>`;
  }

  tableHTML() {
    const t = this.table;
    if (CARD_GAMES[t.game]) return this.shedHTML();
    const you = t.seats.findIndex((s) => s.id === 'you');
    const start = you >= 0 ? you : 0;
    const order = t.seats.map((_, k) => t.seats[(start + k) % t.seats.length]);
    const seats = order.map((s, i) => this.seatHTML(s, i, order.length)).join('');
    const board = [0, 1, 2, 3, 4].map((k) => (t.board[k] ? cardHTML(t.board[k]) : '<span class="ac-card slot"></span>')).join('');
    const status = t.street === 'idle' ? '等发牌' : `第 ${t.hand_no} 手 · ${t.street_zh}`;
    return `<div class="ac-table-wrap">
      <div class="ac-bar">
        <span class="ac-status">${esc(status)}</span>
        ${t.note ? `<span class="ac-note">${esc(t.note)}</span>` : ''}
        <span class="ac-grow"></span>
        <button type="button" class="ac-seg ac-sm" data-think="${t.think ? 0 : 1}" title="成员用模型想 / 本地电脑代打">${t.think ? '成员自己想 ✓' : '本地电脑代打 ✓'}</button>
        <button type="button" class="btn" data-act="leave">离桌</button>
      </div>
      <div class="ac-felt" role="group" aria-label="德州扑克牌桌">
        <div class="ac-center">
          <div class="ac-board">${board}</div>
          <div class="ac-pot"><span class="ac-chip" aria-hidden="true"></span>底池 <b>${fmt(t.pot)}</b></div>
        </div>
        ${seats}
      </div>
      ${this.controlsHTML()}
    </div>`;
  }

  controlsHTML() {
    const t = this.table;
    if (t.street === 'done' || t.street === 'idle') return this.betweenHTML();
    if (!t.seated) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.bet ? `你押了 ${esc(t.bet.name)} ${fmt(t.bet.amount)} 金币` : '这一手没押注'}，看她们打…</span></div>`;
    if (!t.options.length) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.thinking ? '等成员出牌…' : '等别人…'}</span></div>`;
    const btns = [];
    let raise = null;
    for (const o of t.options) {
      if (o.kind === 'raise') {
        raise = raise || o;
        continue;
      }
      const label = o.kind === 'call' ? `跟注 ${fmt(t.to_call)}` : o.kind === 'allin' ? `全下 ${fmt(o.to)}` : KIND[o.kind];
      btns.push(`<button type="button" class="btn ${o.kind === 'fold' ? '' : 'primary'}" data-move="${o.kind}" ${o.to ? `data-to="${o.to}"` : ''}>${label}</button>`);
    }
    let slider = '';
    if (t.raise && raise) {
      const [lo, hi] = t.raise;
      const v = Math.min(hi, Math.max(lo, this.raiseTo ?? lo));
      const quick = t.options.filter((o) => o.kind === 'raise').map((o) => `<button type="button" class="ac-seg ac-sm" data-quick="${o.to}">${fmt(o.to)}</button>`).join('');
      slider = `<div class="ac-raise">
        <label>加到 <b data-raise-out>${fmt(v)}</b><input type="range" data-raise min="${lo}" max="${hi}" step="${Math.max(1, t.blinds[0])}" value="${v}" aria-label="加注到多少"></label>
        ${quick}
        <button type="button" class="btn primary" data-move="raise" data-to="${v}">加注</button>
      </div>`;
    }
    return `<div class="ac-controls hb-glass"><span class="ac-sub">轮到你了</span>${btns.join('')}${slider}</div>`;
  }

  betweenHTML() {
    const t = this.table;
    const rows = (t.results || [])
      .filter((r) => r.won > 0 || r.hand)
      .map((r) => `<li class="${r.won > 0 ? 'win' : ''}"><b>${esc(r.name)}</b>${r.hand ? ` ${esc(r.hand)}` : ''}${r.won > 0 ? ` 赢 ${fmt(r.won)}` : r.folded ? ' 弃牌' : ''}</li>`)
      .join('');
    const lb = t.last_bet;
    const betLine = lb ? `<p class="ac-betres ${lb.win ? 'win' : ''}">你押 ${esc(lb.name)} ${fmt(lb.amount)}：${lb.win ? `赢了，拿回 ${fmt(lb.payout)}` : '输了'}</p>` : '';
    let bet = '';
    if (!t.seated && t.can_deal) {
      const opts = t.seats.filter((s) => s.stack > 0).map((s) => `<option value="${esc(s.id)}" ${this.betOn === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
      bet = `<label class="ac-betpick">押 <select data-bet-on aria-label="押谁赢"><option value="">不押</option>${opts}</select></label>
        <label class="ac-betpick">金币 <input type="number" data-bet-amount min="1" max="${this.info?.holdem?.max_bet || 500}" value="${this.betAmount}" aria-label="押多少"></label>`;
    }
    const over = !t.can_deal ? `<span class="ac-sub">${t.seated ? '这桌打不下去了（你或其他人没筹码了），离桌换一桌吧。' : '有筹码的不到两位了，换一桌吧。'}</span>` : '';
    return `<div class="ac-controls hb-glass">
      ${rows ? `<ul class="ac-results">${rows}</ul>` : ''}
      ${betLine}${bet}${over}
      <button type="button" class="btn primary" data-act="deal" ${t.can_deal ? '' : 'disabled'}>${t.hand_no ? '下一手' : '发牌'}</button>
    </div>`;
  }

  // --- 斗地主 / 跑得快 --------------------------------------------------------------

  shedSeatHTML(s, pos) {
    const cls = ['ac-seat', 'ac-shseat', pos, s.turn ? 'turn' : '', s.id === 'you' ? 'you' : ''].join(' ');
    const brain = s.brain === 'model' ? '<span class="ac-brain" title="这一步是她自己想的">想</span>' : s.brain === 'local' ? '<span class="ac-brain local" title="这一步本地电脑代打">代</span>' : '';
    const role = s.role ? `<span class="ac-role ${s.role === '地主' ? 'lord' : ''}">${esc(s.role)}</span>` : '';
    let cards = '';
    if (s.id !== 'you') {
      cards = s.cards.length
        ? `<div class="ac-fan open">${sortCards(s.cards).map((c) => cardHTML(c, 'ac-mini')).join('')}</div>`
        : `<div class="ac-fan">${s.count ? cardHTML('??', 'ac-mini') : ''}<span class="ac-count${s.count <= 2 && s.count ? ' warn' : ''}">${s.count ? `${s.count} 张${s.count === 1 ? ' · 报单' : ''}` : '出完了'}</span></div>`;
    }
    return `<div class="${cls}" data-seat="${esc(s.id)}">
      ${s.say ? `<span class="ac-say">${esc(s.say)}</span>` : ''}
      <div class="ac-sname">${s.id === 'you' ? '<span class="ac-av ini you">你</span>' : this.avatar(s.id, s.name)}<b>${esc(s.name)}</b>${role}${brain}</div>
      ${cards}
      ${s.thinking ? '<div class="ac-last thinking">在想<span class="ac-dots"><i></i><i></i><i></i></span></div>' : s.last ? `<div class="ac-last">${esc(s.last)}</div>` : ''}
    </div>`;
  }

  shedHTML() {
    const t = this.table;
    this.keepChosen();
    const name = CARD_GAMES[t.game];
    const you = t.seats.findIndex((s) => s.id === 'you');
    const start = you >= 0 ? you : 0;
    const n = t.seats.length;
    // 你在下方，下家在右，上家在左（逆时针出牌）
    const order = [t.seats[start], t.seats[(start + 1) % n], t.seats[(start + 2) % n]];
    const seats = [this.shedSeatHTML(order[0], 'bottom'), this.shedSeatHTML(order[1], 'right'), this.shedSeatHTML(order[2], 'left')].join('');
    const phaseZh = { idle: '等发牌', bid: '叫地主', play: '出牌中', done: '这一局结束' }[t.phase] || '';
    const status = t.round_no ? `${name} · 第 ${t.round_no} 局 · ${phaseZh}` : `${name} · 等发牌`;
    const lead = t.lead
      ? `<div class="ac-lead"><div class="ac-leadcards">${sortCards(t.lead.cards).map((c) => cardHTML(c)).join('')}</div><span class="ac-leadlabel">${esc((t.seats.find((s) => s.id === t.lead.by) || {}).name || '')}：${esc(t.lead.label)}</span></div>`
      : `<div class="ac-lead empty"><span class="ac-leadlabel">${t.phase === 'bid' ? '叫地主中' : t.phase === 'play' ? '新一轮，随便出' : ''}</span></div>`;
    let extra = '';
    if (t.game === 'doudizhu' && t.phase !== 'idle') {
      const bottom = t.bottom?.length ? t.bottom.map((c) => cardHTML(c, 'ac-mini')).join('') : [0, 1, 2].map(() => cardHTML('??', 'ac-mini')).join('');
      extra = `<div class="ac-ddz"><span class="ac-sub">底牌</span><span class="ac-bottom" title="底牌（地主拿走）">${bottom}</span><span>${t.bid ? `底分 ${t.bid}` : '还没叫'}${t.bombs ? ` · 炸了 ${t.bombs} 次` : ''}</span></div>`;
    }
    return `<div class="ac-table-wrap">
      <div class="ac-bar">
        <span class="ac-status">${esc(status)}</span>
        ${t.note ? `<span class="ac-note">${esc(t.note)}</span>` : ''}
        <span class="ac-grow"></span>
        <button type="button" class="ac-seg ac-sm" data-think="${t.think ? 0 : 1}" title="成员用模型想 / 本地电脑代打">${t.think ? '成员自己想 ✓' : '本地电脑代打 ✓'}</button>
        <button type="button" class="btn" data-act="leave">离桌</button>
      </div>
      <div class="ac-felt ac-shfelt" role="group" aria-label="${esc(name)}牌桌">
        <div class="ac-center">${extra}${lead}</div>
        ${seats}
      </div>
      ${this.handHTML(order[0])}
      ${this.shedControlsHTML()}
    </div>`;
  }

  handHTML(me) {
    if (!this.table.seated || me.id !== 'you') return '';
    const can = this.table.phase === 'play' && (this.table.options || []).some((o) => o.kind === 'play');
    const cards = sortCards(me.cards)
      .map((c) => {
        const on = this.chosen.has(c);
        return `<button type="button" class="ac-hcard${on ? ' on' : ''}" data-hand="${esc(c)}" aria-pressed="${on}" ${can ? '' : 'tabindex="-1"'}>${cardHTML(c)}</button>`;
      })
      .join('');
    return `<div class="ac-hand${can ? '' : ' idle'}" role="group" aria-label="你的手牌（点选要出的牌）">${cards || '<span class="ac-sub">出完了</span>'}</div>`;
  }

  shedControlsHTML() {
    const t = this.table;
    if (t.phase === 'done' || t.phase === 'idle') return this.shedBetweenHTML();
    if (!t.seated) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.bet ? `你押了 ${esc(t.bet.name)} ${fmt(t.bet.amount)} 金币` : '这一局没押注'}，看她们打…</span></div>`;
    if (!t.options.length) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.thinking ? '等成员出牌…' : '等别人…'}</span></div>`;
    if (t.phase === 'bid') {
      const btns = t.options.map((o) => `<button type="button" class="btn ${o.bid ? 'primary' : ''}" data-bid="${o.bid}">${o.bid ? `叫 ${o.bid} 分` : '不叫'}</button>`).join('');
      return `<div class="ac-controls hb-glass"><span class="ac-sub">轮到你叫地主</span>${btns}</div>`;
    }
    const canPass = t.options.some((o) => o.kind === 'pass');
    const hasPlay = t.options.some((o) => o.kind === 'play');
    const tip = hasPlay ? (this.chosen.size ? `选了 ${this.chosen.size} 张` : '点手牌选要出的，或者点「提示」') : '压不过，只能不要';
    return `<div class="ac-controls hb-glass">
      <span class="ac-sub">轮到你了 · ${tip}</span>
      ${canPass ? '<button type="button" class="btn" data-shed="pass">不要</button>' : ''}
      ${hasPlay ? '<button type="button" class="btn" data-shed="hint">提示</button>' : ''}
      ${this.chosen.size ? '<button type="button" class="btn" data-shed="clear">重选</button>' : ''}
      ${hasPlay ? `<button type="button" class="btn primary" data-shed="play" ${this.chosen.size ? '' : 'disabled'}>出牌</button>` : ''}
    </div>`;
  }

  shedBetweenHTML() {
    const t = this.table;
    const rows = (t.results || [])
      .map((r) => {
        const pts = Number(r.points);
        const tag = [r.role, r.shut ? '被关' : '', r.left ? `剩 ${r.left} 张` : '出完'].filter(Boolean).join(' · ');
        return `<li class="${pts > 0 ? 'win' : ''}"><b>${esc(r.name)}</b> ${pts > 0 ? '+' : ''}${pts} 分 <span class="ac-sub">${esc(tag)}</span></li>`;
      })
      .join('');
    const sm = t.summary;
    const sumLine = sm ? `<p class="ac-sub">${esc(sm.winner)}赢 · 底分 ${t.bid}${t.bombs ? ` × ${2 ** t.bombs}（炸弹）` : ''}${sm.spring ? ' × 2（春天）' : ''} = ${sm.mult} 倍</p>` : '';
    const coins = t.last_coins;
    const coinLine = coins !== null && coins !== undefined ? `<p class="ac-betres ${coins > 0 ? 'win' : ''}">你这一局 ${coins >= 0 ? `赢 ${fmt(coins)}` : `输 ${fmt(-coins)}`} 金币</p>` : '';
    const lb = t.last_bet;
    const betLine = lb ? `<p class="ac-betres ${lb.win ? 'win' : ''}">你押 ${esc(lb.name)} ${fmt(lb.amount)}：${lb.win ? `赢了，拿回 ${fmt(lb.payout)}` : '输了'}</p>` : '';
    let bet = '';
    if (!t.seated && t.can_deal) {
      const opts = t.seats.map((s) => `<option value="${esc(s.id)}" ${this.betOn === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
      bet = `<label class="ac-betpick">押 <select data-bet-on aria-label="押谁赢"><option value="">不押</option>${opts}</select></label>
        <label class="ac-betpick">金币 <input type="number" data-bet-amount min="1" max="${this.info?.cards?.max_bet || 500}" value="${this.betAmount}" aria-label="押多少"></label>`;
    }
    const broke = t.seated && !t.can_deal && (t.wallet?.coins || 0) <= 0 ? '<span class="ac-sub">金币输光了，明天再来领，或者先旁观押注。</span>' : '';
    return `<div class="ac-controls hb-glass">
      ${rows ? `<ul class="ac-results">${rows}</ul>` : ''}
      ${sumLine}${coinLine}${betLine}${bet}${broke}
      <button type="button" class="btn primary" data-act="deal" ${t.can_deal ? '' : 'disabled'}>${t.round_no ? '再来一局' : '发牌'}</button>
    </div>`;
  }
}
