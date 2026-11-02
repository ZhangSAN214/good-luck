// Live2D 播放页的胶水代码：加载模型、关掉库自带的眨眼 / 呼吸 / 视线，改由 driver.js 驱动参数。
// 依赖全局的 PIXI（web/vendor/pixi）和 Live2DCubismCore（运行时从 Live2D 官方地址加载，不进仓库）；
// 有了 Core 才加载 pixi-live2d-display（它在没有 Core 时一加载就报错）。

import { BehaviorDriver, EMOTIONS, ParamMap, rms } from './driver.js';

const $ = (sel) => document.querySelector(sel);
const status = (text) => {
  $('#status').textContent = text;
};

function modelUrl() {
  const q = new URLSearchParams(location.search);
  return q.get('model') || 'models/model.model3.json';
}

function modelParams(core) {
  const out = [];
  const n = core.getParameterCount();
  const ids = core._model.parameters.ids;
  for (let i = 0; i < n; i++) {
    out.push({
      id: ids[i],
      min: core.getParameterMinimumValue(i),
      max: core.getParameterMaximumValue(i),
    });
  }
  return out;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`加载失败：${src}`));
    document.head.appendChild(s);
  });
}

async function main() {
  if (!window.Live2DCubismCore) {
    status('没有加载到 Cubism Core（需要联网从 Live2D 官方地址加载），模型无法显示。');
    return;
  }
  await loadScript('../vendor/pixi-live2d-display/cubism4.min.js');
  const app = new PIXI.Application({
    view: $('#stage'),
    resizeTo: $('#wrap'),
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    resolution: window.devicePixelRatio || 1,
  });
  status('加载模型中……');
  const url = modelUrl();
  const model = await PIXI.live2d.Live2DModel.from(url, { autoInteract: false });
  app.stage.addChild(model);

  const internal = model.internalModel;
  // 遮罩贴图用 4096，否则高清贴图的遮罩边缘会糊
  internal.renderer.setClippingMaskBufferSize(4096);
  // 眨眼、呼吸、视线都交给驱动器，免得两套叠加
  internal.eyeBlink = undefined;
  internal.breath = undefined;

  const core = internal.coreModel;
  const map = new ParamMap(modelParams(core));
  const driver = new BehaviorDriver({ ranges: map.ranges() });
  $('#missing').textContent = map.missing.length
    ? `模型里没有这些参数（驱动不到）：${map.missing.join('、')}`
    : '标准参数都对上了';

  const audio = { ctx: null, analyser: null, buf: null, el: null };
  let last = performance.now();
  internal.on('afterMotionUpdate', () => {
    const now = performance.now();
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (audio.analyser) {
      audio.analyser.getFloatTimeDomainData(audio.buf);
      driver.setAudioLevel(rms(audio.buf));
    }
    map.apply(driver.update(dt), (id, v) => core.setParameterValueById(id, v));
  });

  // 布局：按窗口放下整个人
  const fit = () => {
    const w = app.renderer.width / app.renderer.resolution;
    const h = app.renderer.height / app.renderer.resolution;
    const s = Math.min(w / model.internalModel.originalWidth, h / model.internalModel.originalHeight) * 0.96;
    model.scale.set(s);
    model.x = (w - model.internalModel.originalWidth * s) / 2;
    model.y = (h - model.internalModel.originalHeight * s) / 2;
  };
  fit();
  window.addEventListener('resize', fit);

  // 注意点：跟随鼠标，或闲置时自己看来看去
  let follow = true;
  window.addEventListener('pointermove', (e) => {
    if (!follow) return;
    const r = $('#stage').getBoundingClientRect();
    const head = { x: r.left + r.width / 2, y: r.top + r.height * 0.2 };
    driver.lookAt((e.clientX - head.x) / (r.width / 2), -(e.clientY - head.y) / (r.height / 2));
  });
  $('#follow').addEventListener('change', (e) => {
    follow = e.target.checked;
    if (!follow) driver.lookAt(null);
  });
  $('#stage').addEventListener('pointerleave', () => driver.lookAt(null));

  // 情绪按钮
  const names = {
    calm: '平静', happy: '开心', shy: '害羞', thinking: '思考', surprised: '惊讶', smirk: '坏笑',
  };
  for (const key of Object.keys(EMOTIONS)) {
    const b = document.createElement('button');
    b.textContent = names[key] || key;
    b.addEventListener('click', () => driver.setEmotion(key));
    $('#emotions').appendChild(b);
  }

  // 说话：没有音频时随机音节；选一个音频文件就按音量对口型
  $('#talk').addEventListener('change', (e) => {
    driver.setAudioLevel(null);
    driver.setSpeaking(e.target.checked);
  });
  async function say(src) {
    // 以后接 TTS：传音频地址或 Blob
    if (!audio.ctx) audio.ctx = new AudioContext();
    if (audio.el) audio.el.pause();
    audio.el = new Audio(typeof src === 'string' ? src : URL.createObjectURL(src));
    const node = audio.ctx.createMediaElementSource(audio.el);
    audio.analyser = audio.ctx.createAnalyser();
    audio.analyser.fftSize = 1024;
    audio.buf = new Float32Array(audio.analyser.fftSize);
    node.connect(audio.analyser);
    audio.analyser.connect(audio.ctx.destination);
    driver.setSpeaking(true);
    audio.el.addEventListener('ended', () => {
      driver.setSpeaking(false);
      audio.analyser = null;
    });
    await audio.el.play();
  }
  $('#audio').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) say(f);
  });

  // 给以后的群聊 / 语音用的接口
  window.live2dStage = {
    driver,
    model,
    setEmotion: (name) => driver.setEmotion(name),
    setMood: (mood) => driver.setMood(mood),
    lookAt: (x, y) => driver.lookAt(x, y),
    say,
  };
  status(`已加载：${url}`);
}

main().catch((err) => {
  console.error(err);
  status(`加载失败：${err && err.message ? err.message : err}`);
});
