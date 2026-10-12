# 成员形象（agent 线）

## 本步：成员头像接入网页（已完成）

讨论 / 协同的消息头像、圆桌座位、互评等用到 `avatar()` 的地方，在**显示模型时**（匿名关闭，或匿名会话已揭晓）改为成员头像；匿名开启且未揭晓时页面上没有任何头像图片或素材地址。没有素材、图片缺失或加载失败时保持原来的单字圆形。

### 素材（在用户本机，不进 git）

- 目录：`<项目根>/data/avatars/roundtable_assets/`，可用环境变量 `ROUNDTABLE_ASSETS_DIR` 覆盖（相对路径按项目根解析）。`data/` 已在 `.gitignore`。
- 入口 `members.json`：`{"version":2,"members":[{"id","name","vendor","nickname","theme_color","order","status","images":{...}}]}`，多余字段忽略。
- `images` 的值是相对 `members.json` 的路径；`avatar_circle_light` / `avatar_circle` 为 `{"256","128","64"}`。
- `vendor` 与 `config/models.yaml` 的 `vendor` 一致（不区分大小写），用它把模型对应到成员；同一 vendor 有多个成员时取 `order` 靠前的。
- `theme_color` 只接受 `#rgb` / `#rrggbb` / `#rrggbbaa`，否则视为没有。

### 代码

- `core/members_art.py`：读取校验（pydantic）、防路径穿越（图片必须在素材目录内，且为 png / jpg / webp）、缺图记 `None`；目录或 json 不存在 / 损坏时 `available=False` 加原因，不抛错。代码里不出现任何厂商或模型名。
- `core/runtime.py`：`Runtime.members_dir`；`Runtime.build(members_dir=None)` 依次取参数、环境变量、默认目录。
- `core/service.py`：`members()`、`member_file(path)`。每次请求重新读取 `members.json`。
- 测试的 `Env` 默认指向不存在的素材目录，不读用户本机素材。

### 接口

`GET /api/members`

```json
{
  "available": true,
  "reason": null,
  "members": [
    {"id": "...", "name": "...", "nickname": "...", "display": "昵称或名称",
     "theme_color": "#rrggbb", "order": 1, "status": "...",
     "images": {"avatar_64": "/api/members/files/…", "avatar_128": "…", "avatar_256": "…", "chibi": "…"}}
  ],
  "models": {"<模型 id>": "<成员 id>"}
}
```

- 头像优先 `avatar_circle_light`，没有就用 `avatar_circle`（逐个尺寸）；`chibi` 取 `chibi_transparent`；不存在为 `null`。
- 这个接口不涉及具体会话（与 `/api/status` 一样会列出模型 id）；匿名保护在前端：`Names.model()` 返回 null 时不查头像。

`GET /api/members/files/{path}`：只返回素材目录内的 png / jpg / webp，否则 404。响应头 `X-Content-Type-Options: nosniff`、`Cache-Control: private, max-age=3600`。

### 前端

- `api.js` 的 `members()`；`app.js` 启动时请求一次，失败当作没有素材。
- `view.js` 的 `setMemberArt(data)`；`Names.speaker()` 带上 `art`；`avatar()` 有头像时渲染 128px `<img>`（`alt` 为昵称，`loading="lazy"`，边框为 `theme_color`），加载失败时移除图片、恢复单字。

## R2：网页小人·动起来（已完成，2026-10-11）

### 做什么

页面右下角浮着一位成员的小人（`/api/members` 的 `images.chibi`，即 `chibi_transparent`），能拖动、松手贴边、点 × 隐藏（边上留一个「小人」按钮可叫回来）。由一个状态机驱动动作和表情：

