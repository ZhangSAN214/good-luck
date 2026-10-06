# AI 圆桌 — 分阶段实施计划

规则见根目录 `CLAUDE.md`。每个阶段结束：lint + 测试全绿 → git 提交（`phase N: ...`）。

---

## Phase 0 — 项目骨架
**产出**
- `pyproject.toml`（依赖：streamlit、httpx、pydantic、pyyaml、python-dotenv；dev：pytest、pytest-asyncio、respx、ruff）
- 目录结构（见 CLAUDE.md §5）、`.gitignore`（含 `.env`、`*.db`）、`.env.example`
- 空的 `config/models.yaml`、`config/roundtable.yaml` 示例
- 架构守卫测试：core 中不得 import streamlit；仓库中不得出现疑似 key（`sk-or-` 等）

**验收**：`pytest` 可运行且守卫测试通过。

## Phase 1 — 配置与模型注册表（`core/config`）
- pydantic 模型：`ModelSpec`（id, provider, model, enabled, params）、`RoundtableConfig`（seats, pipeline, prompt_versions, moderator_strategy, timeouts）
- `load_models()` / `load_roundtable()`：读 YAML、校验、id 唯一、provider 必须已注册
- `Settings`：从环境变量 / `.env` 读取 key（只读，不落盘）

**测试**：合法配置加载；重复 id、未知 provider、缺字段报错；禁用模型被过滤。

## Phase 2 — Provider 适配器（`core/providers`）
- `Provider` 抽象基类 + `Completion` 数据类（text, usage, latency, raw_model）
- 适配器注册表（装饰器 `@register_provider("openrouter")`）
- `OpenRouterProvider`：httpx 异步调用，超时、重试（指数退避）、错误归一化，日志中屏蔽 key
- `FakeProvider`：可编排的确定性回复，供所有测试使用
- `DirectProvider`、`LocalProvider`：接口占位 + 基本实现骨架（OpenAI 兼容 base_url）

**测试**：respx mock OpenRouter 请求/响应、重试、超时、401；Fake 行为；异常信息中无 key。

## Phase 3 — 版本化提示词（`core/prompts`）
- `prompts/answer/v1.md`、`review/v1.md`、`revise/v1.md`、`moderate/v1.md`
- 加载器：按 `(stage, version)` 读取，模板变量渲染（`string.Template` 或 Jinja2，二选一），缺变量报错
- 返回 `RenderedPrompt(text, stage, version, sha256)` 以便入库追溯

**测试**：版本解析、缺文件/缺变量报错、hash 稳定。

## Phase 4 — 抽席与匿名化（`core/seating`）
- `draw_seats(models, n, rng)`：随机抽 N 个（模型数不足时降级，<2 报错）
- `assign_labels(seats, rng)`：随机分配「席位 A/B/C…」
- `shuffled_peers(reviewer, answers, rng)`：返回排除自己后的乱序答案列表
- `pick_moderator(seats, strategy, rng)`：轮值 / 随机
- `scrub_identity(text, known_names)`：脱敏模型自称

**测试**（重点，中立规则的核心）：
- 评审者永远不在自己的被评列表中（参数化 + 多 seed 循环）
- 同 seed 可复现、不同 seed 顺序分布合理
- 标签中不含模型名；脱敏覆盖常见自称

## Phase 5 — SQLite 存储（`core/storage`）
- 表：`discussions`（题目、seed、配置快照、状态、时间）、`seats`（discussion_id、label、model_id、是否主持）、`calls`（stage、seat、prompt_version、prompt_hash、输入、输出、tokens、latency、error）、`results`（共识/分歧汇总）
- 迁移：`migrations/0001_init.sql` + `schema_version` 表
- `DiscussionRepository`：create / append_call / finish / get / list

**测试**：临时 DB 上的增删查、迁移幂等、未揭晓状态下查询接口不返回模型名（匿名视图）。

## Phase 6 — 环节插件与流程引擎（`core/stages`、`core/pipeline`）
- `Stage` 协议：`name`、`async run(ctx) -> None`；`@register_stage("answer")`
- `DiscussionContext`：题目、席位、标签映射、rng、各阶段产出、repository、provider 解析器
- 内置插件：
  1. `answer` — 各席位并发独立作答
  2. `review` — 每席位评审他人匿名答案（乱序、排除自己）
  3. `revise` — 各席位参考评审意见修订自己的答案
  4. `moderate` — 轮值主持汇总共识与分歧
  5. `reveal` — 标记揭晓，生成身份对照
- `PipelineEngine`：按 `roundtable.yaml` 的 pipeline 顺序执行，每步入库，发出进度事件（回调），单席位失败时记录并继续（可配置）

**测试**：FakeProvider 端到端跑完整流程；自定义 pipeline 顺序/跳过环节；插入一个测试用自定义插件无需改引擎；单模型失败的降级；全程发给模型的内容不含真实模型名。

## Phase 7 — 服务层 facade（`core/service.py`）
- `RoundtableService`：`start_discussion(question, seats=None, seed=None)`、`get_discussion(id, revealed=False)`、`list_history()`、`reveal(id)`
- 进度事件流（生成器/回调），供 UI 订阅
- 可选：`python -m roundtable` 命令行入口，便于不开 UI 调试

**测试**：facade 行为、未 reveal 前返回值不含模型身份。

## Phase 8 — Streamlit UI（`ui/`）
- 页面：提问（题目输入、席位数滑块、模型池预览只显示数量）、实时进度（按环节展开，匿名标签）、主持汇总（共识 / 分歧）、「揭晓身份」按钮、历史讨论列表
- 只调用 `RoundtableService`，不直接碰 provider/DB
- 缺少 key 时给出友好提示（指向 `.env.example`）

**测试**：`streamlit.testing.v1.AppTest` + 注入 Fake 服务：页面渲染、揭晓前不出现模型名、揭晓后出现。

## Phase 9 — 打磨
- token / 费用统计展示、并发与速率限制、超时配置
- README（安装、配置 `.env`、加模型示例、加环节示例、加提示词版本示例）
- 可选：CI（GitHub Actions 跑 ruff + pytest）

---

## 待确认的设计决策（暂按默认值推进）
| 问题 | 默认 |
|---|---|
| 主持人是否也参与作答 | 参与（主持人从上桌席位中产生） |
| 互评形式 | 文字点评 + 1–10 分 + 指出错误 |
| 修订轮数 | 1 轮，配置项可调 |
| 默认席位数 N | 3 |
| 单模型调用失败 | 记录错误、该席位后续环节跳过，剩余 ≥2 席则继续 |
| 模板引擎 | `string.Template`（零依赖），需要条件逻辑时再换 Jinja2 |
