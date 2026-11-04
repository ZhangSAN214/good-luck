# 编辑器底座接口（`/kit/editor/editor.js`）

底座已经做好：画布（缩放、平移、棋盘格底）、图层面板（显示 / 隐藏、上移下移、不透明度、改名、复制、删除）、
撤销重做（至少 20 步）、工具栏（移动 V、矩形选区 M、画笔 B、橡皮 E）、属性面板、打开图片（按钮 / 拖进 / 粘贴）、
导出 PNG（原尺寸）、快捷键、出错提示。**这些都不要重写**，你只在它上面加功能。

你的功能模块 `windows/<id>.js`：

```js
export default async function mount(el, ctx) {
  const { editor, kit } = ctx;          // el 是右侧给你的面板内容区
  // 在面板里放控件，在顶栏加按钮，在工具栏加工具
}
export async function test(t) {         // 可选：这个功能自带的测试，验收时自动跑
  // 开始前画布上已有一张 160×120 的测试图（中间一块白，四周渐变）
}
```

## 读状态

- `editor.doc` → `{ width, height }`（还没图片时是 0）
- `editor.layers` → `[{ id, name, visible, opacity, x, y, width, height }]`（下面的在前）
- `editor.active` → `{ id, name } | null`；`editor.selection` → `{ x, y, w, h } | null`（文档像素坐标）
- `editor.history` → `{ undo, redo, limit, labels }`
- `editor.layerCanvas(id?)` → 图层的 canvas（只读着用；要改用 `paint`）
- `editor.on('change' | 'select' | 'selection' | 'tool' | 'error', fn)` → 取消函数

## 改画面（全部可撤销，一次调用 = 一步）

- `editor.applyFilter(名字, css滤镜)`：对当前图层用 canvas 滤镜，如 `'brightness(120%) contrast(110%)'`、`'saturate(0)'`、`'blur(2px)'`
- `editor.paint(名字, (ctx, canvas, layer, before) => { … })`：在当前图层的一份拷贝上画（可 `getImageData` / `putImageData` 逐像素处理），画完自动换上去
- `editor.crop(rect?)`：整张画布裁成 rect（默认用当前选区），所有图层一起裁
- `editor.addLayer({ name, canvas?, x?, y?, opacity? })`、`removeLayer(id?)`、`duplicateLayer(id?)`、`moveLayer(id, ±1)`
- `editor.setLayer(id, { visible?, opacity?, name?, x?, y? })`
- `editor.setSelection(rect | null)`、`editor.select(id)`、`editor.setTool(id)`
- 自己的操作要撤销：`editor.edit(名字, apply, revert, { bytes? })`

拖滑块预览时别每动一下记一步：`input` 时只更新面板上的数字，`change`（松手）时再调一次 `applyFilter`；
或者用 CSS 在自己面板里显示预览值。

## 加界面

- 外壳已经用 `editor.addPanel` 给了你一块面板（就是 `el`）；真要第二块再调 `editor.addPanel({ id, title, icon })` → 面板内容区
- `editor.addAction({ id, label, icon?, key?, run(editor) })`：顶栏按钮（如「一键去背景」）；`key` 如 `'mod+shift+b'`；run 抛错会自动提示
- `editor.addTool({ id, label, icon, key?, down(p, ev), move(p, ev), up(p, ev), options?(el) })`：工具栏工具，`p` 是文档坐标
- `editor.shortcut('mod+k', run, '说明')`
- `editor.notify(文字, 'good' | 'bad')`：提示（'bad' 会显示在顶部的出错条里）
- `editor.busy(文字, async () => { … })`：耗时处理时显示「正在处理」
- 图标名用零件库 `icons.js` 里有的（`sliders-horizontal`、`crop`、`wand-sparkles`、`eraser`、`sun`、`palette`……）

## 导入导出

- `editor.importFile(file | blob)` → 图层或 null（失败时已经提示过）；`editor.open(url)`
- `editor.exportCanvas()` → 合成后的原尺寸 canvas；`editor.exportBlob()` → PNG；`editor.download(文件名)`

## 规矩

- 没有图片时你的按钮要能点：提示「先打开一张图片」，不要抛未处理的错。
- 只改自己的面板和通过接口改画面；不要改 `/kit/` 里的文件，不要替换底座的 DOM。
- 4000 像素宽的大图也要能用：逐像素处理时一次处理完一行再让出一帧，或用 `applyFilter`；超过几百毫秒用 `busy`。
- 颜色只用 `var(--k-*)`；按钮要有文字或 `aria-label`。
- `test(t)`：`t.editor`、`t.assert(条件, 说明)`、`t.pixel(x, y, 图层id?)` → `[r, g, b, a]`、`t.sample(宽, 高)` → PNG。
  测一下你的功能真的改了画面、撤销能回到原样。