- 动作：待机呼吸、眨眼、走动、跳、挥手、思考、说话、打字、庆祝、困、睡、被拖起挣扎、落地。
- 表情：开心、得意、思考、惊讶、困惑、害羞、生气、难过、困。
- 只有一张 chibi 时全部用 CSS/JS 程序动画：身体的缩放 / 倾斜 / 位移 / 滤镜，加头顶的表情符号（♪ ✧ ! ? … 💢 z）、腮红、汗滴、彩纸、对话框等小部件，保证每种状态一眼能分清。
- 有逐帧素材时（`pet.json`）对应的动作 / 表情用逐帧播放，其余仍用程序动画。
- 待机时自己随机眨眼、偶尔走两步 / 挥手 / 跳；一段时间没有操作先困再睡，动鼠标或按键就醒。
- 本步只做小人本身和给下一步用的接口（`pet.act(动作)`、`pet.feel(表情)`），和讨论事件联动、气泡汇报、点击菜单留给 R3。

### 显示规则（防暴露身份）

- 默认显示排序最靠前、有 chibi 的成员；`localStorage` 里存了别的成员 id 且仍有 chibi 时用它（设置界面在 R3）。
- **当前会话是匿名且未揭晓时，小人整个移除**（DOM 里没有图片和素材地址）；揭晓、换到非匿名会话或点「新题目」后再出现。
- 没有素材、没有 chibi 或图片加载失败时不显示、不报错。
- 位置（贴哪条边、坐标）与是否隐藏存 `localStorage`，读写都包 `try/catch`；读不到就用默认位置。窗口变小时把小人拉回可见区域。
- 系统设置「减少动态效果」时只保留轻微动作。
- 前端不写死任何模型、厂商或渠道名，小人的名字只用服务端给的 `display`。

### pet.json（逐帧素材，可选）

成员在 `members.json` 的 `images.pet` 写 `pet.json` 的路径（相对 `members.json`）；没写时找 `<成员 id>/pet.json`。格式：

```json
{
  "version": 1,
  "fps": 8,
  "actions": {"idle": {"frames": ["idle/0.png", "idle/1.png"], "fps": 4, "loop": true},
              "wave": {"frames": ["wave/0.png", "wave/1.png", "wave/2.png"], "loop": false}},
  "expressions": {"happy": {"frames": ["face/happy.png"]}}
}
```

- 帧路径相对 `pet.json` 所在目录，必须在素材目录内、是 png / jpg / webp、文件存在，否则丢掉该帧；一帧都没有的动作丢掉。
- 只接受已知的动作 / 表情名，每个最多 64 帧，`fps` 限制在 1～30。文件缺失或损坏时当作没有（`pet: null`），不影响 chibi。
- 接口里每位成员多一个 `pet`：`{"fps": 8, "actions": {"idle": {"frames": ["/api/members/files/…"], "fps": 4, "loop": true}}, "expressions": {…}}` 或 `null`。

### 改哪些文件

- `src/roundtable/core/members_art.py`：读 `pet.json`（校验、防路径穿越、限制帧数）；`Member.pet`。
- `src/roundtable/core/service.py`：`members()` 输出 `pet`（帧换成 `/api/members/files/…` 地址）。
- `web/js/agent/brain.js`：纯逻辑，无 DOM——动作 / 表情表、状态机（优先级、持续时间、待机随机行为、困 / 睡计时）、位置计算（夹到可见区域、贴边）、安全的 `localStorage` 读写。
- `web/js/agent/pet.js`：DOM 部分——挂载、拖动（pointer 事件）、隐藏 / 叫回、逐帧播放、程序动画的类名与小部件。
- `web/css/agent.css`：小人的样式与关键帧动画；`index.html` 引入。
- `web/js/app.js`：拿到 `/api/members` 后创建小人；每次渲染时按当前会话决定显示还是移除。

### 测试

