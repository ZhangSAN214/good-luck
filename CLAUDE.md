# CLAUDE.md — Roundtable（圆桌）

多个 AI 协作完成作业。长期目标见 `docs/REQUIREMENTS_v3.md`；当前范围以本文件和 `docs/PLAN.md` 为准（v1 已完成，v2 改版阶段 11–20 全部完成（19 媒体生成、20 收尾：限速与并发、README、CI），v2.1 阶段 21 协同流水线 + 昵称 / 塔罗牌代号 / 大米、22 参考图风格与图生图均已完成）。
界面样板：`docs/mockup.html`（v1 只实现其中文字圆桌相关部分）。
按 `docs/PLAN.md` 的阶段推进，不要跳阶段，不要提前实现"后续扩展"里的功能。

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 1. 当前范围

提问时选择两种模式之一（`UserChoice.workflow`，存于 `sessions.workflow`）：
- **讨论模式**（`discussion`，默认）：所选档位全员上桌 → 独立作答 → 互评 → 修订 → 汇总 →（分歧时询问是否升级）→ 结束
- **协同模式**（`collab`）：统筹拆分子任务 → 成员自荐（擅长什么、为什么）→ 统筹按自荐和能力标签分配（每人至少一块）→ 按依赖分批并行完成 → 交叉审查（不审自己的）→ 作者按审查修改 → 统筹合并成完整成果（标注每份成果的采纳情况）→（把握低时询问是否升级）→ 结束
**附件**（阶段 14，网页上传按钮与拖放在阶段 18）：图片、PDF、Word .docx、文本、mp3 / wav 音频，见下方 §2.8。
**防偷懒**：每位成员的作答、互评、修订都做实质内容检查，空泛的打回重做一次，仍不合格标记"敷衍"；每位成员的**贡献**（被采纳的要点、有效评审与问题、被作者采纳的问题、重做与敷衍次数）按桌记录，界面显示本场与历史。

| 角色 | 由谁担任 | 做什么 |
|---|---|---|
| 规划员 | 最便宜的可用便宜档模型；规则能判断时不调用 | 只估计答案长度（用于花费预估），不影响谁上桌 |
| 组员 | 所选范围内除统筹外的**全部**可用模型 | 独立作答、互评他人答案、根据评审修订 |
| 统筹 | 从上桌的模型中按轮换规则选出，本题不作答 | 讨论：读修订后的答案，输出共识、分歧、最终答案；协同：拆分、分配、合并 |

**全员上桌**（规则在 `config/routing.yaml`）：

| 成员档位 | 谁上桌 |
|---|---|
| 节电模式 = 便宜档全员（默认） | 所有可用的便宜档模型：一个当统筹，其余为组员；汇总有未裁定分歧或把握低时**询问**是否用全力模式重做 |
| 全力模式 = 旗舰档全员 | 所有可用的旗舰模型 |
| 自选 | 用户勾选的模型（至少 `min_members + 1` 个），可指定其中一个当统筹 |

- **不按难度减人**，省钱靠选档位。没有可用渠道的模型无法上桌，记为"缺席"（卡片与面板只显示人数，名单在揭晓后 / 匿名关闭时显示）。
- **互评**：每份答案由 `reviews_per_answer`（默认 3）位其他组员评审，分配均衡、不评自己、由 seed 决定；组员较少时等于全员互评。
- **升级从不自动执行**：满足条件时总是弹卡片（升级 / 采用当前结果 / 停止）。
- **执行前预估花费**，并给出各档位的预估；超过单题确认门槛（默认 $0.30）先请用户确认（可改用其他档位）。
- **每题记录**（`RoutingRecord`）：答案长度判断及来源（规则 / 模型 / 默认）、规划员花费、档位与阵容、缺席、预估与实际花费、是否升级及原因。
- **匿名开关**（提问时选择，默认**关闭**）：关闭时界面、API、命令行全程显示真实模型名与每次调用的渠道，没有揭晓步骤；开启时成员用**塔罗牌代号**（22 张大阿卡纳随机分配，不含任何身份信息），结束后由用户点"揭晓身份"。**称呼**：匿名关闭时成员叫"昵称·模式"（厂商昵称 + 档位名，如"鲸鱼娘·全力"，统筹为"昵称·模式（统筹）"），界面、过程区、交接链、贡献统计、成员之间互相称呼（提示词里）都用它；匿名开启时一律用塔罗牌代号。昵称与模式名在 `personas.yaml`。**身份遮蔽只在匿名开启时做**：发给模型的内容不含模型 id、厂商名、昵称（题目和附件里本来就有的名字保留，不遮）；匿名关闭时**不做任何遮蔽**（成员原文、生成提示词、工具描述都原样发出，成员互称昵称）。
- 输入可带附件（§2.8）；讨论模式可选输出图片 / 语音 / 视频、协同模式可有媒体子任务（§2.10）；成员可以用**工具**（§2.9）运行代码、写文件、生成图片、联网搜索，产出代码、图表、Word / Excel / PPT / PDF 等文件。
- **不做**（见 PLAN.md "后续扩展"）：人设模式、记录官与会议记录、多轮群聊、语音合成、视频处理。（图片生成已作为成员的工具提供，见 §2.9。）

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动
- `config/models.yaml`：
  - `channels`：调用渠道。每个渠道有 `adapter`（`openai_compat` / `anthropic` / `gemini`）、`kind`（`aggregator` 聚合平台 / `direct` 官方直连 / `local` 本地）、`base_url`、`key_env`（**只写环境变量名**）、可选 `extra_body`、`param_aliases`（该渠道的参数改名，如 OpenAI 直连把 `max_tokens` 改为 `max_completion_tokens`）。
  - `models`：`id`、`vendor`、`tier`（`flagship` 旗舰 / `budget` 便宜档，启用的模型必填）、可选 `aliases`（别称，用于身份遮蔽）、`price`（输入/输出每百万 token，可选 `cached_input`）、`tags`、`enabled`、可选 `params`、画图模型的 `image_api`（`chat` 对话接口返回图片，如 Gemini 图像模型；`images` 走 OpenRouter 的 `/api/v1/images`，gpt-image 系列只能走这个；路由上也可覆盖），以及按优先顺序排列的 `routes`（每条：`channel`、该渠道上的 `model` ID、可选 `price` / `params` 覆盖）。
  - 标签词表含媒体类：`vision`、`transcribe`、`image_gen`、`image_edit`、`tts`、`stt`、`video_gen`、`music_gen`（后两个预留）。媒体模型 `seat: false`（不上桌、不占座位），可有 `tier`；视频 / 转写 / 语音按秒 / 分钟 / 字符计价，用 `media_price: {unit, usd}`（`price` 写 0），可被路由覆盖；**图像模型在 OpenRouter 上按 token 计价**：`price.output` 填图像输出价格，`image_tokens` 填一张图的典型输出 token 数（只用于预估和渠道没返回用量时的记账，`providers/cost.py` 的 `image_cost`），实际费用一律以渠道返回的 `usage.cost` 为准。
