// 设置与新手指导（docs/GUIDE.md）。
// - 顶栏「设置」：一站式说明书。按五个页面和顶栏工具分组，每个功能一句话 + 两三步怎么用 + 「去打开」；
//   顶栏装不下的小按钮（片头、外观、平板、游戏、备份、星图、上网）原样搬进这里的「常用工具」，按钮和功能不变。
// - 新手指导：第一次进某个页面时，页面顶部弹一张小卡片（最多三句），「知道了」只关这一页，「不再提示」全部关掉；
//   设置里可以重新打开、重新看一遍。看过哪页只存在这台电脑的浏览器里（localStorage），不上传。
// - 只是说明文字和跳转，不调用模型、不花钱；文字里不写任何模型、厂商或渠道名。

import { esc } from './util.js';

const KEY = 'roundtable.guide';

// 每个页面第一次进入时的小卡片：最多三句，每句一行
export const TIPS = {
  hub: {
    title: '这里是终端',
    lines: ['卡片上的数字是要你处理的事，点卡片直接过去。', '右上角「背景」「立绘」能换背景、调立绘；顶栏有音乐、时钟、公告和邮件。'],
  },
  work: {
    title: '这里是办公室',
    lines: [
      '在下面输入题目，选好模式和档位，按 Enter 提交。',
      '提交前会显示预计花费；超过门槛或要升级时会先弹卡片问你。',
      '右边的统筹面板能看流程、互评、用量和历史。',
    ],
  },
  home: {
    title: '这里是公寓楼',
    lines: ['成员下班后住在这里，按作息在各个房间活动。', '点小人能聊一句、看状态；拖到别的房间她会去串门。'],
  },
  chat: {
    title: '这里是群聊',
    lines: ['左边选生活群、工作群或私聊；群里 @昵称 指定谁回。', '右键消息能引用、撤回，双击头像拍一拍。'],
  },
  bench: {
    title: '这里是工作台',
    lines: ['生成器：一句话做一个工作台页面。', '超级电脑：把编程的活交给几位助手分工完成。'],
  },
};

