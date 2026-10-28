// 导出成果：选格式、样式（字号 / 配色 / PPT 比例 / 附题目 / 附过程）和保存位置。
// 转换在服务端本地完成，不调用模型、不花钱。上次的选择记在这台设备的浏览器里。
// 保存位置：浏览器下载 / 每次自己选（浏览器支持时）/ 直接存到电脑上的固定文件夹（只在电脑本机可用）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KEY = 'rt.export';
const DEFAULTS = { format: 'docx', size: 'medium', theme: 'blue', ratio: '16:9', question: true, process: false, dest: 'download' };

export function prefs() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
  } catch {
    return { ...DEFAULTS };
  }
}

function remember(p) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 隐私模式等存不了时，下次用默认值 */
  }
}

/** 下载地址：带上记住的样式。 */
export function exportHref(sid, format, p = prefs()) {
  const q = new URLSearchParams({ format, size: p.size, theme: p.theme, ratio: p.ratio, question: p.question, process: p.process });
  return `/api/sessions/${encodeURIComponent(sid)}/export?${q}`;
}

function refreshLinks() {
  for (const a of document.querySelectorAll('.exports a[data-fmt]')) a.href = exportHref(a.dataset.sid, a.dataset.fmt);
}

const canPick = () => typeof window.showSaveFilePicker === 'function';

function nameFrom(res, fallback) {
  const m = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('Content-Disposition') || '');
  return m ? decodeURIComponent(m[1]) : fallback;
}

export class Exporter {
  constructor(dlg, { api }) {
    this.dlg = dlg;
    this.api = api; // {options(), save(sid, body)}
    this.opts = null;
    this.sid = null;
    this.msg = '';
    this.busy = false;
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('change', (e) => this.onChange(e));
    // 关掉面板后，结束行上的快捷按钮也换成刚选的样式
    dlg.addEventListener('close', () => refreshLinks());
  }

  async open(sid) {
    this.sid = sid;
    this.msg = '';
    if (!this.opts) {
      try {
        this.opts = await this.api.options();
      } catch (e) {
        this.msg = e.message;
      }
    }
    this.p = prefs();
    if (!this.p.folder && this.opts) this.p.folder = this.opts.folder || this.opts.suggested_folder || '';
    if (this.p.dest === 'folder' && !this.opts?.can_save_to_folder) this.p.dest = 'download';
    if (this.p.dest === 'pick' && !canPick()) this.p.dest = 'download';
    this.render();
    if (!this.dlg.open) this.dlg.showModal();
  }

  seg(name, items, value) {
    return `<div class="seg" role="group" data-seg="${name}">${items
      .map((i) => `<button type="button" data-v="${esc(i.value)}" aria-pressed="${String(i.value) === String(value)}">${esc(i.label)}</button>`)
      .join('')}</div>`;
  }

  render() {
    const o = this.opts || { formats: [], themes: [], sizes: [], ratios: [] };
    const p = this.p;
    const dests = [{ value: 'download', label: '浏览器下载' }];
    if (canPick()) dests.push({ value: 'pick', label: '每次自己选位置' });
    if (o.can_save_to_folder) dests.push({ value: 'folder', label: '存到固定文件夹' });
    const destHint = {
      download: '存进浏览器的「下载」文件夹。想改位置：Edge「设置 → 下载」。',
      pick: '点「导出」后弹出系统的保存窗口，自己选文件夹和文件名。',
      folder: '直接存进电脑上的这个文件夹（没有就新建），同名文件不会被覆盖。',
    }[p.dest];
    this.dlg.innerHTML = `
      <div class="vh"><b>导出成果</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb ex">
        <p class="ex-note">在电脑上本地转换，不再调用 AI，不花钱。</p>
        <label>格式</label>${this.seg('format', o.formats, p.format)}
        <label>配色</label>${this.seg('theme', o.themes, p.theme)}
        <label>字号</label>${this.seg('size', o.sizes, p.size)}
        ${p.format === 'pptx' ? `<label>PPT 比例</label>${this.seg('ratio', o.ratios, p.ratio)}` : ''}
        <label>内容</label>
        <div class="ex-checks">
          <label><input type="checkbox" data-check="question" ${p.question ? 'checked' : ''}> 开头附上题目</label>
          <label><input type="checkbox" data-check="process" ${p.process ? 'checked' : ''}> 文末附上过程（各人的初稿、修订、发言）</label>
        </div>
        <label>保存到</label>${this.seg('dest', dests, p.dest)}
        ${p.dest === 'folder' ? `<input class="ex-folder" data-folder value="${esc(p.folder || '')}" placeholder="例如 D:\\圆桌导出" spellcheck="false">` : ''}
        <p class="ex-hint">${esc(destHint)}</p>
        <div class="ex-go"><button type="button" class="btn primary" data-go ${this.busy ? 'disabled' : ''}>${this.busy ? '导出中…' : '导出'}</button></div>
        <p class="ex-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  onChange(e) {
    const c = e.target.closest('[data-check]');
    if (c) this.p[c.dataset.check] = c.checked;
    if (e.target.matches('[data-folder]')) this.p.folder = e.target.value.trim();
    remember(this.p);
  }

  async onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-close')) return this.dlg.close();
    const seg = t.closest('[data-seg]');
    if (seg) {
      this.p[seg.dataset.seg] = t.dataset.v;
      remember(this.p);
      this.msg = '';
      return this.render();
    }
    if (t.hasAttribute('data-go')) await this.go();
  }

  async go() {
    const p = this.p;
    const folder = this.dlg.querySelector('[data-folder]');
    if (folder) p.folder = folder.value.trim();
    remember(p);
    this.busy = true;
    this.msg = '';
    this.render();
    try {
      if (p.dest === 'folder') {
        const r = await this.api.save(this.sid, {
          format: p.format, folder: p.folder, size: p.size, theme: p.theme, ratio: p.ratio, question: p.question, process: p.process,
        });
        this.msg = `已保存：${r.path}`;
        p.folder = r.folder;
        remember(p);
      } else if (p.dest === 'pick') {
        await this.pick();
      } else {
        const a = document.createElement('a');
        a.href = exportHref(this.sid, p.format, p);
        a.download = '';
        document.body.append(a);
        a.click();
        a.remove();
        this.msg = '已开始下载。';
      }
    } catch (err) {
      if (err?.name !== 'AbortError') this.msg = err.message || String(err);
    }
    this.busy = false;
    this.render();
  }

  async pick() {
    const res = await fetch(exportHref(this.sid, this.p.format, this.p));
    if (!res.ok) {
      let detail = `导出失败（${res.status}）`;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        /* 不是 JSON */
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const handle = await window.showSaveFilePicker({ suggestedName: nameFrom(res, `圆桌成果.${this.p.format}`) });
    const w = await handle.createWritable();
    await w.write(blob);
    await w.close();
    this.msg = `已保存：${handle.name}`;
  }
}
