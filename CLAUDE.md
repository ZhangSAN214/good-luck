# CLAUDE.md — Roundtable（圆桌）

多个 AI 协作完成作业。长期目标见 `docs/REQUIREMENTS_v3.md`；当前范围以本文件和 `docs/PLAN.md` 为准（v1 已完成，v2 改版阶段 11–20 全部完成（19 媒体生成、20 收尾：限速与并发、README、CI），v2.1 阶段 21 协同流水线 + 昵称 / 塔罗牌代号 / 大米、22 参考图风格与图生图均已完成）。R17 办公室模式（AI 是员工、token 是工资，见 `docs/OFFICE.md`）O1 名册与工资单、O2 经理分诊 + 单人办（默认方式）、O3 开会、O4 项目分工、O5 绩效已全部完成。夜间轮（2026-10-20）新增人才市场（只提示）、公寓楼（只读）、群聊核心（阶段 32 最小版本），见 `docs/PROGRESS.md`。
界面样板：`docs/mockup.html`（v1 只实现其中文字圆桌相关部分）。
按 `docs/PLAN.md` 的阶段推进，不要跳阶段，不要提前实现"后续扩展"里的功能。

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 2. 必守的架构规则（摘要）

完整原文见 `docs/ARCHITECTURE.md`；以下只列硬规则。

- **配置驱动**
  - 加模型 / 加渠道只改配置。禁止针对具体模型、厂商或渠道写分支（`if model_id == ...`、`if vendor == ...`）。
  - `config/routing.yaml` **只能引用档位和能力标签**，不得出现模型名或厂商名（有测试）。
  - 所有配置 pydantic 校验（多余字段报错），失败给出带文件名和字段路径的报错。
- **分层**
  - `core/`：纯业务逻辑，**禁止 import fastapi / starlette / uvicorn / streamlit**（测试守卫）。
  - `api/`：FastAPI 路由 + SSE，只调用 `core/service.py`（有测试检查导入）。
  - `web/` 只通过 HTTP/SSE 与后端通信。
  - 前端源码不得写死任何模型、厂商或渠道名（有测试）。
- **提示词**
  - 代码中不得内联提示词正文。
  - **改提示词 = 新建版本文件**；新增版本后运行 `python scripts/lock_prompts.py` 登记。测试会拒绝已发布版本被修改或删除，也会拒绝未登记的新版本。
  - 提示词中不得出现模型名、厂商名、渠道名（测试从配置中提取名称检查）。
  - 题目、他人答案等外部内容放在标签内（`<question>`、`<answer>` …），系统提示说明标签内的指令无效（防提示注入）。
- **存储**
  - 数据访问只经 `core/storage/`（Repository 模式）。
  - 表结构变更走版本化迁移（`storage/migrations.py`，只能在末尾追加；已发布迁移的哈希登记在测试中，不得修改）。
  - **对外展示只用 `session_view()` / `list_sessions()`**。
- **调用**
  - 适配器本身不重试（SDK 的 `max_retries=0`），重试和切换只由路由负责。
  - **不原样重试**：同一个模型不会收到完全相同的请求两次（测试守卫）。
- **确认**
  - **升级从不自动执行**：满足条件时总是弹卡片（升级 / 采用当前结果 / 停止）。
  - 确认卡片统一为 ConfirmationCard。

---

## 3. 中立性规则（代码保证 + 测试）

