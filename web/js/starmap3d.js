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
    label: '极简线稿', bg: 0xf3f1ec, bloom: null, sat: 0, light: 0.12, accent: 0xff5a3c,
    star: 'dot', blend: 'normal', line: 0x1b1b1b, lineAlpha: 0.28, dust: 260, dustColor: 0x9a968e, nebula: false, ring: true,
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
function badge(T, { icon, text, accent = false }) {
  const s = 256;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  const m = s / 2;
  g.fillStyle = '#f7f5f0';
  g.strokeStyle = accent ? '#ff5a3c' : '#141414';
  g.lineWidth = 5;
  g.beginPath();
  g.arc(m, m, m - 8, 0, Math.PI * 2);
  g.fill();
  g.stroke();
  g.fillStyle = accent ? '#ff5a3c' : '#141414';
  if (icon && ICONS[icon]) {
    g.save();
    const k = (s * 0.5) / 960;
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
  constructor(host, { theme = 'neon', onPick, onHover } = {}) {
    this.host = host;
    this.themeName = THEMES[theme] ? theme : 'neon';
    this.th = THEMES[this.themeName];
    this.onPick = onPick || (() => {});
    this.onHover = onHover || (() => {});
    const L = lib;
    const T = L.THREE;
    this.T = T;
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
    if (this.th.bg !== null && this.themeName !== 'mono') this.scene.fog = new T.FogExp2(this.th.bg, 0.00055);
    this.camera = new T.PerspectiveCamera(48, 1, 1, 6000);
    this.camera.position.set(0, 320, 900);
    this.controls = new L.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.autoRotate = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.controls.autoRotateSpeed = 0.35;
    this.controls.minDistance = 140;
    this.controls.maxDistance = 2400;
    this.tex = {};
    for (const k of ['glow', 'spark', 'dot', 'ring', 'dash', 'soft']) this.tex[k] = texture(T, k);
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
    this.root = new T.Group();
    this.scene.add(this.root);
    this.nodes = new Map();
    this.labelObjs = [];
    this.pickables = [];
    const add = th.blend === 'add' ? T.AdditiveBlending : T.NormalBlending;
    if (this.orbits) {
      this.orbits.clear();
      for (const [r, op] of [[orbit, 0.5], [orbit * 0.5, 0.25], [orbit * 1.55, 0.18]]) {
        const ring = new T.Mesh(
          new T.RingGeometry(r - 0.6, r + 0.6, 256),
          new T.MeshBasicMaterial({ color: th.line ?? 0x8fa2ff, transparent: true, opacity: op * (th.sat === 0 ? 0.6 : 0.5), side: T.DoubleSide, depthWrite: false }),
        );
        ring.rotation.x = -Math.PI / 2;
        this.orbits.add(ring);
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
        const m = new T.SpriteMaterial({ map: this.tex.dash, color: th.accent ?? 0xff8a80, transparent: true, depthWrite: false });
        const sp = new T.Sprite(m);
        sp.scale.setScalar(size * 2.2);
        g.add(sp);
        parts.push(sp);
      } else if (th.star === 'dot' && n.kind !== 'star') {
        const icon = n.kind === 'member' || (n.kind === 'core' && map.kind === 'life') ? null : n.kind === 'core' ? 'core' : n.group;
        const sp = new T.Sprite(new T.SpriteMaterial({ map: badge(T, { icon, text: [...n.label][0], accent: Boolean(n.missing) }), transparent: true, depthWrite: false }));
        sp.scale.setScalar(size * (n.kind === 'core' ? 1.9 : 2.1));
        g.add(sp);
        parts.push(sp);
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
        const sp = new T.Sprite(new T.SpriteMaterial({ map: this.tex.dash, color: th.accent ?? 0xff8a80, transparent: true, depthWrite: false }));
        sp.scale.setScalar(size * 3.2);
        g.add(sp);
        parts.push(sp);
      }
      if (n.fresh) {
        const sp = new T.Sprite(new T.SpriteMaterial({ map: this.tex.ring, color: th.accent ?? col, transparent: true, depthWrite: false, blending: add }));
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
      this.nodes.set(n.id, { n, g, parts, el, size });
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
          color: th.gold ? 0xf3dca4 : th.sat === 0 ? th.accent : th.line ?? this.color(a.n.group, 0.75),
          dashSize: l.kind === 'peer' ? 9 : 4, gapSize: 5, transparent: true,
          opacity: l.kind === 'peer' ? 0.25 + l.w * 0.5 : 0.6, depthWrite: false,
        });
        const line = new T.Line(geo, mat);
        line.computeLineDistances();
        line.userData = { a: l.a, b: l.b, base: mat.opacity };
        this.root.add(line);
        this.curves.push(line);
      }
    }
    const geo = new T.BufferGeometry().setFromPoints(tree);
    const colors = new Float32Array(tcol.length * 3);
    tcol.forEach((c, i) => colors.set([c.r, c.g, c.b], i * 3));
    geo.setAttribute('color', new T.BufferAttribute(colors, 3));
    this.treeLinks = map.links.filter((l) => l.kind === 'tree' && this.nodes.has(l.a) && this.nodes.has(l.b));
    this.tree = new T.LineSegments(geo, new T.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: th.lineAlpha, depthWrite: false }));
    this.root.add(this.tree);
    this.home(orbit);
    this.select(null);
  }

  home(orbit) {
    this.homeDist = orbit ? orbit * 3.2 : this.homeDist;
    // 竖屏（手机）横向视角窄：退远一点，整张图都放得下
    this.flyTo([0, 0, 0], this.homeDist * Math.max(1, 0.95 / (this.camera.aspect || 1)));
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
        this.root.remove(this.hot);
        this.hot = null;
      }
      if (id) {
        const T = this.T;
        const pts = [];
        for (const l of this.treeLinks) {
          if (l.a === id || l.b === id) pts.push(this.nodes.get(l.a).g.position, this.nodes.get(l.b).g.position);
        }
        if (pts.length) {
          this.hot = new T.LineSegments(
            new T.BufferGeometry().setFromPoints(pts),
            new T.LineBasicMaterial({ color: this.th.sat === 0 ? this.th.accent : this.th.gold ? 0xf3dca4 : 0xffffff, transparent: true, opacity: 0.9 }),
          );
          this.root.add(this.hot);
        }
      }
    }
    for (const c of this.curves || []) {
      const lit = !id || c.userData.a === id || c.userData.b === id;
      c.material.opacity = lit ? (id ? 0.95 : c.userData.base) : 0.05;
    }
    this.controls.autoRotate = !id && !matchMedia('(prefers-reduced-motion: reduce)').matches;
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
    for (const o of this.nodes ? this.nodes.values() : []) {
      const p = o.g.userData.pulse;
      if (p) {
        const k = ((now / 1600 + (hash(o.n.id) % 100) / 100) % 1);
        p.scale.setScalar(o.size * (1.6 + k * 2.2));
        p.material.opacity = (1 - k) * 0.8 * (this.sel && !o.el.classList.contains('near') && o.n.id !== this.sel ? 0.1 : 1);
      }
      if (o.g.userData.ring) o.g.userData.ring.rotation.z += 0.004;
    }
    if (this.dust) this.dust.rotation.y += 0.00012;
    this.controls.update();
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
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
