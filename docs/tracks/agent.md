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

## 下一步：网页小人

用 `images.chibi`（`chibi_transparent`）在圆桌或成员面板显示成员小人，同样只在显示模型时出现。