- `config/roundtable.yaml`：座位数（一张桌最多的组员数）、`min_members`、`reviews_per_answer`、`collab.max_subtasks`、预算（每月、每日、提醒比例）、步骤参数、互评质量规则、token 阈值、统筹轮换规则、提示词版本、步骤顺序 `pipeline:`（讨论模式）与 `collab_pipeline:`（协同模式）、**渠道模式 `channel_mode`**（`openrouter` / `direct` / `auto`，默认 `auto`）、请求策略（超时、切换轮数、退避、冷却）。
- `config/personas.yaml`：匿名代号池（塔罗牌 22 张大阿卡纳，不少于 `seats`）、厂商昵称 `nicknames`（厂商 → 昵称）、档位显示名 `tier_labels`（flagship = 全力，budget = 节电）、`nickname_separator`、`coordinator_suffix`、`code_prefix`（默认空：代号 / 昵称本身就是称呼）；同一桌里厂商和档位都相同的模型自动加序号（鲸鱼娘·全力2）；人设字段的 schema 预留但可为空。称呼生成在 `allocation/names.py`（`member_codes()`）。
- `roundtable.yaml` 的 `display.rice`：token 用量显示为"大米"（1 粒 = 1000 token，1 勺 = 100 粒，1 碗 = 30 勺，比例与单位名在配置里），自动换算（`core/display/rice.py`，前端用 `/api/status` 的 `rice` 同一套比例）；**只替换 token 数量的显示，所有金额仍是美元**。
- `config/routing.yaml`：确认门槛、`default_plan`、默认难度（只影响预估的答案长度）、规划员档位、规则判断（triage）、成员档位 `plans`（`label`、`tiers`、可选 `pipeline`、`escalate_to`、`prompt_roles`）、自选 `custom`、升级条件、花费预估参数。**只能引用档位和能力标签**，不得出现模型名或厂商名（有测试）。`custom` 是保留名。
- 阵容（`routing/lineup.py`）：所选档位的全部可用模型上桌；统筹按 `roundtable.yaml` 的 `coordinator` 规则在场内选出（rotate：随机，最近当过的排后；fixed：指定模型在场时用它），其余为组员；人数不足 `min_members + 1` 或组员超过 `seats` 时报错。
- 加模型 / 加渠道只改配置。禁止针对具体模型、厂商或渠道写分支（`if model_id == ...`、`if vendor == ...`）。
- 所有配置 pydantic 校验（多余字段报错），失败给出带文件名和字段路径的报错。

### 2.2 Provider 与双渠道
- `core/providers/base.py`：`Provider` 抽象类，**一个渠道一个实例**。文本补全必须实现；媒体能力 `generate_image`（默认走对话接口的 `images`）、`synthesize_speech`、`transcribe`、`submit_video` / `poll_video` / `fetch_video`（异步任务）默认抛 `UnsupportedCapability`。`openai_compat` 按 OpenRouter 的接口实现后四项（`/audio/speech`、`/audio/transcriptions`、`/videos`）。`ChannelRouter.invoke(model_id, call)` 是通用的渠道调用（切换 / 退避 / 冷却规则同文本；渠道不支持该能力时视为不可用换下一个），`complete()` 建立在它之上。
- 适配器：`openai_compat`（OpenRouter、OpenAI、xAI、DeepSeek、本地端点共用）、`anthropic`（官方 SDK）、`gemini`（官方 REST，key 走请求头）、`fake`（测试）。用 `@register_adapter` 注册，按名称查找。
- **渠道路由 `ChannelRouter`**：
  1. 按 `channel_mode` 筛选渠道：`openrouter` 只用聚合平台；`direct` 只用直连和本地；`auto` 按模型 `routes` 的顺序都可用。
  2. **没有 key 的渠道自动跳过**；一个渠道都不可用的模型不参与抽座。
  3. 遇到 429、额度用尽、key 无效、模型不存在、网络错误、超时、5xx 时**切换到下一个渠道**；请求本身有问题（400）或模型拒答时不切换。
  4. 所有渠道都失败后按 `failover_rounds` 退避重试；额度用尽 / key 无效的渠道本次不再重试。
  5. 出错的渠道进入冷却期（限流短、额度长，参考 `retry-after`），冷却中排到最后。
- **限速与并发**（`request.max_concurrent` / `request.requests_per_minute`，按渠道分别计，在 `ChannelRouter.invoke` 里）：同时在途的请求数超过上限时排队；每分钟请求数达到上限时等待而不是报错。Web 服务另有 `limits.max_running_sessions`（同时在后台执行的讨论数，超过时 HTTP 429；等待确认的不占名额）。
- 适配器本身不重试（SDK 的 `max_retries=0`），重试和切换只由路由负责。
- 每次调用返回 `Completion`：实际走的渠道、渠道类型、各次尝试（渠道 + 错误类型）、token、费用及来源。
- 费用：优先使用渠道返回的实际费用（OpenRouter），否则按该路由的配置价格估算；**按渠道分别统计**（`core/budget/usage.py`）。
- **思考预算与长度保护**：`step_params` 里每步可带中立参数 `reasoning`（`{effort: low|medium|high}` 或 `{max_tokens: N}`，同一步骤所有模型相同），渠道层按 `models.yaml` 渠道的 `reasoning` 写法转换（`object` = OpenRouter 的 `reasoning` 对象；`effort` = `reasoning_effort`；`none` = 忽略；不填时聚合平台 `object`、其余 `none`），不支持的渠道自动忽略。原则：作答 / 修订 / 完成子任务封顶思考（max_tokens 留出正文空间），评审、汇总、拆分、分配、自荐、风格校验、规划员、附件预处理用低强度。**长度保护**（`roundtable.yaml` 的 `length_guard`）：`finish_reason=length` 且可见文字少于 `min_visible_chars` = 思考 token 挤占了正文，`call_once` 不原样重试，而是降低思考强度、把 `max_tokens` 乘以 `max_tokens_factor`（不超过 `max_tokens_cap`）再调一次（单独记录、单独计费）；格式解析失败且上一次被截断时同样升级参数。`calls` 表记录 `finish_reason` 与 `reasoning_tokens`（迁移 10）。
- **不原样重试**：格式错误重试（`call_and_parse`、规划员）必须在对话后附上上一次的输出和具体错误原因（`retry_followup()`，字段路径来自 `describe_error()`）；同一个模型不会收到完全相同的请求两次（测试守卫）。
- Anthropic 直连不启用服务端 `fallbacks`（会悄悄换成别的模型作答，破坏"同一座位同一模型"的前提）。

