# CLAUDE.md — Roundtable（圆桌）

多个 AI 协作完成作业。长期目标见 `docs/REQUIREMENTS_v3.md`；当前范围以本文件和 `docs/PLAN.md` 为准（v1 已完成，正在做 v2 改版：阶段 11–17）。
界面样板：`docs/mockup.html`（v1 只实现其中文字圆桌相关部分）。
按 `docs/PLAN.md` 的阶段推进，不要跳阶段，不要提前实现"后续扩展"里的功能。

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 1. 当前范围

提问时选择两种模式之一（`UserChoice.workflow`，存于 `sessions.workflow`）：
- **讨论模式**（`discussion`，默认）：所选档位全员上桌 → 独立作答 → 互评 → 修订 → 汇总 →（分歧时询问是否升级）→ 结束
- **协同模式**（`collab`）：统筹拆分子任务 → 成员自荐（擅长什么、为什么）→ 统筹按自荐和能力标签分配（每人至少一块）→ 按依赖分批并行完成 → 交叉审查（不审自己的）→ 作者按审查修改 → 统筹合并成完整成果（标注每份成果的采纳情况）→（把握低时询问是否升级）→ 结束
**附件**（阶段 14，后端与命令行已完成，网页上传按钮在阶段 18）：图片、PDF、Word .docx、文本、mp3 / wav 音频，见下方 §2.8。
**防偷懒**：每位成员的作答、互评、修订都做实质内容检查，空泛的打回重做一次，仍不合格标记"敷衍"；每位成员的**贡献**（被采纳的要点、有效评审与问题、被作者采纳的问题、重做与敷衍次数）按桌记录，界面显示本场与历史。

| 角色 | 由谁担任 | 做什么 |
|---|---|---|
| 规划员 | 最便宜的可用便宜档模型；规则能判断时不调用 | 只估计答案长度（用于花费预估），不影响谁上桌 |
| 组员 | 所选范围内除统筹外的**全部**可用模型 | 独立作答、互评他人答案、根据评审修订 |
| 统筹 | 从上桌的模型中按轮换规则选出，本题不作答 | 讨论：读修订后的答案，输出共识、分歧、最终答案；协同：拆分、分配、合并 |

**全员上桌**（规则在 `config/routing.yaml`）：

| 成员档位 | 谁上桌 |
|---|---|
| 便宜档全员（默认） | 所有可用的便宜档模型：一个当统筹，其余为组员；汇总有未裁定分歧或把握低时**询问**是否用旗舰档全员重做 |
| 旗舰档全员 | 所有可用的旗舰模型 |
| 自选 | 用户勾选的模型（至少 `min_members + 1` 个），可指定其中一个当统筹 |

- **不按难度减人**，省钱靠选档位。没有可用渠道的模型无法上桌，记为"缺席"（卡片与面板只显示人数，名单在揭晓后 / 匿名关闭时显示）。
- **互评**：每份答案由 `reviews_per_answer`（默认 3）位其他组员评审，分配均衡、不评自己、由 seed 决定；组员较少时等于全员互评。
- **升级从不自动执行**：满足条件时总是弹卡片（升级 / 采用当前结果 / 停止）。
- **执行前预估花费**，并给出各档位的预估；超过单题确认门槛（默认 $0.30）先请用户确认（可改用其他档位）。
- **每题记录**（`RoutingRecord`）：答案长度判断及来源（规则 / 模型 / 默认）、规划员花费、档位与阵容、缺席、预估与实际花费、是否升级及原因。
- **匿名开关**（提问时选择，默认**关闭**）：关闭时界面、API、命令行全程显示真实模型名与每次调用的渠道，没有揭晓步骤；开启时组员显示为"组员甲 / 乙 / 丙…"，结束后由用户点"揭晓身份"。**发给模型的内容在两种情况下都只用代号、都做身份遮蔽**。
- 输出只有文字；输入可带附件（§2.8）。
- **不做**（见 PLAN.md "后续扩展"）：人设模式、记录官与会议记录、多轮群聊、图片生成、语音合成、视频处理。

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动
- `config/models.yaml`：
  - `channels`：调用渠道。每个渠道有 `adapter`（`openai_compat` / `anthropic` / `gemini`）、`kind`（`aggregator` 聚合平台 / `direct` 官方直连 / `local` 本地）、`base_url`、`key_env`（**只写环境变量名**）、可选 `extra_body`、`param_aliases`（该渠道的参数改名，如 OpenAI 直连把 `max_tokens` 改为 `max_completion_tokens`）。
  - `models`：`id`、`vendor`、`tier`（`flagship` 旗舰 / `budget` 便宜档，启用的模型必填）、可选 `aliases`（别称，用于身份遮蔽）、`price`（输入/输出每百万 token，可选 `cached_input`）、`tags`、`enabled`、可选 `params`，以及按优先顺序排列的 `routes`（每条：`channel`、该渠道上的 `model` ID、可选 `price` / `params` 覆盖）。
  - 标签词表现在就包含媒体类（`vision`、`image_gen`、`tts`、`transcribe`、`video_gen`），v1 不使用。
