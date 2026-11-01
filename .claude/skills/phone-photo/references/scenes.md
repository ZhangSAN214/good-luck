# 细分题材 → 公寓日常 / 出游拍法配方

清单里的人像、夜景、街拍子类型很多。圆桌成员的照片只在**公寓里**（宿舍、食堂、休息室、健身房、办公室、会议室、天台）和**出游**两类场景出现，
下表挑出能用的，给出可以直接加进 `media.moment_photos.shots` 的英文拍法（`text`）、建议权重和 `her` 取值。不能用的列在最后，说明原因。

## 人像类

| 子类型 | 适用地点 | shot text（英文，直接进配置） | 权重建议 | her |
|---|---|---|---|---|
| 环境人像 | 宿舍、休息室 | `a friend's casual photo of her in her own corner of the room, the room tells what she was doing` | 3 | full |
| 抓拍（职业纪实感） | 办公室、会议室 | `caught working at her desk from across the office, absorbed, not posing` | 2 | full |
| 情侣 / 闺蜜照 | 任意 | `two housemates squeezed into one frame, one holding the phone, both mid-laugh` | 1 | full |
| 自拍 | 宿舍、天台、出游 | `a quick front-camera selfie at arm's length, slightly wide-angle face, half the background cut off` | 1 | full |
| 镜子自拍 | 宿舍、健身房 | `mirror selfie with the phone covering part of her face` | 1 | full |
| 逆光 / 剪影 | 天台傍晚、窗边早上 | `backlit by the window or sunset, her outline glowing, face still readable` | 1 | full |
| 运动人像 | 健身房、天台 | `caught mid-move at the gym, a little motion blur on her hands` | 1 | full |
| 群像 / 合影 | 食堂、天台聚会 | `0.5x ultra-wide group shot around the table, someone half out of frame, nobody posing properly` | 1 | full |
| 雨中 / 雪中 | 出游、天台 | `under an umbrella in the rain, wet ground reflecting light` | 事件有雨才用 | full |

## 静物 / 第一人称类

| 子类型 | shot text | 权重 | her |
|---|---|---|---|
| 桌面俯拍 | `top-down shot of the table just served, her hand reaching in from the edge with her signature item` | 2 | part |
| 第一人称 | `her point of view: her hand holding the thing she is excited about, a signature item visible` | 2 | part |
| 小物微距 | `phone macro close-up of something small and cute next to her signature item` | 1 | part |
| 值得炫的东西 | `only the thing worth showing off (a nice meal, the rooftop view, a new object); she does not appear` | 2 | none |

## 夜景类（天台 / 出游 / 宿舍夜里）

| 子类型 | shot text | 条件 |
|---|---|---|
| 天台城市夜景 | `night view from the rooftop with her silhouette at the railing, phone night mode look` | 夜里、天台 |
| 蓝调时刻 | `blue hour on the rooftop, sky still deep blue, city lights just on` | 傍晚后 |
| 窗景 / 居民楼灯光 | `the lit windows across the street seen past her shoulder` | 夜里、宿舍 |
| 夜间人像（闪光） | `on-camera flash at a late-night snack, bright face, dark background` | 夜里聚会 |
| 室内夜景 | `dim lounge lit by the TV during movie night, faces lit by the screen` | 夜里、休息室 |
| 节日灯会 / 夜市 | `night market stalls glowing behind her, crowd blurred` | 出游、节日 |
| 雨夜 | `rainy night, wet ground reflecting neon` | 出游、下雨 |
| 烟花 | `fireworks over the city, her raised phone in the foreground` | 节日 |

## 街拍类（只用于出游）

| 子类型 | shot text |
|---|---|
| 决定性瞬间 | `street snapshot of the split second she reacts to something on the street` |
| 层叠构图 | `layered street scene, passers-by in the foreground and background, she is in the middle layer` |
| 建筑 + 小人 | `she is small in front of a big building or landscape, wide shot` |
| 窗玻璃反射 | `seen through a shop window, reflections overlapping her` |
| 慢门拖影 | `crowd streaking past with motion blur, she stands still and sharp` |
| 车窗 / 移动中 | `from the bus window, scenery blurred, her reflection faint in the glass` |
| 市场小摊 | `at a food stall, steam rising, she is pointing at what she wants` |

## 不用的子类型（原因）

- 棚拍、商业布光、美妆特写、时尚编辑、证件照：摆拍、不像朋友圈。
- 私房 / boudoir、卖弄身材的角度：用户明确禁止。
- 婚纱、孕妇、新生儿、儿童、老人年代感：与成员设定无关。
- 水下、极限运动、无人机航拍、光绘、天文台、闪电、工业夜景：手机随手拍不现实，只有出游事件明确提到才考虑。
- 盲拍 / 腰平拍、街头随机邀请路人、时装周街拍：涉及拍陌生人，不适合成员人设。
- 双重曝光、拼贴：后期合成，不是随手拍。

## 地点 → 推荐拍法组合

| 地点 | 推荐 |
|---|---|
| 宿舍 | 环境人像、镜子自拍、台灯 / 屏幕光、桌面俯拍 |
| 食堂 | 桌面俯拍、隔桌抓拍、吃到一半、0.5x 群像 |
| 休息室 | 环境人像、电视光、过肩 |
| 健身房 | 运动人像、镜子自拍、顶灯 |
| 办公室 / 会议室 | 隔着门框抓拍、专注、顶灯 |
| 天台 | 逆光 / 剪影、蓝调、城市夜景、自拍 |
| 出游 | 街拍类、建筑 + 小人、黄金时刻、夜市 |