- `tests/core/test_members_art.py`、`tests/api/test_members_api.py`：pet.json 正常 / 缺帧 / 路径穿越 / 损坏 / 未知名称 / 帧数与 fps 限制；接口里的 `pet`。
- `tests/web/test_agent_brain.py`（用 node 跑 brain.js，没有 node 时跳过）：状态机转换与优先级、拖动 → 挣扎 → 落地、困 / 睡与唤醒、贴边与夹回可见区域、`localStorage` 抛错时不报错。
- `tests/web/test_e2e_agent.py`（Playwright）：有素材时出现小人且能拖动、位置刷新后保留；隐藏后能叫回；匿名未揭晓时页面上没有小人和素材地址、揭晓后出现；没有素材时没有小人也没有 JS 报错；`localStorage` 抛错时页面正常；有 pet.json 时用逐帧图片。
- 全部素材都是测试里临时目录的假图片。

### 实现记录（R2 完成，提交 afaba79）

- 按上面的设计实现；与设计不同的细节：贴在角上时 `edge` 记左 / 右边（小人靠着侧边）；轻点小人会开心地跳一下，双击播放全部动作和表情的演示（约 40 秒），方便在本机检查效果。
- 动作 / 表情名在 `core/members_art.py`（`PET_ACTIONS` / `PET_EXPRESSIONS`）和 `web/js/agent/brain.js` 各有一份，`tests/web/test_agent_brain.py` 检查两边一致。眨眼在后端是动作名 `blink`，前端是叠在任何动作上的效果。
- `localStorage` 键 `roundtable.pet`：`{x, y, edge, hidden, member}`；`member` 预留给 R3 的「设置里换成员」。
- 给 R3 的接口：`app.js` 里的 `pet.act(动作, {ms})`、`pet.feel(表情, {ms})`；被拖着时其他动作和表情会被拒绝（返回 false）。
- 测试：`tests/core/test_members_art.py`、`tests/api/test_members_api.py`（pet.json）、`tests/web/test_agent_brain.py`（9 个）、`tests/web/test_e2e_agent.py`（7 个）。

## R3：网页小人·汇报与互动（已完成，2026-10-12）

### 做了什么

- **事件联动**（`web/js/agent/reactions.js` 的 `Reporter`，纯逻辑，node 可测；`app.js` 的 `onEvent` 里调 `pet.report(e)`）：
  - 步骤开始：成员发言类步骤→说话，统筹类步骤（拆分 / 分配 / 合并 / 综合）→思考；冒进度气泡「某步骤中（第 N 桌）」，普通进度气泡至少隔 4 秒，动作照常。
  - 等待确认（`checkpoint` / 状态变为 `awaiting_confirmation`）→招手 + 困惑的问号，气泡「需要你确认」（重要气泡不限频）。
  - 讨论完成→庆祝，气泡「完成啦！点我看结果」；失败→难过。只有事件流快照说这一场正在进行时才庆祝，打开旧会话不会空欢喜。
  - 预算提醒→跳一下 + 困惑（着急），气泡显示服务端的提醒（截断 40 字）。
  - 长时间无操作→困→睡，沿用 R2；鼠标靠近（240px 内）时待机的小人转头看向鼠标。
- **气泡**：点气泡收起并滚到最新的讨论内容；气泡文字只用步骤名、桌号和服务端提示，不含成员身份。
- **免费互动**（不调用任何模型）：点头顶=摸头（害羞）；点身体=被戳（惊讶地跳）；4 秒内连戳 3 下=生气；拖着玩沿用 R2。
- **点击菜单**：鼠标移到小人身上出现左上角「⋯」（也可右键小人）：她是谁（坦白是 AI 成员）、当前状态、跳到讨论、安静一会儿 / 不用安静了（30 分钟内不冒气泡，动作照常）、换个小人（有多位带 chibi 的成员时出现，选择存进 `roundtable.pet.member`）。Esc 或点别处关闭。
- 匿名且未揭晓的会话里小人整个移除（沿用 R2），所以气泡和菜单也不会出现。

### 与设计不同 / 待决