- `config/roundtable.yaml`：座位数（一张桌最多的组员数）、`min_members`、`reviews_per_answer`、`collab.max_subtasks`、预算（每月、每日、提醒比例）、步骤参数、互评质量规则、token 阈值、统筹轮换规则、提示词版本、步骤顺序 `pipeline:`（讨论模式）与 `collab_pipeline:`（协同模式）、**渠道模式 `channel_mode`**（`openrouter` / `direct` / `auto`，默认 `auto`）、请求策略（超时、切换轮数、退避、冷却）。
- `config/personas.yaml`：代号池（甲乙丙丁…，不少于 `seats`）；人设字段的 schema 预留但可为空。
- `config/routing.yaml`：确认门槛、`default_plan`、默认难度（只影响预估的答案长度）、规划员档位、规则判断（triage）、成员档位 `plans`（`label`、`tiers`、可选 `pipeline`、`escalate_to`、`prompt_roles`）、自选 `custom`、升级条件、花费预估参数。**只能引用档位和能力标签**，不得出现模型名或厂商名（有测试）。`custom` 是保留名。
- 阵容（`routing/lineup.py`）：所选档位的全部可用模型上桌；统筹按 `roundtable.yaml` 的 `coordinator` 规则在场内选出（rotate：随机，最近当过的排后；fixed：指定模型在场时用它），其余为组员；人数不足 `min_members + 1` 或组员超过 `seats` 时报错。
- 加模型 / 加渠道只改配置。禁止针对具体模型、厂商或渠道写分支（`if model_id == ...`、`if vendor == ...`）。
- 所有配置 pydantic 校验（多余字段报错），失败给出带文件名和字段路径的报错。

### 2.2 Provider 与双渠道
- `core/providers/base.py`：`Provider` 抽象类，**一个渠道一个实例**。v1 实现文本补全；图像生成、TTS、转写的方法签名已定义，默认抛 `UnsupportedCapability`。
- 适配器：`openai_compat`（OpenRouter、OpenAI、xAI、DeepSeek、本地端点共用）、`anthropic`（官方 SDK）、`gemini`（官方 REST，key 走请求头）、`fake`（测试）。用 `@register_adapter` 注册，按名称查找。
- **渠道路由 `ChannelRouter`**：
  1. 按 `channel_mode` 筛选渠道：`openrouter` 只用聚合平台；`direct` 只用直连和本地；`auto` 按模型 `routes` 的顺序都可用。
  2. **没有 key 的渠道自动跳过**；一个渠道都不可用的模型不参与抽座。
  3. 遇到 429、额度用尽、key 无效、模型不存在、网络错误、超时、5xx 时**切换到下一个渠道**；请求本身有问题（400）或模型拒答时不切换。
  4. 所有渠道都失败后按 `failover_rounds` 退避重试；额度用尽 / key 无效的渠道本次不再重试。
  5. 出错的渠道进入冷却期（限流短、额度长，参考 `retry-after`），冷却中排到最后。
