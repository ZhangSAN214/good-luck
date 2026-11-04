// 裁剪：用矩形选区框出要保留的范围，点「裁剪到选区」
export default async function mount(el, ctx) {
  const { editor } = ctx;
  el.innerHTML = '<p class="k-muted" data-info>先用选区工具在画布上框出范围</p><div class="k-row"><button type="button" class="k-btn k-sm" data-do="tool">选区工具</button><button type="button" class="k-btn k-primary k-sm" data-do="crop">裁剪到选区</button></div>';
  const info = el.querySelector('[data-info]');
  const show = (s) => { info.textContent = s ? `选区 ${Math.round(s.w)} × ${Math.round(s.h)}` : '先用选区工具在画布上框出范围'; };
  editor.on('selection', show);
  el.addEventListener('click', (ev) => {
    const d = ev.target.closest('[data-do]')?.dataset.do;
    if (d === 'tool') { editor.setTool('select'); info.textContent = '在画布上拖出一个框'; return; }
    if (d !== 'crop') return;
    if (!editor.doc.width) { editor.notify('先打开一张图片', 'bad'); return; }
    if (!editor.selection) { editor.notify('先用选区工具框出要保留的范围', 'bad'); return; }
    const r = editor.crop();
    editor.notify(`已裁成 ${r.w} × ${r.h}`);
  });
}

export async function test(t) {
  t.editor.setSelection({ x: 10, y: 20, w: 50, h: 40 });
  t.editor.crop();
  t.assert(t.editor.doc.width === 50 && t.editor.doc.height === 40, `裁完是 ${t.editor.doc.width}×${t.editor.doc.height}`);
  t.editor.undo();
  t.assert(t.editor.doc.width === 160 && t.editor.doc.height === 120, '撤销后尺寸没回来');
  return '裁成 50×40，撤销后回到 160×120';
}