### 2.3 流程步骤 = 插件
- 步骤：讨论模式 `answer`、`review`、`revise`、`synthesize`（选了媒体输出时在 `reveal` 前加 `media`）；协同模式 `decompose`、`volunteer`、`assign`、`work`、`cross_review`、`rework`、`merge`（`steps/collab.py`，数据结构在 `steps/collab_schemas.py`）；两种模式都以 `reveal` 结束。各自实现 `Step` 协议并注册。每张桌子的 pipeline 由模式决定（协同用 `collab_pipeline`，讨论用档位的 `pipeline` 或默认流程），存在 `session_tables`。
- 协同模式规则：
  - 拆分（旧流程，成员不足 `collab.pipeline.min_members` 时）：子任务 1–`max_subtasks` 个，id 唯一、依赖存在且无环，建议标签只能取在座成员的标签；不可用时退化为"整道题一个子任务"（全员各自完成）。
  - **流水线（阶段 21，成员 ≥ 2 时默认）**：协同不是"各做各的"。子任务有**类型** `kind`（`collab.kinds`：analyze 分析 / research 搜索调研 / write 撰写 / prompt 写提示词 / generate 执行生成 / code 写代码 / verify 运行验证 / review 审查 / factcheck 事实核查 / assemble 整合；每类的偏好标签、能否并行、必须依赖的类型、回避规则、工作提示词都在配置里），依赖写成 `{"id", "gives"}`（上游交给下游的内容）。`decompose/v4` 要求流水线。`pipeline_problems()` 校验：不是整题当一个子任务、子任务数 ≥ 成员数（不足就把可并行的步骤**按内容**拆成平行子任务，不允许几个人做同一份内容）、依赖图连通且至少两层、类型不单一、类型规则（执行生成依赖写提示词且需要可用的媒体模型、审查类依赖被审成果、整合至少两个上游）、平行子任务不重复；不合格 → 把具体问题列给统筹重拆一次（`decompose_retry/v1`）→ 仍不合格按**模板**自动生成（`collab.templates`：带参考图的创作 / 无参考图的创作 / 调研（搜索→分析整理→撰写→事实核查→整合）/ 数据代码（理解需求→写代码→运行验证→审查→写报告）/ 文档，按题目性质与可用能力选第一个条件满足的；成员比步骤多时 split 的步骤按内容拆成平行部分，每个部分写明自己负责哪一份；成员少于步骤数时每人负责多个步骤，不合并）。
  - 分配：代码校验每人至少一块、每块至少一人、负担相差不超过 1；不符合时让统筹重新分配一次，仍不行由 `repair_assignment()` 按自荐（想做 2 / 可以 1 / 不适合 -1）+ 能力标签重合补齐（只做必要改动，记录在 `repaired`）。子任务少于人数时多人各自独立完成同一块。
  - **流水线的分配**：统筹的分配合格就原样采用；否则 `solve_assignment()`（爬山 + 随机重启）求解：每个子任务**恰好一位负责人**、负担相差不超过 1、**审查类的负责人回避上游的作者**（`avoid_hard`：写提示词的人永远不审查自己提示词生成的结果；`avoid_soft`：执行生成 / 撰写 / 写代码的作者，成员够多时同样回避，成员太少无法满足时只放宽 soft，写进 `repaired`）、类型偏好标签（权重大于自荐）。子任务比成员少（超过 `max_subtasks` 的大桌）时，没有任务的成员加入可并行类型的子任务。
  - **交接**：下游子任务的 `<dependency>` 标明来自哪个子任务、谁交的、交的是什么（`gives`）；上游生成的文件（依赖链上全部上游，最新版本）放进下游负责人工作目录的 `in/`（`Workspace.add_inputs()`，同名加子任务前缀），图片按 `vision` 规则随附；每次交接记为 `handoff` 产出 + `handoff` 事件（只含代号）；`generate` 类型由负责人先做一次短调用确认提示词（`work_generate`，不得改动风格规范），再由代码调用媒体服务生成，登记在负责人名下。界面"分工"面板有交接链图，过程区逐条显示"X 把〈…〉交给 Y"，命令行 `show` 有"交接链"。
  - 成果编号 `W1…` 由 `work_items()` 按子任务顺序、负责人顺序确定（前后端一致）。
  - 完成：按依赖分层，同层并行；前置子任务的成果放在 `<dependency>` 中传给后续。
  - 交叉审查：每份成果由 `reviews_per_answer` 位非作者审查，有足够的外人时避开同一子任务的其他负责人；先分配可选评审者最少的成果，再做局部调整使负担尽量均衡（避开共同负责人时可能无法完全均衡）。
  - 拆分后，标题 / 要求 / 验收标准中提到其他子任务编号的自动补上依赖（不会成环）；负责人退出、还没有成果的子任务在下一批开始前改派给负担最轻、最合适的在场成员。
  - 合并（`merge/v2`）：`## 完整成果`（Markdown）+ `## 合并说明`（JSON：逐个子任务的采纳情况 full / partial / none、缺失、存疑与把握程度）；说明缺失或损坏时保留成果、不记采纳情况；成果不可用时重试一次，仍失败把各份成果按子任务拼接（把握程度 low）。输出因 `max_tokens` 被截断时错误信息注明长度上限。把握程度构成升级信号。