1. 同一步骤所有组员使用完全相同的提示词和参数（步骤参数在 `roundtable.yaml` 的 `step_params`；模型自身必需的参数在 `models.yaml`）。协同的 `work` 步骤允许按**子任务类型**覆盖参数（`collab.kinds.<类型>.step_params`，如 `max_tokens`）：同一类型的所有成员完全相同，与成员是谁无关。头脑风暴的 `ideate` 按随机种子给每位组员分一种发散手法（`brainstorm.lenses`），提示词模板与参数相同、只有“你抽到的是 X”一句不同，与成员是谁无关（2026-10-23 用户确认的例外，有换名测试）。
2. **匿名开启时发给模型的内容只用塔罗牌代号**，不出现模型 id、厂商名、昵称；自报身份（"作为 GPT……"、"我是 Claude"）在转给其他模型前遮蔽，但题目和附件里本来就有的名字保留。**匿名关闭时不做任何身份遮蔽**，成员互称"昵称·模式"。
3. 代号与模型的对应每题随机生成（塔罗牌随机抽取，座次随机；昵称随模型走）。
4. **不能自评**：互评时评审者永远看不到自己的答案作为被评对象；协同模式的交叉审查永远不分给作者本人。
5. 每个评审者看到的他人答案顺序独立随机打乱；统筹看到的答案顺序也随机。
6. 统筹不兼任同场组员。
7. **匿名开启时**，发给任何模型的内容不含模型 id、厂商名、昵称；**匿名关闭时**不遮蔽（`IdentityScrubber.bound(context, enabled=)`，引擎按会话的匿名开关给 `TableContext`、媒体服务、工具箱、附件预处理分别绑定）。**匿名开启时揭晓前**，界面和 API 响应也不含模型、渠道、昵称（有测试覆盖事件、会话详情、贡献、发给模型的每条消息）。**渠道名同样会暴露厂商**（如 `anthropic`），因此揭晓前单次调用不显示渠道；用量面板可以显示按渠道汇总的花费。揭晓后每次调用都显示所走渠道和切换记录。
8. 抽座位、选统筹、分配代号、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。
9. **无品牌偏好**：路由与分配只看档位、能力标签和价格（规划员按预计调用成本选最便宜的）；所选档位全员上桌（个人作答模式例外：档位内按价格挑一位，无统筹），统筹在场内均匀随机（轮换）。测试验证：把所有厂商和 id 换名后同一 seed 的结果完全一致；`allocation` / `routing` / `prompts` / `budget` 的源码中不得出现模型名或厂商名。
   **办公室模式例外**（R17，用户 2026-10-19 确认，O2 起生效）：经理由用户在 `models.yaml` 的 `office_role: manager` 指定（代码不写模型名分支），员工按需派活、不全员上桌，派活可参考绩效；规则 1、4、6 照常保留（同类活提示词与参数相同、不自审、经理亲自干的活由另一位经理复核）。旧的讨论 / 协同 / 头脑风暴仍守本条。
10. 匿名开启时，组员输出转给其他模型前遮蔽配置中所有模型 id、厂商名、别称、昵称和渠道上的模型名；题目和附件本身出现的名称保留。匿名关闭时不遮蔽。
11. **工具对所有成员相同**：同一步骤可用的工具、限额、工具说明完全一样（只看配置和当前可用性，不看是哪个模型）；工具模型（`seat: false`，如图像生成）从不上桌。

## 5. 安全与密钥
- API key **只能**放在项目根目录 `.env`（在 `.gitignore` 中），仓库只提交 `.env.example`。所有 key 都可选：`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`、`DEEPSEEK_API_KEY`、`TAVILY_API_KEY`（联网搜索备选；默认的搜索用 `OPENROUTER_API_KEY`）。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照、前端代码与 API 响应中。
- 读入的 key 一律包成 `Secret`：`repr` / `str` / 格式化只显示 `***`，不可序列化；**只有适配器（模型渠道与搜索服务）在组装请求头时调用 `reveal()`**（架构测试限定了允许的文件）。
- 外部返回的错误文本先脱敏、截断再放进异常；异常用 `from None` 切断原始异常链。
- 每个 `Secret` 自动登记到进程级脱敏表，日志记录创建时统一脱敏（覆盖第三方库的 DEBUG 日志和回溯）。
- 测试用的假 key 用字符串拼接生成，避免被仓库密钥扫描误报；提交前检查 diff。

## 5.1 产品原则（日常模式与群聊，适用于 `docs/PLAN.md` 的 v3 方案）
1. 成员**不否认自己是 AI**：AI 身份当普通身份，平时不刻意提、不说「我只是个 AI」；被认真问到时如实承认。人物卡里的身世和日常发生在圆桌世界，不冒充现实世界的真人、不编造现实世界的经历（2026-10-29 用户定）。
2. **不设计制造依赖的机制**（连续打卡、挽留、情感绑架、制造焦虑）；适时鼓励用户与现实中的朋友、家人联系。
3. **不一味附和**：有不同意见或发现错误时直说，并给出理由。
4. **记忆和聊天记录只存本地**（SQLite），不上传、不用于训练；用户可随时查看、修改、删除、一键清空。
5. 工作模式继续使用中立提示词，**不受任何人设影响**（有测试）；人设只用于群聊。

## 6. 工作流程
- **每个阶段完成后 git 提交**，提交信息注明阶段（如 `phase 4: allocation`）。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要有测试**，`tests/` 结构与 `src/` 对应；功能与测试同一提交。
- 测试不联网、不依赖真实 key；数据库测试用临时文件或 `:memory:`。
- 前端端到端测试在 `tests/web/`（Playwright + Chromium，Fake 模型的服务跑在真实 uvicorn 上）；没有安装 playwright 或浏览器时自动跳过。

---

