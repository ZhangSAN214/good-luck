// 亮度与对比度：拖滑块看数值，点「应用」对当前图层生效（一次一步，可撤销）
export default async function mount(el, ctx) {
  const { editor } = ctx;
  const vals = { brightness: 100, contrast: 100, saturate: 100 };
  const rows = [['brightness', '亮度'], ['contrast', '对比度'], ['saturate', '饱和度']];
  el.innerHTML = rows.map(([k, label]) => `<label class="k-field">${label} <span class="k-num" data-out="${k}">100%</span><input type="range" class="k-range" data-k="${k}" min="0" max="200" value="100"></label>`).join('')
    + '<div class="k-row"><button type="button" class="k-btn k-primary k-sm" data-do="apply">应用</button><button type="button" class="k-btn k-sm" data-do="reset">复位</button></div>';
  el.addEventListener('input', (ev) => {
    const k = ev.target.dataset.k;
    if (!k) return;
    vals[k] = Number(ev.target.value);
    el.querySelector(`[data-out="${k}"]`).textContent = `${vals[k]}%`;
  });
  const reset = () => {
    for (const [k] of rows) { vals[k] = 100; el.querySelector(`[data-k="${k}"]`).value = 100; el.querySelector(`[data-out="${k}"]`).textContent = '100%'; }
  };
  el.addEventListener('click', (ev) => {
    const d = ev.target.closest('[data-do]')?.dataset.do;
    if (d === 'reset') { reset(); editor.notify('滑块已复位'); }
    if (d !== 'apply') return;
    if (!editor.active) { editor.notify('先打开一张图片', 'bad'); return; }
    editor.applyFilter('调整亮度与对比度', `brightness(${vals.brightness}%) contrast(${vals.contrast}%) saturate(${vals.saturate}%)`);
    editor.notify('已应用，Ctrl+Z 可以撤销');
    reset();
  });
}

export async function test(t) {
  const before = t.pixel(10, 10);
  t.editor.applyFilter('测试变亮', 'brightness(150%)');
  const after = t.pixel(10, 10);
  t.assert(after[2] > before[2], `变亮后蓝色通道没变大（${before} → ${after}）`);
  t.editor.undo();
  t.assert(t.pixel(10, 10).join() === before.join(), '撤销后没有回到原样');
  return '变亮后像素变了，撤销后复原';
}
