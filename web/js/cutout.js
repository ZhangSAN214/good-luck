// 去掉小人素材四周的浅色底：从图片边缘向内漫水填充，和角落颜色相近的像素变透明。
// 素材本身已经透明（角落 alpha 为 0）时原样返回；失败时也返回原地址。结果按地址缓存。

const cache = new Map();

export function cutout(url, tolerance = 22) {
  if (!url) return Promise.resolve(url);
  if (!cache.has(url)) cache.set(url, run(url, tolerance).catch(() => url));
  return cache.get(url);
}

async function run(url, tol) {
  const img = new Image();
  img.decoding = 'async';
  img.src = url;
  await img.decode();
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  if (!w || !h) return url;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(0, 0, w, h);
  const px = data.data;
  if (px[3] < 16) return url; // 已经是透明底
  const [r0, g0, b0] = [px[0], px[1], px[2]];
  const near = (i) => px[i + 3] > 0 && Math.abs(px[i] - r0) + Math.abs(px[i + 1] - g0) + Math.abs(px[i + 2] - b0) <= tol * 3;
  const seen = new Uint8Array(w * h);
  const stack = [];
  for (let x = 0; x < w; x++) stack.push(x, (h - 1) * w + x);
  for (let y = 0; y < h; y++) stack.push(y * w, y * w + w - 1);
  while (stack.length) {
    const p = stack.pop();
    if (seen[p]) continue;
    seen[p] = 1;
    if (!near(p * 4)) continue;
    px[p * 4 + 3] = 0;
    const x = p % w;
    if (x > 0) stack.push(p - 1);
    if (x < w - 1) stack.push(p + 1);
    if (p >= w) stack.push(p - w);
    if (p < w * (h - 1)) stack.push(p + w);
  }
  ctx.putImageData(data, 0, 0);
  const blob = await new Promise((ok) => canvas.toBlob(ok, 'image/png'));
  return blob ? URL.createObjectURL(blob) : url;
}
