// 「片头」设置：开机动画放不放、怎么放，以及 GMU 访问许可卡上的头像、名字、二维码等。
// 全部存在本机 localStorage（roundtable.boot.*），不上传；二维码在本机生成 / 识别，不联网。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const PREFIX = 'roundtable.boot.';
const MODE = [
  ['full', '片头 + 授权'],
  ['intro', '只放 550C 片头'],
  ['permit', '只放授权'],
  ['off', '关闭'],
];
const WHEN = [
  ['session', '每个浏览器标签只放一次'],
  ['always', '每次打开都放'],
];
// 卡片字段：键名与动画页（web/boot/gmu.html）读取的一致；留空就用卡片上的默认值
const CARD = [
  ['org', '机构简称', 'GMU'],
  ['name', '姓名', '操作员'],
  ['en', '英文名 / 拼音', ''],
  ['post', '职务 / 单位', ''],
  ['serial', '证件编号', '留空则每次随机'],
  ['issuer', '签发机构', '早上好联合 · 身份管理局'],
  ['level', '授权等级', 'LEVEL 3 · 生活区通行'],
  ['valid', '有效期', '本次会话 · 随登出失效'],
  ['scope', '许可范围', '550C 终端 · 只读查询'],
  ['bio', '生物特征', '虹膜 · 指纹'],
];
const PHOTO_PX = 256;
const QR_MAX = 300;

function get(key, fallback = '') {
  try {
    const v = localStorage.getItem(PREFIX + key);
    return v === null ? fallback : v;
  } catch {
    return fallback;
  }
}

function set(key, value) {
  try {
    if (value === '' || value === null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
    return true;
  } catch {
    return false; // 存储满了（头像太大）或被禁用
  }
}

function readCard() {
  try {
    return JSON.parse(get('card', '{}')) || {};
  } catch {
    return {};
  }
}

function loadScript(src, ready) {
  if (ready()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error('加载失败：' + src));
    document.head.appendChild(el);
  });
}

/** 文字 → 二维码位矩阵，存成「边长:base64」（动画页按这个格式画）。 */
export function qrPayload(text) {
  const qr = window.qrcode(0, 'M');
  qr.addData(text, 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  const bytes = new Uint8Array(Math.ceil((n * n) / 8));
  for (let y = 0; y < n; y += 1) {
    for (let x = 0; x < n; x += 1) {
      if (!qr.isDark(y, x)) continue;
      const i = y * n + x;
      bytes[i >> 3] |= 1 << (7 - (i & 7));
    }
  }
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return n + ':' + btoa(bin);
}

function readImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('图片打不开'));
      img.src = String(reader.result);
    };
    reader.onerror = () => reject(new Error('读取失败'));
    reader.readAsDataURL(file);
  });
}

