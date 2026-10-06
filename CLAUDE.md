# CLAUDE.md — Roundtable（圆桌）

让多个 AI **像人类学习小组一样**协作完成作业：讨论、分工、接话、质疑、配合、汇总。
模型通过 OpenRouter（默认）调用，组员匿名，真实身份只在最后揭晓。

技术栈：Python 3.11+、Streamlit、SQLite、pydantic、pytest。
实施计划见 `docs/PLAN.md`，按阶段推进，不要跳阶段。

---

## 1. 两层结构（核心概念）

| | 里层（统筹层） | 表层（作业层） |
|---|---|---|
| 角色 | 规划者 Planner、调度者 Dispatcher、记录官 Scribe | 匿名组员 Member |
| 风格 | 理性化：无名字无性格，只输出结构化 JSON | 拟人化：有名字有性格，像同学群聊 |
| 用户可见 | 侧边面板 | 主界面群聊 |

- **规划者**：理解题目、拆子任务、估算轮数/token/费用。
- **调度者**：按能力标签分配子任务、组织结对、推进流程、决定何时问用户、何时结束。
- **记录官**：维护会议记录（minutes）、响应"查原文"、判断是否该结束、统计用量。不参与讨论、不发表观点。
- **组员**：群聊讨论 + 完成分配到的子任务。

---

## 2. 架构规则（必须遵守）

### 2.1 配置驱动，不为具体模型写代码
- `config/models.yaml`：每个模型含 `id`、`provider`、`model`、`vendor`、`price`（输入/输出每百万 token）、`tags`（能力标签，如 `math`、`derivation`、`writing`、`code`、`research`、`long_context`）、`enabled`、可选 `params`。
- `config/roundtable.yaml`：表层座位数、轮数阈值、token 阈值、档位定义、发言长度上限、上下文保留轮数、流程步骤顺序、里层角色分配规则。
- `config/personas.yaml`：组员名字池、性格池。
- 新增模型 / 性格 / 档位 **只改配置**。禁止 `if model_id == ...`、`if vendor == ...` 之类的分支；调度只看标签。
- 所有配置用 pydantic 校验，失败给出清晰报错。

### 2.2 Provider 适配器
- `core/providers/base.py` 定义 `Provider` 抽象类：`complete(messages, model, params) -> Completion`（含文本、token 用量、耗时）。
- 默认 `openrouter`；预留 `direct`（官方 API 直连）、`local`（OpenAI 兼容本地端点）；测试用 `fake`。
- 适配器按 `provider` 名称从注册表查找。支持的模型启用 prompt caching。
- 每次调用的 token 与费用都要计算并入库。

### 2.3 流程步骤 = 插件
- 每个步骤（plan、confirm_task、discuss、execute、reconcile、compose、reveal……）实现 `Step` 协议并注册。
- 顺序由 `config/roundtable.yaml` 的 `pipeline:` 决定；编排引擎不硬编码步骤。
- 每步执行后状态落库，保证可暂停、可从数据库恢复。

### 2.4 提示词外置且带版本
- `prompts/<role>/v<n>.md`（role：planner、dispatcher、scribe、member、…）。代码中不得内联提示词正文。
- 使用的版本由配置指定，并随每次调用写入数据库（版本号 + 内容哈希）。
- 改已有提示词 = **新建版本文件**，旧版本不动。

### 2.5 里层结构化输出
- 里层一律输出 JSON，用 pydantic schema 校验；不寒暄、不拟人。
- 解析失败自动重试一次，仍失败则降级并记录。
- 给用户的确认请求固定格式：**现状 / 选项 / 各选项代价（轮数、token、费用）/ 推荐**。

### 2.6 持久化：SQLite
- 存：会话、座位与角色分配、随机种子、每次调用（角色、提示词版本、输入输出、token、费用、耗时、错误）、群聊发言、会议记录**每个版本**、子任务、结对结果与比对、确认点及用户回复。
- 数据访问只经 `core/storage/`（Repository 模式），其他模块不写 SQL；表结构变更走版本化迁移。

### 2.7 core 与 ui 分离
- `src/roundtable/core/` **禁止 import streamlit**（有测试守卫）。
- `src/roundtable/ui/` 只调用 `core/service.py` 暴露的接口，不直接碰 Provider 或数据库。

---

## 3. 中立性规则（必须由代码保证并有测试）

1. 所有组员使用相同的系统提示模板，只有名字和性格字段不同；同一角色参数一致。
2. 组员之间只以匿名名字出现；能力标签只有里层可见。
3. 性格随机分配，**与厂商无关**。
4. 里层模型（规划者/调度者/记录官）**不兼任同场表层组员**。
5. 结对：同一子任务两名组员各自独立完成，**彼此看不到过程和结果**；尽量来自不同厂商。
6. 任何组员不得核对**自己或搭档**的结果；比对时结果以随机顺序呈现。
7. 组员输出中自报模型身份（"作为 GPT……"、"我是 Claude"）在其他模型看到前遮掉。
8. 真实身份只在 reveal 步骤显示；此前 UI 和发给任何模型的内容都不得含真实模型名。
9. 抽座位、分配性格、结对、排序全部使用可注入的 `random.Random(seed)`，seed 存库，可复现。

---

## 4. 省 token 规则
- 组员上下文 = 会议记录 + 最近 K 轮原文（K 可配置），不发完整历史；做子任务时只给相关上下文。
- 发言有长度上限、可"跳过"、禁止复述和客套。
- 杂活交给记录官（最便宜模型）；里层用结构化短输出。
- 界面实时显示累计 token / 费用。

---

## 5. 用户打扰规则
只在以下情况暂停问用户，其余时间不打扰：
1. 任务确认（理解 + 分工方案）
2. 预估轮数超阈值 → 提供 简单 / 中等 / 完整 三档方案
3. 决定性结果：关键结论、方向选择、无法调和的分歧
4. 下一步预估 token 超阈值

确认卡片：**继续 / 修改（可附文字）/ 停止**。等待期间可关闭页面，之后从数据库恢复。

---

## 6. 安全与密钥
- API key **只能** 放在项目根目录 `.env`（已在 `.gitignore`），仓库只提交 `.env.example`。
- key 不得出现在代码、YAML、提示词、日志、异常信息、SQLite、测试快照中。提交前检查 diff。

---

## 7. 工作流程
- **每个阶段完成后 git 提交**，提交信息注明阶段（如 `phase 4: allocation`）。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要有测试**，`tests/` 结构与 `src/` 对应；功能与测试同一提交。
- 测试不访问网络、不依赖真实 key；数据库测试用临时文件或 `:memory:`。

---

## 8. 目录约定

```
config/        models.yaml  roundtable.yaml  personas.yaml
prompts/       <role>/v<n>.md
src/roundtable/
  core/
    config/        配置加载与校验
    providers/     Provider 基类、注册表、openrouter/direct/local/fake、计费
    prompts/       提示词加载与渲染
    allocation/    选记录官、里层角色、表层座位、性格、结对、身份屏蔽
    storage/       SQLite repository 与迁移
    scribe/        会议记录、原文保留、查原文
    inner/         规划者、调度者、输出 schema、预估与档位
    surface/       群聊引擎、子任务执行、结对独立作业
    orchestrator/  步骤插件、编排引擎、确认点、暂停/恢复
    service.py     对 UI 的 facade
  ui/
    app.py         Streamlit 入口
tests/
docs/PLAN.md
.env.example
```

## 9. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
streamlit run src/roundtable/ui/app.py
```