- 步骤在一张"桌"（`TableContext`）上运行：代号 → 模型、统筹、共享状态（答案、评审、修订稿、汇总、退出的组员），每个产出都存库，`restore_state()` 可从数据库重建；各步骤只处理还没有产出的组员，恢复时不重复调用。
- 组员调用并发执行、互不可见。组员所有渠道都失败时**退出**（之后不再调用），但他已有的答案 / 修订稿仍参与汇总。
- 步骤需要用户先拍板时抛 `NeedsApproval(card, key)`（视频生成确认、预算已用满）：引擎创建确认点并暂停，回复后重新执行该步骤，步骤用 `ctx.approval(key)` 读取回复；步骤做到一半停下后重新进入时不再重复检查开始前的预估 / 超支（`_answered_midstep`）。
- 启动时用 `check_pipelines()` 检查配置里出现的步骤都已注册。
- **媒体步骤（讨论模式）的提示词来源**：汇总的 `usable_final_answer`；汇总失败（降级 / 占位文字，`is_placeholder_text()`）时**绝不**把占位文字当作生成提示词——改由一位随机在场成员按 `media_rewrite/v1` 根据全体修订稿重写（结果存 `outputs` kind=`media_prompt`，恢复时不重复调用）；重写也失败则跳过媒体生成并在步骤说明里提示，不花钱。
- 揭晓步骤只标记"可以揭晓"，真正的揭晓由用户点击触发。
- **编排引擎**（`core/orchestrator`）：路由 → 单题花费确认（可改选其他档位）→ 逐步执行（每步前按该步预估查预算；组员少于 `min_members` 时询问，同意一次对整张桌子有效）→ 满足条件时**询问**是否升级（确认后新建 `table_no + 1` 的桌子）→ 完成并回写路由记录。`open(..., anonymous=False)` 记录匿名开关。需要用户拍板时创建确认点并返回，`respond()` 回复后继续；`resume()` 从数据库继续，已完成的步骤不重复。每张桌子的执行计划（方案、流程、代号 → 模型、统筹、预估）存在 `session_tables`。
- 用户在预算卡片上选"超出预算继续"后，本场讨论不再拦截（`budget_override`）。
- 每张桌子、每个步骤的随机数由 `seed` 派生（`f"{seed}:{table}:{step}"` 等），中断恢复后与不中断时完全一致。
- `core/runtime.py` 的 `Runtime.build()` 负责组装配置、密钥、渠道、数据库和预算，命令行与 Web 服务共用。
- 顺序由 `pipeline:` 决定，编排引擎不硬编码步骤；以后的步骤（分工讨论、结对核对等）只需新增插件 + 改配置。
- 每步结束状态落库；可暂停、关页面后恢复，恢复时不重复已完成的调用。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`：风格：`style_extract`（成员各自看参考图）、`style_merge`（统筹合并成规范 + 清单）、`style_guide`（接在之后的每次调用上）、`media_review`（v2：对照风格清单逐条判定）、`tools`（v5：`in/` 上游文件与 `refs`）；协同模式 `decompose`（v3：子任务可标 `media`；v4：流水线，要求类型 `kind`、`gives` 交接、每人一块不同的工作）、`decompose_retry`（拆分不合格时把问题列给统筹重拆）、`volunteer`、`assign`（v3：流水线分配规则）、`work`（旧流程）、各类型的工作提示词 `work_analyze` / `work_research` / `work_write` / `work_prompt` / `work_generate`（执行生成前确认提示词的短调用）/ `work_code` / `work_verify` / `work_review` / `work_factcheck` / `work_assemble`、`cross_review`、`rework`、`merge`；媒体：`media_brief`（把需求改写成「写生成提示词」的任务）、`media_review`、`media_refine`、`work_media`、`rework_media`；讨论模式 `planner`、`answer`、`review`、`revise`（v2：逐条"采纳 / 部分采纳 / 不采纳"）、`synthesize`（v3：`adopted_from`、注意 flagged 的答案）、`redo`（打回重做：system 段接在原系统提示后，user 段追加在原对话后）；附件 `attachments`（附件说明与 `<attachment>` 块，接在每次调用的系统提示与第一条用户消息之后，所以各步骤提示词不必改版本）、`describe_image`（图片文字版）、`transcribe`（音频转写）；`answer_quick` 已不再引用（已发布版本保留）。代码中不得内联提示词正文。
- 文件格式：YAML 文件头（`description`、`output: text|json`、`variables`）+ `<!-- system -->` / `<!-- user -->` 两段。占位符用 `{{ name }}`（不用 `$`，避免与数学公式冲突）；声明的变量与正文占位符必须一一对应，渲染时缺少或多余参数都报错；只替换一次，用户输入里的 `{{ x }}` 不会被展开。
- 题目、他人答案等外部内容放在标签内（`<question>`、`<answer>` …），系统提示说明标签内的指令无效（防提示注入）。
- 使用的版本由配置指定，随每次调用入库（版本号 + 内容哈希，换行统一为 LF 后计算）。
- **改提示词 = 新建版本文件**。`prompts/versions.lock` 记录每个已发布版本的哈希；新增版本后运行 `python scripts/lock_prompts.py` 登记。测试会拒绝已发布版本被修改或删除，也会拒绝未登记的新版本。
- 提示词中不得出现模型名、厂商名、渠道名（测试从配置中提取名称检查）；组员提示词只允许"代号"这一个与人相关的变量。

### 2.5 结构化输出
- 解析容忍便宜模型的常见毛病（`jsonout.py`、`steps/schemas.py` 的 `_Loose`）：代码块围栏、前后多余文字、字符串里的原始换行、非法反斜杠转义（LaTeX）、末尾多余的逗号、只输出数组、null 字段、数字当文字、文字字段写成列表、互评的 reviews/issues 写成单个对象或字符串、verdict 写法不规范。`review/v3` 另外在提示词里写明格式要求（不要代码块、不要反斜杠、不要 null、枚举值）。
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
- 表：`sessions`、`routing_records`、`seats`（`table_no` 0 为初始圆桌，升级后为 1、2…）、`calls` + `call_attempts`（每次渠道尝试一行）、`outputs`（各步产出，通用表；kind 含 answer / review / revision / synthesis / dropout / effort）、`step_progress`（恢复时跳过已完成步骤）、`checkpoints`、`contributions`（迁移 5：按桌、代号、模型、类别的贡献数量）、`attachments`（迁移 6：上传的文件，`session_id` 在提交题目时填入；文字 / 文字版 / 转写稿及来源、状态、警告；迁移 9 加 `style_ref`：图片是否作为风格参考，默认 1）、`tool_calls` 与 `files`（迁移 7，见 §2.9）、`media_jobs`（迁移 8，见 §2.10；迁移 9 加 `reference_count`：传给画图模型的参考图张数）。
- **对外展示只用 `session_view()` / `list_sessions()`**：匿名开启且未揭晓时去掉模型 id、渠道、切换记录、阵容、缺席名单、规划员模型，错误信息替换为通用提示，模型输出经身份遮蔽；揭晓后或匿名关闭时显示全部原文。存储层 `create_session` 默认匿名（更安全），产品默认值（关闭）由服务层 / 命令行决定。
- `sessions.mode` 自迁移 4 起存成员档位（`budget` / `flagship` / `custom`），旧会话为 `auto` / `preset` / `manual`；旧版本还没路由的会话恢复时明确报错"无法继续"，已有内容照常查看。按渠道的花费汇总（`spent_by_channel()`）可随时展示。

### 2.7 分层
- `core/`：纯业务逻辑，**禁止 import fastapi / starlette / uvicorn / streamlit**（测试守卫）。
- `api/`：FastAPI 路由 + SSE，只调用 `core/service.py`（启动时用 `core/runtime.py` 组装；有测试检查导入）。
- **服务 facade**（`core/service.py`，`RoundtableService`）：返回值都是可 JSON 化的 dict；匿名会话揭晓前全部匿名。`create(question, tier=, models=, coordinator=, anonymous=False, workflow="discussion")`；揭晓只用于匿名会话。提交题目后立即返回会话 id，讨论在后台任务中执行；意外错误把会话标为 `paused`（可 `resume`）。揭晓只允许在讨论结束（完成 / 停止 / 失败）后。
- **HTTP 接口**：`GET /api/status`、`GET /api/budget`、`GET /api/contributions`、`POST /api/estimate`（提交前预估：各模式 × 各档位 / 自选的上桌人数、缺席、预计与最多花费、步骤明细；不调用任何模型，答案长度只用规则判断；返回 `seed`，提交时带上它，阵容与预估一致；匿名时不含模型名单）、`POST /api/uploads?name=`（请求体是文件原始字节，返回附件 id；`POST /api/sessions` 的 `attachments` 带上这些 id）、`GET/POST /api/sessions`（`media` / `media_tier`）、`GET /api/sessions/{id}`、`GET …/files/{fid}`（下载；`?inline=1` 可内联播放 png / jpg / gif / webp 与 mp3 / wav / ogg / mp4 / webm，后者支持 Range）与 `…/files/{fid}/preview`、`POST …/respond`、`POST …/resume`、`POST …/reveal`、`GET …/events`（SSE）。
- **SSE 协议**：第一条 `snapshot`（当前状态），之后是实时事件（只含代号），每当讨论停下来（完成 / 失败 / 停止 / 等待确认 / 暂停）发一条 `state` 并关闭；前端回复确认后重新连接。
- `web/`：静态前端（`index.html`、`css/app.css`、`js/api.js` 通信、`js/view.js` 渲染、`js/app.js` 状态与交互；ES 模块，无构建步骤），只通过 HTTP/SSE 与后端通信，由 FastAPI 挂在 `/`。
  - 事件流用 `fetch` 读取（不用会自动重连的 `EventSource`）：收到停下来的 `state` 后关闭，回复确认卡片或恢复后重新订阅；事件触发重新拉取会话详情再渲染。
  - 提问区（阶段 18）：模式 / 档位 / 匿名开关、「添加附件」与拖放（`POST /api/uploads`，失败的附件显示原因、不随题目提交）、输入题目后防抖调用 `POST /api/estimate` 显示当前模式下各档位的预计花费（超过门槛标红；匿名时不显示名单），提交时带上预估返回的 `seed`。
  - 过程区逐条显示工具调用（`details.tool`：代码 / 搜索词、输出、来源），成员生成的文件显示预览 / 下载，图片显示缩略图；答案里的 `[S1]` 只对该成员检索到的 http(s) 来源加链接。预览在 `<dialog>` 中，HTML / SVG 只显示源代码。面板新增「分工」（子任务、负责人、成果、采纳、自荐表态）与「工具」（工具花费、调用、文件）；座位超过 9 个时缩小（最多 12 个）。
  - 会话详情的 `tables`：每桌方案、流程、代号、已完成步骤（不含模型）；`contributions`：每桌每个代号的贡献（身份未公开时不含模型 id）。
  - 前端源码不得写死任何模型、厂商或渠道名（有测试）；匿名会话揭晓前界面只用塔罗牌代号和"统筹"，匿名关闭时代号就是"昵称·模式"并在旁边显示模型。
  - 会话 id 写在地址栏（`#s=<id>`），刷新或关页面后可回到原讨论。

