# 开机动画的来源与许可

| 文件 | 来源 | 圆桌改了什么 |
|---|---|---|
| `dawn.html` | 圆桌原创：「晨会协议」片头（蓝色晨光、圆桌七席依次醒来、日出、早上好） | — |
| `gmu.html` | 改编自 **dsh-550c-boot**（https://github.com/yannicksong0106/dsh-550c-boot，作者 Ziyang Song，MIT，见同目录 `LICENSE`）的 `preview/ueg.html`（访问授权三幕） | 机构换成 GMU（早上好联合 / GOOD MORNING UNION）；配色改成蓝色系；终端、台词、证件编号、终幕文字等原作里的影视元素全部换成圆桌自己的内容；卡片字段、头像、二维码从「片头」设置读取（只存本机）；去掉录音文件，台词交给外层页面用系统女声朗读 |
| `assets/gmu-seal.svg`、`assets/gmu-lockup.svg` | 圆桌自绘 | 替换原来的徽记和横版标志图片 |
| `assets/land.js` | 同上项目的 `preview/assets/ueg-land.js`（由 Natural Earth 110m 陆地数据烘出，公有领域） | 全局变量改名 `GMU_LAND` |
| `assets/qrcode-gen.js` | qrcode-generator（Kazuhiko Arase，MIT），未改动 | — |
| `assets/jsqr.js` | jsQR（Cosmo Wolfe，Apache-2.0），未改动 | — |

登录授权阶段的交互思路，原项目注明受 **6shenhonghong9/dsh-startup-screen**（沈宏宏，MIT）启发。

音效全部用 WebAudio 现场合成，播报用浏览器 / 系统自带的朗读声音；不包含任何影视作品的画面、声音、音乐、配音、标识或字体文件。
