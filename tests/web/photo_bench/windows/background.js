// 去背景：把和四个角颜色接近的像素变透明（适合纯色或接近纯色的背景）
function strip(ctx, c, tol) {
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  const at = (x, y) => (y * c.width + x) * 4;
  const corners = [at(0, 0), at(c.width - 1, 0), at(0, c.height - 1), at(c.width - 1, c.height - 1)].map((i) => [d[i], d[i + 1], d[i + 2]]);
  const lim = tol * tol * 3;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    for (const [r, g, b] of corners) {
      const dr = d[i] - r, dg = d[i + 1] - g, db = d[i + 2] - b;
      if (dr * dr + dg * dg + db * db <= lim) { d[i + 3] = 0; n++; break; }
    }
  }
  ctx.putImageData(img, 0, 0);
  return n;
}

export default async function mount(el, ctx) {
  const { editor } = ctx;
  let tol = 40;
  el.innerHTML = `<label class="k-field">容差 <span class="k-num" data-out>${tol}</span><input type="range" class="k-range" min="1" max="120" value="${tol}"></label><button type="button" class="k-btn k-primary k-sm" data-do="strip">去掉背景</button>`;
  el.addEventListener('input', (ev) => { tol = Number(ev.target.value); el.querySelector('[data-out]').textContent = tol; });
  editor.addAction({ id: 'strip-bg', label: '去背景', icon: 'wand-sparkles', key: 'mod+shift+b', run: () => go() });
  async function go() {
    if (!editor.active) throw new Error('先打开一张图片');
    let n = 0;
    await editor.busy('正在去背景…', async () => { editor.paint('去背景', (g, c) => { n = strip(g, c, tol); }); });
    editor.notify(n ? `去掉了 ${n} 个像素` : '四角附近没有接近的颜色，调大容差再试');
  }
  el.querySelector('[data-do]').addEventListener('click', () => go().catch((err) => editor.notify(err.message, 'bad')));
}

export async function test(t) {
  t.editor.paint('测试去背景', (g, c) => strip(g, c, 60));
  t.assert(t.pixel(0, 0)[3] === 0, '左上角没变透明');
  t.assert(t.pixel(80, 60)[3] === 255, '中间的白块也被去掉了');
  t.editor.undo();
  t.assert(t.pixel(0, 0)[3] === 255, '撤销后左上角没回来');
  return '四角变透明、中间保留，撤销后复原';
}
