// 编辑器底座的页内自检（docs/KIT.md「K5 企业级验收」）：程序在真浏览器里调用 editor.selftest()，
// 用户也可以打开「成品?selftest=1」自己看结果。会清空当前画布，只在验收时用。
// 每项返回 { id, name, ok, ms, detail, feature }；id 以 k5- 开头的是企业级验收项，其余是功能自带的测试。

const now = () => performance.now();
const frame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

/** 造一张测试图：w × h，渐变 + 色块，内容可辨认（导出后能比对）。 */
export function sampleCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, w, h);
  grad.addColorStop(0, 'rgb(30, 60, 160)');
  grad.addColorStop(1, 'rgb(240, 180, 60)');
  g.fillStyle = grad;
  g.fillRect(0, 0, w, h);
  g.fillStyle = 'rgb(250, 250, 250)';
  g.fillRect(Math.round(w * 0.3), Math.round(h * 0.3), Math.round(w * 0.4), Math.round(h * 0.4));
  return c;
}

export function sampleBlob(w, h) {
  return new Promise((done, fail) => sampleCanvas(w, h).toBlob((b) => (b ? done(b) : fail(new Error('造测试图失败'))), 'image/png'));
}

function signature(editor) {
  const c = editor.exportCanvas();
  const g = c.getContext('2d');
  const px = g.getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data;
  return JSON.stringify({ size: [c.width, c.height], layers: editor.layers.map((l) => [l.name, l.visible, Math.round(l.opacity * 100), l.x, l.y]), px: [...px] });
}

