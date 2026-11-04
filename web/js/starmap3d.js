// 星图的 WebGL 画法（docs/STARMAP.md）：three.js（本地打包在 vendor/three，MIT），辉光后期、DOM 文字标签。
// 只管画和点：位置由 starmap.js 的 layout() 算好传进来；选中 / 时间轴 / 卡片都由 starmap.js 决定。
// 四种风格（THEMES）：霓虹星云 neon、极简线稿 mono、玻璃星球 glass、星座金线 gold。
// 浏览器不支持 WebGL 时 starmap.js 退回 canvas 2D 画法。

import { ICONS } from './starmap_icons.js';

const LIB = '../vendor/three/three-starmap.js';
let lib = null;
export async function loadLib() {
  lib = lib || (await import(LIB));
  return lib;
}

export function webglOK() {
  try {
    const c = document.createElement('canvas');
    return Boolean(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
}

const HUE = {
  core: 44, hub: 198, work: 36, home: 152, chat: 328, bench: 262, members: 14, health: 96,
  identity: 210, personality: 330, values: 46, likes: 16, ability: 162, memory: 262,
  relation: 190, emotion: 352, style: 288, growth: 104,
};
function hash(s) {
  let h = 2166136261;
  for (const c of String(s)) h = Math.imul(h ^ c.codePointAt(0), 16777619);
  return h >>> 0;
}
const hueOf = (g) => (g in HUE ? HUE[g] : hash(g) % 360);

// 每种风格：背景、辉光、星的画法、线的颜色、标签样式（CSS 类 sm3-<名字>）
export const THEMES = {
  neon: {
    label: '霓虹星云', bg: 0x04050d, bloom: [1.25, 0.55, 0.05], sat: 0.85, light: 0.62,
    star: 'glow', blend: 'add', line: null, lineAlpha: 0.22, dust: 900, nebula: true, ring: true,
  },
  mono: {
    // 背景透明，纸面、暗角和点阵由 CSS 画；远处的线和星用雾淡进纸色，近实远虚出层次
    label: '极简线稿', bg: null, fog: 0xf1efe9, bloom: null, sat: 0, light: 0.12, accent: 0xff5a3c,
    star: 'dot', blend: 'normal', line: 0x1b1b1b, lineAlpha: 0.3, dust: 320, dustColor: 0xa7a299, nebula: false, ring: true,
    // 立体：小星是有光影的小球，地面有投影和垂线；手绘：线和轨道是铅笔抖两笔
    clay: true, sketch: true, floor: true,
  },
  glass: {
    label: '玻璃星球', bg: null, bloom: null, sat: 0.7, light: 0.66,
    star: 'glass', blend: 'normal', line: 0x7b80b8, lineAlpha: 0.35, dust: 160, dustColor: 0xffffff, nebula: false, ring: true,
  },
  gold: {
    label: '星座金线', bg: 0x070b1d, bloom: [0.6, 0.35, 0.2], sat: 0.25, light: 0.78,
    star: 'spark', blend: 'add', line: 0xd9b26a, lineAlpha: 0.45, dust: 1400, nebula: false, ring: true, gold: true,
  },
};

function texture(T, kind) {
  const s = 128;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  const m = s / 2;
  if (kind === 'glow') {
    const r = g.createRadialGradient(m, m, 0, m, m, m);
    r.addColorStop(0, 'rgba(255,255,255,1)');
    r.addColorStop(0.18, 'rgba(255,255,255,.85)');
    r.addColorStop(0.42, 'rgba(255,255,255,.22)');
    r.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, s, s);
  } else if (kind === 'spark') {
    const r = g.createRadialGradient(m, m, 0, m, m, m * 0.5);
    r.addColorStop(0, 'rgba(255,255,255,1)');
    r.addColorStop(0.3, 'rgba(255,255,255,.5)');
    r.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, s, s);
    g.globalCompositeOperation = 'lighter';
    for (const [w, h] of [[m, 2.2], [2.2, m]]) {
      const lg = g.createRadialGradient(m, m, 0, m, m, m);
      lg.addColorStop(0, 'rgba(255,255,255,.95)');
      lg.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = lg;
      g.fillRect(m - w, m - h, w * 2, h * 2);
    }
  } else if (kind === 'dot') {
    g.fillStyle = '#fff';
    g.beginPath();
    g.arc(m, m, m * 0.42, 0, Math.PI * 2);
    g.fill();
  } else if (kind === 'ring') {
    g.strokeStyle = '#fff';
    g.lineWidth = 5;
    g.beginPath();
    g.arc(m, m, m * 0.8, 0, Math.PI * 2);
    g.stroke();
  } else if (kind === 'dash') {
    g.strokeStyle = '#fff';
    g.lineWidth = 6;
    g.setLineDash([12, 10]);
    g.beginPath();
    g.arc(m, m, m * 0.78, 0, Math.PI * 2);
    g.stroke();
  } else if (kind === 'pencil') {
    // 手绘圈：两笔不完全重合、首尾略出头
    g.strokeStyle = '#fff';
    g.lineCap = 'round';
    for (const [w, r0, a0, ph] of [[4, 0.78, -0.3, 0.4], [2.4, 0.81, 2.6, 1.9]]) {
      g.lineWidth = w;
      g.beginPath();
      for (let i = 0; i <= 64; i += 1) {
        const a = a0 + (i / 64) * Math.PI * 2.12;
        const r = m * (r0 + 0.025 * Math.sin(a * 3 + ph) + 0.012 * Math.sin(a * 7));
        const x = m + Math.cos(a) * r;
        const y = m + Math.sin(a) * r;
        if (i) g.lineTo(x, y);
        else g.moveTo(x, y);
      }
      g.stroke();
    }
  } else if (kind === 'shadow') {
    const r = g.createRadialGradient(m, m, 0, m, m, m);
    r.addColorStop(0, 'rgba(40,30,15,.55)');
    r.addColorStop(0.45, 'rgba(40,30,15,.22)');
    r.addColorStop(1, 'rgba(40,30,15,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, s, s);
  } else if (kind === 'soft') {
    const r = g.createRadialGradient(m, m, 0, m, m, m);
    r.addColorStop(0, 'rgba(255,255,255,.55)');
    r.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = r;
    g.fillRect(0, 0, s, s);
  }
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  return t;
}

// 极简线稿的星域 / 中心 / 成员：白底细黑圈 + 图标（或名字的第一个字）
function badge(T, { icon, text, accent = null }) {
  const s = 256;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  const m = s / 2;
  // 像一颗立体按钮：左上受光、右下暗一点，底部一圈内阴影
  const R0 = m - 14;
  const fill = g.createRadialGradient(m - R0 * 0.35, m - R0 * 0.4, R0 * 0.1, m, m, R0 * 1.05);
  fill.addColorStop(0, '#ffffff');
  fill.addColorStop(0.6, '#f6f3ed');
  fill.addColorStop(1, '#e4dfd5');
  g.fillStyle = fill;
  g.beginPath();
  g.arc(m, m, R0, 0, Math.PI * 2);
  g.fill();
  const rim = g.createLinearGradient(0, m - R0, 0, m + R0);
  rim.addColorStop(0, 'rgba(255,255,255,.9)');
  rim.addColorStop(1, 'rgba(120,105,80,.25)');
  g.strokeStyle = rim;
  g.lineWidth = 6;
  g.beginPath();
  g.arc(m, m, R0 - 4, 0, Math.PI * 2);
  g.stroke();
  // 铅笔描边：两笔、轻微抖动、首尾出头
  g.strokeStyle = accent || '#1b1a18';
  g.lineCap = 'round';
  for (const [w, r0, a0, ph] of [[4.2, 1, -0.6, 0.3], [2, 1.035, 2.2, 1.7]]) {
    g.lineWidth = w;
    g.beginPath();
    for (let i = 0; i <= 72; i += 1) {
      const a = a0 + (i / 72) * Math.PI * 2.08;
      const r = R0 * (r0 + 0.018 * Math.sin(a * 3 + ph) + 0.008 * Math.sin(a * 8 + ph));
      if (i) g.lineTo(m + Math.cos(a) * r, m + Math.sin(a) * r);
      else g.moveTo(m + Math.cos(a) * r, m + Math.sin(a) * r);
    }
    g.stroke();
  }
  g.fillStyle = accent || '#1b1a18';
  if (icon && ICONS[icon]) {
    g.save();
    const k = (s * 0.44) / 960;
    g.translate(m - 480 * k, m - 480 * k);
    g.scale(k, k);
    g.translate(0, 960);
    g.fill(new Path2D(ICONS[icon]));
    g.restore();
  } else if (text) {
    g.font = '500 112px "Noto Serif SC", serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, m, m + 6);
  }
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

const SIZE = { core: 30, domain: 15, member: 13, star: 6, gap: 9 };

export class Scene3D {
  constructor(host, { theme = 'neon', accent = '#ff5a3c', onPick, onHover } = {}) {
    this.host = host;
    this.themeName = THEMES[theme] ? theme : 'neon';
    this.th = THEMES[this.themeName];
    this.onPick = onPick || (() => {});
    this.onHover = onHover || (() => {});
    const L = lib;
    const T = L.THREE;
    this.T = T;
    this.acc = new T.Color(accent);
    this.accMats = [];
    this.renderer = new T.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer.toneMapping = this.th.star === 'dot' ? T.NoToneMapping : T.ACESFilmicToneMapping;
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.className = 'sm3-gl';
    this.labels = new L.CSS2DRenderer();
    this.labels.domElement.className = `sm3-labels sm3-${this.themeName}`;
    host.appendChild(this.labels.domElement);
    host.classList.add(`sm3-host-${this.themeName}`);
    this.scene = new T.Scene();
    if (this.th.bg !== null) this.scene.background = new T.Color(this.th.bg);
    if (this.th.bg !== null) this.scene.fog = new T.FogExp2(this.th.bg, 0.00055);
    if (this.th.fog) this.scene.fog = new T.Fog(this.th.fog, 500, 2300);
    this.camera = new T.PerspectiveCamera(48, 1, 1, 6000);
    this.camera.position.set(0, 320, 900);
    this.controls = new L.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    // 一直在动：自动缓慢旋转（系统要求减少动态时转得更慢，但不停）
    this.calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = this.calm ? 0.12 : 0.35;
    this.controls.minDistance = 140;
    this.controls.maxDistance = 2400;
    this.tex = {};
    for (const k of ['glow', 'spark', 'dot', 'ring', 'dash', 'soft', 'pencil', 'shadow']) this.tex[k] = texture(T, k);
    if (this.th.sketch) this.tex.ring = this.tex.pencil;
    if (this.th.clay) {
      // 柔和的棚拍光：天光 + 一盏左上方的主光，小球有明暗和高光
      this.scene.add(new T.HemisphereLight(0xffffff, 0xcfc6b4, 2.2));
      const key = new T.DirectionalLight(0xffffff, 2.4);
      key.position.set(-400, 900, 500);
      this.scene.add(key);
    }
    if (this.themeName === 'glass') {
      const pm = new T.PMREMGenerator(this.renderer);
      this.scene.environment = pm.fromScene(new L.RoomEnvironment(), 0.04).texture;
      this.scene.add(new T.AmbientLight(0xffffff, 0.6));
      const d = new T.DirectionalLight(0xffffff, 1.6);
      d.position.set(300, 600, 400);
      this.scene.add(d);
    }
    if (this.th.bloom) {
      this.composer = new L.EffectComposer(this.renderer);
      this.composer.addPass(new L.RenderPass(this.scene, this.camera));
      const [s, r, t] = this.th.bloom;
      this.bloom = new L.UnrealBloomPass(new T.Vector2(256, 256), s, r, t);
      this.composer.addPass(this.bloom);
      this.composer.addPass(new L.OutputPass());
    }
    this.backdrop();
    this.ray = new T.Raycaster();
    this.ptr = new T.Vector2();
    this.bindPointer();
    this.fly = null;
    this.sel = null;
    this.clock = performance.now();
    this.resize();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    const tick = () => {
      this.raf = requestAnimationFrame(tick);
      this.frame();
    };
    this.raf = requestAnimationFrame(tick);
  }

  color(group, light) {
    const T = this.T;
    const th = this.th;
    if (th.gold) return new T.Color().setHSL(group === 'core' ? 0.12 : hueOf(group) / 360, th.sat, light ?? th.light);
    if (th.sat === 0) return new T.Color(0x1b1b1b);
    return new T.Color().setHSL(hueOf(group) / 360, th.sat, light ?? th.light);
  }

  backdrop() {
    const T = this.T;
    const th = this.th;
    const n = th.dust;
    const geo = new T.BufferGeometry();
    const arr = new Float32Array(n * 3);
    let a = 7;
    const rnd = () => {
      a = (a * 16807) % 2147483647;
      return a / 2147483647;
    };
    for (let i = 0; i < n; i += 1) {
      const u = rnd() * 2 - 1;
      const t = rnd() * Math.PI * 2;
      const r = 900 + rnd() * 1800;
      arr.set([Math.sqrt(1 - u * u) * Math.cos(t) * r, u * r * 0.7, Math.sqrt(1 - u * u) * Math.sin(t) * r], i * 3);
    }
    geo.setAttribute('position', new T.BufferAttribute(arr, 3));
    const mat = new T.PointsMaterial({
      size: th.gold ? 3.2 : 4,
      map: this.tex[th.star === 'dot' ? 'dot' : 'glow'],
      color: th.dustColor ?? 0xc9d6ff,
      transparent: true,
      opacity: th.star === 'dot' ? 0.55 : 0.8,
      depthWrite: false,
      blending: th.blend === 'add' ? T.AdditiveBlending : T.NormalBlending,
    });
    this.dust = new T.Points(geo, mat);
    this.scene.add(this.dust);
    if (th.nebula) {
      for (const [x, y, z, h, s] of [[-900, 200, -1400, 0.72, 1900], [1100, -300, -1200, 0.9, 1600], [200, 500, 1500, 0.55, 1500], [-400, -500, 900, 0.83, 1300]]) {
        const sp = new T.Sprite(new T.SpriteMaterial({ map: this.tex.soft, color: new T.Color().setHSL(h, 0.8, 0.45), transparent: true, opacity: 0.32, depthWrite: false, blending: T.AdditiveBlending }));
        sp.position.set(x, y, z);
        sp.scale.setScalar(s);
        this.scene.add(sp);
      }
    }
    if (th.ring) {
      this.orbits = new T.Group();
      this.scene.add(this.orbits);
    }
  }

  setMap(map, pos, orbit) {
    const T = this.T;
    const L = lib;
    const th = this.th;
    if (this.root) {
      this.scene.remove(this.root);
      for (const o of this.labelObjs || []) o.element.remove();
    }
    this.map = map;
    this.pos = pos;
    this.accMats = [];
    this.root = new T.Group();
    this.scene.add(this.root);
    this.nodes = new Map();
    this.labelObjs = [];
    this.pickables = [];
    const add = th.blend === 'add' ? T.AdditiveBlending : T.NormalBlending;
    if (this.orbits) {
      this.orbits.clear();
      const fine = th.star === 'dot';
      if (th.sketch) {
        // 手绘轨道：每圈两笔，半径带一点起伏，首尾不完全接上
        let seed = 11;
        const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
        for (const [r, op] of [[orbit, 0.5], [orbit * 0.5, 0.28], [orbit * 1.55, 0.2]]) {
          for (let k = 0; k < 2; k += 1) {
            const pts = [];
            const a0 = rnd() * Math.PI * 2;
            const ph = rnd() * 6;
            const span = Math.PI * 2 * (1.015 + rnd() * 0.03);
            for (let i = 0; i <= 240; i += 1) {
              const a = a0 + (i / 240) * span;
              const rr = r * (1 + 0.006 * Math.sin(a * 2 + ph) + 0.003 * Math.sin(a * 5 + ph * 2)) + (k ? 2.2 : 0);
              pts.push(new T.Vector3(Math.cos(a) * rr, Math.sin(a * 3 + ph) * 1.2, Math.sin(a) * rr));
            }
            this.orbits.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: th.line, transparent: true, opacity: op * (k ? 0.45 : 1), depthWrite: false })));
          }
        }
      }
      for (const [r, op] of th.sketch ? [] : [[orbit, fine ? 0.42 : 0.5], [orbit * 0.5, fine ? 0.2 : 0.25], [orbit * 1.55, fine ? 0.12 : 0.18]]) {
        const w = fine ? 0.35 : 0.6;
        const ring = new T.Mesh(
          new T.RingGeometry(r - w, r + w, 256),
          new T.MeshBasicMaterial({ color: th.line ?? 0x8fa2ff, transparent: true, opacity: op * (th.sat === 0 ? 0.6 : 0.5), side: T.DoubleSide, depthWrite: false }),
        );
        ring.rotation.x = -Math.PI / 2;
        this.orbits.add(ring);
      }
      if (fine) {
        // 外圈刻度：像仪表盘一样的细刻度，每 30° 一根长的
        const pts = [];
        for (let i = 0; i < 120; i += 1) {
          const a = (i / 120) * Math.PI * 2;
          const r0 = orbit * 1.55;
          const r1 = r0 + (i % 10 === 0 ? 22 : 8);
          pts.push(new T.Vector3(Math.cos(a) * r0, 0, Math.sin(a) * r0), new T.Vector3(Math.cos(a) * r1, 0, Math.sin(a) * r1));
        }
        this.orbits.add(new T.LineSegments(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: th.line, transparent: true, opacity: 0.22, depthWrite: false })));
      }
    }
    for (const n of map.nodes) {
      const p = pos.get(n.id);
      const g = new T.Group();
      g.position.set(p[0], p[1], p[2]);
      const size = SIZE[n.kind] * (n.kind === 'star' ? 0.7 + n.value * 0.8 : 1);
      const col = this.color(n.group, n.kind === 'core' ? 0.85 : undefined);
      const parts = [];
      if (n.kind === 'gap') {
        const m = this.accMat(new T.SpriteMaterial({ map: this.tex.dash, transparent: true, depthWrite: false }));
        const sp = new T.Sprite(m);
        sp.scale.setScalar(size * 2.2);
        g.add(sp);
        parts.push(sp);
      } else if (th.clay && n.kind === 'star') {
        // 陶瓷白小球 + 墨线描边（背面放大一圈画成黑色），立体又像手绘
        const ball = new T.SphereGeometry(size * 0.6, 32, 20);
        const mesh = new T.Mesh(ball, new T.MeshStandardMaterial({ color: 0xf7f4ee, roughness: 0.42, metalness: 0, transparent: true }));
        const ink = new T.Mesh(ball, n.missing ? this.accMat(new T.MeshBasicMaterial({ side: T.BackSide, transparent: true })) : new T.MeshBasicMaterial({ color: 0x1b1a18, side: T.BackSide, transparent: true }));
        ink.scale.setScalar(1.16);
        g.add(ink, mesh);
        parts.push(ink, mesh);
      } else if (th.star === 'dot' && n.kind !== 'star') {
        const icon = n.kind === 'member' || (n.kind === 'core' && map.kind === 'life') ? null : n.kind === 'core' ? 'core' : n.group;
        const look = { icon, text: [...n.label][0] };
        const sp = new T.Sprite(new T.SpriteMaterial({ map: badge(T, { ...look, accent: n.missing ? this.accCss() : null }), transparent: true, depthWrite: false }));
        sp.scale.setScalar(size * (n.kind === 'core' ? 1.9 : 2.1));
        g.add(sp);
        parts.push(sp);
        g.userData.badge = { sp, look, missing: Boolean(n.missing), scale: sp.scale.x };
      } else if (th.star === 'glass' && n.kind !== 'star') {
        const mesh = new T.Mesh(
          new T.SphereGeometry(size * 0.55, 48, 32),
          new T.MeshPhysicalMaterial({ color: col, roughness: 0.08, metalness: 0, transmission: 0.55, thickness: 6, iridescence: 1, iridescenceIOR: 1.4, clearcoat: 1, transparent: true }),
        );
        g.add(mesh);
        parts.push(mesh);
      } else {
        const kind = th.star === 'glass' ? 'dot' : th.star === 'spark' && n.kind !== 'star' ? 'spark' : th.star;
        const glow = new T.Sprite(new T.SpriteMaterial({ map: this.tex[kind], color: col, transparent: true, depthWrite: false, blending: add }));
        glow.scale.setScalar(size * (kind === 'dot' ? (th.star === 'glass' ? 2.2 : 1.6) : kind === 'spark' ? (n.kind === 'star' ? 2.6 : 3.6) : 3.2));
        g.add(glow);
        parts.push(glow);
        if (kind !== 'dot' && n.kind !== 'star') {
          const core = new T.Sprite(new T.SpriteMaterial({ map: this.tex.dot, color: 0xffffff, transparent: true, depthWrite: false, blending: add }));
          core.scale.setScalar(size * 0.9);
          g.add(core);
          parts.push(core);
        }
      }
      if ((n.kind === 'domain' || n.kind === 'core' || n.kind === 'member') && th.star !== 'dot') {
        const ring = new T.Mesh(
          new T.TorusGeometry(size * (n.kind === 'core' ? 1.25 : 1.05), n.kind === 'core' ? 0.5 : 0.35, 8, 96),
          new T.MeshBasicMaterial({ color: th.sat === 0 ? 0x1b1b1b : col, transparent: true, opacity: 0.6, depthWrite: false }),
        );
        ring.rotation.set(1.1 + (hash(n.id) % 7) * 0.1, (hash(n.id) % 5) * 0.3, 0);
        g.add(ring);
        parts.push(ring);
        g.userData.ring = ring;
      }
      if (n.missing && th.star !== 'dot') {
        const sp = new T.Sprite(this.accMat(new T.SpriteMaterial({ map: this.tex.dash, transparent: true, depthWrite: false })));
        sp.scale.setScalar(size * 3.2);
        g.add(sp);
        parts.push(sp);
      }
      if (n.fresh) {
        const sp = new T.Sprite(this.accMat(new T.SpriteMaterial({ map: this.tex.ring, transparent: true, depthWrite: false, blending: add })));
        sp.scale.setScalar(size * 2);
        g.add(sp);
        g.userData.pulse = sp;
      }
      const hit = new T.Mesh(new T.SphereGeometry(Math.max(size * 0.9, 7), 8, 6), new T.MeshBasicMaterial({ visible: false }));
      hit.userData.id = n.id;
      g.add(hit);
      this.pickables.push(hit);
      // 标签（DOM）：中心、星域、成员常显；其余选中 / 相关 / 悬停时显示
      const el = document.createElement('div');
      el.className = `sm3-l k-${n.kind}`;
      el.textContent = n.label;
      el.style.setProperty('--c', `#${this.color(n.group, th.sat === 0 ? 0.1 : 0.78).getHexString()}`);
      const lo = new L.CSS2DObject(el);
      lo.position.set(0, -size * (n.kind === 'core' ? 1.6 : 1.3) - 4, 0);
      lo.center.set(0.5, 0);
      g.add(lo);
      this.labelObjs.push(lo);
      this.root.add(g);
      const ph = ((hash(n.id) % 1000) / 1000) * Math.PI * 2;
      const amp = { core: 0, domain: 1.6, member: 2.2, star: 3.2, gap: 3.2 }[n.kind] ?? 2;
      this.nodes.set(n.id, { n, g, parts, el, size, base: g.position.clone(), ph, amp });
    }
    // 连线：从属线一束，相关 / 亲近画弧线虚线
    const tree = [];
    const tcol = [];
    this.curves = [];
    for (const l of map.links) {
      const a = this.nodes.get(l.a);
      const b = this.nodes.get(l.b);
      if (!a || !b) continue;
      if (l.kind === 'tree') {
        tree.push(a.g.position, b.g.position);
        const ca = th.line !== null ? new T.Color(th.line) : this.color(a.n.group);
        const cb = th.line !== null ? new T.Color(th.line) : this.color(b.n.group);
        tcol.push(ca, cb);
      } else {
        const mid = a.g.position.clone().add(b.g.position).multiplyScalar(0.5);
        mid.y += a.g.position.distanceTo(b.g.position) * 0.22;
        const curve = new T.QuadraticBezierCurve3(a.g.position.clone(), mid, b.g.position.clone());
        const geo = new T.BufferGeometry().setFromPoints(curve.getPoints(40));
        const mat = new T.LineDashedMaterial({
          color: th.gold ? 0xf3dca4 : th.sat === 0 ? this.acc.getHex() : th.line ?? this.color(a.n.group, 0.75),
          dashSize: l.kind === 'peer' ? 9 : 4, gapSize: 5, transparent: true,
          opacity: l.kind === 'peer' ? 0.25 + l.w * 0.5 : 0.6, depthWrite: false,
        });
        if (th.sat === 0) this.accMat(mat);
        const line = new T.Line(geo, mat);
        line.computeLineDistances();
        line.userData = { a: l.a, b: l.b, base: mat.opacity, curve };
        this.root.add(line);
        this.curves.push(line);
      }
    }
    const geo = new T.BufferGeometry().setFromPoints(tree);
    const colors = new Float32Array(tcol.length * 3);
    tcol.forEach((c, i) => colors.set([c.r, c.g, c.b], i * 3));
    geo.setAttribute('color', new T.BufferAttribute(colors, 3));
    this.treeLinks = map.links.filter((l) => l.kind === 'tree' && this.nodes.has(l.a) && this.nodes.has(l.b));
    this.tree = th.sketch
      ? this.sketchLines(this.treeLinks, new T.LineBasicMaterial({ color: th.line, transparent: true, opacity: th.lineAlpha, depthWrite: false }))
      : new T.LineSegments(geo, new T.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: th.lineAlpha, depthWrite: false }));
    this.root.add(this.tree);
    if (th.floor) this.buildFloor();
    this.buildFlow();
    this.home(orbit);
    this.select(null);
  }

  // 手绘线：每条连线两笔，每笔分 SEG 段，中间按固定的随机方向轻轻鼓出去（像铅笔描了两遍）
  sketchLines(links, mat) {
    const T = this.T;
    const SEG = 10;
    this.skOff = this.skOff || new WeakMap();
    for (const l of links) {
      if (this.skOff.has(l)) continue;
      let seed = hash(`${l.a}>${l.b}`) || 1;
      const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
      const strokes = [0, 1].map((k) => {
        const dir = new T.Vector3(rnd(), rnd(), rnd()).normalize();
        const dir2 = new T.Vector3(rnd(), rnd(), rnd()).normalize();
        const bow = 0.6 + Math.abs(rnd()) * 0.8;
        const over = k ? [-0.025, 1.03] : [0.01, 1];
        return { dir, dir2, bow, over, ph: rnd() * 3 };
      });
      this.skOff.set(l, strokes);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(new Float32Array(Math.max(links.length, 1) * 2 * SEG * 2 * 3), 3));
    const line = new T.LineSegments(geo, mat);
    line.frustumCulled = false;
    line.userData.sketch = SEG;
    this.writeSketch(line, links);
    return line;
  }

  writeSketch(line, links) {
    const SEG = line.userData.sketch;
    const a = line.geometry.attributes.position;
    const P = (id) => this.nodes.get(id).g.position;
    let v = 0;
    const put = (p, q, st, u) => {
      const len = p.distanceTo(q);
      const amp = Math.min(7, len * 0.018) * st.bow;
      const w = Math.sin(Math.PI * Math.min(Math.max(u, 0), 1));
      const j = Math.sin(u * 9 + st.ph) * 0.35;
      a.setXYZ(
        v,
        p.x + (q.x - p.x) * u + (st.dir.x * w + st.dir2.x * j) * amp,
        p.y + (q.y - p.y) * u + (st.dir.y * w + st.dir2.y * j) * amp,
        p.z + (q.z - p.z) * u + (st.dir.z * w + st.dir2.z * j) * amp,
      );
      v += 1;
    };
    for (const l of links) {
      const p = P(l.a);
      const q = P(l.b);
      for (const st of this.skOff.get(l)) {
        const [u0, u1] = st.over;
        for (let i = 0; i < SEG; i += 1) {
          put(p, q, st, u0 + ((u1 - u0) * i) / SEG);
          put(p, q, st, u0 + ((u1 - u0) * (i + 1)) / SEG);
        }
      }
    }
    a.needsUpdate = true;
  }

  // 地面：每颗星在下方地面上有一团软投影，再拉一根很淡的垂线，一眼看出高低远近
  buildFloor() {
    const T = this.T;
    let low = Infinity;
    for (const o of this.nodes.values()) low = Math.min(low, o.base.y);
    this.floorY = (Number.isFinite(low) ? low : 0) - 110;
    const plane = new T.PlaneGeometry(1, 1);
    for (const o of this.nodes.values()) {
      const m = new T.Mesh(plane, new T.MeshBasicMaterial({ map: this.tex.shadow, transparent: true, depthWrite: false, opacity: 0.5 }));
      m.rotation.x = -Math.PI / 2;
      m.renderOrder = -1;
      this.root.add(m);
      o.shadow = m;
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(new Float32Array(this.nodes.size * 6), 3));
    this.drops = new T.LineSegments(geo, new T.LineBasicMaterial({ color: this.th.line, transparent: true, opacity: 0.1, depthWrite: false }));
    this.drops.frustumCulled = false;
    this.root.add(this.drops);
  }

  floor() {
    if (!this.drops) return;
    const a = this.drops.geometry.attributes.position;
    let i = 0;
    for (const o of this.nodes.values()) {
      const p = o.g.position;
      const h = Math.max(p.y - this.floorY, 0);
      const vis = o.g.visible;
      const lit = !this.sel || o.el.classList.contains('near') || o.n.id === this.sel;
      const k = o.n.kind === 'star' || o.n.kind === 'gap' ? 2.4 : 3.4;
      o.shadow.visible = vis;
      o.shadow.position.set(p.x, this.floorY, p.z);
      o.shadow.scale.setScalar(o.size * k * (1 + h / 260));
      o.shadow.material.opacity = Math.max(0.16, 0.85 - h / 420) * (lit ? 1 : 0.25);
      const top = vis && o.n.kind !== 'star' ? p.y - o.size : this.floorY;
      a.setXYZ(i * 2, p.x, top, p.z);
      a.setXYZ(i * 2 + 1, p.x, this.floorY, p.z);
      i += 1;
    }
    a.needsUpdate = true;
  }

  accCss() {
    return `#${this.acc.getHexString()}`;
  }

  accMat(m) {
    m.color = this.acc.clone();
    this.accMats.push(m);
    return m;
  }

  // 换主题色：所有用主题色的线、光圈、缺口、流动光点和「有缺口」的徽章一起换，不重建场景
  setAccent(hex) {
    this.acc.set(hex);
    for (const m of this.accMats) m.color.copy(this.acc);
    for (const o of this.nodes ? this.nodes.values() : []) {
      const b = o.g.userData.badge;
      if (b && b.missing) {
        b.sp.material.map.dispose();
        b.sp.material.map = badge(this.T, { ...b.look, accent: this.accCss() });
        b.sp.material.needsUpdate = true;
      }
    }
  }

  // 连线上流动的光点：平时散在所有从属线上，选中一颗星时都跑到和它相连的线上
  buildFlow() {
    const T = this.T;
    if (this.flow) this.root.remove(this.flow);
    const n = 260;
    const geo = new T.BufferGeometry();
    geo.setAttribute('position', new T.BufferAttribute(new Float32Array(n * 3), 3));
    const mat = this.accMat(
      new T.PointsMaterial({
        size: this.th.star === 'dot' ? 5 : 5,
        map: this.tex[this.th.star === 'dot' ? 'dot' : 'glow'],
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
        blending: this.th.blend === 'add' ? T.AdditiveBlending : T.NormalBlending,
      }),
    );
    this.flow = new T.Points(geo, mat);
    this.flow.frustumCulled = false;
    this.root.add(this.flow);
    this.sparks = Array.from({ length: n }, (_, i) => ({ k: i, t: (i * 0.618) % 1, v: 0.0016 + ((i * 37) % 10) * 0.00028 }));
    this.aimFlow(null);
  }

  aimFlow(id) {
    if (!this.sparks || !this.map) return;
    const all = this.map.links.filter((l) => this.nodes.has(l.a) && this.nodes.has(l.b));
    let links = id ? all.filter((l) => l.a === id || l.b === id) : all.filter((l) => l.kind === 'tree');
    if (!links.length) links = all;
    this.flowLinks = links;
    const many = id ? 1 : 0.55; // 平时只放一半光点，安静一些
    this.sparks.forEach((s, i) => {
      s.on = i < this.sparks.length * many;
      s.link = links[i % Math.max(links.length, 1)];
      s.back = Boolean(id) && s.link && s.link.b === id; // 选中时光点都流向选中的星
    });
  }

  home(orbit) {
    this.homeDist = orbit ? orbit * 3.2 : this.homeDist;
    // 竖屏（手机）横向视角窄：退远一点，整张图都放得下
    const d = this.homeDist * Math.max(1, 0.95 / (this.camera.aspect || 1));
    if (this.th.fog && this.scene.fog) {
      // 雾跟着镜头远近走：手机上镜头退得远，雾也推远，不会整张图都发白
      this.scene.fog.near = d * 0.55;
      this.scene.fog.far = d * 2.1;
    }
    this.flyTo([0, 0, 0], d);
  }

  flyTo(target, dist) {
    const T = this.T;
    const t = new T.Vector3(...target);
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    if (target[0] || target[2]) {
      // 从外往里看：镜头放在星的外侧，中心在它身后
      const out = new T.Vector3(target[0], 0, target[2]).normalize();
      dir.copy(out.multiplyScalar(0.85).add(new T.Vector3(0, 0.45, 0))).normalize();
    }
    this.fly = { t, cam: t.clone().add(dir.multiplyScalar(dist)) };
  }

  select(id, near = new Set()) {
    this.sel = id;
    this.haloOn(id);
    const on = (nid) => !id || near.has(nid);
    for (const [nid, o] of this.nodes) {
      const lit = on(nid);
      for (const p of o.parts) {
        p.material.opacity = (p === o.g.userData.ring ? 0.6 : 1) * (lit ? 1 : 0.12);
      }
      o.el.classList.toggle('dim', !lit);
      o.el.classList.toggle('near', Boolean(id) && lit && nid !== id);
      o.el.classList.toggle('sel', nid === id);
    }
    if (this.tree) {
      this.tree.material.opacity = id ? this.th.lineAlpha * 0.25 : this.th.lineAlpha;
      if (this.hot) {
        this.accMats = this.accMats.filter((m) => m !== this.hot.material);
        this.root.remove(this.hot);
        this.hot = null;
      }
      if (id) {
        const T = this.T;
        const pts = [];
        this.hotLinks = this.treeLinks.filter((l) => l.a === id || l.b === id);
        for (const l of this.hotLinks) pts.push(this.nodes.get(l.a).g.position, this.nodes.get(l.b).g.position);
        if (pts.length) {
          const mat = this.accMat(new T.LineBasicMaterial({ transparent: true, opacity: 0.9 }));
          this.hot = this.th.sketch ? this.sketchLines(this.hotLinks, mat) : new T.LineSegments(new T.BufferGeometry().setFromPoints(pts), mat);
          this.root.add(this.hot);
        }
      }
    }
    for (const c of this.curves || []) {
      const lit = !id || c.userData.a === id || c.userData.b === id;
      c.material.opacity = lit ? (id ? 0.95 : c.userData.base) : 0.05;
    }
    // 选中后也不停：绕着选中的星慢慢转
    this.controls.autoRotateSpeed = (this.calm ? 0.12 : 0.35) * (id ? 0.45 : 1);
    this.aimFlow(id);
    if (id && this.nodes.has(id)) {
      const o = this.nodes.get(id);
      const p = o.g.position;
      const d = o.n.kind === 'core' ? this.homeDist : o.n.kind === 'star' || o.n.kind === 'gap' ? 330 : 420;
      if (o.n.kind === 'core') this.home();
      else this.flyTo([p.x, p.y, p.z], d);
    } else if (this.map) {
      this.home();
    }
  }

  // 选中的星外面一圈主题色光环，慢慢呼吸、外面再荡开一圈涟漪
  haloOn(id) {
    const T = this.T;
    if (!this.halo) {
      this.halo = new T.Group();
      for (const k of ['ring', 'ring']) {
        const sp = new T.Sprite(this.accMat(new T.SpriteMaterial({ map: this.tex[k], transparent: true, depthWrite: false })));
        this.halo.add(sp);
      }
    }
    if (this.halo.parent) this.halo.parent.remove(this.halo);
    const o = id && this.nodes && this.nodes.get(id);
    if (!o) return;
    this.halo.userData.size = o.size * (o.n.kind === 'core' ? 1.9 : o.g.userData.badge ? 2.1 : 1.6);
    this.halo.userData.t0 = performance.now();
    o.g.add(this.halo);
  }

  setVisible(fn) {
    for (const o of this.nodes ? this.nodes.values() : []) o.g.visible = fn(o.n);
  }

  bindPointer() {
    const el = this.renderer.domElement;
    let down = null;
    el.addEventListener('pointerdown', (e) => {
      down = [e.clientX, e.clientY];
      this.fly = null;
    });
    el.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
      down = null;
      this.onPick(this.pick(e), e);
    });
    el.addEventListener('pointermove', (e) => {
      if (e.buttons) return;
      const id = this.pick(e);
      el.style.cursor = id ? 'pointer' : 'grab';
      if (id !== this.hoverId) {
        if (this.hoverId && this.nodes.get(this.hoverId)) this.nodes.get(this.hoverId).el.classList.remove('hover');
        this.hoverId = id;
        if (id) this.nodes.get(id).el.classList.add('hover');
        this.onHover(id);
      }
    });
    el.addEventListener('dblclick', (e) => this.onPick(this.pick(e), e, true));
  }

  pick(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.ptr, this.camera);
    const hits = this.ray.intersectObjects(this.pickables.filter((h) => h.parent.visible), false);
    return hits.length ? hits[0].object.userData.id : null;
  }

  resize() {
    const w = this.host.clientWidth;
    const h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.labels.setSize(w, h);
    if (this.composer) this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  frame() {
    const now = performance.now();
    if (this.fly) {
      this.controls.target.lerp(this.fly.t, 0.08);
      this.camera.position.lerp(this.fly.cam, 0.08);
      if (this.camera.position.distanceTo(this.fly.cam) < 1) this.fly = null;
    }
    const t = now / 1000;
    for (const o of this.nodes ? this.nodes.values() : []) {
      // 漂浮：每颗星绕自己的位置轻轻晃（相位按 id 定，彼此不同步）
      o.g.position.set(
        o.base.x + Math.sin(t * 0.55 + o.ph) * o.amp * 0.7,
        o.base.y + Math.sin(t * 0.8 + o.ph * 1.3) * o.amp,
        o.base.z + Math.cos(t * 0.5 + o.ph) * o.amp * 0.7,
      );
      const b = o.g.userData.badge;
      if (b) b.sp.scale.setScalar(b.scale * (1 + 0.04 * Math.sin(t * 1.6 + o.ph)));
      const p = o.g.userData.pulse;
      if (p) {
        const k = ((now / 1600 + (hash(o.n.id) % 100) / 100) % 1);
        p.scale.setScalar(o.size * (1.6 + k * 2.2));
        p.material.opacity = (1 - k) * 0.8 * (this.sel && !o.el.classList.contains('near') && o.n.id !== this.sel ? 0.1 : 1);
      }
      if (o.g.userData.ring) o.g.userData.ring.rotation.z += 0.004;
    }
    if (this.dust) this.dust.rotation.y += 0.00012;
    if (this.halo && this.halo.parent) {
      const s = this.halo.userData.size;
      const k = ((now - this.halo.userData.t0) / 1800) % 1;
      const [a, b] = this.halo.children;
      a.scale.setScalar(s * (1.32 + 0.04 * Math.sin(t * 2.4)));
      a.material.opacity = 0.95;
      b.scale.setScalar(s * (1.3 + k * 1.1));
      b.material.opacity = (1 - k) * 0.55;
    }
    this.follow();
    this.controls.update();
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }

  // 星在漂浮，连线、高亮线、弧线和光点跟着走
  follow() {
    if (!this.nodes) return;
    const P = (id) => this.nodes.get(id).g.position;
    const seg = (line, links) => {
      if (!line || !links) return;
      const a = line.geometry.attributes.position;
      links.forEach((l, i) => {
        const p = P(l.a);
        const q = P(l.b);
        a.setXYZ(i * 2, p.x, p.y, p.z);
        a.setXYZ(i * 2 + 1, q.x, q.y, q.z);
      });
      a.needsUpdate = true;
    };
    if (this.th.sketch) {
      if (this.tree) this.writeSketch(this.tree, this.treeLinks);
      if (this.hot) this.writeSketch(this.hot, this.hotLinks);
    } else {
      seg(this.tree, this.treeLinks);
      seg(this.hot, this.hotLinks);
    }
    this.floor();
    this.tick = (this.tick || 0) + 1;
    if (this.tick % 2 === 0) {
      for (const c of this.curves || []) {
        const { a, b, curve } = c.userData;
        curve.v0.copy(P(a));
        curve.v2.copy(P(b));
        curve.v1.copy(curve.v0).add(curve.v2).multiplyScalar(0.5);
        curve.v1.y += curve.v0.distanceTo(curve.v2) * 0.22;
        c.geometry.setFromPoints(curve.getPoints(40));
        c.computeLineDistances();
      }
    }
    if (this.flow && this.sparks) {
      const arr = this.flow.geometry.attributes.position;
      const T = this.T;
      const m = this.mid || (this.mid = new T.Vector3());
      for (const s of this.sparks) {
        const l = s.link;
        if (!s.on || !l || !this.nodes.get(l.a).g.visible || !this.nodes.get(l.b).g.visible) {
          arr.setXYZ(s.k, 0, -99999, 0);
          continue;
        }
        s.t = (s.t + s.v * (this.sel ? 1.8 : 1)) % 1;
        const u = s.back ? 1 - s.t : s.t;
        const p = P(l.a);
        const q = P(l.b);
        m.copy(p).add(q).multiplyScalar(0.5);
        if (l.kind !== 'tree') m.y += p.distanceTo(q) * 0.22;
        const w0 = (1 - u) * (1 - u);
        const w1 = 2 * (1 - u) * u;
        const w2 = u * u;
        arr.setXYZ(s.k, w0 * p.x + w1 * m.x + w2 * q.x, w0 * p.y + w1 * m.y + w2 * q.y, w0 * p.z + w1 * m.z + w2 * q.z);
      }
      arr.needsUpdate = true;
    }
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labels.domElement.remove();
    this.host.classList.remove(`sm3-host-${this.themeName}`);
  }
}
