# 摄影要素 → 手机随手拍参数

每一节：清单里的要素、手机上的真实取值、可直接用的英文提示词短语、建议做成配置维度还是只写进提示词正文。
「维度」= 像 `media.moment_photos.shots` 那样做成 `{text, weight}` 表、每张随机抽一个；「正文」= 固定写进 `prompts/moment_photo/vN`；「不用」= 对二次元手机照没意义或有反作用。

画风提醒：下面所有短语都描述**取景、光、时机、景深**；不写写实画质词（photorealistic、8k、相机 / 胶片型号）。

---

## 1. 机位与构图 —— 维度「机位」（最值得做）

| 要素 | 手机取值 | 提示词短语 |
|---|---|---|
| 距离远近 | 贴近（一臂内）/ 隔一张桌子 / 几米外 | `from arm's length` · `from across the table` · `from a few meters away` |
| 高度 | 站着往下拍 / 坐着平拍 / 从座位低处往上 | `high angle from standing height` · `eye-level from a seated friend` · `low angle from the seat` |
| 方向 | 正面 / 四分之三侧 / 侧面 / 背影 | `three-quarter view` · `in profile` · `from behind` |
| 过肩 | 朋友的肩膀在前景 | `over a friend's shoulder` |
| 隔物取景 | 门框、窗框、两人肩膀之间 | `framed through the doorway` · `between two people's shoulders` |
| 镜子 | 镜子自拍 / 玻璃反射 | `mirror selfie, phone visible in the mirror` · `reflected in the window glass` |
| 俯拍桌面 | 食物、手账、桌面 | `top-down shot of the table` |

构图法则（写进正文，作为默认审美，不必每张抽）：三分法放人 `subject on a third, not centered`；引导线 `table edges / corridor lines lead to her`；
前景虚化物增加层次 `blurred cup / railing in the foreground`；留白 `plenty of empty wall or sky`；轻微倾斜 `slightly tilted horizon`。
**避免**：完美对称、海报式居中、黄金螺旋这类「设计感」词。

画幅：手机朋友圈主要是竖幅 3:4，其次 1:1、横幅 4:3；超宽 16:9 少见。→ 可做小维度「画幅」，但需绘图模型支持尺寸参数，否则只在 `_jpeg` 裁切时实现（不花钱）。

## 2. 曝光三要素 —— 只取「效果」，不写参数

手机自动曝光，提示词里写效果，不写 f 值 / 快门 / ISO：

| 效果 | 何时用 | 短语 |
|---|---|---|
| 运动凝固 | 白天健身房、天台跳起来 | `motion frozen mid-jump` |
| 手抖 / 运动糊 | 室内弱光、她突然转身 | `slight motion blur on her moving hand` |
| 夜景模式 | 夜里天台、出游夜景 | `phone night mode look: clean shadows, lights glowing softly` |
| 逆光 HDR | 窗边、天台背光 | `backlit by the window but her face still visible` |
| 欠曝氛围 | 熄灯后台灯、看电影 | `dim room, only the lamp lit, rest falls into shadow` |
| 噪点 | 暗处 | 交给 `phone_look`，不写 |

不用：星芒、长曝光车轨、安全快门、测光模式、曝光补偿数值。

## 3. 光线 —— 维度「光」（与时间、地点联动）

| 光源 | 公寓里的对应 | 短语 |
|---|---|---|
| 窗光（侧光） | 宿舍白天、休息室 | `soft side light from the window` |
| 窗前逆光 | 早上拉开窗帘 | `against the bright window, rim light on her hair` |
| 顶灯 | 食堂、办公室日光灯 | `flat overhead ceiling light` |
| 台灯 / 暖光 | 宿舍夜里 | `warm desk lamp as the only light, deep shadows` |
| 屏幕光 | 打游戏、熬夜、看电影 | `lit by the screen glow, cool blue on her face` |
| 黄金时刻 | 傍晚天台、出游 | `golden hour sun, long warm shadows` |
| 蓝调时刻 | 日落后天台 | `blue hour sky, city lights just turning on` |
| 阴天柔光 | 雨天、阴天 | `overcast soft light, no hard shadows` |
| 正午硬光 | 出游户外 | `harsh midday sun, short dark shadows` |
| 闪光直闪 | 夜里聚会 | `on-camera flash, bright face, dark background` |
| 霓虹 / 招牌 | 出游夜街 | `neon sign light spilling color on her` |

光的质感：硬光 / 软光直接跟光源走，不单独抽。伦勃朗光、蝴蝶光、布光器材（柔光箱、反光板、束光筒、旗板）属于棚拍，**不用**；
「每束光都有看得见的来源」写进正文。

时间联动：事件有时间段时，按时段过滤光表（早上：窗光 / 逆光 / 阴天；白天：窗光 / 顶灯 / 正午；傍晚：黄金时刻 / 蓝调；夜里：台灯 / 屏幕 / 顶灯 / 闪光）。