async function check(id, name, fn, feature = '') {
  const t0 = now();
  try {
    const detail = await fn();
    return { id, name, ok: true, ms: Math.round(now() - t0), detail: detail || '', feature };
  } catch (err) {
    return { id, name, ok: false, ms: Math.round(now() - t0), detail: String((err && err.message) || err).slice(0, 300), feature };
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

export async function selftest(editor, { undoMin = 20, bigPx = 4000, bigMaxMs = 8000, only = null } = {}) {
  const results = [];
  const want = (id) => !only || only.includes(id);
  const small = async () => {
    editor.reset();
    const layer = await editor.importFile(await sampleBlob(160, 120), { name: '测试图' });
    assert(layer, '测试图打不开');
    return layer;
  };

  if (want('k5-undo')) {
    results.push(await check('k5-undo', `撤销至少 ${undoMin} 步`, async () => {
      await small();
      const n = undoMin + 5;
      const sigs = [signature(editor)];
      for (let i = 0; i < n; i++) {
        const k = i % 3;
        if (k === 0) editor.addLayer({ name: `步骤 ${i}` });
        else if (k === 1) editor.setLayer(editor.active.id, { opacity: 0.2 + (i % 7) / 10 });
        else editor.paint(`涂一块 ${i}`, (g, c) => { g.fillStyle = `rgb(${(i * 37) % 255}, 80, 120)`; g.fillRect(0, 0, c.width, c.height); });
        sigs.push(signature(editor));
      }
      assert(editor.history.undo >= undoMin, `只记住了 ${editor.history.undo} 步`);
      for (let i = 0; i < undoMin; i++) assert(editor.undo(), `第 ${i + 1} 次撤销没有生效`);
      assert(signature(editor) === sigs[n - undoMin], `撤销 ${undoMin} 步后画面和第 ${n - undoMin} 步时不一样`);
      for (let i = 0; i < undoMin; i++) assert(editor.redo(), `第 ${i + 1} 次重做没有生效`);
      assert(signature(editor) === sigs[n], '全部重做后画面和撤销前不一样');
      return `做了 ${n} 步，撤销 ${undoMin} 步、再重做，画面都对得上（最多记 ${editor.history.limit} 步）`;
    }));
  }

  let bigOk = false;
  if (want('k5-big') || want('k5-export')) {
    const h = Math.round(bigPx * 0.75);
    results.push(await check('k5-big', `打开 ${bigPx}px 大图不卡死`, async () => {
      editor.reset();
      const blob = await sampleBlob(bigPx, h);
      const t0 = now();
      const layer = await editor.importFile(blob, { name: '大图' });
      await frame();
      const open = now() - t0;
      assert(layer, '大图打不开');
      assert(open < bigMaxMs, `打开用了 ${Math.round(open)} 毫秒，超过 ${bigMaxMs}`);
      const t1 = now();
      editor.applyFilter('亮度', 'brightness(110%)');
      await frame();
      const fx = now() - t1;
      assert(fx < bigMaxMs, `调一次亮度用了 ${Math.round(fx)} 毫秒，超过 ${bigMaxMs}`);
      editor.undo();
      // 页面还能响应：一帧之内回得来
      const t2 = now();
      await frame();
      assert(now() - t2 < 1000, '处理完后页面一秒内没有响应');
      bigOk = true;
      return `${bigPx}×${h}：打开 ${Math.round(open)} 毫秒，调亮度 ${Math.round(fx)} 毫秒`;
    }));
  }

  if (want('k5-export')) {
    results.push(await check('k5-export', '导出尺寸不变', async () => {
      if (!bigOk) await small();
      const { width, height } = editor.doc;
      const blob = await editor.exportBlob();
      assert(blob.type === 'image/png', `导出的不是 PNG（${blob.type}）`);
      const bmp = await createImageBitmap(blob);
      const got = [bmp.width, bmp.height];
      bmp.close?.();
      assert(got[0] === width && got[1] === height, `画布 ${width}×${height}，导出 ${got[0]}×${got[1]}`);
      return `画布和导出都是 ${width}×${height}`;
    }));
  }

  if (want('k5-error')) {
    results.push(await check('k5-error', '出错有提示', async () => {
      editor.reset();
      const box = editor.root.querySelector('[role="alert"]');
      if (box) box.hidden = true;
      const got = await editor.importFile(new Blob(['这不是图片'], { type: 'image/png' }), { name: '坏文件' });
      assert(got === null, '坏文件居然打开了');
      await frame();
      const shown = [...document.querySelectorAll('[role="alert"]')].find((el) => !el.hidden && el.textContent.trim());
      assert(shown, '打开坏文件后页面上没有出错提示');
      assert(editor.layers.length === 0, '打开失败却多了图层');
      return `提示：${shown.textContent.trim().slice(0, 60)}`;
    }));
  }

  if (want('k5-keys')) {
    results.push(await check('k5-keys', '快捷键能用', async () => {
      await small();
      editor.addLayer({ name: '快捷键测试' });
      const press = (key, extra = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ctrlKey: true, ...extra }));
      press('z');
      assert(editor.layers.length === 1, 'Ctrl+Z 没有撤销');
      press('z', { shiftKey: true });
      assert(editor.layers.length === 2, 'Ctrl+Shift+Z 没有重做');
      press('j');
      assert(editor.layers.length === 3, 'Ctrl+J 没有复制图层');
      return 'Ctrl+Z、Ctrl+Shift+Z、Ctrl+J 都生效';
    }));
  }

  if (!only || only.includes('features')) {
    for (const t of editor.tests) {
      results.push(await check(`test:${t.name}`, t.name, async () => {
        await small();
        const ctx = {
          editor,
          assert,
          sample: (w = 160, h = 120) => sampleBlob(w, h),
          pixel(x, y, id) {
            const c = id ? editor.layerCanvas(id) : editor.exportCanvas();
            return [...c.getContext('2d').getImageData(x, y, 1, 1).data];
          },
        };
        const r = await t.fn(ctx);
        return typeof r === 'string' ? r : '';
      }, t.feature));
    }
  }

  editor.reset();
  return { ok: results.every((r) => r.ok), results };
}

/** 页面地址带 ?selftest=1 时：跑一遍并把结果显示在右下角（用户自己验收用）。 */
export async function showSelftest(editor, options) {
  const { ok, results } = await selftest(editor, options);
  const box = document.createElement('section');
  box.className = 'k-ie-report';
  box.setAttribute('aria-label', '自检结果');
  box.innerHTML = `<h2>${ok ? '自检全部通过' : '自检有没过的项'}</h2><ul>${results.map((r) => `<li class="${r.ok ? 'k-ie-ok' : 'k-ie-no'}"><strong>${r.ok ? '通过' : '没过'}</strong> ${r.name.replace(/</g, '&lt;')}<span class="k-muted">${String(r.detail).replace(/</g, '&lt;')}</span></li>`).join('')}</ul>`;
  document.body.appendChild(box);
  return { ok, results };
}
