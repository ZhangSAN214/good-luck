// 「游戏区」页（分段栏第六个入口，docs/ARCADE.md）：大厅（游戏卡片）→ 开桌设置 → 牌桌。
// 牌局规则、成员出牌、金币和押注都在后台；这里只画、只发你的操作。成员想的时候每 0.7 秒拉一次牌桌。
// 牌面、牌背、筹码都是自己画的（CSS），不用别家素材。
// 斗地主 / 跑得快：点手牌选中，「出牌」把选中的发给后台（后台认牌型、比大小）；「提示」在能出的组合里轮着挑。

import { esc } from './util.js';
import { setSfx, sfx, sfxOn } from './arcade_sfx.js';
import { TankGame } from './tanks.js';
import { gamesHTML, liveHTML, playHTML, workshopHTML } from './arcade_workshop.js';

const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
const RANK = { T: '10' };
const POLL_MS = 700;
const WS_POLL_MS = 3000; // 工坊在做游戏时多久看一次进度
const KIND = { fold: '弃牌', check: '过牌', call: '跟注', raise: '加注', allin: '全下' };
const fmt = (n) => Number(n || 0).toLocaleString('zh-CN');
const CARD_GAMES = { doudizhu: '斗地主', paodekuai: '跑得快', shengji: '升级', mahjong: '麻将' };
const TABLE_SIZE = { doudizhu: 3, paodekuai: 3, shengji: 4, mahjong: 4 };
const MJ_NUM = '一二三四五六七八九';
const MJ_SUIT = { m: '万', s: '条', p: '筒' };
const MJ_HONOR = '东南西北中发白';

