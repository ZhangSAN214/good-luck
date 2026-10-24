# 开机动画的来源与许可

圆桌的开机动画（`web/boot/`）移植自 **dsh-550c-boot**（https://github.com/yannicksong0106/dsh-550c-boot），
作者 Ziyang Song，MIT 许可（见同目录 `LICENSE`）。

| 文件 | 来源 | 圆桌改了什么 |
|---|---|---|
| `550c.html` | 该项目的 `assets/550C-source.html`，片头原稿由 **Voidpocket**（https://github.com/Voidpocket）提供 | 放完或点击 / Esc 时通知外层页面（`boot:done`），提示语改成「点击或 Esc 继续」 |
| `gmu.html` | 该项目的 `preview/ueg.html`（访问授权三幕） | 所有 UEG 换成 GMU（早上好联合 / GOOD MORNING UNION）；卡片字段、头像、二维码从圆桌的「片头」设置读取（只存本机）；默认二维码由证件编号在本机编码；去掉录音文件，配音改用浏览器自带朗读 |
| `assets/gmu-seal.svg`、`assets/gmu-lockup.svg` | 圆桌自绘 | 替换原来的 UEG 徽记和横版标志图片 |
| `assets/land.js` | 该项目的 `preview/assets/ueg-land.js`（由 Natural Earth 110m 陆地数据烘出，公有领域） | 全局变量改名 `GMU_LAND` |
| `assets/qrcode-gen.js` | qrcode-generator（Kazuhiko Arase，MIT），未改动 | — |
| `assets/jsqr.js` | jsQR（Cosmo Wolfe，Apache-2.0），未改动 | — |

登录授权阶段的交互思路，原项目注明受 **6shenhonghong9/dsh-startup-screen**（沈宏宏，MIT）启发。

不包含任何影视作品的画面、声音、音乐、配音、标识或字体文件。「550C」等名称只用来描述界面风格，
相关作品与名称的权利归其权利人所有；本动画与权利人无关，未获其授权或背书。