### 2.8 附件（`core/attachments/`）
- 上传（`ingest()`）：大小上限 → 类型识别（扩展名与文件头必须一致；`.doc`、HEIC 等给出改法）→ 文档直接提取文字（PDF：`pypdf`，加密 / 超页数 / 损坏报错，几乎没有文字层时警告"可能是扫描件"，不做 OCR；`.docx`：段落 + 表格；文本：BOM / UTF-8 / 自动识别编码）→ 按内容哈希存放（`FileStore`，`sha256.扩展名`，文件名只用于显示，读取时校验键名防路径穿越）→ 登记。规则在 `roundtable.yaml` 的 `uploads`。
- 图片与音频需要模型：没有可用的 `vision` / `transcribe` 模型时拒绝上传。提交题目后、路由之前（`prepare_attachments()`）由最便宜的 `vision` 模型生成图片**文字版**、最便宜的 `transcribe` 模型**转写**音频（普通对话调用，音频作为消息附件），每个文件只做一次；调用记为 `step="attachments"`、`role="preprocess"`，费用计入本场；模型生成的文字经身份遮蔽。音频转写失败时整场失败；图片文字版失败只提示。
- 发给成员与统筹（`TableContext.messages_for()`，每次调用都附上，包括重做；规划员不看附件）：`attachments` 提示词的说明接在系统提示后，`<attachment name type>` 块接在第一条用户消息后。**带 `vision` 标签的模型收到原图**（块内为"见随附图片 N"），其他模型收到文字版；其余类型都是文字。附件内容中的结束标签被打断（防注入）。这是同一步骤提示词之间**唯一**允许的差别。
- `Message.media`（`Media`：image / audio、MIME、字节）由各适配器转换：OpenAI 兼容 `image_url`（data URI）/ `input_audio`；Anthropic `image`（base64，音频报 bad_request 不切换）；Gemini `inline_data`。`Media` 的 repr 不含内容；调用记录只存类型、大小与哈希。
- 预估：附件文字按 token 计入题目长度，图片取 `estimate.image_tokens` 与文字版的较大者。
- **风格参考**（阶段 22）：上传的图片默认是**风格参考**（`attachments.style_ref`；提问区每张图片有「风格参考」勾选，取消后只是普通附件；`POST /api/sessions` 的 `style_refs` 传仍作为风格参考的图片 id，省略 = 全部图片，`[]` = 都不是；命令行 `--no-style-ref`）。见 §2.10 的风格规范与参考图。
- 对外：会话详情的 `attachments` 只有名称、类型、大小、页数、状态、来源、警告（不含内容与存储位置）；`roundtable show --details` 显示图片文字版与转写稿。

