# CLAUDE.md — Roundtable（圆桌）

多个 AI 像真人学习小组一样完成作业：**先讨论分工是否合适，再分工、协作、互相纠错**，不各自答题、不敷衍附和。
作业可跨学科（理科、文科、艺术、设计、音视频），产出可以是文字、图片、音频、动画、文档。

- 需求：`docs/REQUIREMENTS_v3.md`（取代 v1/v2）
- 界面与交互样板：`docs/mockup.html`（剧本演示，实现时以它的布局、流程和交互为准）
- 实施计划：`docs/PLAN.md`，按阶段推进，不要跳阶段

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 1. 两层结构

| | 里层（统筹层） | 表层（作业层） |
|---|---|---|
| 角色 | 统筹 Coordinator（规划 + 调度）、记录官 Scribe | 组员 Member（默认 4 人） |
| 风格 | 理性化：结构化 JSON，只说事实和数字 | 拟人化：有人设，像同学聊天 |
| 界面 | 右侧里层面板 | 左侧圆桌 + 中间群聊 |

- **统筹**：读题、拆交付物和领域、初步分工、定义流水线、预估轮数/token/媒体费用、推进流程、决定何时问用户、检测"连续无新内容"并点名。
- **记录官**：默认自动选最便宜的模型；不参与讨论；维护会议记录、响应"查原文"、统计用量。
- **组员**：讨论分工 → 制作 → 改进 → 核对。

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动
- `config/models.yaml`：`id`、`provider`、`model`、`vendor`、`price`、`tags`、`enabled`、可选 `params`。
  能力标签含领域类（`math`、`writing`、`code`、`design`…）与媒体类（`vision`、`image_gen`、`tts`、`transcribe`、`video_gen`）。
- `config/personas.yaml`：每个模型的昵称、性格、口头禅、专长；匿名代号池（甲乙丙丁…）。
- `config/roundtable.yaml`：座位数、轮数阈值、token 阈值、预算、档位定义、发言上限、上下文轮数、防敷衍开关与参数、流程步骤顺序、里层轮换规则。
- 加模型 / 人设 / 档位 **只改配置**。禁止针对具体模型或厂商写分支（`if model_id == ...`、`if vendor == ...`）；分工只看标签。
- 缺少某媒体能力标签的模型时，界面明确提示该功能不可用，而不是报错。
- 所有配置 pydantic 校验，失败给出清晰报错。

### 2.2 Provider 适配器
- `core/providers/base.py`：`Provider` 抽象类，按能力分方法（文本补全、图像生成、TTS、转写），统一返回含用量与费用的结果。
- 默认 `openrouter`；预留 `direct`、`local`；测试用 `fake`。按 `provider` 名称从注册表查找。
- 费用：**优先使用 OpenRouter 返回的实际费用**（有些模型分时段计价），配置里的价格只用于事前预估。
- 支持的模型开启 prompt caching。

