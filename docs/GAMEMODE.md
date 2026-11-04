# 游戏模式：玩游戏时圆桌让出显卡

你在玩游戏、或别的程序在用显卡时，圆桌自动停掉要用本机显卡的活；显卡**连续空闲 5 分钟**后再逐个补跑。
云端聊天、讨论照常，不受影响。代码在 `src/roundtable/core/gamemode.py`，配置在 `config/roundtable.yaml` 的 `gamemode`。

## 什么时候算「忙」（任一）

| 判断 | 怎么认 | 开关 |
|---|---|---|
| 手动 | 顶栏「游戏」→「现在就开游戏模式」 | 网页上 |
| 在跑游戏 | 「关于你」的游戏表（配置 + 你自己加的）+ Steam / Epic 装的（`steamapps\common\<游戏>`、`Epic Games\<游戏>`），不管在不在前台；启动器、壁纸等见 `not_games` | `detect_games` |
| 前台全屏 | 前台窗口盖满整个显示器（全屏看视频也算） | `detect_fullscreen` |
| 显卡占用 | NVIDIA 驱动报的占用 ≥ `busy_util_pct`（默认 40%） | 设 0 关掉 |
| 别的程序占显存 | 不是圆桌自己（`own_processes`，默认 Python 服务）的程序占的显存 ≥ `busy_vram_mb`（默认 1.5 GB）；Windows 普通显示模式下驱动常常不给每个程序的显存，这时这项不看 | 设 0 关掉 |

`auto: false` 时只认手动开关。圆桌自己在用显卡（本地出图、本地配音）时和做完后 `own_grace_seconds`（60 秒）内不看占用，免得把自己当成别人。
读到的程序名只在内存里用来判断，不写数据库、不进日志。读显卡用 `nvidia-ml-py`（只在 Windows 上装，调用驱动自带的 nvml.dll）；读不到时只看游戏和全屏。

## 忙时做什么

- **用显卡的渠道不调用**：`config/models.yaml` 里 `gpu: true` 的渠道（现在是本地 ComfyUI 出图、本地配音）在路由里直接跳过、换下一个模型（只看配置，不看是哪个渠道）。不算出错，不冷却；渠道状态里「上次跳过原因」写「游戏模式：……」。
- **朋友圈配图**（`photos_when_busy: defer`，默认）：不改走付费模型。还有不花钱、不用显卡的模型（如 ChatGPT 网页）就用它；没有就先发文字，图放进排队，显卡空了补画并挂回原来那条动态。设成 `cloud` 则照旧换付费模型。
- **本地配音**：换下一个语音模型（云端音色），不排队（聊天里的语音要马上念）。
- **CPU**（`cpu_when_busy`）：`low`（默认）把圆桌自己降到最低优先级（只在 Windows 上，不忙时恢复）；`pause` 再停掉后台的生活、革新例会、管家、资料周报（聊天和讨论照常）；`normal` 不管。

## 排队与补跑

- 排队存在数据库旁边的 `gpu_queue.json`（不进 git），重启后还在；最多 `queue_max` 件，满了丢最早的。
- 服务每 `poll_seconds`（10 秒）看一次；显卡连续空闲 `idle_minutes`（5 分钟）后每次补跑一件。补跑时显卡又忙了，这件放回队首。
- 顶栏「游戏」按钮旁的小点亮着 = 正在让路；面板里看原因、显卡读数和排队的活。

## 接口

- `GET /api/gamemode`：状态（`busy`、`reasons`、`gpu_free`、`free_in_s`、`gpu`、`cpu_lowered`、`queue`）。
- `PUT /api/gamemode` `{"manual": true|false}`：手动开关（存 `app_settings` 的 `gamemode_manual`）。

## 以后接入新的显卡活

- 新的本地服务：渠道配置写 `gpu: true`，路由自动让路。
- 后台要等显卡的活：`rt.gamemode.defer(种类, 一句话说明, 参数)`，服务启动时 `rt.gamemode.register(种类, 处理函数)`；处理函数里显卡又忙了就抛 `StillBusy`。