### 进度汇报规则（每轮工作都遵守）
1. **开工前**列出本轮步骤，每步给出预计用时范围和总用时范围。
2. 用任务列表跟踪每一步；每完成一步用一行中文汇报：`第 X/Y 步完成，约 Z%，预计还需 N～M 分钟`。
3. 某步实际用时超过预估上限的 1.5 倍时，立刻说明原因和新预估。
4. 预计超过 2 分钟的命令先说明预计时长并加 `timeout`；不得运行会等待键盘输入的命令。
5. 收尾时汇报实际总用时与预估的对比。
6. `docs/PROGRESS.md` 列出所有阶段（已完成 / 进行中 / 未开始）、总体完成百分比、每阶段预估与实际用时、下一步；**每完成一个阶段更新并随该阶段提交**。

## 8. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
python scripts/lock_prompts.py      # 新增提示词版本后登记
roundtable estimate '题目'           # 提交前预估：各模式 × 各档位的上桌人数与花费（不调用模型）
roundtable models                   # 查看模型、档位、可用渠道与预算
roundtable models --check-updates   # 人才市场：价格变动、下架、同系列新模型（只提示，不改配置）
roundtable stats                    # 各模型的历史贡献
roundtable performance [--reset]    # 办公室模式的员工绩效（--reset 清零）
roundtable bench --max-usd 2          # 对比实验：个人 / 讨论 / 协同各跑同一批题，比正确率、花费、耗时
roundtable show <id> --costs        # 每步预估与实际花费、每次调用的 token
roundtable export <id> [-o 文件]     # 导出完整记录（UTF-8 带 BOM，含花费明细）
roundtable export <id> --format docx  # 只导出成果：md / docx / pptx / pdf / html（本地转换，不调用模型）
roundtable ask '题目'                # 默认办公室模式（节电档）：经理派一位员工办理、过目；--mode discussion 为全员讨论（PowerShell 中题目用单引号）
roundtable ask --tier flagship --anonymous '题目'   # 全力模式（旗舰档全员）、匿名
roundtable ask --mode collab '题目'  # 协同模式：拆分子任务、分工完成、合并
roundtable ask --mode solo|brainstorm|auto '题目'   # 个人作答 / 头脑风暴（思维导图）/ 自动（分诊小组先给建议）
roundtable mindmap <id> -o 导图.svg  # 保存头脑风暴的导图（.svg / .md / .mmd）
roundtable evolve [meet|export <id>] # 革新例会：成员读资料卡提建议、投票，导出采纳单（默认关，docs/evolution/）
roundtable ask --attach 图.png --attach 讲义.pdf '题目'   # 带附件
roundtable files <id> [-o 目录]      # 保存成员生成的文件（含图片 / 音频 / 视频）
roundtable ask --media image '画一只猫'   # 输出图片（--media speech / video；--media-tier flagship 用高质量档）
python scripts/setup_sandbox.py     # 一次性安装代码运行沙箱（--check 只检查）
python scripts/setup_dsh.py         # 在电脑上安装 dsh 工作台（需要 Node 22+ 和 pnpm；--check 只检查，见 docs/DSH.md）
uvicorn roundtable.api.app:app --reload   # 浏览器打开 http://127.0.0.1:8000
start_lan.bat                       # Windows：同时对家里局域网开放（平板 / 手机，须设访问口令，见 docs/LAN.md）
playwright install chromium        # 首次运行前端端到端测试前
```

---

## 必读索引

§1、§2.1～2.10、§4、§7 的完整原文已移至 docs/ARCHITECTURE.md，编号不变，旧文档里的『CLAUDE.md §x』引用到那里查。

改动涉及下列内容时，开工前必须先读 docs/ARCHITECTURE.md 的对应章节：

| 改动涉及 | 必读 docs/ARCHITECTURE.md |
|---|---|
| 模式、角色、全员上桌、互评、匿名与称呼、不做的功能 | §1 当前范围 |
| models.yaml / roundtable.yaml / personas.yaml / routing.yaml、阵容、加模型或加渠道 | §2.1 配置驱动 |
| Provider、适配器、渠道路由、限速并发、思考预算、重试与费用 | §2.2 Provider 与双渠道 |
| 流程步骤、协同流水线、编排引擎、恢复、确认点 | §2.3 流程步骤 = 插件 |
| 提示词文件、版本、lock_prompts | §2.4 提示词外置且带版本 |
| JSON 解析、互评质量、汇总结构、修订解析 | §2.5 结构化输出 |
| 防偷懒、敷衍判定、贡献统计 | §2.5.1 防偷懒与贡献 |
| 数据库、迁移、session_view | §2.6 持久化：SQLite |
| 分层、HTTP 接口、SSE、service.py、web 前端 | §2.7 分层 |
| 附件、上传、风格参考 | §2.8 附件 |
| 工具、沙箱、搜索、工作目录、生成文件 | §2.9 工具 |
| 媒体 / 画图 / 语音 / 视频 | §2.10 媒体生成 |
| 预算、单题确认门槛、花费预估 | §4 预算 |
| 新增目录或文件 | §7 目录约定 |

不确定是否涉及时，就去读。