### 2.9 工具（`core/tools/`、`steps/tooluse.py`）
- **文字协议**（`tools/protocol.py`）：成员在输出中写 `<tool_call name="python">代码</tool_call>`、`<tool_call name="write_file" path="x.md">内容</tool_call>`、`<tool_call name="generate_image" path="x.png">画面描述</tool_call>`；代码执行后把 `<tool_result>` 追加到同一对话再调用，直到不再申请工具；最终结果去掉工具调用。不用各家原生 function calling（格式不一、部分模型不支持）。
- 工具说明 `prompts/tools`（system 段接在该步骤系统提示后，user 段用于每轮发回结果）；**同一步骤所有成员的工具、限额、说明完全相同**。可用工具 = `roundtable.yaml` 的 `tools.by_step` ∩ 当前可用（python 需要沙箱，generate_image 需要可用的 `image_gen` 模型）。默认：作答 / 修订 / 完成子任务 / 修改：全部；互评 / 交叉审查：python；合并：python、write_file；其他步骤没有。
- 限额：每次作答最多 `max_rounds` 轮；python 每步 `max_runs` 次、`timeout_s`、`memory_mb`、输出截断；文件单个 / 每场合计大小、每步个数；每步最多生成 `image.max_per_step` 张图；每步最多搜索 `search.max_per_step` 次（默认 2）；每轮前查预算，额度用完时工具返回"额度已用完"。达到轮数仍申请工具时去掉调用作为结果。
- **沙箱**（`tools/sandbox.py`，`tools.python.backend`）：`wasm`（默认）= Deno + Pyodide，Deno 只能读运行时目录和本次工作目录、只能写 `out/`，没有网络 / 环境变量 / 子进程权限，`runner.mjs` 把 `in/`、`out/` 复制进内存文件系统并屏蔽启动子进程的函数；`docker`（可选，`sandbox/Dockerfile`）= `--network none`、只读根目录、内存 / 进程数限制、`--cap-drop ALL`、非 root、只挂载 `in/`(ro)、`out/`、`main.py`(ro)。两者都由宿主限时、用 psutil 监视内存与 `out/` 大小、截断输出，子进程环境变量只有必需的几项。没有后端时 python 关闭，绝不在本机直接运行。运行时由 `scripts/setup_sandbox.py` 安装到用户缓存目录（不在项目目录，脚本拒绝装到项目里）。越权测试 `tests/core/tools/test_sandbox_escape.py`（没有后端时跳过）。
- **工作目录**（`tools/files.py` 的 `Workspace`）：每位成员每张桌子一个系统临时目录，`in/` = 附件原文件，`out/` = 生成的文件（跨步骤保留，重启后从数据库恢复）。每次工具调用后收集 `out/`：扩展名白名单、拒绝符号链接与硬链接、文件名清理（`clean_path`）、大小与个数限制；通过的按内容哈希存放并登记到 `files`（作者代号、步骤、来源工具调用），不合格的删除并告诉成员。
- **图片模型档位**：`gpt-image-1-mini`（节电档，默认）、`gpt-image-2` 与 `gemini-3.1-flash-image`（旗舰档，同档内随机；后者实测约 $0.067 / 张，比 gpt-image-2 还贵，所以不在节电档）。
- **图像生成**（带风格参考图时见 §2.10：`refs` 属性、优先 `image_edit` 模型）：由 `seat: false`（不上桌、不需要档位）、带 `image_gen` 标签的最便宜可用模型完成（OpenRouter 用 `modalities`，Gemini 返回 `inlineData`，`RawCompletion.images`）；描述经身份遮蔽后放进 `prompts/image_gen`；调用记为 `role="tool"`、代号为申请的成员，照常计费。
- **转交**：成员生成的文件（最新版本）以 `<files>` 块附在他的答案 / 成果后面交给其他成员与统筹（`TableContext.files_note()`，文本类附上前 `share_text_chars` 字，其他只列名称、类型、大小）。**生成的图片**（png / jpg / gif / webp）对带 `vision` 标签的模型作为随附图片发送（`messages_for()` → `_attach_generated_images()`，标签上注明 `attached="随附图片 N"`，每次调用最多 `files.share_images` 张、每张不超过 `share_image_mb`），其他模型只看到名称；这与附件一样只是呈现方式的差别。
- **中文字体**：`setup_sandbox.py` 下载固定版本的 Noto Sans SC（校验 sha256）到运行时目录 `fonts/cjk.otf`；`runner.mjs` 放到 `/usr/share/fonts/roundtable/cjk.otf`，代码用到绘图时登记为 matplotlib 默认字体（Pillow 可直接用该路径）；docker 镜像中是同一文件与路径（`MATPLOTLIBRC`）。`--check` 在缺字体时报错，`start.bat` 会补装。
- 记录：`tool_calls`（迁移 7：步骤、代号、轮次、工具、输入、输出、状态 ok / error / timeout / rejected / limit、耗时、提出申请的模型调用）与 `files`（迁移 7）。每轮工具调用都是一次照常计费的模型调用；预估中能用工具的步骤按 `estimate.tool_rounds` 多估调用次数，有历史后改用实际次数。事件 `tool_started` / `tool_finished`（只含代号）。
- **联网搜索**（`core/search/`，阶段 16）：搜索服务配置在 `models.yaml` 的 `search_providers`（`adapter`、`base_url`、`key_env`、`price.per_search` / `per_fetch`、`params`），按顺序使用，没有 key 的跳过，限流 / 额度 / 网络错误换下一家（`SearchService`，错误分类与模型渠道相同）；适配器用 `@register_search` 注册。**默认是 OpenRouter 自带的联网搜索**（`openrouter`，共用 `OPENROUTER_API_KEY`）：用 `params.model` 指定的便宜模型发一次 `/chat/completions` 并打开搜索（`request: plugin` 为 `plugins: [{id: web}]`，`server_tool` 为 `openrouter:web_search`），只取响应中的 `url_citation` 注释作为结果，费用以 `usage.cost` 为准（搜索引擎按次收费 + 少量 token），发给该模型的指令在 `prompts/web_search`。**读取网页（fetch）默认也用 OpenRouter**：同一模型调用 `openrouter:web_fetch` 服务端工具（`params.fetch_tool`，默认只写类型），取回的正文只交给模型，由模型按 `prompts/web_fetch` 原样输出（失败输出 `FETCH_FAILED`）；注释里有该网址更完整的正文时优先用注释；正文经过转述，结果标注 `via="model"`（`relayed_fetch`），`tools/v4` 提醒成员谨慎引用。`params.fetch: false` 可关闭。**备选 Tavily**（填了 `TAVILY_API_KEY` 才启用；`/search`、`/extract`，key 在 Authorization 头，432 / 433 视为额度用完）直接取回正文，OpenRouter 出错时接替；没有支持读取的服务时 `fetch` 工具关闭。工具 `search`（搜索词）与 `fetch`（`source="S2"`，**只能读本人搜索结果里出现过的来源**）；结果放在 `<search_result id url>` 内（结束标签与属性中的引号、尖括号被处理），来源编号按成员在本桌内连续（S1、S2…，存在 `tool_calls.input.sources`）。**来源标注检查**（`TableContext.citation_problems()`，并入防偷懒检查）：引用了没检索到的编号，或本步骤搜索过却一条都没标注 → 打回重做（`tools.search.require_citations`）。成员检索到的来源以 `<sources>` 块随答案交给评审者与统筹（`member_notes()` = 文件 + 来源）。每次搜索 / 读取记一次 `calls`（`role="tool"`、渠道为搜索服务名、费用按服务返回或配置单价），因而进入月 / 日预算与按渠道花费；预估中能搜索的步骤每个座位按 `estimate.searches` / `fetches` 次数加上搜索费用。题目和搜索词会发给搜索服务。
- 对外：会话详情的 `files`、`tool_calls`（匿名揭晓前经身份遮蔽）；`GET /api/sessions/{id}/files/{fid}`（始终 `attachment` 下载 + `nosniff` + `CSP sandbox`，只有 png / jpg / gif / webp 可以 `?inline=1` 显示）与 `…/preview`（`core/preview.py`：文本 / 代码 / Markdown / CSV / xlsx / docx / pdf；HTML 与 SVG 只作为源代码）；`/api/status` 的 `tools`（各步骤工具与不可用原因）。命令行 `show --details` 显示工具调用，`roundtable files <id>` 保存文件。

### 2.10 媒体生成（`core/media/`、`steps/media.py`，阶段 19）
- **风格规范与参考图（阶段 22，`steps/style.py`）**：
  - **`style` 步骤**（讨论 / 协同共用，`_create_table` 在满足条件时插到 pipeline 最前面）：有标注为风格参考的图片附件，**而且会用到画图**（讨论模式选了图片输出；协同模式且有可用的画图模型）时触发（`style_wanted()`，预估与引擎共用；`style.enabled` 可关）。带 `vision` 标签的在场成员（最多 `style.reviewers` 位）各自看原图，按固定结构（画风 / 配色 / 线条 / 构图 / 版式 / 字体与文字排布 / 禁忌）写风格描述（`style_extract/v1`）；统筹合并成一份**风格规范**和 6–10 条可逐条判定的**风格清单**（`style_merge/v1`，JSON，清单去重截断）。没有 vision 成员时改用图片的文字版，并标注"来自文字描述，可能不准"；合并失败则规范 = 各份描述的拼接、没有清单（不做逐条校验）。结果存为 `outputs` 的 `style_spec`（`StyleSpec`，恢复时不重复调用）。
  - **进入每次调用**：之后每一次模型调用都在系统提示后接 `style_guide/v1` 的说明、在第一条用户消息后接 `<style_spec>` / `<style_checklist>`（`TableContext._attach_style`，和附件同一做法，同一步骤所有成员相同；文字版来源会注明可能不准）。提取 / 合并两次调用本身不带。
  - **参考图传给画图模型**：`MediaService.generate(..., references=)`（图片；数量 `media.references.max`、每张 `max_mb`）。带参考图时优先选带 **`image_edit`** 标签（支持以参考图为输入）的模型，参考图作为 `image_url` 随提示词发出；没有这样的模型时退回只传文字，job 的 `params.warning` 与结果 `warning` 写明"没有支持参考图的画图模型，只能按文字风格规范生成"（事件 `media_warning`，界面和命令行都显示）；`media_jobs.reference_count` 记录实际传了几张。提示词里仍写入风格规范（双保险）。讨论模式的 `media` 步骤、协同模式的 `generate` 子任务、`generate_image` 工具（标签上写 `refs="1 2"`，编号 = 风格参考图的顺序，`tools/v5`）都走这条路径。
  - **对照参考图校验**：讨论模式的评审（`media_review/v2`）带着参考图（附件原图）和风格清单逐条判定 `checks`（符合 / 不符合 + 依据），**任意一条不符合即不通过**（代码保证，不信模型自己的 `satisfied`），统筹的改写意见针对不符合的条目（轮数仍受 `media.max_rounds`）。协同模式生成图片后（完成子任务与按审查修改两处，`make_media()`）立即做**风格校验**（`style_gate()`，事件 `style_checked`）：由 `media.gate_reviewers` 位**非作者**的 vision 成员对照清单判定；不通过则作者按意见改提示词（沿用 `rework_media`）并重画，最多 `media.style_retries` 次（默认 2）；仍不通过时保留最后一版并标"风格未通过"（`style_failed`，合并时 `<work style=…>` 让统筹可见，`merge/v4` 要求如实说明）。每次判定 / 改写都存库（`style_gate` / `style_redraw`，按 phase 区分），恢复时不重复调用；重画受预算和单题确认门槛约束（需要确认或预算不足时不自动重画，不在并发任务里弹卡片）。
  - 界面：提问区的「风格参考」勾选、过程区的风格规范卡片（可折叠，含清单和来源提示）、风格校验逐条通过 / 不通过、生成卡片的"参考图 N 张"与警告；命令行 `show` 有【风格规范】与校验结果。
