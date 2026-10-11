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

## R2：网页小人·动起来（设计，2026-10-11）

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
