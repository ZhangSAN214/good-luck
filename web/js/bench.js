// 「工作台」页（分段栏第五个入口）：生成器、超级电脑和 dsh 三块，风格跟终端首页一致（液态玻璃、衬线标题）。
// 三块的内容分别由 BenchGenPanel、SuperPanel 和 Workbench 画；这里只管页面骨架、标签切换和「现在看得见吗」。

const TABS = ['gen', 'super', 'dsh'];
const KEY = 'roundtable.bench.tab';

export class Bench {
  /** @param {HTMLElement} root 分段栏对应的 section */
  constructor(root) {
    this.root = root;
    let saved = 'gen';
    try {
      saved = localStorage.getItem(KEY) || 'gen';
    } catch {
      /* 本机存储不能用时默认生成器 */
    }
    this.tab = TABS.includes(saved) ? saved : 'gen';
    root.innerHTML = `<div class="bn-wrap">
      <div class="bn-head">
        <div class="bn-title"><span class="bn-zh">工作台</span><span class="bn-en">WORKBENCH</span></div>
        <div class="bn-tabs hb-glass" role="tablist" aria-label="工作台">
          <button type="button" role="tab" data-tab="gen">生成器</button>
          <button type="button" role="tab" data-tab="super">超级电脑</button>
          <button type="button" role="tab" data-tab="dsh" hidden>dsh</button>
        </div>
      </div>
      <div class="bn-card hb-glass" data-pane="gen" role="tabpanel"></div>
      <div class="bn-card hb-glass" data-pane="super" role="tabpanel"></div>
      <div class="bn-card hb-glass" data-pane="dsh" role="tabpanel"></div>
    </div>`;
    root.addEventListener('click', (e) => {
      const b = e.target.closest('[data-tab]');
      if (b) this.select(b.dataset.tab);
    });
    this.paint();
  }

  pane(name) {
    return this.root.querySelector(`[data-pane="${name}"]`);
  }

  tabButton(name) {
    return this.root.querySelector(`[data-tab="${name}"]`);
  }

  /** 这一块现在看得见吗（页面显示中、且选中了这个标签）：看得见时刷新得勤一些。 */
  visible(name) {
    return !this.root.hidden && this.tab === name;
  }

  select(name) {
    if (!TABS.includes(name)) return;
    this.tab = name;
    try {
      localStorage.setItem(KEY, name);
    } catch {
      /* 只是记住上次的标签，存不了也没关系 */
    }
    this.paint();
    this.onSelect?.(name);
  }

  paint() {
    for (const name of TABS) {
      const on = this.tab === name;
      this.tabButton(name).setAttribute('aria-selected', String(on));
      this.pane(name).hidden = !on;
    }
  }

  show() {
    this.root.hidden = false;
    // dsh 标签被藏起来（这份圆桌没带 dsh）时回到生成器
    if (this.tab === 'dsh' && this.tabButton('dsh').hidden) this.select('gen');
    this.onSelect?.(this.tab);
    return Promise.resolve();
  }

  hide() {
    this.root.hidden = true;
  }
}
