# 资料浏览器（办公室）

办公室提问区的「资料」按钮打开一个内置浏览器：搜索、打开网页看正文，把有用的全文或选中的段落存进**资料夹**，提问时点「附上」交给成员。讨论、头脑风暴、协同、办公室模式都能用。

## 怎么用

1. 地址栏输入网址（可以省略 `https://`）打开；输入关键词则联网搜索，点结果打开。
2. 网页以**阅读视图**显示：只有标题和正文段落，正文里的链接收在下面，点了接着打开；「返回」回到上一页或搜索结果；标题下的「↗」在新窗口打开原网页。
3. 「存全文」存整篇正文；选中一段文字后「存选中」只存这一段（更省 token）。
4. 「资料夹」里可以查看、删除，点「附上」后资料作为一个 Markdown 附件出现在提问区。

## 为什么是阅读视图而不是整页嵌入

大多数网站（B站、知乎、淘宝等）禁止被别的网页嵌入（`X-Frame-Options` / `frame-ancestors`），所以不在页面里套网页；由圆桌后端抓取 HTML、提取正文后显示。好处：没有广告和脚本，成员拿到的只有正文，省 token。限制：要登录才能看的页面、完全靠脚本加载内容的页面读不到正文（会提示）。要登录的应用见 `计划/内置应用_方案.md`（桌面版内置窗口）。

## 规则

- **打开网页不调用模型、不花钱**：后端只读 `GET`，不带 cookie、不带任何 key；只接受 `http` / `https`，网页大于 `max_page_kb` 放弃，非网页（图片、视频、下载）拒绝。
- **不能打开本机和内网地址**：域名解析后的每个地址都必须是公网地址（拒绝 127.x、10.x、172.16–31.x、192.168.x、100.64/10（含 Tailscale）、169.254.x、IPv6 本机 / 内网等）；重定向不自动跟随，每一跳重新检查。防止平板局域网模式下被人拿来探测家里的设备。代理软件 fake-ip 段 198.18.0.0/15 放行。
- **搜索**走联网搜索服务（`search_providers`），每次按 `search_estimate_usd` 预估、计入总预算；调用记在隐藏会话 `clipper`，步骤 `clip_search`。
- **存储只在本机**：资料正文按内容哈希存进上传目录（`data/uploads`），清单在数据库 `app_settings` 的 `clipper.clips`；最多 `max_clips` 份（超出删最旧的清单项），每份最多 `max_clip_chars` 字。
- **附上**：附件只能用一次，所以每次「附上」都用资料正文新建一个 `.md` 附件。之后与普通附件完全相同：经 `attachments` 提示词、放在 `<attachment>` 标签里（标签内的指令无效），同一步骤所有成员看到的一样。
- 配置：`config/roundtable.yaml` 的 `clipper`（`enabled`、`timeout_s`、`max_page_kb`、`max_page_chars`、`max_clip_chars`、`max_clips`、`search_results`、`search_estimate_usd`）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/clips` | 资料夹清单、搜索是否可用 |
| POST | `/api/clips/open` | `{url}` → `{title, url, site, blocks, links, chars, hint?}` |
| POST | `/api/clips/search` | `{query}` → `{hits: [{title, url, snippet}], cost_usd}` |
| POST | `/api/clips` | `{url, title, text, partial, note}` 存一份资料 |
| GET | `/api/clips/{id}` | 资料全文 |
| DELETE | `/api/clips/{id}` | 删除 |
| POST | `/api/clips/{id}/attach` | 新建附件，返回与 `POST /api/uploads` 相同的附件信息 |
