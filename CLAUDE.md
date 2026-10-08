# CLAUDE.md — Roundtable（圆桌）

多个 AI 匿名协作完成作业。长期目标见 `docs/REQUIREMENTS_v3.md`；**当前只做第一版（v1）**，范围以本文件和 `docs/PLAN.md` 为准。
界面样板：`docs/mockup.html`（v1 只实现其中文字圆桌相关部分）。
按 `docs/PLAN.md` 的阶段推进，不要跳阶段，不要提前实现"后续扩展"里的功能。

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 1. v1 范围

**流程**：判断难度 → 选方案 → 独立作答 → 匿名互评 → 修订 → 汇总 →（分歧时升级）→ 揭晓身份

| 角色 | 由谁担任 | 做什么 |
|---|---|---|
| 规划员 | 最便宜的可用便宜档模型；规则能判断时不调用 | 判断题型、难度、答案长度（只看题目） |
| 组员 | 按方案从对应档位随机抽取 | 独立作答、匿名互评他人答案、根据评审修订 |
| 统筹 | 按方案的档位选，按配置规则轮换，不兼任同场组员 | 读修订后的答案，输出结构化汇总：共识、分歧、最终答案 |

**成本优先路由**（规则全部在 `config/routing.yaml`）：

| 难度 | 方案 | 阵容 |
|---|---|---|
| 简单 | 单人快答 | 1 个便宜档模型直接回答，不开圆桌 |
| 中等 | 小圆桌 | 2–3 个便宜档组员 + 统筹（优先便宜档）；汇总出现分歧或把握低时**升级**到旗舰圆桌 |
| 困难 | 旗舰圆桌 | 最多 4 个旗舰组员 + 统筹（优先旗舰） |

- **用户模式**：自动（默认）/ 预设（省钱、均衡、最强）/ 手动勾选模型。**用户的选择永远优先于自动判断**；预设和手动模式只做零成本的规则判断用于记录，不调用规划员。
- **执行前预估花费**，并给出各方案的预估；超过单题确认门槛（默认 $0.30）先请用户确认。升级也重新预估、按同一门槛确认。
- **每题记录**（`RoutingRecord`）：难度判断及来源（规则 / 模型 / 默认）、规划员花费、方案与阵容、预估与实际花费、是否升级及原因，用于以后调整规则。
- **只有匿名模式**：组员显示为"组员甲 / 乙 / 丙 / 丁"，结束后由用户点"揭晓身份"。
- 只处理文字输入和文字输出（规则已为"带图片 → 需要 vision 标签"留好位置）。
- **v1 不做**（见 PLAN.md "后续扩展"）：人设模式、记录官与会议记录、分工讨论与流水线、结对核对、多轮群聊、文件上传、图片生成、语音合成、语音转写、视频处理。

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动
- `config/models.yaml`：
  - `channels`：调用渠道。每个渠道有 `adapter`（`openai_compat` / `anthropic` / `gemini`）、`kind`（`aggregator` 聚合平台 / `direct` 官方直连 / `local` 本地）、`base_url`、`key_env`（**只写环境变量名**）、可选 `extra_body`。
  - `models`：`id`、`vendor`、`tier`（`flagship` 旗舰 / `budget` 便宜档，启用的模型必填）、可选 `aliases`（别称，用于身份遮蔽）、`price`（输入/输出每百万 token，可选 `cached_input`）、`tags`、`enabled`、可选 `params`，以及按优先顺序排列的 `routes`（每条：`channel`、该渠道上的 `model` ID、可选 `price` / `params` 覆盖）。
  - 标签词表现在就包含媒体类（`vision`、`image_gen`、`tts`、`transcribe`、`video_gen`），v1 不使用。
