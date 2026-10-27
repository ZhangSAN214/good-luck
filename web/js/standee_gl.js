// 大立绘的网格形变渲染（WebGL）：一张立绘 + 瞳孔层 + 三张权重图，在显卡上逐像素算形变。
// 权重图由 scripts/standee_rig.py 生成（头、脸部纵深、胸口、拿书手臂、左右长发、前发、裙摆、饰品、
// 沿发丝的位置、眼眶开口）。每一帧只改几个 uniform，CPU 几乎不干活，所以能稳定跑满屏幕刷新率。
// 做法是「反向映射」：屏幕上每个像素问「我该显示原图哪一点」，形变量小而平滑，迭代两次就够准。

const VS = `#version 300 es
in vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }`;

const FS = `#version 300 es
precision highp float;
uniform sampler2D uBase, uIris, uW1, uW2, uW3;
uniform vec2 uSize, uCanvas, uPad;
uniform float uFlip, uFadeAt, uFadePx, uTime, uIrisOn;
uniform vec2 uNeck, uFace, uElbow, uWaist, uFeet, uSkirt;
uniform float uRoll, uSway, uArm, uBreath, uWind;
uniform vec2 uHeadT, uFaceT, uArmT, uGaze, uHop;
uniform vec2 uHairL, uHairR, uFront, uSkirtD, uAcc;
out vec4 outColor;

vec2 rot(vec2 p, vec2 c, float a) {
  vec2 d = p - c;
  float s = sin(a), k = cos(a);
  return c + vec2(k * d.x - s * d.y, s * d.x + k * d.y) - p;
}

vec2 disp(vec2 p) {
  vec2 uv = p / uSize;
  vec4 w1 = texture(uW1, uv);
  vec4 w2 = texture(uW2, uv);
  vec4 w3 = texture(uW3, uv);
  float s = w3.g;
  float f = pow(s, 1.35);
  float lift = clamp((uWaist.y - p.y) / (uWaist.y - uNeck.y), 0.0, 1.25);
  vec2 d = rot(p, uFeet, uSway) + uHop * clamp((uFeet.y - p.y) / uFeet.y, 0.0, 1.0);
  // 呼吸：胸口以上往上抬、略微变宽；头跟着胸口走
  d += w1.b * vec2((p.x - uFace.x) * uBreath * 0.0016, -uBreath * lift);
  d += w1.r * (rot(p, uNeck, uRoll) + uHeadT + vec2(0.0, -uBreath * 1.1)) + w1.g * uFaceT;
  d += w1.a * (rot(p, uElbow, uArm) + uArmT);
  vec2 wave = vec2(sin(uTime * 1.25 - s * 3.4), 0.35 * sin(uTime * 1.7 - s * 2.6)) * uWind;
  d += (w2.r * uHairL + w2.g * uHairR + w2.b * uFront * 0.6 + w3.r * uAcc) * f;
  d += (w2.r + w2.g + w2.b * 0.4 + w3.r * 0.7) * f * wave;
  float sy = clamp((p.y - uSkirt.x) / (uSkirt.y - uSkirt.x), 0.0, 1.0);
  float sk = pow(sy, 1.6);
  d += w2.a * (uSkirtD * sk + vec2(sin(uTime * 0.85 - sy * 2.4 + p.x * 0.004) * uWind * 0.9 * sk, 0.0));
  return d;
}

void main() {
  vec2 c = vec2(gl_FragCoord.x, uCanvas.y - gl_FragCoord.y) / uCanvas;
  if (uFlip > 0.5) c.x = 1.0 - c.x;
  vec2 p = (c * (1.0 + 2.0 * uPad) - uPad) * uSize;
  vec2 q = p - disp(p);
  q = p - disp(q);
  if (q.x < 0.0 || q.y < 0.0 || q.x > uSize.x || q.y > uSize.y) { outColor = vec4(0.0); return; }
  vec4 col = texture(uBase, q / uSize);
  float open = texture(uW3, q / uSize).b * uIrisOn;
  if (open > 0.0) {
    vec4 iris = texture(uIris, (q - uGaze) / uSize) * open;
    col = iris + col * (1.0 - iris.a);
  }
  // 首页底边：渐隐进去，不留直边
  float y = gl_FragCoord.y;
  float edge = (1.0 - uFadeAt) * uCanvas.y;
  if (uFadeAt < 1.0) col *= clamp((y - edge) / uFadePx, 0.0, 1.0);
  outColor = col;
}`;