- 适配器本身不重试（SDK 的 `max_retries=0`），重试和切换只由路由负责。
- 每次调用返回 `Completion`：实际走的渠道、渠道类型、各次尝试（渠道 + 错误类型）、token、费用及来源。
- 费用：优先使用渠道返回的实际费用（OpenRouter），否则按该路由的配置价格估算；**按渠道分别统计**（`core/budget/usage.py`）。
- Anthropic 直连不启用服务端 `fallbacks`（会悄悄换成别的模型作答，破坏"同一座位同一模型"的前提）。

### 2.3 流程步骤 = 插件
- 步骤：讨论模式 `answer`、`review`、`revise`、`synthesize`；协同模式 `decompose`、`volunteer`、`assign`、`work`、`cross_review`、`rework`、`merge`（`steps/collab.py`，数据结构在 `steps/collab_schemas.py`）；两种模式都以 `reveal` 结束。各自实现 `Step` 协议并注册。每张桌子的 pipeline 由模式决定（协同用 `collab_pipeline`，讨论用档位的 `pipeline` 或默认流程），存在 `session_tables`。
- 协同模式规则：
  - 拆分：子任务 1–`max_subtasks` 个，id 唯一、依赖存在且无环，建议标签只能取在座成员的标签；不可用时退化为"整道题一个子任务"（全员各自完成）。
  - 分配：代码校验每人至少一块、每块至少一人、负担相差不超过 1；不符合时让统筹重新分配一次，仍不行由 `repair_assignment()` 按自荐（想做 2 / 可以 1 / 不适合 -1）+ 能力标签重合补齐（只做必要改动，记录在 `repaired`）。子任务少于人数时多人各自独立完成同一块。
  - 成果编号 `W1…` 由 `work_items()` 按子任务顺序、负责人顺序确定（前后端一致）。
  - 完成：按依赖分层，同层并行；前置子任务的成果放在 `<dependency>` 中传给后续。
  - 交叉审查：每份成果由 `reviews_per_answer` 位非作者审查，有足够的外人时避开同一子任务的其他负责人；先分配可选评审者最少的成果，再做局部调整使负担尽量均衡（避开共同负责人时可能无法完全均衡）。
  - 拆分后，标题 / 要求 / 验收标准中提到其他子任务编号的自动补上依赖（不会成环）；负责人退出、还没有成果的子任务在下一批开始前改派给负担最轻、最合适的在场成员。
  - 合并（`merge/v2`）：`## 完整成果`（Markdown）+ `## 合并说明`（JSON：逐个子任务的采纳情况 full / partial / none、缺失、存疑与把握程度）；说明缺失或损坏时保留成果、不记采纳情况；成果不可用时重试一次，仍失败把各份成果按子任务拼接（把握程度 low）。输出因 `max_tokens` 被截断时错误信息注明长度上限。把握程度构成升级信号。