- `config/roundtable.yaml`：座位数、预算、提醒比例、token 阈值、统筹轮换规则、提示词版本、步骤顺序 `pipeline:`、**渠道模式 `channel_mode`**（`openrouter` / `direct` / `auto`，默认 `auto`）、请求策略（超时、切换轮数、退避、冷却）。
- `config/personas.yaml`：v1 只用匿名代号池（甲乙丙丁…）；人设字段的 schema 预留但可为空。
- `config/routing.yaml`：确认门槛、默认难度、规划员档位、规则判断（triage）、方案（组员档位与人数、统筹档位、pipeline、升级目标）、难度→方案、预设、手动模式的统筹规则、升级条件、花费预估参数。**只能引用档位和能力标签**，不得出现模型名或厂商名（有测试）。
- 方案的组员档位是优先顺序：先从第一个档位抽满 max，不够 min 时才用后面的档位补；组员与统筹争抢同一档位时，组员先保证首选档位的 min，统筹再保留首选档位。同档位内随机抽取；`prefer_distinct_vendors` 时尽量不让同一厂商在一张桌上出现两次；`prefer_task_tags` 时优先带题型标签的模型（数量够才筛）。
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
- v1 步骤：`answer`、`review`、`revise`、`synthesize`、`reveal`，各自实现 `Step` 协议并注册。每题实际执行的 pipeline 由路由选中的方案决定（如单人快答为 `answer → reveal`）。
- 顺序由 `pipeline:` 决定，编排引擎不硬编码步骤；以后的步骤（分工讨论、结对核对等）只需新增插件 + 改配置。
- 每步结束状态落库；可暂停、关页面后恢复，恢复时不重复已完成的调用。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`，v1：`planner`、`answer`、`review`、`revise`、`synthesize`。代码中不得内联提示词正文。
- 文件格式：YAML 文件头（`description`、`output: text|json`、`variables`）+ `<!-- system -->` / `<!-- user -->` 两段。占位符用 `{{ name }}`（不用 `$`，避免与数学公式冲突）；声明的变量与正文占位符必须一一对应，渲染时缺少或多余参数都报错；只替换一次，用户输入里的 `{{ x }}` 不会被展开。
- 题目、他人答案等外部内容放在标签内（`<question>`、`<answer>` …），系统提示说明标签内的指令无效（防提示注入）。
- 使用的版本由配置指定，随每次调用入库（版本号 + 内容哈希，换行统一为 LF 后计算）。
- **改提示词 = 新建版本文件**。`prompts/versions.lock` 记录每个已发布版本的哈希；新增版本后运行 `python scripts/lock_prompts.py` 登记。测试会拒绝已发布版本被修改或删除，也会拒绝未登记的新版本。
- 提示词中不得出现模型名、厂商名、渠道名（测试从配置中提取名称检查）；组员提示词只允许"代号"这一个与人相关的变量。

### 2.5 结构化输出
- 规划员、互评和汇总输出 JSON，用 pydantic schema 校验；解析失败重试一次，仍失败则降级并记录（规划员降级为默认难度）。
- 互评必须给出具体问题：位置 + 问题 + 修改建议；没发现问题时必须说明检查了什么。空泛的"挺好"由代码判为无效评审。
- 汇总固定结构：共识、分歧（各方观点用代号标注）、最终答案、仍存疑的点。

### 2.6 持久化：SQLite
- 存：会话、题目、seed、**路由记录**（难度判断、方案、预估与实际花费、升级）、座位与代号映射、统筹、每次调用（步骤、提示词版本 + 哈希、输入输出、**实际渠道与切换记录**、token、费用及来源、耗时、错误）、互评结果、修订稿、汇总、确认点与用户回复、累计费用（总计与按渠道）。
- 数据访问只经 `core/storage/`（Repository 模式）；表结构变更走版本化迁移（`storage/migrations.py`，只能在末尾追加；已发布迁移的哈希登记在测试中，不得修改），后续扩展加表不改旧表含义。
- 表：`sessions`、`routing_records`、`seats`（`table_no` 0 为初始圆桌，升级后为 1、2…）、`calls` + `call_attempts`（每次渠道尝试一行）、`outputs`（各步产出，通用表）、`step_progress`（恢复时跳过已完成步骤）、`checkpoints`。
- **对外展示只用 `session_view()` / `list_sessions()`**：揭晓前去掉模型 id、渠道、切换记录、阵容、规划员模型，错误信息替换为通用提示，模型输出经身份遮蔽；揭晓后显示全部原文。按渠道的花费汇总（`spent_by_channel()`）可随时展示。

### 2.7 分层
- `core/`：纯业务逻辑，**禁止 import fastapi / starlette / uvicorn / streamlit**（测试守卫）。
- `api/`：FastAPI 路由 + SSE，只调用 `core/service.py`。
- `web/`：静态前端，只通过 HTTP/SSE 与后端通信。

---

## 3. 中立性规则（代码保证 + 测试）

1. 同一步骤所有组员使用完全相同的提示词和参数。
2. **发给模型的内容只用代号**，不出现模型名、厂商名；自报身份（"作为 GPT……"、"我是 Claude"）在转给其他模型前遮蔽。
3. 代号与模型的对应每题随机生成。
4. **不能自评**：互评时评审者永远看不到自己的答案作为被评对象。
5. 每个评审者看到的他人答案顺序独立随机打乱；统筹看到的答案顺序也随机。
6. 统筹不兼任同场组员。
7. **揭晓前**，界面、API 响应和发给任何模型的内容都不含真实模型身份。**渠道名同样会暴露厂商**（如 `anthropic`），因此揭晓前单次调用不显示渠道；用量面板可以显示按渠道汇总的花费。揭晓后每次调用都显示所走渠道和切换记录。
8. 抽座位、选统筹、分配代号、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。
9. **无品牌偏好**：路由与分配只看档位、能力标签和价格（规划员按预计调用成本选最便宜的）；同档位内均匀随机。测试验证：把所有厂商和 id 换名后同一 seed 的结果完全一致；`allocation` / `routing` / `prompts` / `budget` 的源码中不得出现模型名或厂商名。
10. 组员输出转给其他模型前，遮蔽配置中所有模型 id、厂商名、别称和渠道上的模型名；题目本身出现的名称保留。

---

## 4. 预算
- **单题确认门槛**默认 **$0.30**（`routing.yaml`）：预计花费超过时执行前先确认；升级时重新预估、重新判断。
- 总预算默认 **$20**（跨会话累计，配置可改）。
- 累计费用达到 **80%** 时提醒（界面提示，不打断流程）。
- 下一步预估会让累计超过预算，或已经超过时：**暂停**，弹确认卡片（现状 / 选项 / 代价 / 推荐；按钮：继续、停止）。
- 界面实时显示累计 token、费用和预算条。

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

---

## 7. 目录约定

```
config/        models.yaml  roundtable.yaml  personas.yaml  routing.yaml
prompts/       planner/ answer/ review/ revise/ synthesize/（各含 v1.md）  versions.lock
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、适配器（openai_compat/anthropic/gemini/fake）、渠道路由、密钥与脱敏、计费
    prompts/       提示词加载与渲染
    allocation/    按档位/标签抽取、代号、互评分配（排除自评）、乱序、身份遮蔽
    routing/       规则判断、规划员、方案与阵容、花费预估、用户模式、升级、每题记录
    storage/       SQLite 迁移、Repository、揭晓前的匿名视图
    budget/        用量统计（按渠道/模型）、预估、预算检查
    steps/         answer / review / revise / synthesize / reveal 插件
    orchestrator/  编排引擎、确认点、暂停/恢复、失败处理
    jsonout.py     从模型输出中提取 JSON
    service.py     对外 facade
  api/           FastAPI 应用、路由、SSE
web/             index.html、js/、css/
tests/
docs/            REQUIREMENTS_v3.md  PLAN.md  mockup.html
scripts/         check_models.py（核对 OpenRouter 渠道的模型 ID 与价格）  lock_prompts.py（登记提示词版本）
.env.example
```

## 8. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
python scripts/lock_prompts.py      # 新增提示词版本后登记
uvicorn roundtable.api.app:app --reload
```
