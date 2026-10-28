# 命令行试用（Windows PowerShell）

用真实模型跑完整的圆桌：作答 → 互评 → 修订 → 汇总 →（必要时升级）→ 揭晓。
揭晓前，终端上不会出现任何模型名、厂商名或渠道名。

## 1. 安装（只需一次）

```powershell
cd 项目目录
python -m venv .venv
# 如果提示"禁止运行脚本"，先执行：Set-ExecutionPolicy -Scope Process RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install -e .
```

**网页版一键启动**：双击项目根目录的 `start.bat`。它会进入项目目录、激活 `.venv`（没有时自动创建）、`git pull`、`pip install -e ".[dev]"`、停掉 8000 端口上旧的圆桌服务（只停 python 进程，别的程序占用时报错退出）、启动服务，并自动打开 http://127.0.0.1:8000 。拉取代码时不会等密码窗口（失败就用本地代码启动）。关闭窗口或按 Ctrl+C 停止；后台启动、没有窗口的服务用 `stop.bat` 停掉。

**代码运行环境**（让成员能运行 Python、画图、生成 Word / Excel / PPT / PDF）：`python scripts/setup_sandbox.py` 一次性安装（`start.bat` 会自动安装）。没装时成员仍可写文本文件、生成图片，只是不能运行代码。详见 `sandbox/README.md`。

**联网搜索**：默认使用 OpenRouter 自带的联网搜索（用已有的 `OPENROUTER_API_KEY`，不需要新账号），成员可以搜索，答案里用 [S1] 这样的编号注明来源（题目和搜索词会发给搜索服务）。成员也可以读取搜索结果的网页正文（同样走 OpenRouter，正文由检索用的模型转述）。填写 `TAVILY_API_KEY` 后，Tavily 作为备选，在 OpenRouter 出错时直接取回原文。

## 2. 填写 key

```powershell
Copy-Item .env.example .env
notepad .env
```

所有 key 都是可选的，填了哪个渠道就用哪个。只填 `OPENROUTER_API_KEY` 就能用全部模型。
填完后检查：

```powershell
roundtable models
```

✓ 表示该模型可用，并列出它能走的渠道；最后一行是本月 / 今日的预算使用情况。

## 3. 提问

```powershell
roundtable ask '求函数 f(x)=x^3-3x 在 [-2,2] 上的最大值和最小值'
```

- **题目请用单引号**。PowerShell 会把双引号里的 `$x` 当成变量，数学题里的 `$` 会被吞掉。
- 题目很长或有多行时，写进 UTF-8 文本文件：`roundtable ask --file 题目.txt`
- 直接运行 `roundtable ask` 会提示你输入题目。
- 默认是**办公室模式**、节电档位：经理（`office_role: manager` 的模型）先分诊，决定怎么办，开始前会显示预计花费和其他档位的预估。
  - **单人办**：派一位员工办理，经理过目，不合格退回改一次。
  - **开会**：经理点名 2～3 位员工按随机顺序依次发言，经理拍板写结论和会议纪要。
  - **项目分工**：经理点名 2～4 位员工组成项目组，另开一张「项目组」桌子走协同流水线（拆分 → 分工 → 交叉审查 → 合并），最后由另一位经理复核。
  - 分诊后打印**预算单**（按选定的方式和人数估价）；开会或项目分工超过单件上限时先问你：照办 / 改成单人办 / 停止。
  - 想用原来的全员讨论，加 `--mode discussion`。

常用选项：

| 选项 | 作用 |
|---|---|
| `--tier flagship` | 全力模式（旗舰档全员）上桌（默认 `budget` 节电模式） |
| `--models gpt-6-luna,qwen3.8-flash,deepseek-v4.1-flash --coordinator deepseek-v4.1-flash` | 自选上桌的模型（至少 3 个），可指定其中一个当统筹（id 见 `roundtable models`） |
| `--attach 文件` | 附件，可重复使用：图片（png / jpg / webp / gif）、PDF、Word `.docx`、文本（txt / md / csv）、mp3 / wav 音频。图片会给能看图的模型发原图、给其他模型发文字版；音频先转写成文字 |
| `--mode office` | 办公室模式（默认）：经理分诊后单人办、开会或项目分工，见上 |
| `--mode collab` | 协同模式：统筹拆分子任务 → 成员自荐 → 分配（每人至少一块）→ 分工完成 → 交叉审查 → 修改 → 合并成完整成果（默认 `discussion` 讨论模式：全员各自作答再互评汇总） |
| `--anonymous` | 匿名：结束前成员只显示塔罗牌代号（愚者、魔术师…），结束后可揭晓（默认不匿名：成员叫"昵称·模式"，如 鲸鱼娘·全力，同时显示模型名）；token 用量显示为"大米"（粒 / 勺 / 碗），金额仍是美元 |
| `--details` | 显示每位组员的答案、评审和修订稿（办公室模式：开会时每位员工的发言） |
| `--yes` | 单题花费 / 升级确认自动选"继续"（预算确认仍会询问） |
| `--reveal` / `--no-reveal` | 匿名讨论结束后直接揭晓 / 不揭晓（默认结束时询问） |
| `--seed 42` | 固定随机种子，便于复现 |
| `--raw` | 保持模型输出的原样（默认会把 LaTeX 转成易读的纯文本，如 `\frac{1}{2}` → `1/2`、`x^2` → `x²`，并去掉 `**加粗**` 符号） |

