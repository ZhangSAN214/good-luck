// 小人对圆桌事件的反应：纯逻辑，不碰 DOM。app.js 把 SSE 事件交给 Reporter，
// 它返回「做什么动作 / 什么表情 / 冒什么气泡」，由 pet.js 执行。
// 气泡文字只用步骤名、桌号和服务端给的提示，不含成员身份（匿名未揭晓的会话里小人本来就不出现）。

const RESTING = new Set(['completed', 'stopped', 'failed', 'awaiting_confirmation', 'paused', 'created']);
const COORD = new Set(['synthesize', 'decompose', 'assign', 'accept', 'merge']);
export const NEAR_PX = 240; // 鼠标靠近到这个距离，小人看向鼠标
export const POKE_ANGRY = 3; // 连戳这么多次就生气
export const POKE_WINDOW_MS = 4000;
export const QUIET_MS = 30 * 60 * 1000; // 「安静一会儿」的时长
const BUBBLE_GAP_MS = 4000; // 普通进度气泡的最短间隔

const clip = (s, n) => {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
};

export class Reporter {
  // labels: {步骤 id: 中文名}；now 可注入
  constructor({ labels = {}, now = () => Date.now() } = {}) {
    this.labels = labels;
    this.now = now;
    this.running = false; // 事件流的快照说这场正在进行时才会庆祝，避免打开旧会话时空欢喜
    this.lastProgress = -Infinity;
    this.quietUntil = 0;
    this.status = '空闲'; // 「当前状态」菜单项用
    this.pokes = [];
  }

  quiet(ms = QUIET_MS) {
    this.quietUntil = this.now() + ms;
  }

  wake() {
    this.quietUntil = 0;
  }

  get muted() {
    return this.now() < this.quietUntil;
  }

  // 换会话 / 新题目：状态清零
  reset() {
    this.running = false;
    this.status = '空闲';
    this.lastProgress = -Infinity;
  }

  // 返回 null 或 {act, feel, say, urgent, ms}
  react(e) {
    const d = e.data || {};
    switch (e.type) {
      case 'snapshot':
        this.running = !RESTING.has(e.status);
        this.status = this.running ? '正在讨论' : this.restingText(e.status);
        return null;
      case 'step_started': {
        this.running = true;
        const label = this.labels[e.step] || e.step;
        this.status = `${label}中${e.table_no ? `（第 ${e.table_no} 桌）` : ''}`;
        const coord = COORD.has(e.step);
        return this.progress({ act: coord ? 'think' : 'talk', say: this.status });
      }
      case 'call_done':
        return { act: 'talk', ms: 1400 };
      case 'media_started':
        this.status = '生成媒体中';
        return this.progress({ act: 'type', say: '开始生成了，请稍等' });
      case 'escalating':
        return { feel: 'surprised', ms: 2000 };
      case 'budget_warning':
        return {
          act: 'jump',
          feel: 'confused',
          ms: 4000,
          say: clip(d.message, 40) || '花费快到预算了',
          urgent: true,
        };
      case 'checkpoint':
        this.status = '等你确认';
        return { act: 'wave', feel: 'confused', actMs: 8000, ms: 8000, say: '需要你确认', urgent: true };
      case 'state':
        return this.onState(e.status);
      default:
        return null;
    }
  }

  // 等用户确认中（彩蛋和特殊待机都不打扰）
  get confirming() {
    return this.status === '等你确认';
  }

  restingText(status) {
    return (
      { completed: '已完成', stopped: '已停止', failed: '出错了', awaiting_confirmation: '等你确认', paused: '已暂停' }[status] ||
      '空闲'
    );
  }

  onState(status) {
    const was = this.running;
    this.status = this.restingText(status);
    if (RESTING.has(status)) this.running = false;
    if (!was) return null;
    if (status === 'completed') return { act: 'celebrate', say: '完成啦！点我看结果', urgent: true };
    if (status === 'failed') return { feel: 'sad', ms: 5000, say: '这次没做成，点我看看', urgent: true };
    if (status === 'awaiting_confirmation') {
      return { act: 'wave', feel: 'confused', actMs: 8000, ms: 8000, say: '需要你确认', urgent: true };
    }
    return null;
  }

  // 普通进度气泡限频；重要的（urgent）总会冒
  progress(r) {
    const t = this.now();
    if (t - this.lastProgress < BUBBLE_GAP_MS) return { act: r.act };
    this.lastProgress = t;
    return r;
  }

  // 连戳计数：连戳多次返回 true（该生气了）
  poke() {
    const t = this.now();
    this.pokes = this.pokes.filter((x) => t - x < POKE_WINDOW_MS);
    this.pokes.push(t);
    if (this.pokes.length >= POKE_ANGRY) {
      this.pokes = [];
      return true;
    }
    return false;
  }
}

// 鼠标在小人哪一侧：靠近（水平距离不超过 NEAR_PX 且垂直在附近）时返回 1（向右看）或 -1，否则 0
export function lookDir(petRect, mouse, near = NEAR_PX) {
  const cx = petRect.x + petRect.w / 2;
  const cy = petRect.y + petRect.h / 2;
  const dx = mouse.x - cx;
  const dy = mouse.y - cy;
  if (Math.hypot(dx, dy) > near || Math.abs(dx) < 8) return 0;
  return dx > 0 ? 1 : -1;
}

// 点在小人的哪个部位：上面三分之一算头（摸头），其余算身体（戳）
export function hitPart(relY, height) {
  return height > 0 && relY / height < 0.38 ? 'head' : 'body';
}
