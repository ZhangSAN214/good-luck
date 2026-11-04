# 把圆桌搬到云服务器（阿里云轻量服务器）

圆桌常驻在你自己的云服务器上，24 小时开着；电脑、手机、平板通过 **Tailscale** 打开它。
服务器不对公网开放网页端口：只有你 Tailscale 账号里的设备能连，连上后还要输访问口令（和 [LAN.md](LAN.md) 同一套登录）。

适用：Ubuntu 22.04 / 24.04，2 核 2 GiB 内存起步，40 GiB 硬盘。

## 哪些搬上去、哪些留在电脑

| 功能 | 服务器 | 说明 |
|---|---|---|
| 网页、讨论 / 协同 / 头脑风暴、群聊私聊、朋友圈、生活、公寓、日历、公告 | ✅ | 都走 API，空闲时占内存约 180 MB |
| 联网搜索、读网页、附件（PDF / Word / 图片） | ✅ | |
| 代码运行沙箱（python 工具） | ✅ | 每次运行另占约 200～300 MB，靠 4 GB 交换空间兜底 |
| 图片 / 语音 / 视频生成（API 渠道） | ✅ | |
| ComfyUI 出图、本地配音（IndexTTS） | ❌ 留电脑 | 要显卡。以后可以让服务器经 Tailscale 调电脑上的（另做） |
| ChatGPT 网页画图（朋友圈配图） | ❌ 留电脑 | 要登录过的浏览器；机房 IP 常被网页拦截 |
| 游戏模式、音乐卡片读 QQ 音乐、投屏一起看、桌面版窗口、邮件栏、dsh、超级电脑 | ❌ 留电脑 | 只能读 / 控制电脑本机 |
| 感官本地工具（SenseVoice、RapidOCR 等，计划中） | ⚠️ 勉强 | 一次只跑一个、靠交换空间会很慢；建议走百炼 API 或留电脑 |

服务器在伦敦：OpenRouter、OpenAI、Anthropic、Gemini、xAI、DeepSeek 都能直连，Gemini 官方直连在英国可用。
百炼（阿里云国内站，北京）也能连，每次请求多约 0.2～0.3 秒，不影响使用；国际站（新加坡）的 key 和国内站不通用，填哪个站的 key 就用哪个站的地址。

**数据位置**：搬过去以后，聊天记录、记忆和数据库存在**你自己的服务器**上（不是第三方服务，不上传、不用于训练），
电脑上不再是主库。两边不要同时开圆桌各自写库，会分叉。

## 第一次安装（约 30～45 分钟，大部分是自动运行）

### 1. 打开服务器终端（约 2 分钟）

阿里云控制台 → 轻量应用服务器 → 你的服务器 →「远程连接」→ 用 Workbench 登录（root）。
**任何密码、key 只在这个终端或你自己电脑上输入，不要发到聊天里。**

### 2. 一键安装（约 10～15 分钟，自动运行）

```bash
curl -fsSL https://raw.githubusercontent.com/ZhangSAN214/good-luck/ccr-86975127-sv4cvy/deploy/server/install.sh | bash
```

它会：装系统软件 → 加 4 GB 交换空间 → 建专用用户 `roundtable` → 下载代码到 `/opt/roundtable` → 装依赖和沙箱 →
装 Tailscale → 防火墙只放行 SSH 和 Tailscale → 装成开机自启的服务。可以重复运行。

### 3. 连上 Tailscale、填 key（约 5 分钟）

```bash
tailscale up                       # 打印一个网址：在电脑浏览器打开，登录和电脑同一个 Tailscale 账号
tailscale ip -4                    # 记下服务器的 100.x.y.z 地址
nano /opt/roundtable/.env          # 填 key：Ctrl+O 回车保存，Ctrl+X 退出
systemctl restart roundtable
```

电脑和手机还没装 Tailscale 的，先按 [LAN.md](LAN.md)「离开家里 Wi-Fi 也想用：Tailscale」装好、登录同一个账号。

### 4. 设访问口令（约 3 分钟）

设口令只能在「本机」做。在**电脑**的 PowerShell 里开一条 SSH 隧道（把地址换成第 3 步的 `100.x.y.z`；
第一次要用阿里云控制台「重置密码」给 root 设个密码，在这里输入）：

```powershell
ssh -L 8001:127.0.0.1:8000 root@100.x.y.z
```

保持这个窗口开着，电脑浏览器打开 `http://127.0.0.1:8001`（这时服务器把你当本机），顶部点「平板」设口令。
设好后关掉隧道窗口。以后电脑、手机、平板都打开 `http://100.x.y.z:8000`，输口令或扫「平板」面板里的二维码登录。

### 5. 搬数据（约 5～15 分钟，看数据多少）

1. 电脑上先**关掉圆桌**（stop.bat），在 `E:\good-luck` 里运行：

   ```powershell
   .venv\Scripts\python scripts\pack_server_data.py
   scp data\_cloud\server_pack.zip root@100.x.y.z:/root/
   ```

   包里有数据库快照、头像立绘、表情包、聊天图片、成长备份等；不带浏览器登录状态、局域网口令、截图、日志、缓存和旧备份，也不含 `.env`。
2. 服务器上：

   ```bash
   systemctl stop roundtable
   cd /opt/roundtable && sudo -u roundtable unzip -o /root/server_pack.zip && rm /root/server_pack.zip
   systemctl start roundtable
   ```

3. 打开 `http://100.x.y.z:8000` 看历史会话、成员和朋友圈都在。之后电脑上**不要再用 start.bat 开圆桌**（会写到电脑的旧库）。

## 日常

| 做什么 | 命令（服务器终端，root） |
|---|---|
| 更新到最新代码 | `bash /opt/roundtable/deploy/server/update.sh`（先存档点，冒烟测试不过自动退回） |
| 看状态 / 日志 | `systemctl status roundtable`、`journalctl -u roundtable -n 100` |
| 重启 | `systemctl restart roundtable` |
| 整库备份 | `sudo -u roundtable /opt/roundtable/.venv/bin/roundtable backup now`（见 [BACKUP.md](BACKUP.md)） |
| 看内存 | `free -h` |

## 安全

- 网页端口 8000 不对公网开放：防火墙不放行，而且 `ROUNDTABLE_LAN=tailscale` 让公网来的请求一律 403。
  阿里云控制台的「防火墙」里也**不要**加 8000 端口。
- **不要**加 nginx 反向代理、也不要用 Tailscale Serve / Funnel：经本机转发时所有请求看起来都来自本机，会绕过登录。
- 服务用专用用户 `roundtable` 跑，不用 root；`.env` 权限 600。
- 能 SSH 登录服务器的人等于能完全使用圆桌：root 密码设长一点，或改用密钥登录。