需要确认时会列出选项和各自的预计花费，输入序号回车；**直接回车选推荐项**。

成员的产出没有实质内容（空话、拒答、过短、只复述题目、照抄别人、不回应评审）时会自动打回重做一次，进度里显示"打回重做"；仍不合格会标记"敷衍"。结果末尾的【贡献】列出每位成员被采纳的要点、指出的有效问题等。

## 4. 之后

```powershell
roundtable estimate '题目'      # 提交前预估：讨论 / 协同 × 各档位的上桌人数、预计与最多花费、步骤明细（不调用模型、不花钱）
roundtable history              # 最近的讨论（含会话 id）
roundtable performance          # 办公室模式的员工绩效：单人办 / 开会 / 项目分工的件数、被退回、通过率、平均工资与用时
roundtable performance --reset  # 绩效清零：之后只统计从现在起的会话（不删除任何记录）
roundtable stats                # 各模型的历史贡献：被采纳的要点、有效问题、敷衍次数等（匿名讨论揭晓后才计入）
roundtable mindmap <会话 id> -o 导图.svg   # 保存头脑风暴的思维导图（.svg 图 / .md 点子榜 + 大纲 / .mmd Mermaid）
roundtable evolve                       # 革新例会：开关、资料卡、本月花费、最近的例会（见 docs/evolution/README.md）
roundtable evolve scout                 # 圆桌自己找资料写卡（白名单网站，一位成员写、另一位复核）
roundtable evolve meet                  # 开一场革新例会（大 / 小版本按时间自动判断，--major / --minor 手动改），开完导出采纳单
roundtable evolve export <会话 id>      # 导出某场例会的采纳单
roundtable bench [--modes office:budget,solo:budget,discussion:budget,collab:budget] [--max-usd 2] [-o 结果.csv]   # 对比实验；--list 列历次，--report 批次名 看汇总；vote:budget[:次数] 是自洽投票基线（个人作答跑几次取多数）
roundtable show <会话id> --details
roundtable show <会话id> --costs      # 每桌每步的预估与实际花费、每次调用的 token
roundtable files <会话id>            # 把成员生成的文件（代码、图表、文档等）保存到本地目录
roundtable export <会话id>           # 完整记录（含每位成员的产出和花费明细）导出成 UTF-8 文本文件
roundtable export <会话id> -o 记录.txt
roundtable reveal <会话id>      # 匿名讨论：揭晓身份、每次调用走的渠道和花费
roundtable resume <会话id>      # 中断（Ctrl+C、断网）后继续，已完成的步骤不会重复
```

记录保存在 `data/roundtable.db`（不会提交到 git）。

## 中文显示乱码时

命令行输出统一用 UTF-8（包括用 `>` 或 `|` 重定向时）。要把结果保存成文件，推荐直接用
`roundtable export <会话id>`，它自己写文件（带 BOM 的 UTF-8，记事本能正确打开），不经过 PowerShell 的编码转换。

Windows Terminal + PowerShell 7 一般不需要处理。旧版控制台出现乱码时先执行：

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

## 网页版

```powershell
uvicorn roundtable.api.app:app
```

然后在浏览器打开 <http://127.0.0.1:8000>。网页与命令行共用同一个数据库和预算：在网页里能看到命令行跑过的讨论（「历史」标签），反之亦然。
讨论进行中可以关掉页面，地址栏里带着会话 id（`#s=…`），重新打开该地址即可回到原讨论；已暂停的讨论点「继续」。
默认只监听本机。要在家里的平板 / 手机上用，双击 `start_lan.bat`（需要设访问口令，见 [LAN.md](LAN.md)）；不要只加 `--host 0.0.0.0`——没有 `ROUNDTABLE_LAN=1` 时别的设备会被拒绝。
