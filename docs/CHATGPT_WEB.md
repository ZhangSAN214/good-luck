# ChatGPT 网页出图（朋友圈配图，开机时用）

2026-11-02 用户定：电脑开着时，朋友圈配图用你自己的 ChatGPT 会员在网页里画（会员包月，不另花钱），最好全自动；画不了时用 GPT Image 2.5（每张约 $0.08～0.09）。

## 一次性准备（在电脑上做一次）

1. 在圆桌目录打开终端，运行：

   ```
   roundtable chatgpt-login
   ```

2. 会弹出一个浏览器窗口，**你自己**登录 ChatGPT（圆桌不经手账号密码）。登录好、看到对话输入框后窗口自动关闭，终端显示「登录成功」。
3. 重启圆桌（`stop.bat` 再 `start.bat`）。

之后每天的朋友圈配图会先交给 ChatGPT 网页画。不想用了：`roundtable chatgpt-login --off`（不删登录状态，想再用时重新运行 `roundtable chatgpt-login`）。

## 它怎么画

- 用一个专用的浏览器资料夹（`data\chatgpt_web_profile`，不进 git，和你平时用的浏览器分开）打开 ChatGPT 网页，新开一个对话。
- 上传参考图（房间、入镜成员的形象图，最多 10 张）→ 逐字输入画面描述（不动你的剪贴板）→ 发送 → 等画完（一张约 1～3 分钟，最多等 5 分钟）→ 取图 → 关浏览器。
- 浏览器窗口默认放在屏幕外，不挡你；任务栏里会短暂出现一个浏览器图标。
- 网页里会多出一条条对话（每张图一条），可以在 ChatGPT 里随手删。
- 一次只画一张；正在画时又来一张，那张直接交给 GPT Image 2.5。

## 什么时候改用 GPT Image 2.5

出图顺序里网页排第一（它是「本地、不花钱」的渠道），下面这些情况自动换下一个模型（GPT Image 2.5），朋友圈照常发：

- 还没运行过 `roundtable chatgpt-login`，或登录过期（网页上出现登录按钮）；
- 会员的画图次数用完（网页出现上限提示）；
- 网页打不开、5 分钟没画完、网页改版找不到输入框；
- 电脑没开时圆桌本身不运行，朋友圈也不会发。

失败过的渠道会歇一会儿再试，不会每张都先撞一次网页。

## 网页改版时

网页上找输入框、发送按钮、画好的图用的是一组「位置」（selectors）。ChatGPT 改版后找不到时，在 `config\models.local.yaml` 里覆盖，不用改代码：

```yaml
model_params:
  chatgpt-web:
    selectors:
      send: "button[aria-label='发送']"
```

能改的项和默认值见 `src/roundtable/core/providers/chatgpt_web.py` 的 `SELECTORS`。其他参数：`timeout_s`（最多等多久）、`browser`（`msedge` / `chrome` 用本机浏览器；留空用 Playwright 自带的 Chromium，没装时自动改用 Edge）、`headless`（默认 `false`：无头浏览器容易被网页拦）、`offscreen`（窗口放屏幕外）。

## 注意

- 需要 `playwright`（`pip install -e ".[dev]"` 已带；只装了运行依赖时 `pip install playwright`）。
- 用程序操作网页不是 ChatGPT 官方支持的用法，有被限制账号的风险（已告知用户，11-02 用户决定用）。一天几张、一次一张，频率很低。
- 用 Google 账号登录时，Google 可能拒绝在这个专用浏览器里登录；改用邮箱 + 密码登录 ChatGPT 即可。
