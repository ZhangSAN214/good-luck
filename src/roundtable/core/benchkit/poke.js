// 自动点一遍（docs/KIT.md「自动点一遍」）：在页面里逐个点每个窗口的按钮、标签、可点的列表项，看点了以后哪些窗口变了。
async () => {
  const MAX_PER_WIN = 6, MAX_ALL = 40, WAIT = 180;
  const SKIP = /删除|清空|重置|退出|注销|打印|下载|导出|delete|remove|clear|reset|logout|print|download|export/i;
  const name = (el) => (el.getAttribute('aria-label') || el.innerText || el.title || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 30);
  const sig = (el) => {
    // 窗口的「样子」：文字 + 结构 + 关键属性；点了以后变没变就比这个
    const s = el.innerText + '|' + el.querySelectorAll('*').length + '|' +
      [...el.querySelectorAll('[aria-selected="true"],[aria-pressed="true"],[aria-expanded="true"],.on,.active,.is-active,[open],:checked')].length;
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return h;
  };
  const wins = [...document.querySelectorAll('[data-window]')];
  const snap = () => Object.fromEntries(wins.map((w) => [w.dataset.window, sig(w)]));
  const pageSig = () => location.hash + '|' + document.title + '|' + document.querySelectorAll('dialog[open],[role="dialog"],.k-toast,[role="alert"],[role="status"]').length;
  const pick = (w) => [...w.querySelectorAll('button,[role="tab"],[role="option"],[role="button"],a[href^="#"],li[tabindex],tr[tabindex],[data-action]')]
    .filter((el) => {
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return false;
      if (el.closest('form') && el.type === 'submit' && el.closest('form').querySelector('input:invalid')) return false;
      return !SKIP.test(name(el));
    })
    .slice(0, MAX_PER_WIN);
  const out = [];
  let n = 0;
  for (const w of wins) {
    for (const el of pick(w)) {
      if (n++ >= MAX_ALL || !el.isConnected) break;
      const before = snap(), pb = pageSig();
      try { el.click(); } catch (e) { /* 页面报错另外记 */ }
      await new Promise((r) => setTimeout(r, WAIT));
      const after = snap();
      const changed = Object.keys(after).filter((k) => after[k] !== before[k]);
      out.push({ win: w.dataset.window, name: name(el) || el.tagName.toLowerCase(), changed, page: pageSig() !== pb });
    }
  }
  return out;
}
