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

## PixiJS 6.5.10 — `pixi/`

MIT License. Copyright (c) 2013-2017 Mathew Groves, Chad Engler. https://github.com/pixijs/pixijs
（Live2D 样机播放页 web/live2d/ 用；取自 npm 包 pixi.js@6.5.10 的 dist/browser/pixi.min.js，未改动，
许可原文在 `pixi/LICENSE`。）

## pixi-live2d-display 0.4.0 — `pixi-live2d-display/`

MIT License. Copyright (c) 2020 Guan. https://github.com/guansss/pixi-live2d-display
（Live2D 样机播放页用；取自 npm 包 pixi-live2d-display@0.4.0 的 dist/cubism4.min.js，未改动，许可原文在
`pixi-live2d-display/LICENSE`。它内含 Live2D Cubism SDK for Web 的 Framework 部分，那部分按
Live2D Open Software License 发布：https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html 。
**Cubism Core 不在仓库里**：它是 Live2D 的专有组件（Live2D Proprietary Software License），播放页运行时从
Live2D 官方地址加载。用两个开源工具生成的 .moc3 只供个人样机；商用前要先确认 Live2D 的条款
（https://www.live2d.com/en/sdk/license/ ）。）

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
