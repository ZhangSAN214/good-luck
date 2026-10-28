# 桌面版（Windows / macOS）

双击图标打开圆桌：自带一个窗口，没有黑色命令行窗口；关掉窗口，服务也跟着停。代码和数据照旧在原来的目录（例如 `E:\good-luck`），只是多了图标入口。

## 安装（第一次约 5～10 分钟，大部分时间在下载依赖）

**Windows**

1. 先关掉正在运行的黑色圆桌窗口（或双击 `stop.bat`）。
2. 双击项目根目录的 `install_desktop.bat`。它会 `git pull`、安装依赖（多装一个应用窗口组件 pywebview）、安装代码沙箱，再在**桌面和开始菜单**各放两个图标：
   - **圆桌**：只有这台电脑能用（和 `start.bat` 一样）。
   - **圆桌（平板可连）**：同时让家里 Wi-Fi 下的平板 / 手机连进来（和 `start_lan.bat` 一样，见 [LAN.md](LAN.md)）。
3. 以后双击桌面上的「圆桌」即可。可以右键图标「固定到任务栏」。

**macOS**（需要先装 Python 3.11 以上：<https://www.python.org/downloads/macos/>）

1. 把仓库克隆到 Mac 上，复制一份 `.env`（key 只放在这里）。
2. 在访达里双击 `install_desktop.command`（第一次如果提示「无法打开」，右键 → 打开）。它会在「应用程序」（个人目录下的 `Applications`）里生成「圆桌.app」和「圆桌（平板可连）.app」。
3. 打开「圆桌」，可以拖到程序坞。

`.app` 是在你自己的 Mac 上生成的小外壳（指向代码目录），不是从网上下载的，所以**不需要苹果开发者账号、签名或公证**。只有要把做好的安装包发给别人用时才需要（每年 99 美元），现在不做。

## 更新

再双击一次 `install_desktop.bat`（Mac 上是 `install_desktop.command`）：会 `git pull`、重装依赖、重建图标。更新前先关掉圆桌窗口。

## 它是怎么工作的

- 图标运行的是 `.venv` 里的 `pythonw -m roundtable.desktop`（Mac 上是 `.venv/bin/python`），在同一个进程里起服务，就绪后打开窗口。
- 窗口优先用 pywebview（Windows 是系统自带的 Edge WebView2，Mac 是系统 WebKit）。没装上时改用 Edge / Chrome 的「应用窗口」（没有地址栏），都没有时用默认浏览器打开（这时关浏览器不会停服务，要双击 `stop.bat`）。
- 窗口里的浏览器数据存在 `data/desktop/`：立绘位置、背景音乐、时钟这些「只存本机浏览器」的设置会保留，但和你平时用的 Edge / Chrome 里的是**两份**，第一次打开需要重新设一下。
- 8000 端口上已经有圆桌在跑（比如 `start.bat` 开的）时，图标只开一个窗口连过去，关窗口不会停它。8000 被别的程序占用时自动换到 8001～8010。
- 没有命令行窗口，日志写在 `data/logs/desktop.log`；启动失败时会打开一页说明原因。
- 关掉窗口 = 停止服务，成员的「生活」和每周例会这些后台任务也随之暂停（和关掉黑色窗口一样）。

`start.bat`、`start_lan.bat`、`update_and_start.bat` 都照旧可用。

## 卸载

删掉桌面 / 开始菜单里的「圆桌」快捷方式（Mac 上把「应用程序」里的两个 `.app` 拖进废纸篓）即可，代码和数据不受影响。

## 测试

- `tests/desktop/`：选端口、识别已在运行的圆桌、窗口降级顺序、Mac `.app` 结构、Windows 快捷方式参数。
- `.github/workflows/desktop.yml`：在 GitHub 的真 Windows 和 macOS 机器上安装、`python -m roundtable.desktop --check`（起服务、确认就绪、退出，不开窗口）、生成快捷方式 / `.app` 并检查。只在桌面版相关文件变动的 PR 上跑。没有 Mac 也能靠它确认 Mac 版能装能起。
