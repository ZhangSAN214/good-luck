// 圆桌中央的「花切」转场：成员思考时一副塔罗牌在桌面上循环切牌、开扇；结束时翻开一张牌再淡出，露出结果。
// 牌面是原创的西式线条画（大阿卡纳的经典意象，自己画的几何线稿，不用任何现成牌图），只用 transform / opacity 做动画。
// 动画元素一直挂在 #stage 里、不随座位图重画，所以每个事件刷新座位时循环不会被打断。

const FRAME =
  '<rect x="2" y="2" width="56" height="96" rx="4"/><rect class="hair" x="4.6" y="4.6" width="50.8" height="90.8" rx="2.4"/>' +
  '<path class="hair" d="M4.6 15.5h50.8M4.6 84.5h50.8"/>';

/** 极坐标上的一圈短线，用来画光芒、刻度、花环叶片。 */
function rays(cx, cy, n, r1, r2, phase = 0) {
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = ((k + phase) / n) * Math.PI * 2;
    const c = Math.cos(a);
    const s = Math.sin(a);
    d += `M${(cx + r1 * c).toFixed(2)} ${(cy + r1 * s).toFixed(2)}L${(cx + r2 * c).toFixed(2)} ${(cy + r2 * s).toFixed(2)}`;
  }
  return d;
}

/** 波浪光芒（太阳牌上直光与曲光交替）。 */
function wavy(cx, cy, n, r1, r2, phase) {
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = ((k + phase) / n) * Math.PI * 2;
    const pt = (r, off) => {
      const b = a + off;
      return `${(cx + r * Math.cos(b)).toFixed(2)} ${(cy + r * Math.sin(b)).toFixed(2)}`;
    };
    const m = (r1 + r2) / 2;
    d += `M${pt(r1, 0)}Q${pt((r1 + m) / 2, 0.09)} ${pt(m, 0)}T${pt(r2, 0)}`;
  }
  return d;
}

/** 八角星：外半径 R、内半径 r。 */
function star(cx, cy, R, r, n = 8) {
  let d = '';
  for (let k = 0; k < n * 2; k++) {
    const a = (k / (n * 2)) * Math.PI * 2 - Math.PI / 2;
    const rr = k % 2 ? r : R;
    d += `${k ? 'L' : 'M'}${(cx + rr * Math.cos(a)).toFixed(2)} ${(cy + rr * Math.sin(a)).toFixed(2)}`;
  }
  return `${d}Z`;
}

const waves = (y, x1 = 9, x2 = 51) => {
  let d = `M${x1} ${y}`;
  for (let x = x1; x < x2; x += 6) d += `q1.5 -1.4 3 0t3 0`;
  return d;
};