export class BootSettings {
  constructor(dialog) {
    this.dlg = dialog;
    this.msg = '';
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('change', (e) => this.onChange(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
  }

  open() {
    this.msg = '';
    this.render();
    if (!this.dlg.open) this.dlg.showModal();
  }

  close() {
    this.dlg.close();
  }

  render() {
    const mode = get('mode', 'full');
    const when = get('when', 'session');
    const card = readCard();
    const photo = get('photo');
    const qrText = get('qrText');
    const radios = (name, list, cur) =>
      list
        .map(([v, label]) => `<label><input type="radio" name="${name}" value="${v}"${v === cur ? ' checked' : ''}> ${label}</label>`)
        .join('');
    const fields = CARD.map(
      ([k, label, ph]) =>
        `<label class="bt-f"><span>${label}</span><input name="${k}" maxlength="60" value="${esc(card[k] || '')}" placeholder="${esc(ph)}"></label>`,
    ).join('');
    this.dlg.innerHTML = `
      <div class="vh"><b>片头：开机动画</b><button type="button" class="btn" data-preview>预览</button><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb bt">
        <p class="hint">打开圆桌时先放 550C 片头，再接一段「早上好联合（GMU）」访问授权，然后进首页。点「跳过」或按 Esc 随时跳过；网址加 <code>?boot=0</code> 这一次不放。设置和头像只存在这台电脑的浏览器里。</p>
        <div class="bt-row" role="radiogroup" aria-label="播放">${radios('mode', MODE, mode)}</div>
        <div class="bt-row" role="radiogroup" aria-label="什么时候放">${radios('when', WHEN, when)}</div>
        <div class="bt-row">
          <label><input type="checkbox" name="auto"${get('auto', 'on') !== 'off' ? ' checked' : ''}> 授权页自动往下走（不勾就要点「申请访问许可」）</label>
          <label><input type="checkbox" name="voice"${get('voice', 'on') !== 'off' ? ' checked' : ''}> 英文配音（浏览器自带朗读）</label>
        </div>
        <h3>访问许可卡</h3>
        <div class="bt-card">
          <div class="bt-photo">
            <div class="bt-thumb"${photo ? ` style="background-image:url('${esc(photo)}')"` : ''}>${photo ? '' : '头像'}</div>
            <label class="btn">选图片<input type="file" accept="image/*" data-photo hidden></label>
            <button type="button" class="btn" data-photo-clear${photo ? '' : ' disabled'}>清除</button>
          </div>
          <form id="bt-card" class="bt-fields">${fields}
            <div class="bt-save"><button class="btn primary" type="submit">保存卡片</button></div>
          </form>
        </div>
        <h3>二维码</h3>
        <form id="bt-qr" class="bt-qr">
          <input name="qr" maxlength="${QR_MAX}" value="${esc(qrText)}" placeholder="填一段文字或链接，在本机编成二维码；留空用装饰点阵" aria-label="二维码内容">
          <button class="btn" type="submit">生成</button>
          <label class="btn">从图片识别<input type="file" accept="image/*" data-qr-image hidden></label>
          <button type="button" class="btn" data-qr-clear${get('qr') ? '' : ' disabled'}>清除</button>
        </form>
        <p class="hint">${get('qr') ? `已设置：${esc(qrText || '（从图片识别）')}` : '未设置：用证件编号生成装饰点阵'}</p>
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  say(msg) {
    this.msg = msg;
    this.render();
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    if (t.closest('[data-preview]')) {
      this.close();
      window.RoundtableBoot?.play(get('mode', 'full') === 'off' ? 'full' : get('mode', 'full'));
      return;
    }
    if (t.closest('[data-photo-clear]')) {
      set('photo', '');
      return this.say('头像已清除');
    }
    if (t.closest('[data-qr-clear]')) {
      set('qr', '');
      set('qrText', '');
      return this.say('二维码已清除');
    }
  }

  async onChange(e) {
    const t = e.target;
    if (t.name === 'mode' || t.name === 'when') {
      set(t.name, t.value);
      return this.say('已保存');
    }
    if (t.name === 'auto' || t.name === 'voice') {
      set(t.name, t.checked ? 'on' : 'off');
      return this.say('已保存');
    }
    if (t.matches('[data-photo]') && t.files?.[0]) return this.setPhoto(t.files[0]);
    if (t.matches('[data-qr-image]') && t.files?.[0]) return this.decodeQr(t.files[0]);
  }

  onSubmit(e) {
    e.preventDefault();
    const form = e.target.getAttribute('id');
    if (form === 'bt-card') {
      const data = new FormData(e.target);
      const card = {};
      for (const [k] of CARD) {
        const v = String(data.get(k) || '').trim();
        if (v) card[k] = v;
      }
      set('card', Object.keys(card).length ? JSON.stringify(card) : '');
      return this.say('卡片已保存');
    }
    if (form === 'bt-qr') return this.makeQr(String(new FormData(e.target).get('qr') || '').trim());
  }

  async setPhoto(file) {
    try {
      const img = await readImage(file);
      // 裁成正方形、缩到 256px 再存，localStorage 放得下
      const side = Math.min(img.width, img.height);
      const canvas = document.createElement('canvas');
      canvas.width = PHOTO_PX;
      canvas.height = PHOTO_PX;
      canvas
        .getContext('2d')
        .drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, PHOTO_PX, PHOTO_PX);
      const ok = set('photo', canvas.toDataURL('image/jpeg', 0.86));
      this.say(ok ? '头像已保存' : '存不下这张图，换一张小一点的试试');
    } catch (err) {
      this.say(err.message || '头像没存上');
    }
  }

  async makeQr(text) {
    if (!text) {
      set('qr', '');
      set('qrText', '');
      return this.say('二维码已清除');
    }
    try {
      await loadScript('boot/assets/qrcode-gen.js', () => typeof window.qrcode === 'function');
      set('qr', qrPayload(text));
      set('qrText', text);
      this.say('二维码已生成');
    } catch (err) {
      this.say('生成失败：' + (err.message || err));
    }
  }

  async decodeQr(file) {
    this.say('正在识别…');
    try {
      await loadScript('boot/assets/jsqr.js', () => typeof window.jsQR === 'function');
      const img = await readImage(file);
      // jsQR 对尺寸敏感：按几个长边尺寸各试一次，命中即止
      const long = Math.max(img.width, img.height);
      let found = null;
      for (const target of [900, 700, 1100, 1400, long]) {
        const k = Math.min(3, target / long);
        const w = Math.max(1, Math.round(img.width * k));
        const h = Math.max(1, Math.round(img.height * k));
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, w, h);
        const r = window.jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: 'attemptBoth' });
        if (r?.data) {
          found = r.data;
          break;
        }
      }
      if (!found) return this.say('没识别出二维码，换一张清晰、四周留白的图试试');
      await this.makeQr(found.slice(0, QR_MAX));
    } catch (err) {
      this.say('识别失败：' + (err.message || err));
    }
  }
}