// 说明书：open 写法 {entry} 切到那个页面；{click} 点一下页面里已有的按钮；{call} 调用 app.js 交进来的打开函数
export const FEATURES = [
  {
    id: 'hub',
    title: '终端',
    open: { entry: 'hub' },
    items: [
      { name: '首页卡片', what: '一眼看到等你拍板的事、未读消息、余额。', steps: ['打开终端', '点任意一张卡片，直接跳到对应页面'], open: { entry: 'hub' } },
      { name: '大立绘', what: '首页的成员立绘，会动、会说话。', steps: ['点她会说一句，摸头有反应', '右上角「立绘」能换一套、调位置和大小'], open: { entry: 'hub' } },
      { name: '换背景', what: '首页背景可以换成夜景、白天或自己的图。', steps: ['点右上角「背景」', '选一张预设，或上传自己的图（只存在本机）'], open: { entry: 'hub' } },
      { name: '背景音乐', what: '放电脑里的音乐，或跟着电脑上的播放器显示正在放的歌。', steps: ['点顶栏的音乐小胶囊', '「添加」挑音频文件，或切到播放器模式'], open: { entry: 'hub' } },
      { name: '时钟', what: '同时看几个城市的时间，带秒表和倒计时。', steps: ['点顶栏时钟', '增删城市、选主时区，或开秒表 / 倒计时'], open: { entry: 'hub' } },
      { name: '公告与日历', what: '看每次更新改了什么，看节日、生日，加自己的纪念日。', steps: ['点顶栏「公告」', '展开一条就算看过；上面一栏是日历'], open: { entry: 'hub' } },
      { name: '邮件', what: '只读看你的学校邮箱，提醒截止日期。', steps: ['点顶栏「邮件」', '点「登录学校邮箱」，在弹出的窗口里自己登录'], open: { entry: 'hub' } },
      { name: '余额与用量', what: '看账户余额、本月花了多少、钱都花在哪。', steps: ['点终端的「余额」卡片', '或者在这里直接打开'], open: { call: 'usage' } },
    ],
  },
  {
    id: 'work',
    title: '办公室',
    open: { entry: 'work' },
    items: [
      { name: '提一个题目', what: '几位成员一起讨论、分工，给你一个答案。', steps: ['在下面输入题目', '选模式（讨论 / 协同 / 个人 / 头脑风暴 / 自动）和档位', '看一眼预计花费，按 Enter 提交'], open: { entry: 'work' } },
      { name: '附件和资料', what: '把图片、PDF、Word、音频交给成员看。', steps: ['点「添加附件」或把文件拖进输入框', '「资料」能搜网页、存成资料再附上'], open: { entry: 'work' } },
      { name: '确认卡片', what: '花费超过门槛或要升级时，先问你再继续。', steps: ['讨论区出现卡片时点一个选项', '也可以按数字键 1～9 选'], open: { entry: 'work' } },
      { name: '匿名和揭晓', what: '匿名时成员互相不知道是谁，结束后可以揭晓。', steps: ['提交前勾上「匿名」', '讨论结束后点「揭晓身份」'], open: { entry: 'work' } },
      { name: '统筹面板', what: '看流程、分工、互评、用量、历史和员工。', steps: ['看右边的面板', '点上面的标签切换；「收起面板」能腾出地方'], open: { entry: 'work' } },
      { name: '导出成果', what: '把答案存成 Word、PPT、PDF、网页或 Markdown。', steps: ['讨论结束后点讨论区里的「导出」', '选格式和样式，保存'], open: { entry: 'work' } },
      { name: '革新例会', what: '成员读资料、提改进建议、投票，你只管点做或不做。', steps: ['点顶部「革新例会」', '看建议，逐条点「做 / 不做」'], open: { click: '#evolve' } },
      { name: '快捷键', what: '不用鼠标也能操作。', steps: ['/ 或 Ctrl+K 跳到输入框，N 新题目', 'Alt+1～5 切换办公室、公寓楼、群聊、终端、工作台'], open: { entry: 'work' } },
    ],
  },
  {
    id: 'home',
    title: '公寓楼',
    open: { entry: 'home' },
    items: [
      { name: '看谁在哪', what: '成员下班后的住处，按作息在各房间活动。', steps: ['打开公寓楼', '点房间放大看详情，也可以切到俯视「布局图」'], open: { entry: 'home' } },
      { name: '和小人互动', what: '摸头、聊一句、拖去串门。', steps: ['点小人：摸头 / 聊一句 / 看状态 / 私聊', '拖到别的房间，过几分钟她会自己回去'], open: { entry: 'home' } },
      { name: '朋友圈', what: '成员按当天发生的事发动态，你也能发、赞、评。', steps: ['点小人菜单里的「朋友圈」，或群聊左边的「朋友圈」', '右上角能选只看某个人'], open: { call: 'moments' } },
      { name: '成员设定与记忆', what: '每位成员的人设、档案和她记得的事，可以改、删、清空。', steps: ['在这里打开，或群聊顶部「成员设定与记忆」', '选一位成员，看「关于你」「记忆」等分组'], open: { call: 'people' } },
    ],
  },
  {
    id: 'chat',
    title: '群聊',
    open: { entry: 'chat' },
    items: [
      { name: '聊天', what: '像微信一样和成员聊：生活群、工作群、私聊。', steps: ['左边选一个会话', '群里输入 @ 选人，@全员 大家都说；Enter 发送'], open: { entry: 'chat' } },
      { name: '表情、图片、语音', what: '发表情包、图片、视频和语音。', steps: ['点输入框上方的工具条', '表情包和语音点完直接发出'], open: { entry: 'chat' } },
      { name: '消息操作', what: '引用、复制、撤回、拍一拍。', steps: ['右键（平板上长按）一条消息', '双击成员头像拍一拍'], open: { entry: 'chat' } },
    ],
  },
  {
    id: 'bench',
    title: '工作台',
    open: { entry: 'bench' },
    items: [
      { name: '生成器', what: '一句话生成一个工作台页面，自动质检、返工。', steps: ['打开工作台的「生成器」', '写一句你想要的页面，看它一步步做出来'], open: { entry: 'bench' } },
      { name: '超级电脑', what: '把编程的活交给几位助手，自动拆分、分工、合并。', steps: ['打开「超级电脑」', '写清要做什么，点交活；进度和结果在下面'], open: { entry: 'bench' } },
    ],
  },
  {
    id: 'tools',
    title: '顶栏工具',
    items: [
      { name: '出问题点这里', what: '一键自检，生成能发给我们的体检报告。', steps: ['点顶栏「出问题点这里」', '按现场筛选，点生成报告'], open: { click: '#firstaidbtn' } },
      { name: '一起看', what: '把屏幕给成员看，她们陪你一起看、一起吐槽。', steps: ['点顶栏「一起看」', '选整屏或某个窗口，开始'], open: { click: '#watchbtn' } },
      { name: '片头', what: '开机动画放不放、放哪一种。默认是「线条汇聚」：八位成员的线稿汇聚，最后落到首页立绘上。', steps: ['在下面「常用工具」点「片头」'], open: { click: '#bootcfg' } },
      { name: '外观', what: '在方舟式外观和原来的外观之间切换。', steps: ['在下面「常用工具」点「外观」'], open: { click: '#skin' } },
      { name: '平板', what: '在家里的平板、手机上用圆桌。', steps: ['点「平板」', '设口令，或扫码配对'], open: { click: '#lanbtn' } },
      { name: '游戏模式', what: '你玩游戏时圆桌自动让出显卡。', steps: ['点「游戏」', '打开自动，或手动开 / 关'], open: { click: '#gamemodebtn' } },
      { name: '备份', what: '整库备份成员的记忆、关系和聊天，坏了一键恢复。', steps: ['点「备份」', '「现在备份」，需要时选一份恢复'], open: { click: '#backupbtn' } },
      { name: '星图', what: '圆桌和每位成员的立体星图，看变化和缺口。', steps: ['点「星图」', '拖动转动，点星看详情'], open: { click: '#starmapbtn' } },
      { name: '上网', what: '看成员自己逛了什么网站、去哪旅行。', steps: ['点「上网」', '看浏览记录，或让她看一个视频'], open: { click: '#wanderbtn' } },
    ],
  },
];

function readStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}');
    return {
      off: Boolean(raw?.off),
      seen: Array.isArray(raw?.seen) ? raw.seen.filter((x) => typeof x === 'string') : [],
    };
  } catch {
    return { off: false, seen: [] };
  }
}

function writeStore(v) {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    // 存不了（隐私模式）就只在这次生效
  }
}

/** 按搜索词筛说明书：名字、说明、步骤里有这个词就留下；整组标题命中时整组都留下。 */
export function filterFeatures(groups, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return groups;
  const hit = (s) => String(s).toLowerCase().includes(q);
  return groups
    .map((g) => (hit(g.title) ? g : { ...g, items: g.items.filter((it) => [it.name, it.what, ...it.steps].some(hit)) }))
    .filter((g) => g.items.length);
}

/** 第一次进页面的小卡片。 */
export class Coach {
  constructor({ host = document.body, tips = TIPS } = {}) {
    this.host = host;
    this.tips = tips;
    this.el = null;
    this.current = null;
  }

  get state() {
    return readStore();
  }

  /** 进入页面 m 时调用：没看过、没关掉就弹卡片；换页时先收起上一张（算看过）。 */
  show(m) {
    if (this.current) this.dismiss(); // 没点就换了页：也算看过，不再追着弹
    else this.hide();
    const tip = this.tips[m];
    const st = readStore();
    if (!tip || st.off || st.seen.includes(m)) return false;
    this.current = m;
    const el = document.createElement('div');
    el.className = 'coach';
    el.setAttribute('role', 'status');
    el.dataset.entry = m;
    el.innerHTML = `<b class="coach-t">${esc(tip.title)}</b>
      <ul class="coach-l">${tip.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
      <div class="coach-a"><button type="button" class="btn" data-coach="off">不再提示</button><button type="button" class="btn primary" data-coach="ok">知道了</button></div>`;
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-coach]');
      if (!b) return;
      if (b.dataset.coach === 'off') this.turnOff();
      else this.dismiss();
    });
    this.host.appendChild(el);
    this.el = el;
    return true;
  }

  hide() {
    this.el?.remove();
    this.el = null;
    this.current = null;
  }

  /** 「知道了」：这一页以后不再弹。 */
  dismiss() {
    const st = readStore();
    if (this.current && !st.seen.includes(this.current)) st.seen.push(this.current);
    writeStore(st);
    this.hide();
  }

  turnOff() {
    writeStore({ ...readStore(), off: true });
    this.hide();
  }

  /** 设置里的「重新看一遍」：清掉看过的记录并打开。 */
  reset() {
    writeStore({ off: false, seen: [] });
  }

  setOff(off) {
    writeStore({ ...readStore(), off: Boolean(off) });
    if (off) this.hide();
  }
}