### 2.3 流程步骤 = 插件
- 步骤（plan、discuss_division、confirm_task、work_round、pair_check、compose、reveal…）实现 `Step` 协议并注册。
- 顺序由 `config/roundtable.yaml` 的 `pipeline:` 决定；编排引擎不硬编码步骤。
- 每步结束状态落库；可暂停、关页面后从数据库恢复，恢复时不重复已完成的调用。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`（coordinator、scribe、member、reviewer、checker…）。代码中不得内联提示词正文。
- 使用的版本由配置指定，随每次调用入库（版本号 + 内容哈希）。改提示词 = 新建版本文件。

### 2.5 里层结构化输出
- 一律 JSON + pydantic schema 校验；解析失败重试一次，仍失败则降级并记录。
- 确认卡片固定格式：**现状 / 选项 / 各选项代价（轮数、token、费用）/ 推荐**；按钮：各选项、修改（可附文字）、停止。

### 2.6 持久化：SQLite
- 存：会话、座位与角色、人设与代号映射、随机种子、每次调用（角色、提示词版本、token、费用、耗时、错误）、群聊消息、附件、产物及其所有版本、会议记录每个版本、子任务与流水线、结对核对结果、确认点及用户回复、用户上传的头像。
- 数据访问只经 `core/storage/`（Repository 模式）；表结构变更走版本化迁移。
- 大文件（附件、产物）存在本地数据目录，库中只存路径、哈希、元数据（头像除外）。

### 2.7 分层
- `core/`：纯业务逻辑，**禁止 import fastapi / starlette / uvicorn / streamlit**（测试守卫）。
- `api/`：FastAPI 路由 + SSE 推送，只调用 `core/service.py`。
- `web/`：静态前端（由 `docs/mockup.html` 演化），只通过 HTTP/SSE 与后端通信。

---

## 3. 中立性规则（代码保证 + 测试）

1. 所有组员使用同一份提示模板，只有人设字段不同。
2. **发给模型的内容只用代号（甲乙丙丁），不出现模型名、厂商名、昵称**；人设只展示给用户。
3. 自报模型身份（"作为 GPT……"、"我是 Claude"）在发给其他模型前遮蔽。
4. 里层模型不兼任同场组员；统筹按配置规则轮换。
5. **作者不核对自己的作品**；两名核对者互不可见、尽量跨厂商；结果以随机顺序呈现给里层。
6. 匿名模式下，揭晓前 API 不返回任何模型身份。
7. 抽座位、分配人设（匿名模式）、结对、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。

---

## 4. 防敷衍规则
- 每次发言必须含新内容：新信息、具体修改建议、指出错误、提出问题或交付产物。纯附和计为"跳过本轮"。
- 审稿/核对必须给出具体位置和修改建议。
- 发现错误必须指出；被指出后要么改正要么给出理由。
- 检测方式：代码规则先判（零成本），模糊情况由记录官在每轮的结构化输出中一并判定；统筹据此点名或推进。

---

## 5. 省 token 规则
- 组员上下文 = 当前会议记录 + 最近 K 轮原文（默认 2）+ 自己任务相关的上下文；不发完整历史。
- 关键原文由**代码**从原消息逐字复制并校验，不靠记录官手抄。
- 发言上限默认 200 字（产物不限），无新内容就跳过。
- 记录每次调用的 token、费用、媒体生成费，界面实时显示，预算条（默认 $100）。

---

## 6. 用户打扰规则
只在以下情况暂停问用户：
1. 任务确认（开始前，同时提示可上传参考资料）
2. 预估轮数 > 阈值 → 简单 / 中等 / 完整 三档
3. 决定性结果：方向选择（可附缩略图）、无法调和的分歧
4. 大额消耗：下一步预估 > token 阈值，或要调用付费媒体生成（真实视频生成**每次**必问）

用户可随时以"组长"身份插话，统筹把意见分派给最相关组员并写入会议记录。

---

## 7. 安全与密钥
- API key **只能**放在项目根目录 `.env`（已在 `.gitignore`），仓库只提交 `.env.example`。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照、前端代码与 API 响应中。
- 上传文件做类型/大小校验；文档解析在受限条件下进行（不执行宏、不加载外部资源）。
- 提交前检查 diff 中没有密钥。

---

## 8. 工作流程
- **每个阶段完成后 git 提交**，提交信息注明阶段（如 `phase 4: allocation`）。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要有测试**，`tests/` 结构与 `src/` 对应；功能与测试同一提交。
- 测试不联网、不依赖真实 key；数据库测试用临时文件或 `:memory:`；媒体生成用 Fake provider 产出占位文件。

---

## 9. 目录约定

```
config/        models.yaml  roundtable.yaml  personas.yaml
prompts/       <role>/v<n>.md
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、注册表、openrouter/direct/local/fake、计费
    prompts/       提示词加载与渲染
    allocation/    里层角色、座位、人设/代号、结对、核对人选、身份遮蔽
    storage/       SQLite repository 与迁移
    scribe/        会议记录、原文校验、查原文
    inner/         统筹：计划/流水线 schema、分工更新、预估与档位、确认卡片
    surface/       群聊引擎、发言顺序、防敷衍检测、上下文构建、流水线交接
    media/         输入解析（文档/转写/抽帧/读图路由）、输出生成（图/音/动画/文档）、产物版本
    orchestrator/  步骤插件、编排引擎、暂停/恢复、失败处理
    service.py     对外 facade
  api/           FastAPI 应用、路由、SSE
web/             index.html、js/、css/（由 mockup 演化）
tests/
docs/            REQUIREMENTS_v3.md  PLAN.md  mockup.html
.env.example
```

## 10. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
uvicorn roundtable.api.app:app --reload
```
