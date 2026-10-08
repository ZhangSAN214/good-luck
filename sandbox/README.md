# 代码运行沙箱

成员可以申请运行 Python 代码（画图、计算、生成 Word / Excel / PPT / PDF 等文件）。代码只在隔离环境中运行：
没有网络，看不到 `.env`、项目文件、系统文件和环境变量，只能读写本次的临时工作目录（`in/` 附件、`out/` 生成的文件），
限时、限内存、输出截断。没有可用的沙箱时 python 工具自动关闭（不会退回到直接在本机运行）。

## wasm 后端（默认，推荐）

Pyodide（WebAssembly 版 CPython）跑在 Deno 里，Windows / macOS / Linux 都能用，不需要 Docker。一次性安装：

```bash
python scripts/setup_sandbox.py           # 下载 Deno 与 Pyodide（约 60 MB；CDN 不可用时约 340 MB）
python scripts/setup_sandbox.py --check   # 检查能否运行
```

安装到用户缓存目录（Windows：`%LOCALAPPDATA%\roundtable\sandbox`；其他：`~/.cache/roundtable/sandbox`），
不在项目目录里。`start.bat` 第一次启动时会自动安装。

## docker 后端（可选）

```bash
docker build -t roundtable-sandbox:1 sandbox/
```

然后在 `config/roundtable.yaml` 中把 `tools.python.backend` 设为 `docker`（`auto` 时先用 wasm，没有再用 docker）。

## 越权测试

`tests/core/tools/test_sandbox_escape.py` 在真实沙箱里尝试读 `.env`、项目源码、系统文件、环境变量里的 key，
联网、启动子进程、写到工作目录之外、放符号链接、死循环、占满内存、超长输出，全部必须失败或被拦下。
本机没有对应后端时这些测试自动跳过。
