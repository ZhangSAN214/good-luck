# dsh 工作台

圆桌在电脑上管一个 dsh（DeepSeek Harness）进程当「边车」，dsh 的插件在它里面跑，圆桌在分段栏「工作台」页的「dsh」标签里把 dsh 的网页嵌进来。

## 硬性原则

1. **圆桌是主体，dsh 是可拔掉的外设。** 只有 `core/service.py` 可选地导入 `core/dsh/`；把 `src/roundtable/core/dsh/`、`config/dsh_plugins.yaml`、`scripts/setup_dsh.py` 删掉，圆桌照常运行（CI 的 `unplug-dsh` 任务每次都这样验证）。
2. **dsh 不写圆桌的数据。** dsh 的运行时、家目录、工作目录、花费记录都在 `data/dsh/`（不进 git）；删掉 `data/dsh/` 就是卸载。
3. **控制只从圆桌流向 dsh。** 圆桌启动、停止、查状态；dsh 不调用圆桌的接口。
4. **dsh 不带人格。** 白名单里不收人设类插件；叠加配置层把 dsh 的身份换成中立的「工作台助手」（提示词 `dsh_workbench/v1`）。圆桌的成员不在工作台里出现。
5. **只装白名单里的插件。** `config/dsh_plugins.yaml` 写明每个插件的下载地址、sha256、版本、许可证和审查过的提交；下载后核对哈希，不对就拒绝安装。
6. **依赖到一定程度就自己重写。** 某个 dsh 插件用得多、圆桌离不开时，按插件榜的门槛（1000 星以上才搬代码，否则借思路）在圆桌里原生实现，dsh 那份就可以拔掉。

## 安装（在电脑上，一次）

需要 Node 22 以上、npm、pnpm（`npm install -g pnpm`）。

```bash
python scripts/setup_dsh.py --check   # 只核对 Node / npm / pnpm、运行时文件和版本、插件安装包
python scripts/setup_dsh.py           # 装 dsh 运行时（约 0.9 GB）和白名单插件；装完逐项核对，没过就把运行时整批退回（docs/FIRSTAID.md）
```

安装时不使用任何 key。装好后在 `.env` 里放 `DEEPSEEK_API_KEY`（在 DeepSeek 官网充值；不经过 OpenRouter），重启圆桌，点分段栏「工作台」→「dsh」→「启动」。

## 运行方式

- 启动参数：`node <运行时>/lib/bin.js --profile web --patch data/dsh/overlay.yml --no-open --host 127.0.0.1 --port 3080`，工作目录 `data/dsh/work/`。
- 子进程的环境变量从空白开始，只带系统必需的几个、`DSH_HOME`、`DSH_TELEMETRY_DISABLED=1`、`DSH_PERMISSION_MODE`（默认 `read-only`）和 dsh 自己的 key；圆桌的其他 key 一个都不给。
- 叠加配置层（`data/dsh/overlay.yml`，每次启动重写）：关掉会话日志上传、插件清单上报、遥测；把身份换成中立提示词。
- dsh 打印的网页地址带一次性口令，只在内存里；日志里替换成 `***`。网页地址只发给电脑本机的浏览器，平板和手机上只能看状态（dsh 只监听 127.0.0.1）。

## 花费

dsh 直接用 DeepSeek 官方 key，钱不经过圆桌，所以圆桌按**余额差额**估算：启动时记一次余额，之后每 5 分钟（`dsh.balance_check_minutes`）查一次，余额下降算作花费，充值不算；如果圆桌自己的渠道也用同一个 key，把渠道名写进 `dsh.shared_channels`，这部分花费会从差额里扣掉。

每月软上限 `dsh.monthly_usd`（默认 $3，UTC 自然月）：超过就停掉 dsh，下个月 1 号恢复。因为查余额有间隔，可能多花几毛。余额接口返回的各币种（比如人民币和一条 0 美元）都按 `dsh.usd_rates` 折成美元加起来。页面上的花费、上限和余额默认按人民币显示（`dsh.display_currency`，可改成 `USD`），上限本身仍按美元算。

## 已知不足

- worktable 的终端窗格相当于一个 PowerShell，不受 dsh 只读模式约束，用它跑命令等于在电脑上直接跑。
- 工作台只能在电脑本机上用；平板和手机看不到 dsh 的网页。
- 上限是软的：靠查余额发现，不能精确到分。
- 工具桥（成员调用 dsh 插件的工具）和事件桥属于后续的 A2、A3，默认不做。
