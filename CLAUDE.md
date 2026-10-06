# CLAUDE.md — AI 圆桌（AI Roundtable）

多个 AI 通过 OpenRouter **匿名协作**解答作业题的辅助平台。
流程：独立作答 → 匿名互评 → 修订 → 轮值主持汇总共识与分歧 → 揭晓身份。

技术栈：Python 3.11+、Streamlit、SQLite、pytest。

实施计划见 `docs/PLAN.md`，按阶段推进，不要跳阶段。

---

## 1. 架构规则（必须遵守）

### 1.1 模型注册表：只改配置就能加模型
- 所有模型定义在 `config/models.yaml`，每条至少包含：`id`（内部唯一名）、`provider`（适配器名）、`model`（提供方的模型标识）、`enabled`，可选 `params`（temperature、max_tokens 等）。
- 新增模型 **只能** 改 `config/models.yaml`，不得为某个具体模型写分支代码（禁止 `if model_id == "..."`）。
- 配置用 pydantic 校验，加载失败要给出清晰报错。

### 1.2 Provider 适配器模式
- 统一接口：`core/providers/base.py` 定义 `Provider` 抽象类（如 `complete(messages, model, params) -> Completion`）。
- 默认适配器：`openrouter`。预留：`direct`（直连官方 API）、`local`（本地模型，如 Ollama / OpenAI 兼容端点）。
- 适配器通过注册表按 `provider` 名称查找，新增适配器不修改调用方代码。
- 测试一律使用 `fake` 适配器或 HTTP mock，**测试不得访问真实网络**。

### 1.3 席位数动态
- 每题上桌席位数 `N` 来自配置（`config/roundtable.yaml`），可被 UI 覆盖。
- 可用模型数 > N 时，**每题随机抽取 N 个**上桌；可用模型数 < N 时按实际数量开桌并提示（至少 2 个）。
- 随机数使用可注入的 `random.Random(seed)`，seed 写入数据库，保证可复现。

### 1.4 流程环节 = 插件
- 每个环节（作答、互评、修订、主持汇总、揭晓……）是一个实现 `Stage` 协议的插件，通过注册表按名称注册。
- 执行顺序由 `config/roundtable.yaml` 的 `pipeline:` 列表决定；引擎只按顺序执行，不硬编码环节。
- 新增环节 = 新增一个插件文件 + 改配置，不改引擎。

### 1.5 提示词外置且带版本
- 所有提示词放在 `prompts/` 下，命名 `prompts/<stage>/v<版本>.md`（如 `prompts/review/v1.md`）。
- 代码中不得出现内联提示词正文。
- 使用的提示词版本由配置指定，并随每次调用写入数据库。
- 修改已发布提示词时 **新建版本文件**，不改旧版本（保证历史讨论可追溯）。

### 1.6 持久化：SQLite
- 每场讨论（题目、席位、匿名标签映射、每个环节的每次调用、提示词版本、模型、token/耗时、最终汇总、seed）完整写入 SQLite。
- 数据访问集中在 `core/storage/`（Repository 模式），其他模块不直接写 SQL。
- 表结构变更通过版本化的迁移脚本完成。

### 1.7 core 与 ui 分离
- `src/roundtable/core/`：纯业务逻辑，**禁止 import streamlit**。
- `src/roundtable/ui/`：Streamlit 界面，只通过 core 暴露的服务接口（facade）调用，不直接调用 Provider 或数据库。
- 有测试检查 core 中不出现 `streamlit` 依赖。

---

## 2. 中立规则（公平性，必须遵守）

1. **统一提示词**：同一环节所有模型使用完全相同的提示词与参数（模型自身 `params` 的差异仅限于 API 必需项）。
2. **匿名**：讨论过程中模型只以匿名标签（如「席位 A / B / C」）出现；标签与模型的映射每题随机生成。模型输出中可能暴露身份的自称（如 "As GPT…"、"我是 Claude"）在转给其他模型前要做脱敏。
3. **随机顺序**：每个评审者看到的他人答案顺序独立随机打乱；席位标签分配也随机。
4. **不能自评**：互评环节评审者 **永远看不到自己的答案**作为被评对象；由代码强制保证并有测试覆盖。
5. **轮值主持**：主持人从本题上桌席位中轮换/随机产生，主持人同样只看到匿名内容。
6. **最后才揭晓**：模型身份只在流程最后的「揭晓」环节对用户展示；在此之前 UI 和任何发给模型的内容都不得包含真实模型名。

---

## 3. 安全与密钥

- API key **只能** 放在项目根目录 `.env`，通过 `python-dotenv` / 环境变量读取。
- `.env` 必须在 `.gitignore` 中；仓库只提交 `.env.example`（占位符，无真实值）。
- key 不得出现在：代码、配置 YAML、提示词、日志、异常信息、SQLite、测试快照中。
- 提交前检查 diff 中没有密钥。

---

## 4. 工作流程

- **每个阶段完成后进行一次 git 提交**（阶段内可多次小提交），提交信息注明阶段，例如 `phase 2: provider adapters`。
- 提交前必须：`ruff check`、`ruff format --check`、`pytest` 全部通过。
- **每个模块都要写测试**，放在 `tests/` 下与源码结构对应；新功能与测试同一次提交。
- 测试不访问网络、不依赖真实 key；数据库测试使用临时文件或 `:memory:`。

---

## 5. 目录约定

```
config/
  models.yaml          # 模型注册表
  roundtable.yaml      # 席位数、pipeline 顺序、提示词版本、主持策略
prompts/
  <stage>/v1.md        # 版本化提示词
src/roundtable/
  core/
    config/            # 配置加载与校验
    providers/         # Provider 基类、注册表、openrouter/direct/local/fake
    prompts/           # 提示词加载与渲染
    seating/           # 抽席、匿名标签、乱序、脱敏
    stages/            # 环节插件
    pipeline/          # 引擎与上下文
    storage/           # SQLite repository 与迁移
    service.py         # 对 UI 暴露的 facade
  ui/
    app.py             # Streamlit 入口
tests/                 # 与 src 结构对应
docs/PLAN.md           # 分阶段实施计划
.env.example
```

## 6. 常用命令

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
streamlit run src/roundtable/ui/app.py
```