- **模型**：图片（`image_gen`，通过对话接口返回图片）、文字转语音（`tts`）、语音转文字（`stt`，Whisper 系列）、视频（`video_gen`，异步）都是 `seat: false` 的工具模型。选择只看能力标签和档位（`pick_media_model`：先取指定档位，没有则取未分档的，再取另一档；同组内先限定在带偏好标签的模型里（语音合成：脚本主要是中文时偏好 `zh` 标签）、再限定在 `default: true` 的模型里（有的话），最后按 seed 随机，换名不影响）；同一场同种类同档位总是同一个模型（语音合成按脚本语言选，例外）。按 token 计价的语音模型用 `speech_tokens_per_char` 估算（渠道没返回费用时记账用）。`config/models.yaml` 里的媒体模型 ID 与价格来自网页搜索、**尚未核对**，用 `python scripts/check_models.py` 核对（存在性检查；列表含 `output_modalities=image / audio / speech / transcription / video`，转写模型必须用 `transcription` 才查得到；按秒 / 字符 / token 计价的媒体模型只列出远端 pricing 供人工核对）。
- **画图接口与换模型重试**：`Provider.generate_image(..., images=, api=)`；`openai_compat` 的 `api="images"` 请求 `POST images`（body：`model`、`prompt`、`n`、其余 params（去掉对话接口专用的 `modalities`/`max_tokens`）、参考图放 `images:[{image_url: data URI}]`；响应取 `data[*].b64_json` / `url`，`usage.cost` 为实际费用）——**请求/响应格式按 OpenAI 图像接口推测，尚未对真实 OpenRouter 核对**。一个画图模型的所有渠道都失败（或没返回图片）时，`MediaService.generate` 与 `generate_image` 工具自动换**同档**的其他画图模型重试（带参考图时先试带 `image_edit` 的），每次尝试一行 `media_jobs`，全部失败才报失败。
- **配置**（`roundtable.yaml` 的 `media`）：`default_tier`、`max_rounds`（生成 → 评审 → 重新生成，含第一次）、`reviewers`、`confirm_video`、语音（voice / format）、视频（时长 / 分辨率 / 轮询间隔 / 超时 / 重试 / 截帧数）、预估参数。
- **`MediaService`**：`generate(kind, prompt, Placement(table, step, round, code, subtask))` 按位置幂等（已完成的不重复生成 / 计费）；提示词发出前遮蔽身份；每次生成一行 `media_jobs`（提示词、模型、渠道、状态、花费、第几轮、尝试次数、文件），成功的同时记入 `calls`（`role="media"`），所以进入每月 / 每日预算与按渠道花费。计价：渠道返回实际费用优先，否则按 `media_price`（张 / 秒 / 分钟 / 字符），图像模型再按用量 token 或 `image_tokens` 估算。**视频**：提交 → 轮询 → 下载，任务 id 与轮询地址存库，暂停 / 重启后继续轮询同一个任务；超时（`video.timeout_s`，按提交时间的墙钟计）、任务失败或提交失败按 `submit_retries` 重新提交；轮询连续出错 5 次或鉴权 / 额度错误则失败；失败不计费。生成的文件登记在 `files`（kind image / audio / video），始终经下载接口取用。
- **讨论模式**（提问时选输出类型，存在 `sessions.choice`）：题目经 `media_brief` 改写为「商定并写出生成提示词」，流程照常（作答 → 互评 → 修订 → 汇总），汇总的最终答案就是第一版提示词；`media` 步骤：生成 → 带 `vision` 标签的在场成员评审（图片、视频截帧作为随附图片真正发给他们；非 vision 成员不参与）→ 统筹（`media_refine`）决定是否改提示词重新生成，最多 `max_rounds` 轮，最后一轮不再评审。语音只生成一次（没有可评审的画面）。没有 vision 评审者时保留第一版并提示。评审与决定存 `outputs`（`media_review`、`media_decision`），恢复时不重复调用。协同模式不提供「输出类型」选择（拒绝），由拆分决定。
- **协同模式**：`decompose/v3` 的子任务可带 `media`（image / speech / video，只接受当前有可用模型的种类，其余丢弃）；负责人用 `work_media` 写生成提示词，程序生成（第 1 轮），其他成员交叉审查（生成物随 `<files>` 转交，vision 成员收到图片 / 视频截帧），作者按审查用 `rework_media` 改提示词，提示词有变化且有有效审查时重新生成（第 2 轮）。
- **确认与预算**：视频每次生成前都必须确认（`media_gate`：卡片 `kind="media"`，选项 生成 / 不生成 / 停止，`--yes` 也不自动同意）；图片、语音只在预计超过单题门槛时确认；协同模式按「批」确认（`work` 每层、`rework` 一次）。每次生成前还会查预算（`ctx.budget_gate`），不足时弹预算卡片（继续 = 超出预算继续）。步骤开始前照常按该步预估查预算；`media` 的花费不计入超支保护（它有自己的确认）。
- **预估**（`routing/media_estimate.py`）：`Question.media` 非空时在 `reveal` 前加一项 `media`：生成费用取候选模型的平均（上限取最大）× `expected_rounds`（上限 `max_rounds`）+ 评审者与统筹改写提示词的模型调用；`POST /api/estimate` 与命令行 `estimate --media` 单列显示。协同模式的媒体子任务拆分前未知，不在预估内，由确认与预算兜住。
- **音频转写**：上传音频时优先用 `stt` 模型（`provider.transcribe`，按渠道返回的费用，否则每分钟单价 × 时长），失败或没有时回退到带 `transcribe` 标签的对话模型（旧方式）；任一可用即允许上传。
- **图像生成工具**（§2.9 `generate_image`）并入这里的档位选择（`media_tier`，默认 `default_tier`）；按张计价的图像模型在渠道没有返回实际费用时按每张单价记账。
- **对外**：会话详情 `media`（每次生成：轮次、尝试、提示词、状态、花费、文件；模型和渠道揭晓前为空，提示词经遮蔽）、`media_kind` / `media_tier`；`/api/status` 的 `media`（各种类是否可用及原因、两个档位是否有模型，不含模型名）。事件 `media_started` / `media_progress` / `media_done` / `media_failed`（只含种类、轮次、状态）。网页：提问区「输出」选择（文字 / 图片 / 语音 / 视频，不可用的灰掉并说明原因）与「普通 / 高质量」；过程区每次生成一张卡片（第几轮、状态、花费、提示词、图片 / 音频 / 视频播放器与下载），评审与统筹决定逐条显示；面板「媒体」标签列出全部生成与成果。命令行 `ask --media image|speech|video --media-tier budget|flagship`、`estimate --media`；`show` 有「媒体生成」段；`files` 一并保存媒体文件。视频截帧用 PyAV（可选依赖 `pip install 'roundtable[media]'`；没有时评审者只看文字说明并被告知）。

