// 工作台质检在页面里跑的测量脚本（quality.py 读入后交给 page.evaluate）：只读，返回原始数据，判定在 Python 里做。
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) return false;
    }
    return true;
  };
  // 可读的名字：自己的 aria-label / title / aria-labelledby、文字（看不见的折叠内容 innerText 是空的，所以再看 textContent）、
  // 里面图标的 aria-label 或 <title>、图片的 alt
  const label = (el) => {
    const by = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map((id) => id && document.getElementById(id)?.textContent).join(' ');
    const inner = el.querySelector('svg[aria-label],[role="img"][aria-label]')?.getAttribute('aria-label') || el.querySelector('svg title')?.textContent || el.querySelector('img[alt]')?.getAttribute('alt');
    const picks = [el.getAttribute('aria-label'), el.getAttribute('title'), by, el.innerText, el.textContent, el.value, inner];
    return picks.map((x) => String(x || '').trim()).find(Boolean) || '';
  };
  // 在哪个窗口里（工作台生成器按窗口把问题交回作者）
  const win = (el) => el.closest('[data-window]')?.dataset.window || '';
  const path = (el) => {
    const bits = [];
    for (let e = el; e && e !== document.body && !e.dataset?.window && bits.length < 4; e = e.parentElement) {
      bits.unshift(e.tagName.toLowerCase() + (e.id ? '#' + e.id : e.classList[0] ? '.' + e.classList[0] : ''));
    }
    const w = win(el);
    return (w ? `[窗口 ${w}] ` : '') + bits.join(' > ');
  };
  const bgOf = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.backgroundImage && s.backgroundImage !== 'none' && !s.backgroundImage.startsWith('radial-gradient')) return { image: true };
      const c = s.backgroundColor;
      if (c && c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)') {
        layers.push(c);
        const translucent = /rgba\(.*,\s*(0?\.\d+|0)\)$/.test(c) || /\/\s*(0?\.\d+|0)\)$/.test(c);
        if (!translucent) break;
      }
    }
    layers.push(getComputedStyle(document.documentElement).backgroundColor || 'rgb(255, 255, 255)');
    return { layers };
  };
  const doc = document.documentElement;
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const n = walker.currentNode, t = n.textContent.trim();
    const el = n.parentElement;
    if (!t || !el || seen.has(el) || el.closest('script,style,noscript,svg title,[aria-hidden="true"]')) continue;
    seen.add(el);
    if (!vis(el)) continue;
    const s = getComputedStyle(el);
    const disabled = !!el.closest('[disabled],[aria-disabled="true"],.k-disabled');
    const inSvg = !!el.closest('svg');
    texts.push({ text: t.slice(0, 40), where: path(el), win: win(el), size: parseFloat(s.fontSize), weight: +s.fontWeight || 400,
      color: s.color, bg: inSvg ? { image: true } : bgOf(el), disabled });  // 图里的字压在图形上，底色量不准，只查字号
  }
  const buttons = [...document.querySelectorAll('button,[role="button"],a[href],input:not([type="hidden"]),select,textarea')]
    .filter(vis).map((el) => {
      const r = el.getBoundingClientRect();
      const lab = label(el) || (el.id && document.querySelector(`label[for="${el.id}"]`)?.innerText) || el.closest('label')?.innerText || el.getAttribute('placeholder') || '';
      return { where: path(el), win: win(el), name: lab.trim().slice(0, 30), w: r.width, h: r.height, tag: el.tagName.toLowerCase(), type: el.type || '' };
    });
  const images = [...document.querySelectorAll('img')].filter(vis)
    .map((el) => ({ where: path(el), win: win(el), alt: el.getAttribute('alt'), ok: el.complete && el.naturalWidth > 0 }));
  const charts = [...document.querySelectorAll('.k-chart,[data-k-chart]')].filter(vis)
    .map((el) => ({ where: path(el), win: win(el), drawn: !!el.querySelector('svg,canvas') || !!el.querySelector('.k-empty') }));
  // 每个窗口卡片的高度（手机上太长要拆或折叠）
  const windows = [...document.querySelectorAll('[data-window]')].map((el) => {
    const card = el.closest('.k-card') || el;
    return { id: el.dataset.window, h: card.getBoundingClientRect().height };
  });
  const scripts = [...document.querySelectorAll('script[src]')].map((s) => s.src);
  const sheets = [...document.querySelectorAll('link[rel="stylesheet"]')].map((l) => l.href);
  const kit = sheets.some((h) => /\/kit\.css(\?|$)/.test(h)) && !!document.querySelector('.k-root');
  const overflow = [...document.querySelectorAll('body *')].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.right > innerWidth + 1 && !el.closest('.k-table-wrap,[data-k-scroll],.k-tabs') && getComputedStyle(el).position !== 'fixed';
  }).slice(0, 5).map(path);
  return {
    title: document.title, kit, scripts, overflow, charset: document.characterSet,
    scrollWidth: doc.scrollWidth, width: innerWidth, height: innerHeight, windows,
    bodyText: document.body.innerText.slice(0, 20000),
    texts, buttons, images, charts,
  };
}
