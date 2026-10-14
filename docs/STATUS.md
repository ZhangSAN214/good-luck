# 分支与进度总览（Roundtable + 小人）

更新：2026-10-14。这份文件把「圆桌平台」和「圆桌智能体拟人立绘 / 小人」两条线合在一起看；步骤编号以 `docs/ROADMAP.md`（R0～R16）为准，阶段编号（0～36）以 `docs/PROGRESS.md` 为准。

## 1. 分支现状：其实是一条直线，没有需要合并的分叉

```
main (fdd82b0)
  └─ ccr-86975127-sv4cvy  (+25，你的开发分支；R1 头像 PR #2 已并入)
       └─ roadmap/auto  (+15，R2 小人动起来、R3 / R3.1 汇报与互动)
            └─ track/agent-claude-behavior  (+13，R4 差分工具、R5～R8 协同、R13～R16)  ← 当前最前
```

- `track/agent-claude-behavior` 已经包含 `roadmap/auto` 和 `ccr-86975127-sv4cvy` 的全部提交（合并基点就是 `roadmap/auto` 的顶端 502ba27），所以两个项目的成果已经在同一个分支里。
- `agent-avatars`、`slim-claude-md-djgom1`：内容都已在 `ccr-86975127-sv4cvy` 里（领先 0 个提交），可以删除。
- `main` 比以上分支多一个提交 `fdd82b0`（上传了 `xuanji.html`），其他分支没有它。
- `track/agent`、`track/collab`、`track/groupchat`（以及对应的 `worktree-agent-*`）停在旧提交 f474b88，没有新工作，是并行试验留下的空壳。
- 规则（ROADMAP「分支规则」）：自动步骤不得推送到 `ccr-86975127-sv4cvy` 或 `main`；是否合并由你决定。

### 建议的合并方式（需你确认，我不会自己合并）
1. 在 GitHub 上开一个 PR：`track/agent-claude-behavior` → `ccr-86975127-sv4cvy`（线性，无冲突）。
2. 合并后再决定 `ccr-86975127-sv4cvy` → `main`；`main` 上的 `xuanji.html` 需要先 `git merge origin/main` 带进来。
3. 合并完成后删除：`roadmap/auto`、`agent-avatars`、`slim-claude-md-djgom1`、`track/agent`、`track/collab`、`track/groupchat`、`worktree-agent-*`。

## 2. 进度（R 步骤）

| 线 | 步骤 | 状态 |
|---|---|---|
| 小人 | R1 头像接入 | 已完成（PR #2） |
| 小人 | R2 网页小人动起来 | 已完成 |
| 小人 | R3 / R3.1 汇报与互动、特殊待机、彩蛋 | 已完成，待你在本机试用 |
| 小人 | R4 差分素材：清单、检查脚本、生成脚本、桌面版方案 | 工具与文档完成；克劳德已验收；其余 7 位的图要你在网页端生成 |
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
