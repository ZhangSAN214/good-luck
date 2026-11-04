# 第三方前端库（本地加载，不走外网）

聊天里「办公室做好了」的结果卡片用它们显示图表和公式（docs/CHAT.md G14）。文件取自 MIT 授权的
dsh-raw-html（https://github.com/plolpl789/dsh-raw-html ，Copyright (c) 2026 plolpl789）随带的
vendor 目录，未改动。只按需加载：卡片里有 Mermaid 图或公式时才下载。

## KaTeX 0.16.9 — `katex/`

MIT License. Copyright (c) 2013-2020 Khan Academy and other contributors.
https://github.com/KaTeX/KaTeX

## Mermaid 11.9.0 — `mermaid/`

MIT License. Copyright (c) 2014 - 2022 Knut Sveidqvist.
https://github.com/mermaid-js/mermaid

## three.js 0.187.0 — `three/three-starmap.js`

星图（docs/STARMAP.md）的立体画法用它。取自 npm 包 three@0.187.0，把核心库和用到的几个示例模块
（OrbitControls、EffectComposer、RenderPass、UnrealBloomPass、OutputPass、CSS2DRenderer、RoomEnvironment）
用 esbuild 打成一个文件，未改源码。只在打开星图时下载。

MIT License. Copyright © 2010-2026 three.js authors. https://github.com/mrdoob/three.js

## Geist 字体 — `geist/`

星图的西文和数字字体。Vercel 出品，取自 npm 包 geist@1.9.0 的可变字体（Geist / Geist Mono），未改动。
SIL Open Font License 1.1，全文见 `geist/OFL.txt`。https://github.com/vercel/geist-font

## Material Symbols — `web/js/starmap_icons.js`

星图星域的线稿图标。Google 出品，取自 npm 包 @material-symbols/svg-300@0.48.3（Outlined），只摘了用到的
19 个图标的路径，未改形状。Apache License 2.0。https://github.com/google/material-design-icons

---

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
