# 整库备份（成员自主成长 G0）

成员的记忆、日记、关系、对你的看法、成长时间线、聊天记录、朋友圈……全在 `data/roundtable.db` 这一个 SQLite 文件里。
硬盘坏了、误删、或者某次更新把数据弄乱，都能从这里恢复。全部在本机做，不联网、不花钱。

## 什么时候备份

| 种类 | 什么时候 | 留几份 |
|---|---|---|
| 每天一份 | 每个本地日期第一次开着圆桌时（那天没开电脑就跳过），开着时每 `check_minutes` 分钟看一眼 | `keep_daily`（默认 14） |
| 存档点 | `update_and_start.bat` 拉代码前自动存（标签 `update`，网页上显示「更新前」）；恢复前自动存（`恢复前`）；网页或命令行手动存 | `keep_checkpoints`（默认 10），和每天的分开数 |

文件放在数据库旁边的 `db_backups/`（默认 `data/db_backups/`），名字如
`roundtable-daily-20261102.db`、`roundtable-checkpoint-20261102-093000-试跑前.db`。
用 SQLite 自己的在线备份接口，服务开着也能备份，不会备出半截文件；先写 `.part` 再改名。

## 怎么恢复

- **网页**：顶栏「备份」→ 列表里选一份 →「恢复这份」→「确定恢复」。只能在电脑上用，平板上会被拒绝。
- **命令行**：先关掉圆桌，再
  ```bash
  roundtable backup                      # 看列表
  roundtable backup restore 文件名        # 会让你输入 yes
  ```

恢复会：①校验这份备份（能打开、`PRAGMA integrity_check` 通过、是圆桌的库、版本不比程序新）——不通过就什么都不动；
②自动存一个「恢复前」存档点（反悔就恢复它）；③把备份整个写回正在用的数据库，旧备份缺的表结构自动补上迁移。
**恢复完要关掉圆桌再打开**，让各模块重新读取。

## 其他命令

```bash
roundtable backup now                  # 马上备份（今天已经有了就存一个「手动」存档点）
roundtable backup checkpoint 试跑前     # 存一个带标签的存档点
```

## 不在备份里的

只备份数据库本身。下面这些是数据库旁边的文件夹，体积大、丢了也不影响成员的记忆和关系，需要时手动复制整个 `data/`：
`uploads/`、`chat_media/`（你发的图片视频语音）、`stickers/`（表情包图片）、`voice_cache/`、`perception/`、`growth_backups/`（成长每周的设定备份，另有一份）。

## 设置

`config/roundtable.yaml` 的 `backup`：`enabled`（每天自动备份，关了仍能手动存和恢复）、`keep_daily`、`keep_checkpoints`、`check_minutes`。
