# 终端邮件栏（学校邮箱，只读）

方案见项目资料 `计划/终端邮件栏_方案.md`（v2：圆桌里真登录）。本文件说明做了什么、怎么用、安全边界。

## 怎么用

1. 终端首页右上角点「邮件」→「打开邮件栏」（默认关）。
2. 点「登录学校邮箱」：电脑上会弹出一个**单独的浏览器窗口**（优先用电脑自带的 Edge / Chrome 程序，配一个独立的配置目录 `data/mail_browser/`，与平时用的浏览器互不相干），打开微软官方登录页。
3. **你本人**在那个窗口里输入学校账号密码、完成多重验证；看到「保持登录状态？」可以选「是」。登录好以后窗口自动关掉，邮件栏显示收件箱最近 14 天的邮件。
4. 之后打开终端时、以及每 15 分钟（后端限制最短 10 分钟）在后台不弹窗刷新一次；也可以点「刷新」。登录过期时显示「需要重新登录」，再点一次登录即可。
5. 点开一封看纯文本正文；正文里找到的截止日期在「在邮件里找到的日期」里，点「加入提醒」才算。
6. 设置里可以改「重要」规则、保留天数、清空缓存、**退出登录并清除全部**。

电脑上找不到 Edge / Chrome 时，在项目目录运行 `python -m playwright install chromium`（约 150 MB）后再试。

## 安全边界

- **不碰密码**：密码只由用户本人输入在微软登录页；圆桌不代填、不读取输入框、不保存密码，聊天里也从不索要。
- **登录状态只在本机**：cookie 由浏览器自己保存在 `data/mail_browser/`（Windows 上浏览器用系统 DPAPI 加密，只有当前 Windows 账户能解开），不进 git。「退出登录」删掉整个目录。
- **只读**：专用窗口里装了只读守卫（`core/mail/owa.py` 的 `block_reason`）——只放行微软登录、多重验证、网页版邮箱和它的静态资源这些网站（学校若把登录转到自己的统一认证页，可在设置里加网站）；邮箱网站上的写入类请求（发送、删除、移动、标已读、改设置、GraphQL mutation、PUT / PATCH / DELETE）在浏览器里直接拦掉。读取只用 FindItem / GetItem 这类读操作，GetItem 不会把邮件标成已读。
- **借用的请求头只在内存里**：读取时借网页版那一次请求用的头补发只读请求，用完即丢，不落盘、不进日志。
- **只给你看**：邮件缓存在本机数据库（迁移 21：`mail_messages`、`mail_reminders`、`mail_audit`），只保留最近 N 天；成员（群聊、生活）读不到，也不发给任何模型。「重要」和日期都按本机规则算，不花钱。
- **审计**：每次登录 / 刷新 / 读正文 / 清空 / 退出都记进 `mail_audit`；设置里能看到上次读取时拦下了哪些请求。
- **出错信息**：先去掉网址参数（可能带登录码）、脱敏、截断再显示。

## 代码位置

- `core/mail/rules.py`：「重要」规则（`MailRules`、`judge`）与截止日期提取（`extract_dates`）。
- `core/mail/owa.py`：专用浏览器（`OwaBrowser`，Playwright 同步接口，在后台线程里跑）、只读守卫、网页版数据解析（大小写不敏感，兼容旧接口 `Subject` 与新接口 `subject` 两种形状）。
- `core/mail/box.py`：开关、设置（存 `app_settings` 的 `mail.*`）、后台任务（同一时间只跑一件）、提醒、审计。
- 服务：`RoundtableService.mail_*`；接口：`GET /api/mail`、`PUT /api/mail/settings`、`POST /api/mail/login`、`POST /api/mail/refresh`（`manual`）、`GET /api/mail/messages/{id}`、`POST /api/mail/reminders/{id}`（accept / dismiss / undo）、`POST /api/mail/logout`、`DELETE /api/mail/cache`。
- 前端：`web/js/mail.js`、`web/css/mail.css`。
- 测试：`tests/core/mail/`（含本机假邮箱 + 真 Chromium 的流程测试）、`tests/api/test_mail_api.py`、`tests/web/test_e2e_mail.py`。

## 已知限制

- 还没在真实的曼大邮箱上实测：微软网页版的内部接口没有公开文档，读取用的请求格式和字段名按常见格式写，并且准备了退路（补发的列表请求失败时，用网页版自己加载到的数据）。第一次实测时可能要调。
- 学校如果设置了「只允许受管设备」或频繁重新验证，会更常要求重新登录。
- 正文只显示文字；附件、图片、回信请到网页版邮箱里操作。
- AI 摘要没有做（方案里默认关，要开需另外确认上限）。