function shader(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || 'shader');
  return s;
}

/** 有没有 WebGL2（没有就用原来的切条画法）。 */
export function glSupported() {
  try {
    return Boolean(document.createElement('canvas').getContext('webgl2'));
  } catch {
    return false;
  }
}

export class RigRenderer {
  /** rig：/api/standee 里这一套的 rig；images：{iris, w1, w2, w3} 已解码的图片。 */
  constructor(rig, images) {
    this.rig = rig;
    this.canvas = document.createElement('canvas');
    const gl = this.canvas.getContext('webgl2', { premultipliedAlpha: true, alpha: true, antialias: false });
    if (!gl) throw new Error('no webgl2');
    this.gl = gl;
    const prog = gl.createProgram();
    gl.attachShader(prog, shader(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, shader(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) || 'link');
    gl.useProgram(prog);
    this.prog = prog;
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.u = {};
    const n = gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) {
      const name = gl.getActiveUniform(prog, i).name;
      this.u[name] = gl.getUniformLocation(prog, name);
    }
    this.tex = {};
    ['uBase', 'uIris', 'uW1', 'uW2', 'uW3'].forEach((name, unit) => {
      const t = gl.createTexture();
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.uniform1i(this.u[name], unit);
      this.tex[name] = { t, unit };
    });
    // 权重图是数据不是颜色：不预乘、不做色彩转换
    this.upload('uW1', images.w1, false);
    this.upload('uW2', images.w2, false);
    this.upload('uW3', images.w3, false);
    this.upload('uIris', images.iris, true);
    const P = rig.pivots;
    const v2 = (k, val) => gl.uniform2f(this.u[k], val[0], val[1]);
    v2('uSize', rig.size);
    v2('uNeck', P.neck);
    v2('uFace', P.face);
    v2('uElbow', P.elbow);
    v2('uWaist', P.waist);
    v2('uFeet', P.feet);
    v2('uSkirt', rig.skirt);
    gl.clearColor(0, 0, 0, 0);
  }

  upload(name, src, premult) {
    const gl = this.gl;
    const { t, unit } = this.tex[name];
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, premult);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, premult ? gl.BROWSER_DEFAULT_WEBGL : gl.NONE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
  }

  /**
   * 立绘本体（抠好底、叠好表情差分的画布）变了才重新上传。rect（像素 [x, y, w, h]）给出时只传这一块
   * （眨眼、说话只改脸部贴片那一小块，不用每次传整张大图，避免掉帧）。
   */
  setBase(canvas, rect = null) {
    if (!rect || !this.hasBase) {
      this.upload('uBase', canvas, true);
      this.hasBase = true;
      return;
    }
    const [x, y, w, h] = rect;
    if (!this.patch) this.patch = document.createElement('canvas');
    const p = this.patch;
    if (p.width !== w || p.height !== h) {
      p.width = w;
      p.height = h;
    }
    const ctx = p.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(canvas, x, y, w, h, 0, 0, w, h);
    const gl = this.gl;
    const { t, unit } = this.tex.uBase;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.BROWSER_DEFAULT_WEBGL);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, x, y, gl.RGBA, gl.UNSIGNED_BYTE, p);
  }

  /** 画一帧到自己的画布上（尺寸 w×h），返回画布，调用方再贴到页面上的 2D 画布里。 */
  render(w, h, pose, view) {
    const gl = this.gl;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const u = this.u;
    gl.uniform2f(u.uCanvas, w, h);
    gl.uniform2f(u.uPad, view.pad.x, view.pad.y);
    gl.uniform1f(u.uFlip, view.flip ? 1 : 0);
    gl.uniform1f(u.uFadeAt, view.fadeAt);
    gl.uniform1f(u.uFadePx, view.fadePx);
    gl.uniform1f(u.uTime, pose.time);
    gl.uniform1f(u.uIrisOn, pose.irisOn);
    for (const k of ['roll', 'sway', 'arm', 'breath', 'wind']) gl.uniform1f(u[`u${k[0].toUpperCase()}${k.slice(1)}`], pose[k]);
    for (const k of ['headT', 'faceT', 'armT', 'gaze', 'hop', 'hairL', 'hairR', 'front', 'skirtD', 'acc']) {
      gl.uniform2f(u[`u${k[0].toUpperCase()}${k.slice(1)}`], pose[k][0], pose[k][1]);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    return this.canvas;
  }
}