- 「设置里换成员」做成了小人菜单里的「换个小人」，没有单独的设置页；默认仍是排序最靠前的成员（待决：用户是否要默认某位）。
- 轻点身体不再是「开心地跳」，改为被戳（惊讶地跳）。
- 小人聊天（日常模式）属于 R9，需用户先确认，这里不做。
- 点气泡「点我看结果」目前是滚到讨论区最新处，没有按具体子任务定位。

### 测试

- `tests/web/test_agent_reactions.py`（node，8 个）：步骤→动作、限频、确认卡片、只庆祝进行中的一场、失败 / 预算、安静模式与连戳计数、看向方向与头 / 身体判定。
- `tests/web/test_e2e_agent.py` 新增 4 个：讨论中冒气泡、完成后庆祝并可点气泡；摸头 / 戳 / 生气；菜单（她是谁、当前状态、安静、Esc 关闭）；匿名未揭晓时没有气泡。「换个小人」只有单位成员的测试数据，未做端到端覆盖。

## R3.1 特殊待机、彩蛋、行礼与手机（2026-10-12）

素材由 pet.json 声明，全部配置驱动，代码里没有成员名；没声明的成员行为不变。

- **新动作**：`talk_book / letter / gift / curtsy / flip / bookface`（`core/members_art.py` 的 `PET_ACTIONS` 与 `brain.js` 的 `ACTIONS` 一致，有测试）；`hold_last_s` 为末帧停留秒数（0–10）。
- **特殊待机** `special_idle {action, after_s}`（after_s 默认 600，服务端限制 5–7200；动作必须有帧才生效）：声明后**取代**犯困 / 睡觉，无操作满 after_s 秒进入该动作并一直持续，**任何活动**（鼠标 / 键盘 / 滚动 / 触摸 / 窗口获得焦点 / 页面回到前台）立刻停笔回到站立。任务进行中、等待确认、菜单打开、拖动时不进入。
- **彩蛋** `easter_eggs[{action, probability, cooldown_s, max_per_day, triggers}]`（默认 0.05 / 12 小时 / 每天 1 次 / user_return + task_done；最多 4 个）：`user_return`＝从特殊待机或离开很久（≥ after_s，无特殊待机时 10 分钟）后回来的第一次活动；`task_done`＝庆祝结束后。忙碌、冷却中、当天已满、正在播一次性动作时不掷；随机函数可注入；冷却和次数存本机 `roundtable.pet.egg`。不计数展示、不提示错过、没有奖惩（§5.1）。播放时长按帧数 / fps 加末帧停留算。
- **摸头**：点头部（上 38%）有 `curtsy` 帧则行礼，否则沿用害羞；行礼 / 彩蛋 / 庆祝播放中再点会被忽略；点身体仍是戳。
- **手机**：视口 ≤ 600px 加 `.phone`（小人 96×120，触摸屏 `(hover:none)` 下「⋯」「×」常显且热区 ≥ 44px）；触摸要按住 300ms 才能拖动，之前的移动算轻划，不拖也不算点击；页面切后台时停掉所有定时器，回来算一次活动；`prefers-reduced-motion` 沿用。
- **测试**：`test_agent_brain.py` +7（特殊待机假时钟、忙碌、彩蛋概率 0/1/种子、冷却 / 每日上限 / 重启恢复、长按）；`test_e2e_agent.py` +5（行礼与忽略连点、写信直到活动、回来触发彩蛋并落库、手机长按拖动 / 轻划不拖、后台暂停动画）；后端 `test_members_art.py`、`test_members_api.py` 各有新增。
- **待用户在本机验证**：真机长按 / 贴边手感；写信循环的观感；彩蛋 0.05 的出现频率；members.json 的 `images.pet` 指向素材包的 pet.json。
- 未做：彩蛋气泡文案（需要配置字段，暂不冒气泡）；设置里调 after_s（目前只能改 pet.json）。

## 下一步：R4 差分素材清单与检查脚本