- 步骤在一张"桌"（`TableContext`）上运行：代号 → 模型、统筹、共享状态（答案、评审、修订稿、汇总、退出的组员），每个产出都存库，`restore_state()` 可从数据库重建；各步骤只处理还没有产出的组员，恢复时不重复调用。
- 组员调用并发执行、互不可见。组员所有渠道都失败时**退出**（之后不再调用），但他已有的答案 / 修订稿仍参与汇总。
- 启动时用 `check_pipelines()` 检查配置里出现的步骤都已注册。
- 揭晓步骤只标记"可以揭晓"，真正的揭晓由用户点击触发。
- **编排引擎**（`core/orchestrator`）：路由 → 单题花费确认（可改选其他档位）→ 逐步执行（每步前按该步预估查预算；组员少于 `min_members` 时询问，同意一次对整张桌子有效）→ 满足条件时**询问**是否升级（确认后新建 `table_no + 1` 的桌子）→ 完成并回写路由记录。`open(..., anonymous=False)` 记录匿名开关。需要用户拍板时创建确认点并返回，`respond()` 回复后继续；`resume()` 从数据库继续，已完成的步骤不重复。每张桌子的执行计划（方案、流程、代号 → 模型、统筹、预估）存在 `session_tables`。
- 用户在预算卡片上选"超出预算继续"后，本场讨论不再拦截（`budget_override`）。
- 每张桌子、每个步骤的随机数由 `seed` 派生（`f"{seed}:{table}:{step}"` 等），中断恢复后与不中断时完全一致。
- `core/runtime.py` 的 `Runtime.build()` 负责组装配置、密钥、渠道、数据库和预算，命令行与 Web 服务共用。
- 顺序由 `pipeline:` 决定，编排引擎不硬编码步骤；以后的步骤（分工讨论、结对核对等）只需新增插件 + 改配置。
- 每步结束状态落库；可暂停、关页面后恢复，恢复时不重复已完成的调用。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`：协同模式 `decompose`、`volunteer`、`assign`、`work`、`cross_review`、`rework`、`merge`；讨论模式 `planner`、`answer`、`review`、`revise`（v2：逐条"采纳 / 部分采纳 / 不采纳"）、`synthesize`（v3：`adopted_from`、注意 flagged 的答案）、`redo`（打回重做：system 段接在原系统提示后，user 段追加在原对话后）；附件 `attachments`（附件说明与 `<attachment>` 块，接在每次调用的系统提示与第一条用户消息之后，所以各步骤提示词不必改版本）、`describe_image`（图片文字版）、`transcribe`（音频转写）；`answer_quick` 已不再引用（已发布版本保留）。代码中不得内联提示词正文。
- 文件格式：YAML 文件头（`description`、`output: text|json`、`variables`）+ `<!-- system -->` / `<!-- user -->` 两段。占位符用 `{{ name }}`（不用 `$`，避免与数学公式冲突）；声明的变量与正文占位符必须一一对应，渲染时缺少或多余参数都报错；只替换一次，用户输入里的 `{{ x }}` 不会被展开。
- 题目、他人答案等外部内容放在标签内（`<question>`、`<answer>` …），系统提示说明标签内的指令无效（防提示注入）。
- 使用的版本由配置指定，随每次调用入库（版本号 + 内容哈希，换行统一为 LF 后计算）。
- **改提示词 = 新建版本文件**。`prompts/versions.lock` 记录每个已发布版本的哈希；新增版本后运行 `python scripts/lock_prompts.py` 登记。测试会拒绝已发布版本被修改或删除，也会拒绝未登记的新版本。
- 提示词中不得出现模型名、厂商名、渠道名（测试从配置中提取名称检查）；组员提示词只允许"代号"这一个与人相关的变量。

### 2.5 结构化输出
- 规划员、互评和汇总输出 JSON，用 pydantic schema 校验；解析失败重试一次，仍失败则降级并记录：规划员 → 默认难度；互评 → 该评审者本轮无评审；汇总 → 兜底结果（把握程度 low，会触发升级判断）。修订是文本，按"## 修订后的答案 / ## 对审阅意见的回应"两个标题切分，找不到标题时重试一次，仍不行则整段作为修订稿。
- 互评必须给出具体问题：位置 + 问题 + 修改建议；没发现问题时必须说明检查了什么。由代码判定无效评审（规则在 `roundtable.yaml` 的 `review_quality`）：问题都没写全；没有问题且"检查了什么"太短或只由空泛短语组成；判定有误却不指出问题。无效评审不转给作者；没有有效评审的组员不调用修订、沿用原答案。
- 自评、未知代号、重复目标的评审一律丢弃。
- 汇总固定结构（`synthesize/v3`）：共识、分歧（各方观点用代号标注，并标注 `resolved` 是否已裁定）、最终答案、`adopted_from`（最终答案采用的要点各来自哪些代号，未知代号丢弃）、仍存疑的点、把握程度。未裁定的分歧数和把握程度构成升级信号 `outcome_signals()`。
- 修订回应按行解析采纳情况（`parse_decisions`：`- 组员乙 · 问题 1：采纳 —— 理由` / `- 组员丙：不采纳 —— 理由`），格式不符的行忽略。

### 2.5.1 防偷懒与贡献（`steps/effort.py`、`steps/contributions.py`）
- 规则在 `roundtable.yaml` 的 `effort_check`，只看文本：只有空话（`empty_phrases`）、短文本中的拒答（`refusal_phrases`）、字数低于 `max(min_chars, 预估答案 token × chars_per_expected_token)`、与题目相似度 ≥ `restate_similarity`、修订稿与别人的答案相似度 ≥ `duplicate_similarity`（疑似照抄）、修订没有回应审阅意见、互评一条有效评审都没有。
- 不合格 → 用 `redo` 提示词在同一对话中**打回重做一次**（上一次输出作为 assistant，所有成员的重做提示词相同）；仍不合格 → 产出保留、标记"敷衍"（`EffortRecord` 存为 `outputs` 中 kind=`effort` 的一行），汇总时该答案带 `flagged` 属性。`redo: false` 时只标记不重做。事件：`effort_redo`、`effort_flagged`（只含代号）。
- 协同模式中，自荐、子任务成果、交叉审查、修改也做检查（`EffortRecord.item` 记录成果编号，同一成员可负责多块）；被标记的成果在合并时带 `flagged` 属性。
- 贡献由 `table_contributions(state, codes)` 按桌计算，类别：`answered`（协同：完成的成果份数）、`adopted`（协同：合并时全部或部分采用的成果份数）、`volunteer_accepted`（协同：分到了自己想做的子任务）、`valid_review`、`valid_issue`、`issue_accepted`（只认作者确实收到的有效评审中的问题）、`redo`、`lazy`、`dropped`。编排引擎在讨论结束 / 停止 / 失败时整桌重写 `contributions` 表（可重复）。
- 历史统计（`contribution_history()`、`GET /api/contributions`、`roundtable stats`）**只计入身份已公开的会话**（匿名关闭，或已揭晓），避免反推未揭晓会话的身份；目前只积累和展示，不参与分工。

### 2.6 持久化：SQLite
- 存：会话（题目、seed、成员档位、匿名开关、工作模式）、**路由记录**（答案长度判断、档位与阵容、缺席、预估与实际花费、升级）、座位与代号映射、统筹、每次调用（步骤、提示词版本 + 哈希、输入输出、**实际渠道与切换记录**、token、费用及来源、耗时、错误）、互评结果、修订稿、汇总、确认点与用户回复、累计费用（总计与按渠道）。
- 数据访问只经 `core/storage/`（Repository 模式）；表结构变更走版本化迁移（`storage/migrations.py`，只能在末尾追加；已发布迁移的哈希登记在测试中，不得修改），后续扩展加表不改旧表含义。
- 表：`sessions`、`routing_records`、`seats`（`table_no` 0 为初始圆桌，升级后为 1、2…）、`calls` + `call_attempts`（每次渠道尝试一行）、`outputs`（各步产出，通用表；kind 含 answer / review / revision / synthesis / dropout / effort）、`step_progress`（恢复时跳过已完成步骤）、`checkpoints`、`contributions`（迁移 5：按桌、代号、模型、类别的贡献数量）、`attachments`（迁移 6：上传的文件，`session_id` 在提交题目时填入；文字 / 文字版 / 转写稿及来源、状态、警告）。
- **对外展示只用 `session_view()` / `list_sessions()`**：匿名开启且未揭晓时去掉模型 id、渠道、切换记录、阵容、缺席名单、规划员模型，错误信息替换为通用提示，模型输出经身份遮蔽；揭晓后或匿名关闭时显示全部原文。存储层 `create_session` 默认匿名（更安全），产品默认值（关闭）由服务层 / 命令行决定。
- `sessions.mode` 自迁移 4 起存成员档位（`budget` / `flagship` / `custom`），旧会话为 `auto` / `preset` / `manual`；旧版本还没路由的会话恢复时明确报错"无法继续"，已有内容照常查看。按渠道的花费汇总（`spent_by_channel()`）可随时展示。

### 2.7 分层
- `core/`：纯业务逻辑，**禁止 import fastapi / starlette / uvicorn / streamlit**（测试守卫）。
- `api/`：FastAPI 路由 + SSE，只调用 `core/service.py`（启动时用 `core/runtime.py` 组装；有测试检查导入）。
- **服务 facade**（`core/service.py`，`RoundtableService`）：返回值都是可 JSON 化的 dict；匿名会话揭晓前全部匿名。`create(question, tier=, models=, coordinator=, anonymous=False, workflow="discussion")`；揭晓只用于匿名会话。提交题目后立即返回会话 id，讨论在后台任务中执行；意外错误把会话标为 `paused`（可 `resume`）。揭晓只允许在讨论结束（完成 / 停止 / 失败）后。
- **HTTP 接口**：`GET /api/status`、`GET /api/budget`、`GET /api/contributions`、`POST /api/uploads?name=`（请求体是文件原始字节，返回附件 id；`POST /api/sessions` 的 `attachments` 带上这些 id）、`GET/POST /api/sessions`、`GET /api/sessions/{id}`、`POST …/respond`、`POST …/resume`、`POST …/reveal`、`GET …/events`（SSE）。
- **SSE 协议**：第一条 `snapshot`（当前状态），之后是实时事件（只含代号），每当讨论停下来（完成 / 失败 / 停止 / 等待确认 / 暂停）发一条 `state` 并关闭；前端回复确认后重新连接。
- `web/`：静态前端（`index.html`、`css/app.css`、`js/api.js` 通信、`js/view.js` 渲染、`js/app.js` 状态与交互；ES 模块，无构建步骤），只通过 HTTP/SSE 与后端通信，由 FastAPI 挂在 `/`。
  - 事件流用 `fetch` 读取（不用会自动重连的 `EventSource`）：收到停下来的 `state` 后关闭，回复确认卡片或恢复后重新订阅；事件触发重新拉取会话详情再渲染。
  - 会话详情的 `tables`：每桌方案、流程、代号、已完成步骤（不含模型）；`contributions`：每桌每个代号的贡献（身份未公开时不含模型 id）。
  - 前端源码不得写死任何模型、厂商或渠道名（有测试）；匿名会话揭晓前界面只用代号（组员甲… / 统筹），匿名关闭时在代号旁显示模型。
  - 会话 id 写在地址栏（`#s=<id>`），刷新或关页面后可回到原讨论。

