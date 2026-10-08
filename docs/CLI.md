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

常用选项：

| 选项 | 作用 |
|---|---|
| `--preset saver` / `balanced` / `strongest` | 预设：省钱 / 均衡 / 最强（不按难度自动选） |
| `--members gpt-6-luna,qwen3.8-flash --coordinator deepseek-v4.1-flash` | 手动指定组员和统筹（id 见 `roundtable models`） |
| `--details` | 显示每位组员的答案、评审和修订稿 |
| `--yes` | 单题花费 / 升级确认自动选"继续"（预算确认仍会询问） |
| `--reveal` / `--no-reveal` | 结束后直接揭晓 / 不揭晓（默认结束时询问） |
| `--seed 42` | 固定随机种子，便于复现 |

需要确认时会列出选项和各自的预计花费，输入序号回车；**直接回车选推荐项**。

## 4. 之后

```powershell
roundtable history              # 最近的讨论（含会话 id）
roundtable show <会话id> --details
roundtable reveal <会话id>      # 揭晓身份、每次调用走的渠道和花费
roundtable resume <会话id>      # 中断（Ctrl+C、断网）后继续，已完成的步骤不会重复
```

记录保存在 `data/roundtable.db`（不会提交到 git）。

## 中文显示乱码时

Windows Terminal + PowerShell 7 一般不需要处理。旧版控制台出现乱码时先执行：

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```