/** 一张麻将牌：?? 是背面。牌面是自己画的（汉字 + 花色色块），不用别家素材。 */
export function tileHTML(t, cls = '') {
  if (!t || t === '??') return `<span class="ac-tile back ${cls}" aria-label="牌背"></span>`;
  const n = Number(t[0]);
  const s = t[1];
  if (s === 'z') {
    const ch = MJ_HONOR[n - 1];
    const tone = n === 5 ? 'red' : n === 6 ? 'green' : n === 7 ? 'blank' : 'ink';
    return `<span class="ac-tile z ${tone} ${cls}" aria-label="${ch}"><b>${n === 7 ? '' : ch}</b></span>`;
  }
  return `<span class="ac-tile ${s} ${cls}" aria-label="${MJ_NUM[n - 1]}${MJ_SUIT[s]}"><b>${MJ_NUM[n - 1]}</b><i>${MJ_SUIT[s]}</i></span>`;
}
const tileZh = (t) => (t[1] === 'z' ? MJ_HONOR[Number(t[0]) - 1] : `${MJ_NUM[Number(t[0]) - 1]}${MJ_SUIT[t[1]]}`);
const SUIT_ZH = SUIT;
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
  const pip = cls.includes('ac-mini') ? '' : `<em aria-hidden="true">${SUIT[c[1]]}</em>`;
  return `<span class="ac-card ${red ? 'red' : ''} ${cls}" aria-label="${esc(r + SUIT[c[1]])}"><b>${esc(r)}</b><i>${SUIT[c[1]]}</i>${pip}</span>`;
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
    this.chosen = new Set(); // 出牌类：选中的手牌（按手牌里的位置记，升级有两副牌、同一张牌会有两张）
    this.handOrder = []; // 手牌显示顺序
    this.handKey = '';
    this.hintAt = -1;
    this.error = '';
    this.timer = null;
    this.snap = null; // 上一次画的牌桌摘要：和这次比，决定放什么动效和音效
    this.tankUI = null; // 铁甲突围：正在打的那一局
    this.tankRun = null; // 后台给的这一局（凭证、种子）
    this.tankLevel = 'normal';
    this.tankMode = 'ffa'; // 各自为战 / 2 对 2
    this.ws = null; // 游戏工坊的状态
    this.wsDraft = ''; // 还没交的要求（重画时不丢）
    this.wsDel = ''; // 点过一次「删除」、等第二下确认的那个游戏
    this.wsGame = null; // 正在玩的工坊游戏
    this.wsTimer = null;
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
    clearTimeout(this.wsTimer);
    if (this.tankUI && !this.tankUI.paused && !this.tankUI.ended) this.tankUI.togglePause();
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
    await this.loadWorkshop();
    this.render();
    this.maybePoll();
  }

  // --- 游戏工坊 --------------------------------------------------------------------

  async loadWorkshop() {
    if (!this.api.workshop) return;
    try {
      this.ws = await this.api.workshop();
    } catch {
      this.ws = null; // 工坊出问题不影响打牌
    }
    this.wsPoll();
  }

  wsPoll() {
    clearTimeout(this.wsTimer);
    if (this.root.hidden || !this.ws?.busy) return;
    this.wsTimer = setTimeout(async () => {
      const was = this.ws;
      await this.loadWorkshop();
      if (!this.ws || this.screen !== 'lobby') return;
      if (was?.busy && !this.ws.busy) {
        this.render(); // 做完了：整块重画（按钮、游戏列表）
        if (this.ws.job?.status === 'done') sfx('win');
        return;
      }
      // 还在做：只换进度那一块，不打断正在写的要求
      const live = this.root.querySelector('[data-ws-live]');
      if (live) live.innerHTML = liveHTML(this.ws.job);
    }, WS_POLL_MS);
  }

  async wsRun(fn) {
    try {
      this.ws = await fn();
      this.error = '';
    } catch (e) {
      this.error = e.message;
    }
    this.render();
    this.wsPoll();
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
    const before = JSON.stringify(this.table);
    const err = this.error;
    try {
      this.table = await this.api.table();
      this.error = '';
    } catch (e) {
      this.error = e.message;
    }
    // 牌桌没变就不重画（不打断正在放的发牌、出牌动效）
    if (JSON.stringify(this.table) !== before || this.error !== err) this.render();
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

  /** 出牌类：手牌变了（出掉了、拿了底牌）就清掉选中的。 */
  keepChosen() {
    const me = this.table?.seats?.find((s) => s.id === 'you');
    const order = me ? (['shengji', 'mahjong'].includes(this.table.game) ? [...me.cards] : sortCards(me.cards)) : [];
    const key = order.join(',');
    if (key !== this.handKey) {
      this.chosen.clear();
      this.hintAt = -1;
    }
    this.handOrder = order;
    this.handKey = key;
  }

  chosenCards() {
    return [...this.chosen].sort((a, b) => a - b).map((i) => this.handOrder[i]).filter(Boolean);
  }

  /** 把一组牌（可能有重复）对到手牌的位置上选中。 */
  chooseCards(cards) {
    const used = new Set();
    for (const c of cards) {
      const i = this.handOrder.findIndex((x, k) => x === c && !used.has(k));
      if (i >= 0) used.add(i);
    }
    this.chosen = used;
  }

  // --- 事件 ----------------------------------------------------------------------

  onInput(e) {
    const t = e.target;
    if (t.matches('[data-ws-text]')) {
      this.wsDraft = t.value;
    } else if (t.matches('[data-raise]')) {
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
    if (d.sfx !== undefined) {
      setSfx(d.sfx === '1');
      if (d.sfx === '1') sfx('pick');
      this.render();
    } else if (d.hand !== undefined && this.table?.game === 'mahjong') {
      const k = Number(d.hand);
      const can = (this.table.options || []).some((o) => o.kind === 'play');
      if (!can) return;
      if (this.chosen.has(k)) {
        await this.run(() => this.api.play('play', [this.handOrder[k]]));
        return;
      }
      this.chosen = new Set([k]);
      sfx('pick');
      this.render();
    } else if (d.hand !== undefined) {
      const k = Number(d.hand);
      if (this.chosen.has(k)) this.chosen.delete(k);
      else this.chosen.add(k);
      sfx('pick');
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
    } else if (d.god !== undefined) {
      await this.run(() => this.api.god(d.god === '1'));
    } else if (d.act === 'back') {
      this.screen = 'lobby';
      this.render();
    } else if (d.act === 'ws-make') {
      const text = this.wsDraft.trim();
      if (!text) {
        this.error = '先写一下想要什么游戏';
        this.render();
        return;
      }
      await this.wsRun(async () => {
        const res = await this.api.workshopMake(text);
        this.wsDraft = '';
        return res;
      });
    } else if (d.act === 'ws-cancel') {
      await this.wsRun(() => this.api.workshopCancel());
    } else if (d.wsDel) {
      if (this.wsDel !== d.wsDel) {
        this.wsDel = d.wsDel;
        const box = this.root.querySelector('[data-ws-games]');
        if (box) box.innerHTML = gamesHTML(this.ws?.games, this.wsDel);
        return;
      }
      this.wsDel = '';
      await this.wsRun(() => this.api.workshopDelete(d.wsDel));
    } else if (d.wsPlay) {
      this.wsGame = (this.ws?.games || []).find((g) => g.id === d.wsPlay) || null;
      if (!this.wsGame) return;
      this.screen = 'wsplay';
      this.render();
      this.root.querySelector('[data-ws-frame]')?.focus();
    } else if (d.act === 'ws-back') {
      this.wsGame = null;
      this.screen = 'lobby';
      await this.refresh();
    } else if (d.act === 'open' && this.game === 'tanks') {
      await this.startTanks();
    } else if (d.act === 'tanks-again') {
      await this.startTanks();
    } else if (d.act === 'tanks-pause') {
      this.tankUI?.togglePause();
    } else if (d.act === 'tanks-back' || d.act === 'tanks-quit') {
      this.stopTanks();
      this.screen = 'lobby';
      await this.refresh();
    } else if (d.tmode) {
      this.tankMode = d.tmode === 'team' ? 'team' : 'ffa';
      this.fitPicks();
      this.render();
    } else if (d.level) {
      this.tankLevel = d.level;
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
    } else if (d.shed === 'play' || d.shed === 'bury') {
      const cards = this.chosenCards();
      await this.run(() => this.api.play(d.shed, cards));
    } else if (d.shed === 'hint') {
      const kind = this.table?.phase === 'bury' ? 'bury' : 'play';
      const plays = (this.table?.options || []).filter((o) => o.kind === kind);
      if (plays.length) {
        this.hintAt = (this.hintAt + 1) % plays.length;
        this.chooseCards(plays[this.hintAt].cards);
      }
      this.render();
    } else if (d.sjopt !== undefined) {
      const o = (this.table?.options || [])[Number(d.sjopt)];
      if (o) await this.run(() => this.api.play(o.kind, o.cards || [], 0));
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
    if (this.game === 'tanks' && this.tankMode === 'team') return this.sit ? 3 : 4;
    if (this.game === 'tanks') return this.sit ? this.info?.tanks?.max_members || 3 : 4;
    if (CARD_GAMES[this.game]) return TABLE_SIZE[this.game] - (this.sit ? 1 : 0);
    return this.info?.holdem?.max_members || 7;
  }

  minPick() {
    if (this.game === 'tanks' && this.tankMode === 'team') return this.maxPick();
    if (this.game === 'tanks') return this.sit ? 1 : 2;
    if (CARD_GAMES[this.game]) return this.maxPick();
    return this.sit ? 1 : 2;
  }

  /** 换游戏 / 换上桌方式后，把选的成员补齐或删到合适人数。 */
  fitPicks() {
    const max = this.maxPick();
    const want = CARD_GAMES[this.game] || this.game === 'tanks' ? max : Math.min(3, max);
    for (const id of [...this.pick].slice(max)) this.pick.delete(id);
    if (this.pick.size < want && this.info) {
      for (const m of this.info.members.filter((x) => x.available)) {
        if (this.pick.size >= want) break;
        this.pick.add(m.id);
      }
    }
  }

  /** 旁观时的上帝视角开关（看所有人的手牌）。 */
  godBtn() {
    const t = this.table;
    if (!t || t.seated) return '';
    return `<button type="button" class="ac-seg ac-sm" data-god="${t.god ? 0 : 1}" aria-pressed="${!!t.god}" title="旁观时看所有人的手牌">上帝视角${t.god ? ' ✓' : ''}</button>`;
  }

  thinkOn() {
    return this.think ?? this.info?.members_think ?? true;
  }

  // --- 画 ------------------------------------------------------------------------

  render() {
    const w = this.table?.wallet || this.info?.wallet || { coins: 0 };
    const sp = this.table?.spend || this.info?.spend || { today: 0, cap: 0 };
    const gift = w.gifted ? `<span class="ac-gift">今天送了 ${fmt(w.gifted)}</span>` : '';
    const body =
      this.screen === 'tanks' && this.tankUI
        ? '<div data-tankslot></div>'
        : this.screen === 'wsplay' && this.wsGame
          ? playHTML(this.wsGame)
          : this.screen === 'table' && this.table
          ? this.tableHTML()
          : this.screen === 'setup'
            ? this.setupHTML()
            : this.lobbyHTML();
    const fx = this.diffFx(w.coins);
    this.root.innerHTML = `<div class="ac-wrap">
      <div class="ac-head">
        <div class="bn-title"><span class="bn-zh">游戏区</span><span class="bn-en">GAME ROOM</span></div>
        <div class="ac-meta">
          <span class="ac-coin hb-glass" title="圆桌里的虚拟金币：每天送一点，不能充值、不能换真钱"><span class="ac-chip" aria-hidden="true"></span>金币 <b data-coins>${fmt(w.coins)}</b>${gift}</span>
          <span class="ac-spend hb-glass" title="成员用自己的模型出牌，今天真掏钱的部分（送的额度不算）；到上限后本地电脑代打">成员出牌今天 $${Number(sp.today).toFixed(3)} / $${Number(sp.cap).toFixed(2)}</span>
          <button type="button" class="ac-seg ac-sm" data-sfx="${sfxOn() ? 0 : 1}" aria-pressed="${sfxOn()}" title="出牌、炸弹、筹码的声音（自己合成的，开关存在这台电脑上）">音效${sfxOn() ? ' 开' : ' 关'}</button>
        </div>
      </div>
      ${this.error ? `<p class="ac-err" role="alert">${esc(this.error)}</p>` : ''}
      ${body}
    </div>`;
    const slot = this.root.querySelector('[data-tankslot]');
    if (slot && this.tankUI) {
      slot.replaceWith(this.tankUI.el);
      this.tankUI.resize();
    }
    this.applyFx(fx);
  }

  // --- 动效和音效：和上一次画的比，变了什么就放什么 -----------------------------------

  /** 牌桌摘要（只留比较要用的）。 */
  snapshot(coins) {
    const t = this.screen === 'table' ? this.table : null;
    if (!t || !t.seats) return { coins, table: '' };
    const open = (s) => (s.cards?.length ? 1 : 0) + ((s.hole || []).some((c) => c && c !== '??') ? 1 : 0);
    const me = t.seats.find((s) => s.id === 'you');
    return {
      coins,
      table: `${t.game}|${t.seats.map((s) => s.id).join(',')}`,
      round: `${t.hand_no ?? ''}|${t.round_no ?? ''}`,
      phase: t.street || t.phase || '',
      last: Object.fromEntries(t.seats.map((s) => [s.id, s.thinking ? '' : s.last || ''])),
      lead: t.lead ? `${t.lead.by}|${t.lead.label}|${t.lead.cards.join('')}` : '',
      leadLabel: t.lead?.label || '',
      trick: (t.trick || []).map((p) => `${p.seat}:${p.cards.join('')}`),
      board: (t.board || []).length,
      open: Object.fromEntries(t.seats.map((s) => [s.id, open(s)])),
      turn: !!(t.seated && t.options?.length),
      hand: me ? (me.cards || me.hole || []).length : 0,
    };
  }

  /** 这次要放的动效（画之前算，画完了 applyFx 套到新的元素上）。 */
  diffFx(coins) {
    const now = this.snapshot(coins);
    const was = this.snap;
    this.snap = now;
    const fx = { sound: [], fly: [], flip: [], deal: false, board: -1, lead: false, shake: false, coinsFrom: null };
    if (!was) return fx;
    if (was.coins !== now.coins && Number.isFinite(was.coins) && Number.isFinite(now.coins)) {
      fx.coinsFrom = was.coins;
      fx.sound.push(['chip']);
    }
    if (!now.table) return fx;
    const t = this.table;
    if ((was.table !== now.table || was.round !== now.round) && now.phase !== 'idle') {
      fx.deal = true;
      fx.sound.push(['deal', Math.min(12, Math.max(4, now.hand || 6))]);
      return fx;
    }
    if (was.table !== now.table) return fx;
    if (now.board > was.board) {
      fx.board = was.board;
      fx.sound.push(['deal', now.board - was.board]);
    }
    const rank = { pass: 1, chip: 2, play: 3, bomb: 4 };
    let kind = '';
    const hit = (k) => {
      if ((rank[k] || 0) > (rank[kind] || 0)) kind = k;
    };
    const classify = (label) => (/炸/.test(label) ? 'bomb' : /不要|不叫|过牌|弃牌|不亮|不出|^过$/.test(label) ? 'pass' : /跟注|加注|全下|叫|下注|盲|碰|杠/.test(label) ? 'chip' : 'play');
    for (const [id, label] of Object.entries(now.last)) {
      if (label && label !== was.last[id]) {
        fx.fly.push(id);
        hit(classify(label));
      }
    }
    if (now.lead && now.lead !== was.lead) {
      fx.lead = true;
      hit(classify(now.leadLabel));
    }
    const fresh = now.trick.filter((p) => !was.trick.includes(p));
    for (const p of fresh) fx.fly.push(`played:${p.split(':')[0]}`);
    if (fresh.length) hit('play');
    if (kind === 'bomb') fx.shake = true;
    if (kind) fx.sound.push([kind]);
    for (const [id, n] of Object.entries(now.open)) if (n > (was.open[id] || 0)) fx.flip.push(id);
    if (now.turn && !was.turn) fx.sound.push(['turn']);
    if (was.phase !== 'done' && now.phase === 'done') {
      let mine = null;
      if (t.seated) mine = t.last_coins ?? (t.results || []).find((r) => r.id === 'you')?.won ?? null;
      else if (t.last_bet) mine = t.last_bet.win ? 1 : -1;
      if (mine !== null && mine !== undefined) fx.sound.push([Number(mine) > 0 ? 'win' : 'lose']);
    }
    return fx;
  }

  /** 把动效的样式套到刚画好的元素上，再放声音。 */
  applyFx(fx) {
    const root = this.root;
    const add = (el, cls, i = 0) => {
      if (!el) return;
      el.style.setProperty('--i', String(i));
      el.classList.add(cls);
    };
    const seat = (id) => `[data-seat="${CSS.escape(id)}"]`;
    if (fx.deal) {
      root.querySelectorAll('.ac-hcard, .ac-seat .ac-cards .ac-card, .ac-seat .ac-fan .ac-card').forEach((el, k) => add(el, 'ac-deal', Math.min(k, 16)));
    }
    if (fx.board >= 0) {
      root.querySelectorAll('.ac-board .ac-card:not(.slot)').forEach((el, k) => {
        if (k >= fx.board) add(el, 'ac-flip', k - fx.board);
      });
    }
    if (fx.lead) root.querySelectorAll('.ac-leadcards .ac-card').forEach((el, k) => add(el, 'ac-fly', k));
    for (const id of fx.fly) {
      if (id.startsWith('played:')) root.querySelectorAll(`${seat(id.slice(7))} .ac-played .ac-card`).forEach((el, k) => add(el, 'ac-fly', k));
      else add(root.querySelector(`${seat(id)} .ac-last`), 'ac-pop');
    }
    for (const id of fx.flip) {
      root.querySelectorAll(`${seat(id)} .ac-cards .ac-card, ${seat(id)} .ac-fan.open .ac-card`).forEach((el, k) => add(el, 'ac-flip', Math.min(k, 20)));
    }
    if (fx.shake) add(root.querySelector('.ac-felt'), 'ac-shake');
    if (fx.coinsFrom !== null) this.countUp(root.querySelector('[data-coins]'), fx.coinsFrom, this.snap.coins);
    for (const [name, n] of fx.sound) sfx(name, n);
  }

  /** 金币数字从旧值跳到新值。 */
  countUp(el, from, to) {
    if (!el || from === to) return;
    el.classList.add(to > from ? 'ac-up' : 'ac-down');
    setTimeout(() => el.classList.remove('ac-up', 'ac-down'), 1400);
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const t0 = performance.now();
    const step = (now) => {
      if (!el.isConnected) return;
      const k = Math.min(1, (now - t0) / 650);
      el.textContent = fmt(Math.round(from + (to - from) * (1 - (1 - k) ** 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
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
      <p class="ac-hint">对手是圆桌的成员：她们用自己的模型想怎么出（每天有花费上限，到了换本地电脑代打）。你可以亲自上桌，也可以旁观押谁赢。</p>
      ${workshopHTML(this.ws, this.wsDraft, this.wsDel)}`;
  }

  // --- 铁甲突围 ------------------------------------------------------------------

  tanksSetupHTML() {
    const info = this.info || { members: [] };
    const tk = info.tanks || {};
    const team = this.tankMode === 'team';
    const order = [...this.pick];
    const badge = (id) => {
      if (!team || !this.pick.has(id)) return '';
      const k = order.indexOf(id);
      const zh = this.sit ? (k === 0 ? '队友' : '对手') : k < 2 ? '左队' : '右队';
      return `<em class="ac-tbadge">${zh}</em>`;
    };
    const members = info.members
      .map((m) => {
        const on = this.pick.has(m.id);
        return `<button type="button" class="ac-pick${on ? ' on' : ''}" data-member="${esc(m.id)}" aria-pressed="${on}">${this.avatar(m.id, m.name)}<span>${esc(m.name)}</span>${badge(m.id)}</button>`;
      })
      .join('');
    const need = this.minPick();
    const max = this.maxPick();
    const lv = this.tankLevel;
    const win = team ? '你们队赢' : '你拿第一';
    const coins = this.sit
      ? `${win}给 ${fmt(tk.win_coins)} 金币，每击毁一次 ${fmt(tk.kill_coins)}；每天最多 ${fmt(tk.daily_cap)}（今天已领 ${fmt(tk.today)}）。`
      : '旁观不算金币，只记战绩。';
    const pickTip = team ? (this.sit ? '先点的那位是你的队友，另外两位是对手。' : '先点的两位一队，后两位一队。') : '';
    return `<div class="ac-setup hb-glass">
      <h3>铁甲突围 · 开一局</h3>
      <p class="ac-sub">原创坦克对战：地图每局自动生成，四个角出生。每辆坦克 ${fmt(tk.lives || 3)} 条命，最后剩下的一方（或者 ${tk.minutes || 3} 分钟到点时击毁多的）赢。成员的坦克由本地电脑开（实时的游戏等不了模型想，不花钱），开局和打中人时会冒个小气泡说话。</p>
      <div class="ac-row" role="group" aria-label="玩法">
        <button type="button" class="ac-seg" data-tmode="ffa" aria-pressed="${!team}">各自为战</button>
        <button type="button" class="ac-seg" data-tmode="team" aria-pressed="${team}">2 对 2</button>
      </div>
      <div class="ac-picks" role="group" aria-label="选成员">${members}</div>
      ${pickTip ? `<p class="ac-sub">${pickTip}</p>` : ''}
      <div class="ac-row" role="group" aria-label="怎么玩">
        <button type="button" class="ac-seg" data-sit="1" aria-pressed="${this.sit}">我亲自开</button>
        <button type="button" class="ac-seg" data-sit="0" aria-pressed="${!this.sit}">旁观</button>
      </div>
      <div class="ac-row" role="group" aria-label="对手多厉害">
        ${[['easy', '新手'], ['normal', '普通'], ['hard', '高手']].map(([k, zh]) => `<button type="button" class="ac-seg" data-level="${k}" aria-pressed="${lv === k}">对手：${zh}</button>`).join('')}
      </div>
      <p class="ac-sub">${coins}${this.sit ? ' 操作：方向键 / WASD 开动，空格 / J 开炮，E / K 放地雷，P 暂停；手机上有触屏按键。' : ''}</p>
      <div class="ac-row">
        <button type="button" class="btn" data-act="back">返回</button>
        <button type="button" class="btn primary" data-act="open" ${this.pick.size >= need ? '' : 'disabled'}>开打</button>
        <span class="ac-sub">${this.pick.size < need ? `再选 ${need - this.pick.size} 位` : need === max ? `正好 ${max} 位` : `最多 ${max} 位`}</span>
      </div>
      ${this.tankStatsHTML(tk.stats)}
    </div>`;
  }

  tankStatsHTML(stats) {
    if (!stats?.length) return '';
    const rows = stats
      .map((r) => `<tr><td>${esc(r.name)}</td><td>${fmt(r.games)}</td><td>${fmt(r.wins)}</td><td>${r.games ? Math.round((r.wins / r.games) * 100) : 0}%</td><td>${fmt(r.kills)}</td></tr>`)
      .join('');
    return `<details class="ac-tstats" open><summary>战绩</summary><table><thead><tr><th>谁</th><th>局数</th><th>赢</th><th>胜率</th><th>击毁</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }

  async startTanks() {
    this.stopTanks();
    const members = [...this.pick];
    const team = this.tankMode === 'team';
    let run;
    try {
      run = await this.api.tanks(members, this.tankMode, !this.sit);
    } catch (err) {
      this.error = err.message;
      this.render();
      return;
    }
    this.error = '';
    this.tankRun = run;
    // 2 对 2：你和第一位一队；旁观时前两位一队
    const teamOf = (k) => (this.sit ? (k === 0 ? 0 : 1) : k < 2 ? 0 : 1);
    const players = [...(this.sit ? [{ id: 'you', name: '你', human: true, team: 0 }] : []), ...run.members.map((m, k) => ({ id: m.id, name: m.name, team: team ? teamOf(k) : undefined }))];
    this.tankUI = new TankGame({
      players,
      seed: run.seed,
      lives: run.lives,
      minutes: run.minutes,
      level: this.tankLevel,
      mode: run.mode || this.tankMode,
      lines: Object.fromEntries(run.members.map((m) => [m.id, m.lines || {}])),
      onEnd: (r) => this.tanksOver(r),
    });
    this.screen = 'tanks';
    this.render();
    this.tankUI.start();
  }

  async tanksOver(r) {
    const run = this.tankRun;
    if (!run?.token) return;
    this.tankRun = null;
    const rank = (r.rank || []).filter((x) => x.id !== 'you').map((x) => ({ id: x.id, place: x.place, kills: x.kills }));
    try {
      const res = await this.api.tanksResult(run.token, r.place || 1, r.kills || 0, rank);
      if (this.info) {
        this.info.wallet = res.wallet;
        if (this.info.tanks) this.info.tanks.today = res.today;
      }
      if (this.sit) this.tankUI?.showOver(res.coins ? `拿到 ${fmt(res.coins)} 金币` : res.note || '这一局没拿到金币');
      this.render();
    } catch (err) {
      this.tankUI?.showOver(err.message);
    }
  }

  stopTanks() {
    this.tankUI?.stop();
    this.tankUI = null;
  }

  avatar(id, name) {
    const art = this.avatarOf ? this.avatarOf(id, name) : '';
    return art || `<span class="ac-av ini">${esc((name || '?').slice(0, 1))}</span>`;
  }

  setupHTML() {
    if (this.game === 'tanks') return this.tanksSetupHTML();
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
    const size = TABLE_SIZE[this.game];
    const rule = { doudizhu: '叫分定地主，炸弹、王炸、春天翻倍。', paodekuai: '每人 16 张，黑桃 3 先出，有大必压，下家报单要出最大的。', shengji: '两副牌，对家一队（你的对家坐你对面）。亮主、扣底，闲家拿够 80 分换庄，不够庄家升级。', mahjong: '推倒胡：136 张，能碰能杠不能吃，点炮、自摸都能胡；七对、碰碰胡、清一色加番。' }[this.game];
    const intro = cardGame
      ? `${size} 个人一桌。${this.sit ? `你加上 ${size - 1} 位成员` : `选 ${size} 位成员打，你在旁边看`}。${rule}`
      : `无限注，盲注 ${fmt(h.blinds?.[0])} / ${fmt(h.blinds?.[1])}，每人 ${fmt(h.stack)} 筹码。最多选 ${h.max_members} 位成员。`;
    const money = cardGame
      ? this.sit
        ? this.game === 'mahjong'
          ? `每一局按得分算金币：1 分 = ${fmt(cg.coin_per_point)} 金币（自摸三家各付番数，点炮的付两倍），输的最多输光手上的金币。`
          : this.game === 'shengji'
          ? `每一局按升了几级算金币：1 级 = ${fmt(cg.coin_per_level)} 金币（至少算 1 级），输的最多输光手上的金币。`
          : `每一局按得分算金币：1 分 = ${fmt(cg.coin_per_point)} 金币，输的最多输光手上的金币。`
        : `每一局开始前可以押一位成员赢，一局最多 ${fmt(cg.max_bet)} 金币；赢了按人数赔（${this.game === 'shengji' ? '押中的那一队两人平分' : this.game === 'mahjong' ? '流局退回' : '斗地主押中农民两人平分'}）。`
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
    if (t.game === 'shengji') return this.sjHTML();
    if (t.game === 'mahjong') return this.mjHTML();
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
        ${this.godBtn()}
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
    const cls = ['ac-seat', 'ac-shseat', `pos-${pos}`, s.turn ? 'turn' : '', s.id === 'you' ? 'you' : ''].join(' ');
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
        ${this.godBtn()}
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
    const opts = this.table.options || [];
    const can = (this.table.phase === 'play' || this.table.phase === 'bury') && opts.some((o) => o.kind === 'play' || o.kind === 'bury');
    const cards = this.handOrder
      .map((c, k) => {
        const on = this.chosen.has(k);
        return `<button type="button" class="ac-hcard${on ? ' on' : ''}" data-hand="${k}" aria-pressed="${on}" ${can ? '' : 'tabindex="-1"'}>${cardHTML(c)}</button>`;
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

  // --- 升级 ----------------------------------------------------------------------

  sjSeatHTML(s, pos, t) {
    const cls = ['ac-seat', 'ac-shseat', 'ac-sjseat', `pos-${pos}`, s.turn ? 'turn' : '', s.id === 'you' ? 'you' : ''].join(' ');
    const brain = s.brain === 'model' ? '<span class="ac-brain" title="这一步是她自己想的">想</span>' : s.brain === 'local' ? '<span class="ac-brain local" title="这一步本地电脑代打">代</span>' : '';
    const role = s.role ? `<span class="ac-role ${s.role === '庄家' ? 'lord' : ''}">${esc(s.role)}</span>` : '';
    let cards = '';
    if (s.id !== 'you') {
      cards = s.cards.length
        ? `<div class="ac-fan open">${s.cards.map((c) => cardHTML(c, 'ac-mini')).join('')}</div>`
        : `<div class="ac-fan">${s.count ? cardHTML('??', 'ac-mini') : ''}<span class="ac-count">${s.count ? `${s.count} 张` : '出完了'}</span></div>`;
    }
    const play = (t.trick || []).find((p) => p.seat === s.id);
    const old = !t.trick?.length && t.last_trick ? t.last_trick.plays.find((p) => p.seat === s.id) : null;
    const shown = play || old;
    const win = (play && t.winning === s.id) || (old && t.last_trick.winner === s.id);
    const played = shown ? `<div class="ac-played pos-${pos}${old ? ' old' : ''}${win ? ' win' : ''}">${shown.cards.map((c) => cardHTML(c, 'ac-mini')).join('')}</div>` : '';
    return `<div class="${cls}" data-seat="${esc(s.id)}">
      ${s.say ? `<span class="ac-say">${esc(s.say)}</span>` : ''}
      <div class="ac-sname">${s.id === 'you' ? '<span class="ac-av ini you">你</span>' : this.avatar(s.id, s.name)}<b>${esc(s.name)}</b>${role}${brain}</div>
      ${cards}
      ${s.thinking ? '<div class="ac-last thinking">在想<span class="ac-dots"><i></i><i></i><i></i></span></div>' : t.phase === 'bid' && s.last ? `<div class="ac-last">${esc(s.last)}</div>` : ''}
      ${played}
    </div>`;
  }

  sjHTML() {
    const t = this.table;
    this.keepChosen();
    const you = t.seats.findIndex((s) => s.id === 'you');
    const start = you >= 0 ? you : 0;
    const at = (k) => t.seats[(start + k) % 4];
    const seats = [this.sjSeatHTML(at(0), 'bottom', t), this.sjSeatHTML(at(1), 'right', t), this.sjSeatHTML(at(2), 'top', t), this.sjSeatHTML(at(3), 'left', t)].join('');
    const phaseZh = { idle: '等发牌', bid: '亮主', bury: '庄家扣底', play: '出牌中', done: '这一局结束' }[t.phase] || '';
    const status = t.round_no ? `升级 · 第 ${t.round_no} 局 · ${phaseZh}` : '升级 · 等发牌';
    const bank = t.seats.find((s) => s.id === t.banker);
    const myTeam = you >= 0 ? you % 2 : 0;
    const lv = t.levels || ['2', '2'];
    const rk = (r) => (r === 'T' ? '10' : r);
    const teams = `<span class="ac-sjteams">${you >= 0 ? '我方' : `${esc(at(0).name)}队`} 打 ${rk(lv[myTeam])} · ${you >= 0 ? '对方' : `${esc(at(1).name)}队`} 打 ${rk(lv[1 - myTeam])}</span>`;
    const trumpRed = t.trump === 'h' || t.trump === 'd';
    const info = `<div class="ac-sjinfo">
        <span class="ac-trump${trumpRed ? ' red' : ''}" title="主牌">主 <b>${esc(t.trump_zh || '')}</b></span>
        <span>${t.phase === 'done' ? '下一局打' : '本局打'} <b>${rk(t.level)}</b></span>
        ${bank ? `<span class="ac-sjbank">${t.phase === 'done' ? '下一局庄家' : '庄家'} <b>${esc(bank.name)}</b></span>` : ''}
        ${teams}
      </div>
      <div class="ac-sjpts">闲家得分 <b>${fmt(t.points)}</b> / 80</div>
      ${t.kitty?.length && t.phase !== 'play' ? `<div class="ac-ddz"><span class="ac-sub">底牌</span><span class="ac-bottom">${t.kitty.map((c) => cardHTML(c, 'ac-mini')).join('')}</span></div>` : ''}`;
    return `<div class="ac-table-wrap">
      <div class="ac-bar">
        <span class="ac-status">${esc(status)}</span>
        ${t.note ? `<span class="ac-note">${esc(t.note)}</span>` : ''}
        <span class="ac-grow"></span>
        ${this.godBtn()}
        <button type="button" class="ac-seg ac-sm" data-think="${t.think ? 0 : 1}" title="成员用模型想 / 本地电脑代打">${t.think ? '成员自己想 ✓' : '本地电脑代打 ✓'}</button>
        <button type="button" class="btn" data-act="leave">离桌</button>
      </div>
      <div class="ac-felt ac-shfelt ac-sjfelt" role="group" aria-label="升级牌桌">
        <div class="ac-center">${info}</div>
        ${seats}
      </div>
      ${this.handHTML(at(0))}
      ${this.sjControlsHTML()}
    </div>`;
  }

  sjControlsHTML() {
    const t = this.table;
    if (t.phase === 'done' || t.phase === 'idle') return this.sjBetweenHTML();
    if (!t.seated) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.bet ? `你押了 ${esc(t.bet.name)} ${fmt(t.bet.amount)} 金币` : '这一局没押注'}，看她们打…</span></div>`;
    if (!t.options.length) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.thinking ? '等成员出牌…' : '等别人…'}</span></div>`;
    if (t.phase === 'bid') {
      const btns = t.options.map((o, k) => `<button type="button" class="btn ${o.kind === 'declare' ? 'primary' : ''}" data-sjopt="${k}">${esc(o.label)}</button>`).join('');
      return `<div class="ac-controls hb-glass"><span class="ac-sub">轮到你亮主${t.declared ? `（已经有人${esc(t.declared.label)}）` : ''}</span>${btns}</div>`;
    }
    const n = this.chosen.size;
    if (t.phase === 'bury') {
      return `<div class="ac-controls hb-glass">
        <span class="ac-sub">你是庄家，底牌已经拿到手里了。选 8 张扣回去（已选 ${n} / 8），扣下的分闲家最后一轮赢了会翻倍拿走</span>
        <button type="button" class="btn" data-shed="hint">提示</button>
        ${n ? '<button type="button" class="btn" data-shed="clear">重选</button>' : ''}
        <button type="button" class="btn primary" data-shed="bury" ${n === 8 ? '' : 'disabled'}>扣底</button>
      </div>`;
    }
    const need = t.trick?.length ? t.trick[0].cards.length : 0;
    const tip = need ? `要跟 ${need} 张，选了 ${n} 张` : n ? `选了 ${n} 张` : '你先出：单张、对子、拖拉机，或者甩牌';
    return `<div class="ac-controls hb-glass">
      <span class="ac-sub">轮到你了 · ${tip}</span>
      <button type="button" class="btn" data-shed="hint">提示</button>
      ${n ? '<button type="button" class="btn" data-shed="clear">重选</button>' : ''}
      <button type="button" class="btn primary" data-shed="play" ${n && (!need || n === need) ? '' : 'disabled'}>出牌</button>
    </div>`;
  }

  // --- 麻将 ----------------------------------------------------------------------

  mjSeatHTML(s, pos) {
    const cls = ['ac-seat', 'ac-shseat', 'ac-sjseat', 'ac-mjseat', `pos-${pos}`, s.turn ? 'turn' : '', s.id === 'you' ? 'you' : ''].join(' ');
    const brain = s.brain === 'model' ? '<span class="ac-brain" title="这一步是她自己想的">想</span>' : s.brain === 'local' ? '<span class="ac-brain local" title="这一步本地电脑代打">代</span>' : '';
    const role = s.role ? `<span class="ac-role lord">${esc(s.role)}</span>` : '';
    const melds = (s.melds || []).map((m) => `<span class="ac-meld">${m.tiles.map((x) => tileHTML(x, 'ac-sm')).join('')}</span>`).join('');
    let hand = '';
    if (s.id !== 'you') {
      hand = s.cards.length
        ? `<div class="ac-fan open ac-mjfan">${s.cards.map((x) => tileHTML(x, 'ac-sm')).join('')}</div>`
        : `<div class="ac-fan">${s.count ? tileHTML('??', 'ac-sm') : ''}<span class="ac-count">${s.count} 张</span></div>`;
    }
    return `<div class="${cls}" data-seat="${esc(s.id)}">
      ${s.say ? `<span class="ac-say">${esc(s.say)}</span>` : ''}
      <div class="ac-sname">${s.id === 'you' ? '<span class="ac-av ini you">你</span>' : this.avatar(s.id, s.name)}<b>${esc(s.name)}</b>${role}${brain}</div>
      ${hand}
      ${melds ? `<div class="ac-melds">${melds}</div>` : ''}
      ${s.thinking ? '<div class="ac-last thinking">在想<span class="ac-dots"><i></i><i></i><i></i></span></div>' : s.last ? `<div class="ac-last">${esc(s.last)}</div>` : ''}
    </div>`;
  }

  mjHTML() {
    const t = this.table;
    this.keepChosen();
    const you = t.seats.findIndex((s) => s.id === 'you');
    const start = you >= 0 ? you : 0;
    const at = (k) => t.seats[(start + k) % 4];
    const seats = [this.mjSeatHTML(at(0), 'bottom'), this.mjSeatHTML(at(1), 'right'), this.mjSeatHTML(at(2), 'top'), this.mjSeatHTML(at(3), 'left')].join('');
    const phaseZh = { idle: '等发牌', play: '摸牌打牌', claim: '等人碰 / 胡', done: '这一局结束' }[t.phase] || '';
    const status = t.round_no ? `麻将 · 第 ${t.round_no} 局 · ${phaseZh}` : '麻将 · 等发牌';
    const ld = t.last_discard;
    const ldName = ld ? (t.seats.find((s) => s.id === ld.seat) || {}).name || '' : '';
    const rivers = [0, 1, 2, 3]
      .map((k) => at(k))
      .map((s) => `<div class="ac-river${s.turn ? ' turn' : ''}"><span class="ac-rname">${esc(s.id === 'you' ? '你' : s.name)}</span><span class="ac-rtiles">${(s.river || []).map((x, n, all) => tileHTML(x, `ac-xs${ld && ld.seat === s.id && n === all.length - 1 ? ' hot' : ''}`)).join('') || '<span class="ac-sub">还没打</span>'}</span></div>`)
      .join('');
    const center = `<div class="ac-mjinfo">
        <span>牌墙 <b>${fmt(t.wall)}</b> 张</span>
        ${t.dealer ? `<span>庄家 <b>${esc(t.seats.find((s) => s.id === t.dealer)?.name || '')}</b></span>` : ''}
        ${ld ? `<span class="ac-ldis">${esc(ldName)} 打出 ${tileHTML(ld.tile, 'ac-sm')}</span>` : ''}
      </div>
      <div class="ac-rivers">${rivers}</div>`;
    return `<div class="ac-table-wrap">
      <div class="ac-bar">
        <span class="ac-status">${esc(status)}</span>
        ${t.note ? `<span class="ac-note">${esc(t.note)}</span>` : ''}
        <span class="ac-grow"></span>
        ${this.godBtn()}
        <button type="button" class="ac-seg ac-sm" data-think="${t.think ? 0 : 1}" title="成员用模型想 / 本地电脑代打">${t.think ? '成员自己想 ✓' : '本地电脑代打 ✓'}</button>
        <button type="button" class="btn" data-act="leave">离桌</button>
      </div>
      <div class="ac-felt ac-shfelt ac-sjfelt ac-mjfelt" role="group" aria-label="麻将牌桌">
        <div class="ac-center">${center}</div>
        ${seats}
      </div>
      ${this.mjHandHTML(at(0))}
      ${this.mjControlsHTML()}
    </div>`;
  }

  mjHandHTML(me) {
    if (!this.table.seated || me.id !== 'you') return '';
    const can = (this.table.options || []).some((o) => o.kind === 'play');
    const drawnAt = me.drawn ? this.handOrder.length - 1 : -1;
    const tiles = this.handOrder
      .map((x, k) => {
        const on = this.chosen.has(k);
        return `<button type="button" class="ac-hcard ac-htile${on ? ' on' : ''}${k === drawnAt ? ' drawn' : ''}" data-hand="${k}" aria-pressed="${on}" aria-label="${esc(tileZh(x))}" ${can ? '' : 'tabindex="-1"'}>${tileHTML(x)}</button>`;
      })
      .join('');
    const melds = (me.melds || []).map((m) => `<span class="ac-meld">${m.tiles.map((x) => tileHTML(x, 'ac-sm')).join('')}</span>`).join('');
    return `<div class="ac-hand ac-mjhand${can ? '' : ' idle'}" role="group" aria-label="你的手牌（点一下选中，再点一下打出）">${tiles}${melds ? `<span class="ac-mymelds">${melds}</span>` : ''}</div>`;
  }

  mjControlsHTML() {
    const t = this.table;
    if (t.phase === 'done' || t.phase === 'idle') return this.mjBetweenHTML();
    if (!t.seated) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.bet ? `你押了 ${esc(t.bet.name)} ${fmt(t.bet.amount)} 金币` : '这一局没押注'}，看她们打…</span></div>`;
    if (!t.options.length) return `<div class="ac-controls hb-glass"><span class="ac-sub">${t.thinking ? '等成员出牌…' : '等别人…'}</span></div>`;
    const special = t.options
      .map((o, k) => [o, k])
      .filter(([o]) => o.kind !== 'play')
      .map(([o, k]) => `<button type="button" class="btn ${o.kind === 'pass' ? '' : 'primary'}" data-sjopt="${k}">${esc(o.text || o.label)}</button>`)
      .join('');
    if (t.phase === 'claim') {
      const ld = t.last_discard;
      const who = ld ? (t.seats.find((s) => s.id === ld.seat) || {}).name || '' : '';
      return `<div class="ac-controls hb-glass"><span class="ac-sub">${esc(who)}打出 ${ld ? esc(tileZh(ld.tile)) : ''}，你要吗？</span>${special}</div>`;
    }
    const pick = this.chosenCards()[0];
    return `<div class="ac-controls hb-glass">
      <span class="ac-sub">轮到你了 · ${pick ? `选了 ${esc(tileZh(pick))}，再点一下或者按「打出」` : '点一张要打的牌'}</span>
      ${special}
      <button type="button" class="btn primary" data-shed="play" ${pick ? '' : 'disabled'}>打出</button>
    </div>`;
  }

  mjBetweenHTML() {
    const t = this.table;
    const sm = t.summary;
    let sumLine = '';
    if (sm) {
      sumLine = sm.winner
        ? `<p class="ac-sub">${esc(sm.winner)}${sm.self_drawn ? '自摸' : `胡了${esc(sm.from)}打的${sm.tile ? ` ${esc(tileZh(sm.tile))}` : ''}`}：${esc((sm.fans || []).join('、'))}，共 ${sm.fan} 番${sm.kong_points ? ` · 杠了 ${sm.kong_points} 次` : ''}</p>`
        : `<p class="ac-sub">牌摸完了没人胡：流局，庄家连庄${sm.kong_points ? ` · 杠分照算` : ''}</p>`;
    }
    const rows = (t.results || [])
      .map((r) => `<li class="${r.points > 0 ? 'win' : ''}"><b>${esc(r.name)}</b> ${r.points > 0 ? '+' : ''}${r.points} 分 <span class="ac-sub">${esc(r.role)}</span></li>`)
      .join('');
    const coins = t.last_coins;
    const coinLine = coins !== null && coins !== undefined ? `<p class="ac-betres ${coins > 0 ? 'win' : ''}">你这一局 ${coins >= 0 ? `赢 ${fmt(coins)}` : `输 ${fmt(-coins)}`} 金币</p>` : '';
    const lb = t.last_bet;
    const betLine = lb ? `<p class="ac-betres ${lb.win ? 'win' : ''}">你押 ${esc(lb.name)} ${fmt(lb.amount)}：${lb.draw ? '流局，退回' : lb.win ? `赢了，拿回 ${fmt(lb.payout)}` : '输了'}</p>` : '';
    let bet = '';
    if (!t.seated && t.can_deal) {
      const opts = t.seats.map((s) => `<option value="${esc(s.id)}" ${this.betOn === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
      bet = `<label class="ac-betpick">押 <select data-bet-on aria-label="押谁胡"><option value="">不押</option>${opts}</select></label>
        <label class="ac-betpick">金币 <input type="number" data-bet-amount min="1" max="${this.info?.cards?.max_bet || 500}" value="${this.betAmount}" aria-label="押多少"></label>`;
    }
    const broke = t.seated && !t.can_deal && (t.wallet?.coins || 0) <= 0 ? '<span class="ac-sub">金币输光了，明天再来领，或者先旁观押注。</span>' : '';
    return `<div class="ac-controls hb-glass">
      ${rows ? `<ul class="ac-results">${rows}</ul>` : ''}
      ${sumLine}${coinLine}${betLine}${bet}${broke}
      <button type="button" class="btn primary" data-act="deal" ${t.can_deal ? '' : 'disabled'}>${t.round_no ? '再来一局' : '发牌'}</button>
    </div>`;
  }

  sjBetweenHTML() {
    const t = this.table;
    const sm = t.summary;
    const rk = (r) => (r === 'T' ? '10' : r);
    let sumLine = '';
    if (sm) {
      const bonus = sm.bonus ? `（含底牌 ${sm.kitty_points} 分翻倍 = ${sm.bonus}）` : '';
      const up = sm.winner === '庄家' ? `庄家升 ${sm.up} 级` : sm.up ? `闲家上台，再升 ${sm.up} 级` : '闲家上台';
      sumLine = `<p class="ac-sub">闲家得分 ${sm.attack_points}${bonus} · ${sm.winner}赢 · ${up}${sm.passed ? ' · 打过 A 通关，从 2 重新打' : ''} · 现在 ${rk(sm.levels[0])} : ${rk(sm.levels[1])} · 下一局庄家 ${esc(sm.next_banker)}</p>`;
    }
    const rows = (t.results || [])
      .map((r) => `<li class="${r.points > 0 ? 'win' : ''}"><b>${esc(r.name)}</b> ${r.points > 0 ? '+' : ''}${r.points} <span class="ac-sub">${esc(r.role)}</span></li>`)
      .join('');
    const coins = t.last_coins;
    const coinLine = coins !== null && coins !== undefined ? `<p class="ac-betres ${coins > 0 ? 'win' : ''}">你这一局 ${coins >= 0 ? `赢 ${fmt(coins)}` : `输 ${fmt(-coins)}`} 金币</p>` : '';
    const lb = t.last_bet;
    const betLine = lb ? `<p class="ac-betres ${lb.win ? 'win' : ''}">你押 ${esc(lb.name)} ${fmt(lb.amount)}：${lb.win ? `赢了，拿回 ${fmt(lb.payout)}` : '输了'}</p>` : '';
    let bet = '';
    if (!t.seated && t.can_deal) {
      const opts = t.seats.map((s) => `<option value="${esc(s.id)}" ${this.betOn === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
      bet = `<label class="ac-betpick">押 <select data-bet-on aria-label="押谁赢（押的人那一队赢就算赢）"><option value="">不押</option>${opts}</select></label>
        <label class="ac-betpick">金币 <input type="number" data-bet-amount min="1" max="${this.info?.cards?.max_bet || 500}" value="${this.betAmount}" aria-label="押多少"></label>`;
    }
    return `<div class="ac-controls hb-glass">
      ${rows ? `<ul class="ac-results">${rows}</ul>` : ''}
      ${sumLine}${coinLine}${betLine}${bet}
      <button type="button" class="btn primary" data-act="deal" ${t.can_deal ? '' : 'disabled'}>${t.round_no ? '再来一局' : '发牌'}</button>
    </div>`;
  }
}