// 牌面中间的画：画布 60×100，画面区 y 16～84
const ART = {
  sun: () =>
    `<circle cx="30" cy="42" r="10.5"/><path class="hair" d="${rays(30, 42, 16, 13, 19.5)}"/><path d="${wavy(30, 42, 16, 13, 21, 0.5)}"/>` +
    '<path class="hair" d="M25.6 40.4q1.3-1 2.6 0M31.8 40.4q1.3-1 2.6 0M30 41.6v3.4l-1 .7M27.2 47.2q2.8 1.8 5.6 0"/>' +
    '<path d="M8 74h44M8 74v8M52 74v8"/><path class="hair" d="M8 78h44M16 74v4M24 74v4M32 74v4M40 74v4M48 74v4M12 78v4M20 78v4M28 78v4M36 78v4M44 78v4"/>' +
    `<circle cx="15" cy="68" r="2.2"/><path class="hair" d="${rays(15, 68, 10, 2.4, 4.2)}M15 70.2V74"/>` +
    `<circle cx="45" cy="68" r="2.2"/><path class="hair" d="${rays(45, 68, 10, 2.4, 4.2)}M45 70.2V74"/>`,
  moon: () =>
    '<circle cx="30" cy="32" r="11"/><path d="M33.5 22.2a10.2 10.2 0 1 0 0 19.6 8 8 0 0 1 0-19.6z"/>' +
    `<path class="hair" d="${rays(30, 32, 24, 12.5, 15.5)}"/>` +
    '<path class="hair" d="M22 47.5q.8 1.6 0 2.4-.8-.8 0-2.4zM30 49q.8 1.6 0 2.4-.8-.8 0-2.4zM38 47.5q.8 1.6 0 2.4-.8-.8 0-2.4z"/>' +
    '<path d="M8 80V56h6v24M46 80V56h6v24M7.5 56l3.5-3 3.5 3M45.5 56l3.5-3 3.5 3"/><path class="hair" d="M10 60.5h2v3h-2zM48 60.5h2v3h-2z"/>' +
    '<path class="hair" d="M30 84c-7-3 7-6 0-10s4-6 0-9-3-5 0-7"/>' +
    `<path d="${waves(81, 14, 46)}"/>`,
  star: () =>
    `<path d="${star(30, 28, 10.5, 3.8)}"/><path class="hair" d="${star(30, 28, 6.5, 2.4)}"/>` +
    [
      [12, 22],
      [48, 22],
      [9, 36],
      [51, 36],
      [16, 45],
      [44, 45],
      [30, 46],
    ]
      .map(([x, y]) => `<path class="hair" d="${star(x, y, 3, 1.1)}"/>`)
      .join('') +
    '<path d="M21 56c-1.8 0-2.8 1.3-2.6 3.2l.8 5.6h6.4l.8-5.6c.2-1.9-.8-3.2-2.6-3.2zM25.4 58.2l3-1.6"/>' +
    '<path class="hair" d="M28.6 56.6c2.4 3.2 3.4 8.4 3 15M29.4 56.4c3 3 5 9 4.8 15.4"/>' +
    `<path d="${waves(73, 10, 50)}"/><path class="hair" d="${waves(77, 13, 47)}${waves(81, 17, 43)}"/>`,
  wheel: () =>
    '<circle cx="30" cy="48" r="17"/><circle class="hair" cx="30" cy="48" r="13.2"/><circle cx="30" cy="48" r="3.4"/>' +
    `<path class="hair" d="${rays(30, 48, 8, 3.4, 13.2, 0)}"/><path class="hair" d="${rays(30, 48, 32, 15.2, 17)}"/>` +
    '<circle class="hair" cx="30" cy="48" r="15.2"/>' +
    [0, 1, 2, 3, 4, 5, 6, 7]
      .map((k) => {
        const a = ((k + 0.5) / 8) * Math.PI * 2;
        const x = (30 + 9 * Math.cos(a)).toFixed(2);
        const y = (48 + 9 * Math.sin(a)).toFixed(2);
        return k % 2 ? `<circle class="hair" cx="${x}" cy="${y}" r="1.2"/>` : `<path class="hair" d="${star(+x, +y, 1.6, 0.6, 4)}"/>`;
      })
      .join('') +
    `<path d="${star(30, 48, 2.2, 0.9, 4)}"/>` +
    '<path class="hair" d="M8 25q2-3 5-1.6 1.6-2.8 5-1.2 3 .2 2.8 2.8M40 25q.4-2.6 3.2-2.8 3.4-1.6 5 1.2 3-1.4 5 1.6M8 75q2 3 5 1.6 1.6 2.8 5 1.2 3-.2 2.8-2.8M40 75q.4 2.6 3.2 2.8 3.4 1.6 5-1.2 3 1.4 5-1.6"/>',
  tower: () =>
    '<path d="M22 82l2.4-44h11.2L38 82M23.6 38l1.8-5.4 2.2 3.2 2.4-5 2.4 5 2.2-3.2 1.8 5.4"/>' +
    '<path class="hair" d="M28 47h4v5h-4zM26.4 62h3v4.5h-3zM30.6 62h3v4.5h-3zM28.2 74h3.6v8h-3.6"/>' +
    '<path d="M52 16l-8.6 9.8h5.2l-9.2 9.6"/>' +
    '<path class="hair" d="M16 44l-1.6 3M13 54l-1.4 2.6M44 46l1.6 3M47 56l1.4 2.6M18 30l-1 2M42 32l1 2"/>' +
    `<path d="${waves(84, 10, 50).replace('M10 84', 'M10 83.4')}"/>`,
  magician: () =>
    '<path class="hair" d="M24.5 19.5c2.2-3 4.6-3 5.5 0s3.3 3 5.5 0-3.3-3-5.5 0-3.3 3-5.5 0z"/>' +
    '<circle cx="30" cy="29.4" r="3.4"/><path class="hair" d="M28.2 32.6v1.6M31.8 32.6v1.6"/>' +
    '<path d="M30 34.4c-3.4 0-5 1.6-5.6 5.2L22.6 62h14.8l-1.8-22.4c-.6-3.6-2.2-5.2-5.6-5.2z"/><path class="hair" d="M24.2 46.4h11.6M27.6 46.4l-1.2 15.6M32.4 46.4l1.2 15.6"/>' +
    '<path d="M34.6 37.2c1.8-1.4 3-4 4.4-8M25.2 38.8c-1.4 2.6-3.2 5.4-5.6 7.6"/><path class="hair" d="M38 30.6l4.2-8.4M41.6 23.4l1.6-.6.2 1.6M38.4 29.8l-1.2.6M19.6 46.4l-1.4.8"/>' +
    '<path d="M10 62h40M10 62v2.4h40V62M13 64.4V80M47 64.4V80"/>' +
    `<path class="hair" d="M15.4 61.6v-3.4c0-1.6 4.6-1.6 4.6 0v3.4M15 56.6h5.4M17.7 56.6v-1.2"/><circle class="hair" cx="25.6" cy="59.6" r="2"/><path class="hair" d="${star(25.6, 59.6, 1.5, 0.6, 5)}M31.4 61.6l5.6-5.6M35.8 55l1.8 1.8M42 61.6v-6.2M40.4 55.4h3.2"/>` +
    `<path class="hair" d="${waves(84, 12, 48).replace('M12 84', 'M12 83')}"/>`,
  world: () =>
    '<ellipse cx="30" cy="50" rx="13" ry="22"/><ellipse class="hair" cx="30" cy="50" rx="10.6" ry="19.4"/>' +
    (() => {
      let d = '';
      for (let k = 0; k < 28; k++) {
        const a = (k / 28) * Math.PI * 2;
        const x = 30 + 11.8 * Math.cos(a);
        const y = 50 + 20.7 * Math.sin(a);
        const tx = -Math.sin(a) * 1.8;
        const ty = Math.cos(a) * 1.8;
        d += `M${(x - tx).toFixed(2)} ${(y - ty).toFixed(2)}q${(tx + Math.cos(a) * 1.2).toFixed(2)} ${(ty + Math.sin(a) * 1.2).toFixed(2)} ${(2 * tx).toFixed(2)} ${(2 * ty).toFixed(2)}`;
      }
      return `<path class="hair" d="${d}"/>`;
    })() +
    '<path d="M26 27.4l8 2.6M26 30l8-2.6M26 70l8 2.6M26 72.6l8-2.6"/>' +
    `<path d="${star(30, 50, 6.2, 2.4)}"/><circle class="hair" cx="30" cy="50" r="1.4"/>` +
    '<circle class="hair" cx="11" cy="24" r="3"/><path class="hair" d="M46 27l3-5.4 3 5.4zM8 73h6v6H8zM52.6 74.4a3.2 3.2 0 1 1-3.2-3.2 2.4 2.4 0 0 0 3.2 3.2z"/>',
};