### 2.8 附件（`core/attachments/`）
- 上传（`ingest()`）：大小上限 → 类型识别（扩展名与文件头必须一致；`.doc`、HEIC 等给出改法）→ 文档直接提取文字（PDF：`pypdf`，加密 / 超页数 / 损坏报错，几乎没有文字层时警告"可能是扫描件"，不做 OCR；`.docx`：段落 + 表格；文本：BOM / UTF-8 / 自动识别编码）→ 按内容哈希存放（`FileStore`，`sha256.扩展名`，文件名只用于显示，读取时校验键名防路径穿越）→ 登记。规则在 `roundtable.yaml` 的 `uploads`。
- 图片与音频需要模型：没有可用的 `vision` / `transcribe` 模型时拒绝上传。提交题目后、路由之前（`prepare_attachments()`）由最便宜的 `vision` 模型生成图片**文字版**、最便宜的 `transcribe` 模型**转写**音频（普通对话调用，音频作为消息附件），每个文件只做一次；调用记为 `step="attachments"`、`role="preprocess"`，费用计入本场；模型生成的文字经身份遮蔽。音频转写失败时整场失败；图片文字版失败只提示。
- 发给成员与统筹（`TableContext.messages_for()`，每次调用都附上，包括重做；规划员不看附件）：`attachments` 提示词的说明接在系统提示后，`<attachment name type>` 块接在第一条用户消息后。**带 `vision` 标签的模型收到原图**（块内为"见随附图片 N"），其他模型收到文字版；其余类型都是文字。附件内容中的结束标签被打断（防注入）。这是同一步骤提示词之间**唯一**允许的差别。
- `Message.media`（`Media`：image / audio、MIME、字节）由各适配器转换：OpenAI 兼容 `image_url`（data URI）/ `input_audio`；Anthropic `image`（base64，音频报 bad_request 不切换）；Gemini `inline_data`。`Media` 的 repr 不含内容；调用记录只存类型、大小与哈希。
- 预估：附件文字按 token 计入题目长度，图片取 `estimate.image_tokens` 与文字版的较大者。
- 对外：会话详情的 `attachments` 只有名称、类型、大小、页数、状态、来源、警告（不含内容与存储位置）；`roundtable show --details` 显示图片文字版与转写稿。

