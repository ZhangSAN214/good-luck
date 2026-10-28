"""桌面版（Windows / macOS）：双击图标启动圆桌，自带窗口，关窗口即停止服务，没有黑色命令行窗口。

- launcher.py：启动本机服务、打开窗口（pywebview → Edge / Chrome 应用窗口 → 默认浏览器）
- install.py：在桌面 / 开始菜单生成快捷方式（Windows），或在「应用程序」里生成 .app（macOS）

不属于 core/：可以 import uvicorn；业务逻辑仍只经 api → service。说明见 docs/DESKTOP.md。
"""
