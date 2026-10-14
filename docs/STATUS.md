# 分支与进度总览（Roundtable + 小人）

更新：2026-10-14。这份文件把「圆桌平台」和「圆桌智能体拟人立绘 / 小人」两条线合在一起看；步骤编号以 `docs/ROADMAP.md`（R0～R16）为准，阶段编号（0～36）以 `docs/PROGRESS.md` 为准。

## 1. 分支现状（2026-10-14，PR #3 合并后重新核对）

```
main (fdd82b0，另有 1 个提交：xuanji.html)
  └─ ccr-86975127-sv4cvy  (5da2f4c，已含 PR #3；比 main 多 55 个提交)
       ├─ 已并入：track/agent-claude-behavior、agent-avatars、slim-claude-md-djgom1
       └─ roadmap/auto  (fedb05f)：比开发分支多 4 个提交，全是 R4 —— 没有并入
```

**更正**：之前写的「roadmap/auto 已被包含」不对，是本地分支引用过期造成的。每日自动任务在 2026-10-13 又往 `roadmap/auto` 推了 4 个 R4 提交（8352845、37525d3、ea715e5、fedb05f），内容：

- `scripts/check_pet_assets.py` + `tests/scripts/test_check_pet_assets.py`（素材检查与 pet.json 生成）
- `docs/agent/差分清单.md`、`docs/agent/桌面版方案.md`（与开发分支里的同名文件内容不同）
- `docs/ROADMAP.md`、`docs/tracks/agent.md` 的 R4 记录

开发分支里已经有另一套 R4：`scripts/check_pet_art.py`、`gen_pet_frames.py`、同名的差分清单和桌面版方案。**两套做的是同一件事、文件名不同（`check_pet_assets.py` ↔ `check_pet_art.py`），合并会得到两份重复的检查脚本和两份互相冲突的文档。**

其他分支：
- `agent-avatars`、`slim-claude-md-djgom1`：已并入，可以删。
- `track/agent`、`track/collab`、`track/groupchat`（及 `worktree-agent-*`）：停在旧提交，没有新工作。
- `main` 比开发分支多一个提交 `fdd82b0`（上传 `xuanji.html`）。
- 规则：自动步骤不得推送到开发分支或 `main`；合并由用户决定。

### 需要你决定
1. R4 保留哪一套：（a）保留开发分支里已有的（建议，已经测试通过并合并），把 `roadmap/auto` 的 4 个提交当作重复丢弃，删除分支前先打一个标签保存；（b）取 `roadmap/auto` 的检查脚本替换；（c）两套都留。
2. 之后：是否把开发分支并入 `main`（先把 `xuanji.html` 带进来）；是否删除上面列出的已并入 / 空壳分支。

## 2. 进度（R 步骤）

| 线 | 步骤 | 状态 |
|---|---|---|
| 小人 | R1 头像接入 | 已完成（PR #2） |
| 小人 | R2 网页小人动起来 | 已完成 |
| 小人 | R3 / R3.1 汇报与互动、特殊待机、彩蛋 | 已完成，待你在本机试用 |
| 小人 | R4 差分素材：清单、检查脚本、生成脚本、桌面版方案 | 开发分支里已有一套；`roadmap/auto` 上还有另一套重复实现（见第 1 节）；克劳德已验收；其余 7 位的图要你在网页端生成 |
| 协同 | R5 中途插话 | 已完成 |
| 协同 | R6 分工讨论 + 进度看板 | 部分完成：下游向上游的澄清提问未做 |
| 协同 | R7 验收与模板 | 已完成 |
| 协同 | R8 降低花费 | 部分完成：「合并不传上游全文」未做（原因见 `docs/COST_ANALYSIS.md`） |
| 平台 | R13 个人作答 | 完成 |
| 平台 | R14 对比实验 `roundtable bench` | 完成，需真实 key 跑一次（`--max-usd 2`）看数据 |
| 平台 | R15 分诊小组 | 完成 |
| 平台 | R16 头脑风暴与思维导图 | 完成（图表仅思维导图） |
| 需你决定 | R0 本机实测、R9～R12（v3：三入口、记忆、群聊） | 等你回答 `docs/PLAN.md` 末尾的 v3 待决问题，确认前不开工 |

## 3. 其他待办

- 小人：举牌子动作的代码、`docs/agent/小人总览.md` §6 的 5 个待决问题；其余 7 位成员的差分图（在网页端用参考图生成，目前卡在参考图上传）。
- 文档：`docs/PROGRESS.md` 里的总体完成度和「分支」仍是修复轮之后的旧写法（62%，分支 `ccr-86975127-sv4cvy`），没有包含 R 步骤；等你确认合并方式后再统一更新。
- 你本机要做的：`update_and_start.bat`、`roundtable bench --max-usd 2`、试用网页小人。
- `data/`（素材、头像）永远不进 git。