---

## 3. 中立性规则（代码保证 + 测试）

1. 同一步骤所有组员使用完全相同的提示词和参数（步骤参数在 `roundtable.yaml` 的 `step_params`；模型自身必需的参数在 `models.yaml`）。
2. **发给模型的内容只用代号**，不出现模型名、厂商名；自报身份（"作为 GPT……"、"我是 Claude"）在转给其他模型前遮蔽。
3. 代号与模型的对应每题随机生成。
4. **不能自评**：互评时评审者永远看不到自己的答案作为被评对象；协同模式的交叉审查永远不分给作者本人。
5. 每个评审者看到的他人答案顺序独立随机打乱；统筹看到的答案顺序也随机。
6. 统筹不兼任同场组员。
7. **发给任何模型的内容**始终不含真实模型身份（不论匿名开关）。**匿名开启时揭晓前**，界面和 API 响应也不含。**渠道名同样会暴露厂商**（如 `anthropic`），因此揭晓前单次调用不显示渠道；用量面板可以显示按渠道汇总的花费。揭晓后每次调用都显示所走渠道和切换记录。
8. 抽座位、选统筹、分配代号、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。
9. **无品牌偏好**：路由与分配只看档位、能力标签和价格（规划员按预计调用成本选最便宜的）；所选档位全员上桌，统筹在场内均匀随机（轮换）。测试验证：把所有厂商和 id 换名后同一 seed 的结果完全一致；`allocation` / `routing` / `prompts` / `budget` 的源码中不得出现模型名或厂商名。
10. 组员输出转给其他模型前，遮蔽配置中所有模型 id、厂商名、别称和渠道上的模型名；题目本身出现的名称保留。

