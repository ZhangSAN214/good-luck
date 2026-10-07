# CLAUDE.md — Roundtable（圆桌）

多个 AI 匿名协作完成作业。长期目标见 `docs/REQUIREMENTS_v3.md`；**当前只做第一版（v1）**，范围以本文件和 `docs/PLAN.md` 为准。
界面样板：`docs/mockup.html`（v1 只实现其中文字圆桌相关部分）。
按 `docs/PLAN.md` 的阶段推进，不要跳阶段，不要提前实现"后续扩展"里的功能。

技术栈：Python 3.11+、pydantic、SQLite、FastAPI + SSE、原生 JS 前端（无构建步骤）、pytest、Playwright（端到端）。
模型统一走 OpenRouter（默认），预留直连与本地。

---

## 1. v1 范围

**流程**：独立作答 → 匿名互评 → 修订 → 汇总 → 揭晓身份

| 角色 | 由谁担任 | 做什么 |
|---|---|---|
| 组员（默认 4 个座位） | 每题从可用模型中随机抽取 | 独立作答、匿名互评他人答案、根据评审修订 |
| 统筹 | 从可用模型中按规则轮换，不兼任同场组员 | 读修订后的答案，输出结构化汇总：共识、分歧、最终答案 |

- **只有匿名模式**：组员显示为"组员甲 / 乙 / 丙 / 丁"，结束后由用户点"揭晓身份"。
- 只处理文字输入和文字输出。
- **v1 不做**（见 PLAN.md "后续扩展"）：人设模式、记录官与会议记录、分工讨论与流水线、结对核对、多轮群聊、文件上传、图片生成、语音合成、语音转写、视频处理。

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动
- `config/models.yaml`：`id`、`provider`、`model`、`vendor`、`price`（输入/输出每百万 token）、`tags`、`enabled`、可选 `params`。
  标签词表现在就包含媒体类（`vision`、`image_gen`、`tts`、`transcribe`、`video_gen`），v1 不使用。
- `config/roundtable.yaml`：座位数、预算、提醒比例、token 阈值、统筹轮换规则、提示词版本、步骤顺序 `pipeline:`。
- `config/personas.yaml`：v1 只用匿名代号池（甲乙丙丁…）；人设字段的 schema 预留但可为空。
- 加模型只改配置。禁止针对具体模型或厂商写分支（`if model_id == ...`、`if vendor == ...`）。
- 所有配置 pydantic 校验，失败给出清晰报错。

### 2.2 Provider 适配器
- `core/providers/base.py`：`Provider` 抽象类。v1 实现文本补全；图像生成、TTS、转写的方法签名**现在就定义**，默认抛出"不支持"。
- 默认 `openrouter`；预留 `direct`、`local`；测试用 `fake`。按 `provider` 名称从注册表查找。
- 费用：优先使用 OpenRouter 返回的实际费用，配置里的价格只用于事前预估。

### 2.3 流程步骤 = 插件
- v1 步骤：`answer`、`review`、`revise`、`synthesize`、`reveal`，各自实现 `Step` 协议并注册。
- 顺序由 `pipeline:` 决定，编排引擎不硬编码步骤；以后的步骤（分工讨论、结对核对等）只需新增插件 + 改配置。
- 每步结束状态落库；可暂停、关页面后恢复，恢复时不重复已完成的调用。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`，v1：`answer`、`review`、`revise`、`synthesize`。代码中不得内联提示词正文。
- 使用的版本由配置指定，随每次调用入库（版本号 + 内容哈希）。改提示词 = 新建版本文件。

### 2.5 结构化输出
- 互评和汇总输出 JSON，用 pydantic schema 校验；解析失败重试一次，仍失败则降级并记录。
- 互评必须给出具体问题：位置 + 问题 + 修改建议；没发现问题时必须说明检查了什么。空泛的"挺好"由代码判为无效评审。
- 汇总固定结构：共识、分歧（各方观点用代号标注）、最终答案、仍存疑的点。

### 2.6 持久化：SQLite
- 存：会话、题目、seed、座位与代号映射、统筹、每次调用（步骤、提示词版本 + 哈希、输入输出、token、费用、耗时、错误）、互评结果、修订稿、汇总、确认点与用户回复、累计费用。
- 数据访问只经 `core/storage/`（Repository 模式）；表结构变更走版本化迁移，后续扩展加表不改旧表含义。

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
7. **揭晓前**，界面、API 响应和发给任何模型的内容都不含真实模型身份。
8. 抽座位、选统筹、分配代号、排序全部用可注入的 `random.Random(seed)`，seed 存库，可复现。

---

## 4. 预算
- 总预算默认 **$20**（跨会话累计，配置可改）。
- 累计费用达到 **80%** 时提醒（界面提示，不打断流程）。
- 下一步预估会让累计超过预算，或已经超过时：**暂停**，弹确认卡片（现状 / 选项 / 代价 / 推荐；按钮：继续、停止）。
- 界面实时显示累计 token、费用和预算条。

---

## 5. 安全与密钥
- API key **只能**放在项目根目录 `.env`（在 `.gitignore` 中），仓库只提交 `.env.example`。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照、前端代码与 API 响应中。
- 提交前检查 diff 中没有密钥。

---

## 6. 工作流程
- **每个阶段完成后 git 提交**，提交信息注明阶段（如 `phase 4: allocation`）。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要有测试**，`tests/` 结构与 `src/` 对应；功能与测试同一提交。
- 测试不联网、不依赖真实 key；数据库测试用临时文件或 `:memory:`。

---

## 7. 目录约定

```
config/        models.yaml  roundtable.yaml  personas.yaml
prompts/       answer/ review/ revise/ synthesize/   （各含 v1.md）
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、注册表、openrouter/direct/local/fake、计费
    prompts/       提示词加载与渲染
    allocation/    选统筹、抽座位、代号、乱序、排除自评、身份遮蔽
    storage/       SQLite repository 与迁移
    budget/        用量统计、预估、预算检查
    steps/         answer / review / revise / synthesize / reveal 插件
    orchestrator/  编排引擎、确认点、暂停/恢复、失败处理
    service.py     对外 facade
  api/           FastAPI 应用、路由、SSE
web/             index.html、js/、css/
tests/
docs/            REQUIREMENTS_v3.md  PLAN.md  mockup.html
scripts/         check_models.py（本地核对 OpenRouter 模型 ID 与价格）
.env.example
```

## 8. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
uvicorn roundtable.api.app:app --reload
```
