# 设计套件的第三方素材

- 图标：[Lucide](https://lucide.dev)（lucide-static 1.64.0），ISC 许可。`icons.js` 只收了工作台常用的一部分图标的路径数据。

  ISC License — Copyright (c) 2026 Lucide Icons and Contributors. Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.

  2026-11-04 为编辑器底座加了 undo-2、redo-2、eye-off、arrow-up、arrow-down、move、brush、eraser、crop、square-dashed、zoom-in、zoom-out、image-plus、sliders-horizontal、scan，同样来自 Lucide。

其余样式和脚本都是本项目自己写的，包括编辑器底座 `editor/`（没有引外部画布库：评估过 Fabric.js、Konva，均为 MIT，但都是对象 / 矢量模型，不合修图的像素图层和原尺寸导出，见 docs/KIT.md「K 通用底座」）。