const CARDS = [
  { key: 'sun', n: 'XIX', title: 'THE SUN' },
  { key: 'moon', n: 'XVIII', title: 'THE MOON' },
  { key: 'star', n: 'XVII', title: 'THE STAR' },
  { key: 'wheel', n: 'X', title: 'FORTUNE' },
  { key: 'tower', n: 'XVI', title: 'THE TOWER' },
  { key: 'magician', n: 'I', title: 'MAGICIAN' },
];
const RESULT = { key: 'world', n: 'XXI', title: 'THE WORLD' };

function face(c) {
  return `<svg viewBox="0 0 60 100" class="dk-face">${FRAME}<text x="30" y="11.6">${c.n}</text>${ART[c.key]()}<text x="30" y="91.6" class="t">${c.title}</text></svg>`;
}

const BACK = `<svg viewBox="0 0 60 100" class="dk-back">${FRAME.replace('<path class="hair" d="M4.6 15.5h50.8M4.6 84.5h50.8"/>', '')}
<circle cx="30" cy="50" r="15"/><circle class="hair" cx="30" cy="50" r="11.6"/>
<path class="hair" d="${rays(30, 50, 24, 15.8, 20.5)}"/>
<path d="M33 41.6a8.6 8.6 0 1 0 0 16.8 6.8 6.8 0 0 1 0-16.8z"/><path class="hair" d="${star(32.8, 50, 2.6, 1)}"/>
<path class="hair" d="M30 9.5l2.4 3.4-2.4 3.4-2.4-3.4zM30 83.7l2.4 3.4-2.4 3.4-2.4-3.4zM9.6 9.6l5 5M50.4 9.6l-5 5M9.6 90.4l5-5M50.4 90.4l-5-5"/>
<path class="hair" d="M30 22v4M30 74v4M14 50h-4M46 50h4"/></svg>`;