---

## 3. 中立性规则（代码保证 + 测试）

1. 同一步骤所有组员使用完全相同的提示词和参数（步骤参数在 `roundtable.yaml` 的 `step_params`；模型自身必需的参数在 `models.yaml`）。
2. **匿名开启时发给模型的内容只用塔罗牌代号**，不出现模型 id、厂商名、昵称；自报身份（"作为 GPT……"、"我是 Claude"）在转给其他模型前遮蔽，但题目和附件里本来就有的名字保留。**匿名关闭时不做任何身份遮蔽**，成员互称"昵称·模式"。
3. 代号与模型的对应每题随机生成（塔罗牌随机抽取，座次随机；昵称随模型走）。
4. **不能自评**：互评时评审者永远看不到自己的答案作为被评对象；协同模式的交叉审查永远不分给作者本人。
5. 每个评审者看到的他人答案顺序独立随机打乱；统筹看到的答案顺序也随机。
6. 统筹不兼任同场组员。
7. **匿名开启时**，发给任何模型的内容不含模型 id、厂商名、昵称；**匿名关闭时**不遮蔽（`IdentityScrubber.bound(context, enabled=)`，引擎按会话的匿名开关给 `TableContext`、媒体服务、工具箱、附件预处理分别绑定）。**匿名开启时揭晓前**，界面和 API 响应也不含模型、渠道、昵称（有测试覆盖事件、会话详情、贡献、发给模型的每条消息）。**渠道名同样会暴露厂商**（如 `anthropic`），因此揭晓前单次调用不显示渠道；用量面板可以显示按渠道汇总的花费。揭晓后每次调用都显示所走渠道和切换记录。
8. 抽座位、选统筹、分配代号、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。
9. **无品牌偏好**：路由与分配只看档位、能力标签和价格（规划员按预计调用成本选最便宜的）；所选档位全员上桌，统筹在场内均匀随机（轮换）。测试验证：把所有厂商和 id 换名后同一 seed 的结果完全一致；`allocation` / `routing` / `prompts` / `budget` 的源码中不得出现模型名或厂商名。
10. 匿名开启时，组员输出转给其他模型前遮蔽配置中所有模型 id、厂商名、别称、昵称和渠道上的模型名；题目和附件本身出现的名称保留。匿名关闭时不遮蔽。
11. **工具对所有成员相同**：同一步骤可用的工具、限额、工具说明完全一样（只看配置和当前可用性，不看是哪个模型）；工具模型（`seat: false`，如图像生成）从不上桌。

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
- API key **只能**放在项目根目录 `.env`（在 `.gitignore` 中），仓库只提交 `.env.example`。所有 key 都可选：`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`XAI_API_KEY`、`DEEPSEEK_API_KEY`、`TAVILY_API_KEY`（联网搜索备选；默认的搜索用 `OPENROUTER_API_KEY`）。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照、前端代码与 API 响应中。
- 读入的 key 一律包成 `Secret`：`repr` / `str` / 格式化只显示 `***`，不可序列化；**只有适配器（模型渠道与搜索服务）在组装请求头时调用 `reveal()`**（架构测试限定了允许的文件）。
- 外部返回的错误文本先脱敏、截断再放进异常；异常用 `from None` 切断原始异常链。
- 每个 `Secret` 自动登记到进程级脱敏表，日志记录创建时统一脱敏（覆盖第三方库的 DEBUG 日志和回溯）。
- 测试用的假 key 用字符串拼接生成，避免被仓库密钥扫描误报；提交前检查 diff。

## 5.1 产品原则（日常模式与群聊，适用于 `docs/PLAN.md` 的 v3 方案）
1. 成员**坦诚自己是 AI**，不假装是真人、不编造现实经历；被问到时直接承认。
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

## 7. 目录约定

```
README.md      安装、.env、加模型 / 步骤 / 提示词版本、上传、沙箱、搜索与隐私
.github/workflows/ci.yml   ruff + pytest（含 Playwright 端到端）
config/        models.yaml  roundtable.yaml  personas.yaml  routing.yaml
prompts/       planner/ answer/ answer_quick/ review/ revise/ synthesize/ redo/
               attachments/ describe_image/ transcribe/ tools/（v4） image_gen/ web_search/ web_fetch/
               decompose/（v4） decompose_retry/ volunteer/ assign/（v3） work/ work_<类型>/ cross_review/ rework/ merge/
               media_brief/ media_review/ media_refine/ work_media/ rework_media/  versions.lock
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、适配器（openai_compat/anthropic/gemini/fake）、渠道路由、密钥与脱敏、计费
    prompts/       提示词加载与渲染
    allocation/    按档位/标签抽取、代号、互评分配（排除自评）、乱序、身份遮蔽
    routing/       规则判断、规划员、方案与阵容、花费预估、用户模式、升级、每题记录
    storage/       SQLite 迁移、Repository、揭晓前的匿名视图
    attachments/   上传文件的识别、文字提取、存放、图片文字版 / 音频转写、发给模型时的呈现
    media/         媒体生成：模型选择（标签 + 档位）、计价、MediaService（图片 / 语音 / 视频异步任务）、视频截帧
    tools/         工具协议、沙箱（wasm / docker，runner.mjs）、工作目录与文件收集、ToolBox（执行与额度）
    search/        联网搜索服务（注册、OpenRouter 搜索（默认）、Tavily、按顺序切换的 SearchService）
    preview.py     成员生成的文件的预览
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
scripts/         check_models.py（核对 OpenRouter 渠道的模型 ID 与价格；带 vision / image_edit 标签的模型还核对远端 input_modalities 含 image）  lock_prompts.py（登记提示词版本）
                 setup_sandbox.py（安装代码运行沙箱）
sandbox/         Dockerfile（可选的 docker 沙箱）、README.md
.env.example
```

## 8. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
python scripts/lock_prompts.py      # 新增提示词版本后登记
roundtable estimate '题目'           # 提交前预估：各模式 × 各档位的上桌人数与花费（不调用模型）
roundtable models                   # 查看模型、档位、可用渠道与预算
roundtable stats                    # 各模型的历史贡献
roundtable show <id> --costs        # 每步预估与实际花费、每次调用的 token
roundtable export <id> [-o 文件]     # 导出完整记录（UTF-8 带 BOM，含花费明细）
roundtable ask '题目'                # 节电模式（便宜档全员）上桌（PowerShell 中题目用单引号）
roundtable ask --tier flagship --anonymous '题目'   # 全力模式（旗舰档全员）、匿名
roundtable ask --mode collab '题目'  # 协同模式：拆分子任务、分工完成、合并
roundtable ask --attach 图.png --attach 讲义.pdf '题目'   # 带附件
roundtable files <id> [-o 目录]      # 保存成员生成的文件（含图片 / 音频 / 视频）
roundtable ask --media image '画一只猫'   # 输出图片（--media speech / video；--media-tier flagship 用高质量档）
python scripts/setup_sandbox.py     # 一次性安装代码运行沙箱（--check 只检查）
uvicorn roundtable.api.app:app --reload   # 浏览器打开 http://127.0.0.1:8000
playwright install chromium        # 首次运行前端端到端测试前
```