/** 顶栏「设置」：说明书 + 常用工具 + 新手指导开关。 */
export class Settings {
  /**
   * @param {HTMLDialogElement} dlg
   * @param {HTMLButtonElement} button 顶栏的「设置」
   * @param {{tuck?: HTMLElement[], go?: (m: string) => any, calls?: Record<string, () => any>, coach?: Coach, current?: () => string}} opts
   *   tuck：要从顶栏搬进来的按钮（元素本身搬过来，原来绑的功能不变）
   */
  constructor(dlg, button, { tuck = [], go = () => {}, calls = {}, coach = null, current = () => '' } = {}) {
    this.dlg = dlg;
    this.button = button;
    this.go = go;
    this.calls = calls;
    this.coach = coach;
    this.current = current;
    this.query = '';
    this.dlg.innerHTML = `
      <div class="vh"><b>设置与说明</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="gd-body">
        <input type="search" class="gd-search" placeholder="搜功能，比如「备份」「导出」「拍一拍」" aria-label="搜功能">
        <section class="gd-sec"><h3>常用工具</h3><div class="gd-tools"></div></section>
        <section class="gd-sec gd-coach"></section>
        <section class="gd-sec"><h3>功能说明书</h3><p class="gd-sub">每个功能一句话、两三步；点「去打开」直接过去。</p><div class="gd-list"></div></section>
      </div>`;
    const tools = this.dlg.querySelector('.gd-tools');
    this.tucked = tuck.filter(Boolean);
    for (const b of this.tucked) {
      b.classList.remove('link', 'bt-open');
      b.classList.add('btn', 'gd-tool');
      tools.appendChild(b);
    }
    // 搬进来的按钮有状态点时（游戏模式开着、有人在上网），顶栏「设置」也带一个点
    const sync = () => this.button.classList.toggle('gd-dot', this.tucked.some((b) => /-on\b/.test(b.className)));
    if (typeof MutationObserver !== 'undefined') {
      const mo = new MutationObserver(sync);
      for (const b of this.tucked) mo.observe(b, { attributes: true, attributeFilter: ['class'] });
    }
    sync();
    button.addEventListener('click', () => this.open());
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.querySelector('.gd-search').addEventListener('input', (e) => {
      this.query = e.target.value;
      this.renderList();
    });
  }

  open() {
    this.renderCoach();
    this.renderList();
    if (!this.dlg.open) this.dlg.showModal();
  }

  close() {
    if (this.dlg.open) this.dlg.close();
  }

  renderCoach() {
    const st = this.coach ? this.coach.state : { off: true };
    this.dlg.querySelector('.gd-coach').innerHTML = `<h3>新手指导</h3>
      <p class="gd-sub">第一次进每个页面时弹一张小卡片，最多三句。</p>
      <div class="gd-row"><label class="hint switch"><input type="checkbox" data-coach-on ${st.off ? '' : 'checked'}> 显示新手指导</label>
      <button type="button" class="btn" data-coach-again>重新看一遍</button></div>`;
  }

  renderList() {
    const groups = filterFeatures(FEATURES, this.query);
    const searching = Boolean(String(this.query).trim());
    this.dlg.querySelector('.gd-list').innerHTML = groups.length
      ? groups
          .map(
            (g) => `<details class="gd-group" data-group="${esc(g.id)}" ${searching ? 'open' : ''}>
        <summary><b>${esc(g.title)}</b><small>${g.items.length} 项</small></summary>
        ${g.items
          .map(
            (it) => `<div class="gd-item">
          <div class="gd-head"><b>${esc(it.name)}</b>${it.open ? `<button type="button" class="btn gd-go" data-go="${esc(g.id)}" data-name="${esc(it.name)}">去打开</button>` : ''}</div>
          <p>${esc(it.what)}</p>
          <ol>${it.steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
        </div>`,
          )
          .join('')}
      </details>`,
          )
          .join('')
      : '<p class="gd-none">没找到，换个词试试。</p>';
  }

  /** 「去打开」：先关设置，再切页面 / 点按钮 / 调打开函数。 */
  async run(open) {
    if (!open) return;
    this.close();
    if (open.entry) await this.go(open.entry);
    if (open.click) document.querySelector(open.click)?.click();
    if (open.call) await this.calls[open.call]?.();
  }

  onClick(e) {
    if (e.target === this.dlg || e.target.closest('[data-close]')) {
      this.close();
      return;
    }
    if (e.target.closest('.gd-tool')) {
      this.close(); // 按钮自己的功能照常执行（打开它的面板），设置先让开
      return;
    }
    const go = e.target.closest('[data-go]');
    if (go) {
      const item = FEATURES.find((g) => g.id === go.dataset.go)?.items.find((it) => it.name === go.dataset.name);
      this.run(item?.open);
      return;
    }
    const on = e.target.closest('[data-coach-on]');
    if (on) {
      this.coach?.setOff(!on.checked);
      return;
    }
    if (e.target.closest('[data-coach-again]')) {
      this.coach?.reset();
      this.close();
      this.coach?.show(this.current());
    }
  }
}