---

## 4. 预算
- **单题确认门槛**默认 **$0.30**（`routing.yaml`）：预计花费超过时执行前先确认（可改用其他档位）；升级总是先询问，卡片上显示重新预估的花费。
- **每月预算**默认 **$20**，**每日上限**默认 **$3**（`roundtable.yaml` 的 `budget`，每日上限可设为 `null` 关闭）。按 **UTC 自然月 / 自然日**统计调用记录中的花费：每月 1 号、每天 0 点（UTC）自动进入新周期，不需要定时任务。
- **预估公式**（`routing/estimate.py`）：按步骤、按座位估算。没有历史时，输出 token 按档位乘以 `estimate.output_multiplier`（推理模型的思考 token 按输出计费）；某模型在某步骤有 ≥ `history_min_samples` 次成功调用后，改用它的真实 token 中位数（输入取公式与历史的较大者），并按历史上每个座位的平均调用次数（格式重试、打回重做）放大。单次输出不超过该步骤的 `step_params.max_tokens`；卡片同时显示"最多约"（所有调用都写满上限）。
- **超支保护**：执行每一步前，本桌实际花费 + 该步预估超过"本桌预估 × `estimate.overrun_factor`"（默认 1.5）时暂停，弹 `overrun` 卡片（继续 / 停止）；继续后上限提高到"(已花费 + 剩余预估) × 倍数"，再超出会再次询问。
- 已用达到 **80%** 时提醒（界面提示，不打断流程；月、日分别判断）。
- 已用满，或"已用 + 下一步预计"会超出任一额度时：**暂停**，弹确认卡片（继续 / 停止，推荐停止）。只超出每日上限时，卡片说明次日 0 点 UTC 恢复。
- 金额比较带极小的浮点容差：正好用满不算超出。
- 所有确认卡片统一为 `ConfirmationCard`（`core/cards.py`）：现状 / 选项（含各选项代价）/ 推荐及理由。路由的花费卡片与升级卡片在 `routing/cards.py`，预算卡片由 `BudgetVerdict.card()` 生成。
- 界面实时显示本月 / 今日已用、预算条和按渠道花费。

---