## 4. 对焦与景深 —— 正文 + 小维度

- 默认 `deep phone-camera depth of field, background mostly sharp`。
- 小概率人像模式 `portrait mode, background softly blurred with slightly artificial edges`（建议权重 ≤ 2/10）。
- 对焦失误（真实感彩蛋）`focus slightly missed, landed on the cup instead of her`（权重 ≤ 1/10）。
- 微距：手机微距模式拍小东西 `phone macro close-up of the hairclip`（配合 `cues`）。
- 不用：超焦距、景深合成、移轴。

## 5. 镜头与滤镜 —— 维度「焦段」（小）

| 档 | 短语 | 适合 |
|---|---|---|
| 0.5x 超广角 | `0.5x ultra-wide, edges slightly stretched, whole room in frame` | 宿舍全景、天台、聚会合影 |
| 1x 主摄 | （默认，不写或 `normal phone lens`） | 大多数 |
| 2x–3x | `2x zoom, a little compressed, from across the room` | 偷拍朋友、远一点的抓拍 |
| 前摄 | `front camera selfie at arm's length, slight wide-angle face` | 自拍 |

光学瑕疵只取手机会有的：逆光眩光 `small lens flare from the window`、夜里灯的鬼影；暗角、色散、呼吸效应不用。
滤镜：CPL / ND / GND / 黑柔 / 星光镜都是相机配件，**不用**；手机自带「滤镜」放到第 10 节。

## 6. 机身与传感器 —— 不用（由 phone_look 承担）

传感器尺寸、像素、RAW、果冻效应、防抖、连拍都是机身属性。唯一可借：**连拍里挑的一张**（`one frame from a burst, caught mid-motion`）可写进「时机」维度。

## 7. 支撑与稳定 —— 只取「谁拿手机」

不用三脚架、稳定器。真正影响画面的是**谁在拍**：她自己（自拍 / 第一人称）、朋友帮拍（抓拍、偷拍）、手机靠在东西上定时（`phone propped on the table, timer shot, slightly low and crooked`）。
这一点已部分体现在现有 `shots`（`her: none / part / full`），可以扩成「拍摄者」维度。

## 8. 被摄对象 —— 正文 + 形象卡

- 人物：姿态自然、表情来自事件（吃到好吃的、被逗笑、专注），眼神方向：大多不看镜头（自拍除外）→ `not looking at the camera` / `glancing at the camera mid-laugh`。
- 妆造服装：用形象卡 `look` / `must` / `daily_must`；日常不带武器大道具。
- 静物：只拍值得炫的（刚上桌的饭、天台夜景、可爱小物），陈设保持生活杂乱，不摆拍。
- 运动：健身房、打闹时写方向 `caught mid-turn` / `running toward the camera`。

## 9. 环境与时机 —— 维度「时机」（最能去 AI 感）

| 时机 | 短语 |
|---|---|
| 决定性瞬间 | `the split second right after something happens` |
| 笑到一半 | `mid-laugh, eyes squeezed` |
| 吃到一半 | `mid-bite, cheeks full` |
| 刚转身 | `just turning around after hearing her name` |
| 没准备好 | `caught before she noticed the camera` |
| 发现被拍 | `just noticed the camera, hand half raised to block it` |
| 安静时刻 | `a quiet moment, absorbed in what she is doing` |

天气（雨、雪、雾）、月相、季节由事件 / 生活引擎提供时才写；不要编出天气。

## 10. 色彩与影调 —— 小维度「手机滤镜」（可选）

真人发朋友圈会用手机自带滤镜，但用户反感过饱和。建议只给 3～4 档，原色最高权重：
原色（不写）· `slightly warm phone filter` · `slightly cool, faded phone filter` · 偶尔 `black-and-white phone filter`（≤ 1/20）。
高调 / 低调由光决定，不单独抽。互补色、单色这类配色词不用（会变成设计稿）。

## 11. 后期 —— 已由本机完成

`phone_look`：轻微裁歪、降饱和、噪点、JPEG 压缩。可扩展（不花钱，代码里做）：
- 二次构图：按画幅维度裁成 3:4 / 1:1；
- 小概率加手机自带「美颜感」——**不建议**，用户反感 AI 光滑感。
不用：曲线、HSL、LUT、堆栈、液化、合成。

## 12. 拍摄者本身 —— 维度「拍照习惯」（每位成员不同）

审美、预判、与被摄者沟通、风格取舍 → 映射成每位成员**自己拍照时的偏好权重**：爱拍什么、爱用哪种机位 / 焦段 / 滤镜、自拍多不多。
朋友帮她拍时用朋友的习惯还是通用习惯，见 `habits.md`。

## 13. 输出与呈现 —— 已有配置

`max_side`（长边像素）、JPEG 压缩、朋友圈九宫格显示。打印、校色、色彩空间不用。
