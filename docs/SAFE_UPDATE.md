# 安全更新

电脑上更新圆桌时：**先备份、只许改白名单、冒烟测试不过就原样退回**。思路学自 dsh-nurse 的说明（AGPL，只学思路，代码自己写）。
和整库备份（docs/BACKUP.md）衔接：用的是同一个备份目录、同一种存档点。

## 更新时（start.bat / update_and_start.bat 自动做）

`python scripts/safe_update.py update`：

1. 记下现在的代码版本和数据库版本，存「更新前」存档点（标签 `update`）。**存档点没存成就不更新**，照旧用本地代码启动。
2. `git fetch`；有新版本才 `git pull`（start.bat 用 `--ff-only`，update_and_start.bat 用普通合并，都带 `--autostash`）。
3. **白名单**：这次更新改到的文件不许落在 `data/`、`.env`、`.venv/`、`*.db` 上，碰了就退回。
4. `pip install -e`（start.bat 装 `dev,windows`，update_and_start.bat 装 `dev,media`）。
5. **冒烟测试**：把数据库复制到临时目录，另起一个进程用新代码在副本上迁移、建服务、打开首页和几个只读接口。
   正在用的数据库这一步完全不动。
6. 第 3～5 步任何一步没过：`git reset --keep` 回到更新前的版本、重新 `pip install`，**照常启动旧版本**，
   窗口里提示，自检页「想撤销刚才的更新」里写着原因。数据库没动过，不用恢复。

退出码：0 已更新或已是最新；2 没过、已退回；3 没更新成、照旧用本地代码；1 本地代码也装不上（窗口停住）。

## 启动时数据库要升级

新代码第一次启动、数据库要迁移时，先存「迁移前」存档点再迁移。**存档点没存成就不迁移、不启动**
（报错里会说，多半是磁盘满了；运行 `roundtable doctor` 看详情）。新建的空数据库不存。

## 撤销上一次更新

双击 `undo_update.bat`（或先关掉圆桌再 `python scripts/safe_update.py undo`）：

- 只能撤销**通过了冒烟测试**的那次更新。
- 代码 `git reset --keep` 回到更新前（你改过、又和这次更新冲突的文件会让它停下，什么都不动）。
- 这次更新升级过数据库，就先存「撤销前」存档点，再把「更新前」存档点写回（更新后新写的数据在「撤销前」里，反悔能恢复）；
  没升级过就不动数据库。
- 记住撤销的这一版：之后 start.bat 不再自动装它，远端有更新的版本时才更新。

## 记录

`data/update_state.json`：`phase`（begun / ok / rolled_back / failed / undone）、更新前后的 commit、数据库版本、存档点文件名、原因、时间。
不含 key 和任何聊天内容。
