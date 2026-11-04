# 这个应用自带的测试

- **企业级验收**（底座自带，K5）：撤销至少 20 步、打开 4000 像素宽的大图不卡死、导出尺寸不变、
  出错有提示、快捷键能用。门槛写在 `app.json` 的 `checks` 里。
- **功能自带的测试**：`windows/<功能>.js` 可以导出 `export async function test(t)`，
  `t.editor` 是底座、`t.assert(条件, 说明)`、`t.pixel(x, y)` 读合成后某一点的颜色、`t.sample(宽, 高)` 造一张测试图。
  每个测试开始前画布上已经有一张 160×120 的测试图。

怎么跑：

- 圆桌每轮质检都会自动跑一遍，结果在 `report/round-N/app_report.json`，截图是同目录下的 `应用_*.png`。
- 自己看：在圆桌里打开 `index.html?selftest=1`，右下角显示每一项的结果（会清空画布）。