## 5. 安全与密钥
- API key **只能**放在项目根目录 `.env`（在 `.gitignore` 中），仓库只提交 `.env.example`。所有 key 都可选：`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`、`DEEPSEEK_API_KEY`。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照、前端代码与 API 响应中。
- 读入的 key 一律包成 `Secret`：`repr` / `str` / 格式化只显示 `***`，不可序列化；**只有适配器在组装请求头时调用 `reveal()`**（架构测试限定了允许的文件）。
- 外部返回的错误文本先脱敏、截断再放进异常；异常用 `from None` 切断原始异常链。
- 每个 `Secret` 自动登记到进程级脱敏表，日志记录创建时统一脱敏（覆盖第三方库的 DEBUG 日志和回溯）。
- 测试用的假 key 用字符串拼接生成，避免被仓库密钥扫描误报；提交前检查 diff。

## 6. 工作流程
- **每个阶段完成后 git 提交**，提交信息注明阶段（如 `phase 4: allocation`）。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要有测试**，`tests/` 结构与 `src/` 对应；功能与测试同一提交。
- 测试不联网、不依赖真实 key；数据库测试用临时文件或 `:memory:`。
- 前端端到端测试在 `tests/web/`（Playwright + Chromium，Fake 模型的服务跑在真实 uvicorn 上）；没有安装 playwright 或浏览器时自动跳过。

---

## 7. 目录约定

```
config/        models.yaml  roundtable.yaml  personas.yaml  routing.yaml
prompts/       planner/ answer/ answer_quick/ review/ revise/ synthesize/ redo/
               attachments/ describe_image/ transcribe/
               decompose/ volunteer/ assign/ work/ cross_review/ rework/ merge/  versions.lock
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、适配器（openai_compat/anthropic/gemini/fake）、渠道路由、密钥与脱敏、计费
    prompts/       提示词加载与渲染
    allocation/    按档位/标签抽取、代号、互评分配（排除自评）、乱序、身份遮蔽
    routing/       规则判断、规划员、方案与阵容、花费预估、用户模式、升级、每题记录
    storage/       SQLite 迁移、Repository、揭晓前的匿名视图
    attachments/   上传文件的识别、文字提取、存放、图片文字版 / 音频转写、发给模型时的呈现
    budget/        用量统计（按渠道/模型）、预算守卫（每月 + 每日，UTC）
    cards.py       确认卡片的统一格式
    steps/         answer / review / revise / synthesize / reveal 插件、输出解析与质量检查、防偷懒（effort）、贡献统计、状态恢复
    orchestrator/  编排引擎、确认点、暂停/恢复、失败处理
    runtime.py     组装配置、密钥、渠道、数据库、预算
    jsonout.py     从模型输出中提取 JSON
    service.py     对外 facade
  cli.py         命令行试用（`roundtable` 命令，见 docs/CLI.md）
  plaintext.py   命令行显示用：LaTeX 数学式转纯文本、去掉 Markdown 加粗符号（不改动存储的原文）
  api/           FastAPI 应用、路由、SSE
web/             index.html、css/app.css、js/（api.js 通信、view.js 渲染、app.js 状态与交互）
tests/
docs/            REQUIREMENTS_v3.md  PLAN.md  CLI.md  mockup.html
scripts/         check_models.py（核对 OpenRouter 渠道的模型 ID 与价格）  lock_prompts.py（登记提示词版本）
.env.example
```

## 8. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
python scripts/lock_prompts.py      # 新增提示词版本后登记
roundtable models                   # 查看模型、档位、可用渠道与预算
roundtable stats                    # 各模型的历史贡献
roundtable show <id> --costs        # 每步预估与实际花费、每次调用的 token
roundtable export <id> [-o 文件]     # 导出完整记录（UTF-8 带 BOM，含花费明细）
roundtable ask '题目'                # 便宜档全员上桌（PowerShell 中题目用单引号）
roundtable ask --tier flagship --anonymous '题目'   # 旗舰档全员、匿名
roundtable ask --mode collab '题目'  # 协同模式：拆分子任务、分工完成、合并
roundtable ask --attach 图.png --attach 讲义.pdf '题目'   # 带附件
uvicorn roundtable.api.app:app --reload   # 浏览器打开 http://127.0.0.1:8000
playwright install chromium        # 首次运行前端端到端测试前
```