const N = CARDS.length;
const half = N / 2;

/** 一张牌：--i 原来的位置，--j 切牌后的位置，--s 属于左（-1）/ 右（1）牌叠，--f 开扇时的角度系数（按切牌后的顺序）。 */
function card(c, i) {
  const s = i < half ? -1 : 1;
  const j = s < 0 ? i + half : i - half;
  const f = j - (N - 1) / 2;
  return `<div class="dk-card" style="--i:${i};--j:${j};--s:${s};--f:${f}"><div class="dk-in">${BACK}${face(c)}</div></div>`;
}

export class Deck {
  constructor() {
    this.el = document.createElement('div');
    this.el.className = 'deck';
    this.el.setAttribute('aria-hidden', 'true');
    this.el.innerHTML = CARDS.map(card).join('');
    // 结束时翻开的那张：放在最上面，平时不显示
    this.hero = document.createElement('div');
    this.hero.className = 'dk-card dk-hero';
    this.hero.style.cssText = `--i:${N};--j:${N};--s:1;--f:0`;
    this.hero.innerHTML = `<div class="dk-in">${BACK}${face(RESULT)}</div>`;
    this.el.append(this.hero);
    this.state = 'idle';
    this.offTimer = null;
    this.doneTimer = null;
  }

  /** 把动画挂到 #stage 里（只挂一次；之后重画座位时它一直在，不重启动画）。 */
  mount(stage) {
    if (this.el.parentNode !== stage) stage.append(this.el);
  }

  /** thinking：有成员正在思考；result：这一场刚结束时的状态（completed 才翻开结果牌）。 */
  set(thinking, status) {
    if (thinking) {
      clearTimeout(this.offTimer);
      this.offTimer = null;
      if (this.state !== 'loop') this.loop();
      return;
    }
    if (this.state !== 'loop' || this.offTimer) return;
    // 两个步骤之间会有一瞬间没人思考：稍等一下再判断，免得循环断开又重来
    this.offTimer = setTimeout(() => {
      this.offTimer = null;
      if (status() === 'completed') this.reveal();
      else this.stop();
    }, 700);
  }

  loop() {
    clearTimeout(this.doneTimer);
    this.state = 'loop';
    this.el.classList.remove('reveal');
    this.el.classList.add('on');
    this.el.parentNode?.classList.add('shuffling');
  }

  reveal() {
    this.state = 'reveal';
    this.el.classList.remove('on');
    void this.el.offsetWidth; // 重新开始揭牌动画
    this.el.classList.add('reveal');
    // 牌翻开、停一会儿后桌面上的结果文字浮回来，最后整副牌淡出
    this.doneTimer = setTimeout(() => {
      this.el.parentNode?.classList.remove('shuffling');
      this.doneTimer = setTimeout(() => this.stop(), 900);
    }, 1900);
  }

  stop() {
    clearTimeout(this.offTimer);
    clearTimeout(this.doneTimer);
    this.offTimer = null;
    this.state = 'idle';
    this.el.classList.remove('on', 'reveal');
    this.el.parentNode?.classList.remove('shuffling');
  }
}
